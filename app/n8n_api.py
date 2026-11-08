"""Cổng chỉ đọc cho n8n trên Z2 (Đợt 4 rút gọn đăng web, TUẤN 08/11/2026).

n8n gọi lúc 8:00 và 16:00 để biết Đăng web có việc nào chờ người, rồi nhắn Zalo kèm link về app.
n8n KHÔNG duyệt, không đăng được gì: người vẫn phải mở app bấm "Duyệt và đăng".
Xác thực bằng khóa riêng "n8n" tạo ở Quản trị › Trợ lý AI (phạm vi n8n, chỉ dùng từ trong máy chủ).
Chỉ đọc thư mục hàng việc Đăng web, không đụng database nghiệp vụ.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from dang_web import hang_doi as hd

from . import ai_agent
from .config import get_settings
from .db import get_session

router = APIRouter(prefix='/n8n', tags=['n8n'])

MAX_LINES = 5   # mỗi nhóm liệt kê tối đa chừng này việc, còn lại ghi "và N việc khác"
OLD_HOURS = 24  # chờ xem trước quá chừng này giờ thì ghi chú "chờ hơn 1 ngày"
NHOM = (
    (hd.XEM_TRUOC, 'Chờ anh xem trước', True),
    (hd.CO_THE_TRUNG, 'Có thể trùng, máy chưa viết', False),
    (hd.CHO_DAN, 'Chờ dán câu trả lời ChatGPT', False),
    (hd.LOI, 'Máy đăng web bị lỗi', False),
)


def n8n_key(request: Request, session: Session = Depends(get_session)):
    raw = request.headers.get('authorization', '')
    raw = raw[7:].strip() if raw.lower().startswith('bearer ') else ''
    return ai_agent.authenticate(session, raw, request.headers, 'n8n')


def _base() -> str:
    from .dispatch_alerts import base_url

    return base_url()


def _cho_nguoi(v: dict) -> bool:
    """Việc đang chờ người làm. Bài mạng xã hội anh đăng tay nên không nhắc; việc chờ Codex là máy đang làm."""
    if v.get('loai') == 'mang_xa_hoi':
        return False
    if v.get('trang_thai') == hd.CHO_DAN and v.get('cach_viet') == 'codex':
        return False
    return v.get('trang_thai') in hd.NGUOI_LAM


def _ten(v: dict) -> str:
    ma, ten = v.get('ma') or '', v.get('ten') or v.get('chu_de') or ''
    if ma and ten and ten != ma:
        return f'{ma} · {ten}'
    return ma or ten or v.get('id', '')


def _cu(v: dict, now: datetime) -> bool:
    try:
        return now - datetime.fromisoformat(v.get('cap_nhat') or '') > timedelta(hours=OLD_HOURS)
    except ValueError:
        return False


def summary(goc, base: str, now: datetime | None = None) -> dict:
    """Việc Đăng web đang chờ người, kèm tin Zalo soạn sẵn. Không có việc thì tin rỗng."""
    now = now or hd.bay_gio()
    viec = hd.ds(goc)
    cho = [v for v in viec if _cho_nguoi(v)]
    nhom = {}
    blocks = []
    for tt, tieu_de, danh_dau_cu in NHOM:
        ds = sorted((v for v in cho if v.get('trang_thai') == tt), key=lambda v: v.get('cap_nhat', ''))
        nhom[tt] = len(ds)
        if not ds:
            continue
        dong = [f'{tieu_de} ({len(ds)}):']
        for v in ds[:MAX_LINES]:
            ghi = ' (chờ hơn 1 ngày)' if danh_dau_cu and _cu(v, now) else ''
            dong.append(f"- {_ten(v)}{ghi}: {base}/dang-web/viec/{v['id']}")
        if len(ds) > MAX_LINES:
            dong.append(f'- và {len(ds) - MAX_LINES} việc khác')
        blocks.append('\n'.join(dong))
    may_cho = sum(1 for v in viec if v.get('trang_thai') in (hd.CHO_SOAN, hd.CHO_DANG))
    nhip = hd.doc_nhip(goc)
    may_tat = may_cho > 0 and not (nhip and nhip.get('song'))
    if may_tat:
        blocks.append(f'Máy đăng web trên Z2 đang tắt, {may_cho} việc chưa ai làm. Trên Z2 chạy lại lệnh cập nhật app.')
    so_viec = len(cho)
    ra = {'so_viec': so_viec, 'cho_xem_truoc': nhom.get(hd.XEM_TRUOC, 0), 'co_the_trung': nhom.get(hd.CO_THE_TRUNG, 0),
          'cho_dan': nhom.get(hd.CHO_DAN, 0), 'loi': nhom.get(hd.LOI, 0), 'may_tat': may_tat,
          'link': f'{base}/dang-web?tab=san-pham&loc=xem-truoc', 'co_tin': False, 'tin': ''}
    if not blocks:
        return ra
    head = f'Đăng web: {so_viec} việc đang chờ anh' if so_viec else 'Đăng web: cần xem máy đăng web'
    tin = '\n\n'.join([head] + blocks + [f"Mở app: {ra['link']}"])
    return {**ra, 'co_tin': True, 'tin': tin[:1900]}


@router.get('/viec-cho', dependencies=[Depends(n8n_key)])
def waiting():
    return summary(Path(get_settings().web_dir), _base())
