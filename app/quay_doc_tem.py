"""Chụp tem máy tại quầy (TUẤN duyệt mẫu 06/11/2026, https://claude.ai/artifact/HzeEaGGoz17dychKBFm3cR).

- Màn Nhận máy khách mang tới có ô Chụp tem. Ảnh gửi máy Z2 (Codex/Claude, cùng hàng chờ với Trợ lý Zalo) đọc model + serial.
- App (không phải AI) tra serial: có thì trả khách, máy, bảo hành, lần sửa gần nhất; chưa có thì gợi ý mã hàng theo model.
- Chỉ điền sẵn trên trình duyệt. Nhân viên vẫn bấm Nhận máy; AI không lưu gì. Z2 không chạy thì nhập tay, không gọi API.
- Ảnh lưu ở files_dir/zalo_anh/quay/u{người chụp}/..., chỉ người chụp xem được; tự xóa sau 30 ngày
  (khi tick "Thêm ảnh tem vào Ảnh máy lúc nhận" thì ảnh được chép vào phiếu, không phụ thuộc tệp này).
"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import products, service_counter, zalo_agent, zalo_doc_anh
from .equipment import normalize_serial
from .errors import BusinessError
from .models import Customer, Equipment, Product, ServiceTicket, User, ZaloAiJob

SUB = 'quay'
WAIT = 200  # giây: quá thì đóng lượt, màn báo nhập tay
MAX_BYTES = 30 * 1024 * 1024

PROMPT = """Bạn đọc MỘT ảnh chụp tem/nhãn dán trên máy in, máy photo, máy scan do khách mang tới cửa hàng Tin Học Thành Đức.
Ảnh nằm ở tệp đính kèm{where}. Chỉ đọc chữ/số NHÌN THẤY trên tem. Không đoán, không bịa: chỗ nào mờ, lóa, bị che thì để trống
và ghi lý do vào "chua_ro". Nếu không thấy ảnh hoặc ảnh không phải tem máy thì trả {{"loai":"KHAC","ro":false,"chua_ro":"<lý do>"}}.

Serial thường ghi sau chữ S/N, Serial No., SER, Số máy (KHÔNG lấy mã sản phẩm P/N, Product No., mã vạch model, số CE, số REG).
Model là tên máy (ví dụ LaserJet Pro M404dn, LBP2900, L3110, ECOSYS M2040dn); "hang" là hãng (HP, Canon, Epson, Brother, Ricoh...).

