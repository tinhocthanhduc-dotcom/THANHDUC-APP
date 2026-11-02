"""Cửa /ai/dang-web cho Codex trên máy chủ Z2 (TUẤN 01/11/2026, đợt 2).

Codex không gọi API OpenAI: máy đăng web vẫn soạn câu lệnh như cách "ChatGPT web (dán tay)", chỉ khác là
Codex (gói ChatGPT trên Z2) lấy câu lệnh qua cửa này, trả lời, và app ghi câu trả lời như khi anh dán tay.
Codex chỉ trả lời câu lệnh có sẵn: không tạo việc, không đăng, không xem giá. Mọi lần trả lời đều vào nhật ký.
"""
from __future__ import annotations

from dang_web import hang_doi as hd
from dang_web.viet_bai import doc_json_tu_van_ban

from . import audit
from .errors import BusinessError

TOI_DA_TRA_LOI = 60_000


def _goc():
    from .web.dang_web import goc
    return goc()


def _can_ghi(g):
    loi = hd.kiem_ghi_duoc(g)
    if loi:
        raise BusinessError('WEB_DIR', loi, status=503)


def lay_viec(session, key) -> list[dict]:
    """Codex hỏi việc: ghi nhịp (để app biết Codex đang chạy) rồi trả các câu lệnh đang chờ."""
    g = _goc()
    _can_ghi(g)
    hd.ghi_nhip_codex(g)
    return [dict(id=v['id'], loai=v.get('loai'), kieu=v.get('kieu') or 'soan', ten=v.get('ten') or v.get('chu_de') or v.get('ma') or '',
                 lenh=v['lenh_chatgpt']) for v in hd.viec_cho_codex(g)]


def _viec_codex(g, viec_id):
    v = hd.doc(g, viec_id)
    if not v:
        raise BusinessError('NOT_FOUND', 'Không thấy việc này.', status=404)
    if v.get('cach_viet') != 'codex':
        raise BusinessError('AI_SCOPE', 'Việc này không giao cho Codex.', status=403)
    if v.get('trang_thai') != hd.CHO_DAN or not hd.TEN_FILE_RE.match(v.get('file_tra_loi') or ''):
        raise BusinessError('BAD_STATE', 'Việc này không còn chờ câu trả lời.', status=409)
    return v


def tra_loi(session, key, viec_id: str, van_ban: str) -> dict:
    g = _goc()
    _can_ghi(g)
    van_ban = (van_ban or '').strip()
    if not van_ban:
        raise BusinessError('BAD_FORM', 'Câu trả lời trống.', status=422)
    if len(van_ban) > TOI_DA_TRA_LOI:
        raise BusinessError('BAD_FORM', 'Câu trả lời quá dài.', status=422)
    try:
        doc_json_tu_van_ban(van_ban)
    except RuntimeError as exc:
        raise BusinessError('BAD_FORM', str(exc), status=422) from exc
    v = _viec_codex(g, viec_id)
    thu_muc = hd.thu_muc_mot_viec(g, viec_id)
    ten = v['file_tra_loi']

    def ham(x):
        if x.get('cach_viet') != 'codex' or x.get('file_tra_loi') != ten:
            raise BusinessError('BAD_STATE', 'Việc vừa đổi cách viết.', status=409)
        (thu_muc / ten).write_text(van_ban, encoding='utf-8')
        x.update(trang_thai=hd.CHO_SOAN, loi='')

    if hd.sua(g, viec_id, ham, (hd.CHO_DAN,)) is None:
        raise BusinessError('BAD_STATE', 'Việc vừa đổi trạng thái.', status=409)
    hd.ghi_nhip_codex(g)
    audit.record(session, None, 'ai.dang_web_answer', 'dang_web', viec_id, dict(trang_thai=hd.CHO_DAN),
                 dict(trang_thai=hd.CHO_SOAN, key=key.prefix, ky_tu=len(van_ban)))
    session.commit()
    return dict(id=viec_id, trang_thai=hd.CHO_SOAN)


