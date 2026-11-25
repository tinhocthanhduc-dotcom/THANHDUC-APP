"""Cổng gửi ảnh sản phẩm sang Đăng web cho app Dịch vụ SEO (TUẤN chọn phương án A 25/11/2026, Đợt 2).

Anh chụp sản phẩm ở cổng tải của app Dịch vụ SEO, app đó khớp ra mã TD rồi gửi sang đây: ảnh + mã → một việc Đăng web
loại sản phẩm, đánh dấu tự đăng (không giá, không số tháng bảo hành). Phần soạn, kiểm trùng, tách nền, AI viết vẫn là máy
đăng web như mọi việc khác.

Khóa riêng "growth-os-dang" (phạm vi marketing_post), chỉ dùng trong máy chủ Z2 như khóa Growth OS khác. Khóa này
chỉ tạo việc sản phẩm và đọc trạng thái việc mình gửi, báo "đã nhắn Zalo" để máy hẹn giờ tự đăng. Không đọc khách,
không đụng tiền, kho, giá.

- POST /marketing/v1/dang-web/san-pham   (form: ref, ma, ghi_chu, anh × tối đa 6) → {viec_id, trang_thai}
- GET  /marketing/v1/dang-web/viec/{id}   → trạng thái, link trên web, giờ hẹn tự đăng, lý do không tự đăng
- POST /marketing/v1/dang-web/viec/{id}/da-bao → hẹn tự đăng sau 4 giờ (chỉ khi việc còn ở Xem trước)
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from dang_web import hang_doi as hd

from . import ai_agent, audit
from .config import get_settings
from .db import get_session
from .errors import BusinessError
from .models import AiKey, Product

router = APIRouter(prefix='/marketing/v1/dang-web', tags=['Growth OS (gửi ảnh sản phẩm)'])

SCOPE = 'marketing_post'  # cột scopes chỉ 16 ký tự
NGUON = 'dvseo'
TOI_DA_ANH = 6
REF_RE = re.compile(r'^[A-Za-z0-9_-]{1,40}$')


def publish_key(request: Request, session: Session = Depends(get_session)) -> AiKey:
    raw = request.headers.get('authorization', '')
    raw = raw[7:].strip() if raw.lower().startswith('bearer ') else ''
    return ai_agent.authenticate(session, raw, request.headers, SCOPE)


def goc() -> Path:
    return Path(get_settings().web_dir)


def _log(session: Session, key: AiKey, title: str, data: dict):
    audit.record(session, None, 'ai.marketing_publish', 'ai_key', str(key.id), None, dict(title=title, **data))
    session.commit()


def _cach_viet(g) -> str:
    # Codex trên Z2 (gói ChatGPT, không tốn API) chạy thì dùng; không thì AI tự động nếu máy có khóa API.
    if hd.codex_song(g):
        return 'codex'
    nhip = hd.doc_nhip(g)
    if nhip and nhip.get('ai'):
        return 'tu_dong'
    raise BusinessError('NO_WRITER', 'Codex trên Z2 đang tắt và máy đăng web chưa có khóa AI, chưa soạn được.', status=503)


def ket_qua(v: dict) -> dict:
    """Trạng thái gọn cho app Dịch vụ SEO."""
    return dict(viec_id=v['id'], trang_thai=v.get('trang_thai'), nhan=hd.NHAN.get(v.get('trang_thai'), ''),
                ma=v.get('ma', ''), ten=(v.get('noi_dung') or {}).get('ten_web') or v.get('ten', ''),
                tu_dang=bool(v.get('tu_dang')), tu_dang_luc=v.get('tu_dang_luc', ''),
                ly_do_khong_tu_dang=v.get('ly_do_khong_tu_dang', ''), loi=(v.get('loi') or '')[:300],
                wp_id=v.get('wp_id'), link_xem=v.get('link_xem', ''), trang_thai_web=v.get('trang_thai_web', ''),
                duyet_boi=v.get('duyet_boi', ''), da_go=bool(v.get('da_go')), so_trung=len(v.get('trung') or []),
                dung_tu_dang=hd.doc_cong_tac(goc())['dung'])


def _viec_cua_minh(viec_id: str) -> dict:
    v = hd.doc(goc(), viec_id)
    if not v or v.get('nguon') != NGUON:
        raise BusinessError('NOT_FOUND', 'Không thấy việc này.', status=404)
    return v


@router.post('/san-pham')
async def create(request: Request, key: AiKey = Depends(publish_key), session: Session = Depends(get_session)):
    form = await request.form()
    ref = str(form.get('ref', '')).strip()
    ma = str(form.get('ma', '')).strip().upper()
    if not REF_RE.match(ref):
        raise BusinessError('BAD_REF', 'Thiếu mã phiếu (ref).', status=422)
    g = goc()
    loi = hd.kiem_ghi_duoc(g)
    if loi:
        raise BusinessError('WEB_DIR', loi, status=503)
    cu = hd.tim_theo_ref(g, NGUON, ref)
    if cu:  # gửi lại (mạng chập chờn): trả việc cũ, không tạo trùng
        return ket_qua(cu)
    sp = session.get(Product, ma) if ma else None
    if not sp or not sp.active or sp.is_test:
        raise BusinessError('BAD_PRODUCT', f'Mã {ma or "(trống)"} không có trong Hàng hóa đang dùng.', status=422)
    gioi_han = get_settings().max_upload_mb * 1024 * 1024
    anh = []
    for up in form.getlist('anh'):
        if not isinstance(up, UploadFile) or not up.filename:
            continue
        data = await up.read(gioi_han + 1)
        if len(data) > gioi_han:
            raise BusinessError('FILE_TOO_LARGE', f'Ảnh {up.filename} quá lớn (tối đa {get_settings().max_upload_mb} MB).', status=422)
        if data:
            anh.append((up.filename, data))
    if not anh:
        raise BusinessError('NO_PHOTO', 'Cần ít nhất một ảnh sản phẩm.', status=422)
    anh = anh[:TOI_DA_ANH]
    viec_id = hd.tao_viec(g, 'san_pham', dict(
        ma=sp.code, ten=sp.name, cach_viet=_cach_viet(g), ghi_chu=str(form.get('ghi_chu', '')).strip()[:1500],
        nguon=NGUON, ref=ref, tu_dang=True), anh)
    _log(session, key, f'Growth OS gửi ảnh {sp.code} sang Đăng web', dict(ma=sp.code, ref=ref, so_anh=len(anh), viec=viec_id))
    return ket_qua(hd.doc(g, viec_id))


@router.get('/viec/{viec_id}')
def status(viec_id: str, key: AiKey = Depends(publish_key)):
    return ket_qua(_viec_cua_minh(viec_id))


@router.post('/viec/{viec_id}/da-bao')
def notified(viec_id: str, key: AiKey = Depends(publish_key), session: Session = Depends(get_session)):
    v = _viec_cua_minh(viec_id)
    if hd.doc_cong_tac(goc())['dung']:
        raise BusinessError('TU_DANG_DUNG', 'Tự đăng đang dừng ở màn Đăng web.', status=409)
    moi = hd.da_bao(goc(), viec_id)
    if moi is None:
        raise BusinessError('BAD_STATE', 'Việc này không hẹn tự đăng được (chưa soạn xong, đã hoãn hoặc có lý do chặn).', status=409)
    _log(session, key, f'Đã nhắn Zalo, hẹn tự đăng {v.get("ma")} lúc {moi.get("tu_dang_luc")}',
         dict(viec=viec_id, tu_dang_luc=moi.get('tu_dang_luc')))
    return ket_qua(moi)