Chỉ trả về MỘT khối JSON đúng dạng sau, không thêm chữ nào khác:
{{"loai": "TEM_MAY", "ro": true, "chua_ro": "", "tem": {{"hang": "", "model": "", "serial": ""}}}}
"ro" = false nếu không đọc chắc được serial."""


def _prefix(user: User) -> str:
    return f'{zalo_doc_anh.FOLDER}/{SUB}/u{user.id}/'


def _save(user: User, content: bytes) -> str:
    rel = zalo_doc_anh.save(content)  # zalo_anh/YYYY/MM/<uuid>.jpg
    src = zalo_doc_anh.path_of(rel)
    new_rel = _prefix(user) + rel.removeprefix(zalo_doc_anh.FOLDER + '/')
    target = zalo_doc_anh._root() / new_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    src.replace(target)
    return new_rel


def start(session: Session, user: User, data: bytes) -> ZaloAiJob:
    service_counter._require(user)
    if not data:
        raise BusinessError('PHOTO_REQUIRED', 'Chưa có ảnh tem.')
    if len(data) > MAX_BYTES:
        raise BusinessError('FILE_TOO_LARGE', 'Ảnh lớn hơn 30 MB.')
    cfg = zalo_agent.config(session)
    if cfg.staff_engine not in ('codex', 'claude') or not zalo_agent.z2_alive(session):
        raise BusinessError('NO_Z2', 'Máy Z2 đang không chạy nên chưa đọc ảnh được. Anh/chị nhập tay bên dưới.', status=409)
    content = zalo_doc_anh.prepare(data)
    cleanup(session)
    where = ' (tệp anh.jpg trong thư mục hiện tại)' if cfg.staff_engine == 'claude' else ''
    job = ZaloAiJob(log_id=None, engine=cfg.staff_engine, prompt=PROMPT.format(where=where), image_path=_save(user, content))
    session.add(job)
    session.commit()
    return job


def load(session: Session, user: User, job_id: int) -> ZaloAiJob:
    service_counter._require(user)
    job = session.get(ZaloAiJob, job_id)
    if job is None or job.log_id is not None or not job.image_path.startswith(_prefix(user)):
        raise BusinessError('NOT_FOUND', 'Không có lượt đọc tem này.', status=404)
    return job


def image(session: Session, user: User, job_id: int):
    path = zalo_doc_anh.path_of(load(session, user, job_id).image_path)
    if path is None:
        raise BusinessError('NOT_FOUND', 'Ảnh tem đã bị xóa.', status=404)
    return path


def photo(session: Session, user: User, job_id) -> tuple[str, bytes] | None:
    """Ảnh tem để thêm vào Ảnh máy lúc nhận (ô tick). Lượt không hợp lệ / ảnh đã xóa thì bỏ qua, không chặn nhận máy."""
    try:
        path = image(session, user, int(job_id))
    except (BusinessError, ValueError, TypeError):
        return None
    return 'tem-may.jpg', path.read_bytes()


def _d(v):
    return v.strftime('%d/%m/%Y') if v else ''


def _product_hint(session: Session, hang: str, model: str) -> dict:
    """Mã hàng gợi ý theo model: chỉ chọn sẵn khi một cách tìm ra đúng 1 mã; không thì để nhân viên chọn trong danh sách."""
    tries = [model, f'{hang} {model}'.strip()]
    tries += [w for w in model.split() if any(c.isdigit() for c in w) and len(w) >= 3]
    for q in tries:
        if not q.strip():
            continue
        rows = products.search(session, q, active_only=True, limit=2)
        if len(rows) == 1:
            return dict(code=rows[0].code, name=rows[0].name)
    return {}


def lookup(session: Session, read: dict) -> dict:
    tem = read.get('tem') if isinstance(read.get('tem'), dict) else {}
    hang, model = str(tem.get('hang') or '').strip()[:60], str(tem.get('model') or '').strip()[:100]
    serial = normalize_serial(str(tem.get('serial') or ''))[:64]
    out = dict(status='XONG', hang=hang, model=model, serial=serial, ro=bool(read.get('ro')) and bool(serial),
               chua_ro=str(read.get('chua_ro') or '')[:200], loai=read.get('loai'))
    if not out['ro']:
        if not out['chua_ro']:
            out['chua_ro'] = 'không thấy số serial' if read.get('loai') == 'TEM_MAY' else 'ảnh không giống tem máy'
        return out
    eq = session.get(Equipment, serial)
    if eq is None:
        out['found'] = False
        out['near'] = list(session.scalars(select(Equipment.serial).where(Equipment.serial.ilike(f'%{serial}%'), Equipment.serial != serial).limit(3)))
        out['product'] = _product_hint(session, hang, model)
        return out
    from . import service_warranty
    from .zalo_tra_cuu import _now
    today = _now().date()
    product = session.get(Product, eq.product_code) if eq.product_code else None
    customer = session.get(Customer, eq.customer_code) if eq.customer_code else None
    out.update(found=True, machine=f"{eq.serial} · {product.name if product else (eq.model_name or 'chưa rõ model')}",
               machine_name=product.name if product else (eq.model_name or ''), active=bool(eq.active),
               customer=dict(code=customer.code, name=customer.name,
                             active=customer.status == 'DANG_DUNG') if customer else None,
               warranty_until=_d(eq.warranty_until), warranty_ok=bool(eq.warranty_until and eq.warranty_until >= today))
    if eq.custody_location != 'TAI_KHACH' and eq.custody_ticket_id:
        held = session.get(ServiceTicket, eq.custody_ticket_id)
        out['held'] = held.number if held else f'#{eq.custody_ticket_id}'
    last = session.scalars(select(ServiceTicket).where(ServiceTicket.serial == eq.serial, ServiceTicket.status.in_(('HOAN_THANH', 'DA_DONG')))
                           .order_by(ServiceTicket.priced_at.desc().nulls_last(), ServiceTicket.id.desc()).limit(1)).first()
    if last is not None:
        end = service_warranty.warranty_end(session, last)
        when = last.priced_at or last.created_at
        out['last'] = dict(number=last.number, date=_d(when.date() if when else None), until=_d(end), ok=bool(end and end >= today))
    return out


def state(session: Session, user: User, job_id: int) -> dict:
    job = load(session, user, job_id)
    if job.status in ('CHO', 'DANG'):
        if zalo_agent._now() - job.created_at > timedelta(seconds=WAIT):
            job.status, job.error, job.done_at = 'LOI', f'Quá {WAIT} giây máy Z2 chưa trả lời', zalo_agent._now()
            session.commit()
        else:
            return dict(status='DANG')
    if job.status == 'LOI':
        return dict(status='LOI', error='Máy Z2 chưa đọc được ảnh (' + (job.error or 'không rõ lý do')[:120] + '). Chụp lại hoặc nhập tay.')
    return lookup(session, zalo_doc_anh.parse(job.answer))


def cleanup(session: Session) -> int:
    """Xóa ảnh tem quá 30 ngày (lượt đọc tem không gắn nhật ký Zalo)."""
    old = list(session.scalars(select(ZaloAiJob).where(
        ZaloAiJob.log_id.is_(None), ZaloAiJob.image_path.like(f'{zalo_doc_anh.FOLDER}/{SUB}/%'),
        ZaloAiJob.created_at < zalo_agent._now() - timedelta(days=zalo_doc_anh.KEEP_DAYS)).limit(200)))
    for job in old:
        p = zalo_doc_anh.path_of(job.image_path)
        if p is not None:
            p.unlink(missing_ok=True)
        job.image_path = ''
    session.commit()
    return len(old)