def khong_lam_duoc(session, key, viec_id: str, ly_do: str = '') -> dict:
    """Codex lỗi hoặc hết lượt: chuyển sang AI tự động (nếu máy đăng web có khóa API), không thì về dán tay."""
    g = _goc()
    _can_ghi(g)
    _viec_codex(g, viec_id)
    ly_do = (ly_do or '').strip()[:300]
    nhip = hd.doc_nhip(g) or {}
    co_api = bool(nhip.get('ai'))

    def ham(x):
        if x.get('cach_viet') != 'codex':
            raise BusinessError('BAD_STATE', 'Việc vừa đổi cách viết.', status=409)
        if co_api:
            x.update(cach_viet='tu_dong', trang_thai=hd.CHO_SOAN, lenh_chatgpt='', file_tra_loi='',
                     loi='Codex không làm được' + (f' ({ly_do})' if ly_do else '') + '; chuyển sang AI tự động (API).')
        else:
            x.update(cach_viet='chatgpt_web',
                     loi='Codex không làm được' + (f' ({ly_do})' if ly_do else '') + '. Dán tay câu lệnh vào ChatGPT web.')

    kq = hd.sua(g, viec_id, ham, (hd.CHO_DAN,))
    if kq is None:
        raise BusinessError('BAD_STATE', 'Việc vừa đổi trạng thái.', status=409)
    audit.record(session, None, 'ai.dang_web_fail', 'dang_web', viec_id, dict(cach_viet='codex'),
                 dict(cach_viet=kq['cach_viet'], key=key.prefix), ly_do)
    session.commit()
    return dict(id=viec_id, trang_thai=kq['trang_thai'], cach_viet=kq['cach_viet'])


# ---------- vẽ ảnh minh họa bài viết (TUẤN duyệt mẫu D4/4, D5/5 ngày 02/11/2026) ----------
def lay_ve_anh(session, key) -> list[dict]:
    """Codex hỏi yêu cầu vẽ ảnh: chuyển sang đang vẽ, trả câu lệnh vẽ."""
    from dang_web import ve_anh

    g = _goc()
    _can_ghi(g)
    hd.ghi_nhip_codex(g)
    return [dict(id=d['id'], ten=d.get('chu_de', ''), so_anh=d.get('so_anh', ve_anh.SO_ANH), lenh=d['lenh'])
            for d in ve_anh.nhan_cho_codex(g)]


def nhan_anh(session, key, yc_id: str, data: bytes) -> dict:
    from dang_web import ve_anh

    g = _goc()
    _can_ghi(g)
    if not ve_anh.doc(g, yc_id):
        raise BusinessError('NOT_FOUND', 'Không thấy yêu cầu vẽ ảnh này.', status=404)
    try:
        ten = ve_anh.nhan_anh(g, yc_id, data)
    except ValueError as exc:
        raise BusinessError('BAD_FORM', str(exc), status=422) from exc
    except LookupError as exc:
        raise BusinessError('BAD_STATE', str(exc), status=409) from exc
    audit.record(session, None, 'ai.dang_web_image', 'dang_web', yc_id, None, dict(ten=ten, key=key.prefix, byte=len(data)))
    session.commit()
    return dict(id=yc_id, ten=ten)


def xong_ve_anh(session, key, yc_id: str, ly_do: str = '') -> dict:
    from dang_web import ve_anh

    g = _goc()
    _can_ghi(g)
    kq = ve_anh.ket_thuc(g, yc_id, (ly_do or '').strip()[:300])
    if kq is None:
        raise BusinessError('BAD_STATE', 'Yêu cầu này không còn chờ ảnh.', status=409)
    hd.ghi_nhip_codex(g)
    return dict(id=yc_id, trang_thai=kq['trang_thai'], so_anh=len(kq.get('anh') or []))
