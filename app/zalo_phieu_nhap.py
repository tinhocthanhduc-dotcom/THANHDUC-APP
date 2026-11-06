"""Phiếu dịch vụ / mực nháp từ Zalo (TUẤN duyệt mẫu 06/11/2026, Trợ lý Zalo đợt 2).

- Bot chỉ ghi một dòng NHÁP (zalo_ticket_drafts): khách, loại, lỗi, hẹn mong muốn, mức gấp, tin gốc.
  Nháp không phải phiếu thật: không vào hàng điều phối, KPI, báo cáo, không giữ máy, không giao thợ.
- Điều phối bấm Tạo phiếu: mở màn Nhận đơn dịch vụ đang dùng, đã điền sẵn; điều phối tự chọn máy, thợ rồi Lưu.
  Phiếu thật vẫn đi qua service_tickets.create như bấm tay. Lưu xong nháp chuyển "Đã tạo phiếu".
- Bỏ nháp: chọn lý do. Mọi thao tác có nhật ký (audit). Bot báo lại nhân viên đã nhắn; khách nhắn thì điều phối gọi lại.
"""
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import audit, doc_numbers
from .errors import BusinessError
from .models import Customer, ServiceTicket, User, ZaloChat, ZaloTicketDraft
from .security import has_role
from .service_sla import TZ

VIEWS = {'cho': ('Chờ duyệt', 'CHO'), 'da-tao': ('Đã tạo phiếu', 'DA_TAO'), 'da-bo': ('Đã bỏ', 'DA_BO')}
KINDS = {'SUA_TAI_CHO': 'Sửa tại chỗ', 'MANG_VE': 'Mang về xưởng', 'MUC_DINH_KY': 'Mực định kỳ'}
PRIORITIES = {'KHAN': 'Khẩn', 'GAP': 'Gấp', 'THUONG': 'Thường'}
SLOTS = {'SANG': 'Sáng', 'CHIEU': 'Chiều'}
DROP_REASONS = {'TRUNG': 'Trùng phiếu đã có', 'KHACH_HUY': 'Khách không cần nữa', 'HIEU_SAI': 'Bot hiểu sai tin', 'KHAC': 'Lý do khác'}
SOURCES = {'NHAN_VIEN': 'Nhân viên nhắn', 'KHACH': 'Khách nhắn'}
MAX_DAYS = 30


def _now():
    return datetime.now(TZ)


def check_reader(user: User) -> None:
    if not user.active or not has_role(user, 'dispatcher'):
        raise BusinessError('FORBIDDEN', 'Chỉ điều phối hoặc giám đốc được xem phiếu nháp từ Zalo.', status=403)


def can_draft(user: User) -> bool:
    """Nhân viên được nhờ bot lên phiếu nháp: người được nhận đơn dịch vụ trên app."""
    from .service_tickets import CREATORS
    return user.active and has_role(user, *CREATORS)


def _want_date(raw) -> date | None:
    try:
        value = date.fromisoformat(str(raw or '')[:10])
    except ValueError:
        return None
    today = _now().date()
    return value if today <= value <= today + timedelta(days=MAX_DAYS) else None


def create(session: Session, *, message_id: int, log_id: int | None, source: str, sender: User | None, customer_code: str,
           kind: str, symptom: str, want_date, slot: str, priority: str, ink_hint: str, question: str) -> ZaloTicketDraft:
    """Ghi một phiếu nháp. Mỗi tin Zalo tối đa một nháp. Lỗi trả BusinessError để bot báo lại cho AI."""
    customer = session.get(Customer, str(customer_code or '').strip())
    if customer is None or customer.status != 'DANG_DUNG':
        raise BusinessError('BAD_CUSTOMER', 'Không có mã khách này hoặc khách đã ngừng. Tìm lại bằng tim_khach.')
    symptom = str(symptom or '').strip()[:2000]
    kind = kind if kind in KINDS else None
    if kind == 'MUC_DINH_KY' and len(symptom) < 3:
        symptom = 'Giao mực định kỳ'
    if len(symptom) < 3:
        raise BusinessError('BAD_SYMPTOM', 'Thiếu mô tả lỗi hoặc yêu cầu của khách.')
    if session.scalar(select(ZaloTicketDraft.id).where(ZaloTicketDraft.message_id == message_id)) is not None:
        raise BusinessError('DUPLICATE', 'Tin này đã có phiếu nháp rồi.')
    day = _want_date(want_date)
    row = ZaloTicketDraft(number=doc_numbers.next_number(session, 'NZ', _now().date()), message_id=message_id, log_id=log_id,
                          source=source, sender_id=sender.id if sender else None, customer_code=customer.code, kind=kind,
                          priority=priority if priority in PRIORITIES else 'THUONG', symptom=symptom, want_date=day,
                          slot=slot if day and slot in SLOTS else None, ink_hint=str(ink_hint or '')[:500], question=str(question or '')[:2000])
    session.add(row)
    session.flush()
    audit.record(session, sender, 'zalo_draft.create', 'zalo_ticket_draft', str(row.id), None,
                 dict(number=row.number, customer_code=row.customer_code, kind=row.kind, source=source))
    session.commit()
    return row


def want_label(row: ZaloTicketDraft) -> str:
    if row.want_date is None:
        return 'Chưa hẹn'
    return (SLOTS[row.slot] + ' ' if row.slot else '') + row.want_date.strftime('%d/%m')


def counts(session: Session) -> dict:
    got = dict(session.execute(select(ZaloTicketDraft.status, func.count()).group_by(ZaloTicketDraft.status)).all())
    return {key: int(got.get(status, 0)) for key, (_, status) in VIEWS.items()}


def waiting(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(ZaloTicketDraft).where(ZaloTicketDraft.status == 'CHO')) or 0)


def rows(session: Session, view: str, offset: int, size: int) -> list[ZaloTicketDraft]:
    status = VIEWS.get(view, VIEWS['cho'])[1]
    order = (ZaloTicketDraft.created_at.asc(), ZaloTicketDraft.id.asc()) if status == 'CHO' else (ZaloTicketDraft.decided_at.desc(), ZaloTicketDraft.id.desc())
    return list(session.scalars(select(ZaloTicketDraft).where(ZaloTicketDraft.status == status).order_by(*order).offset(offset).limit(size)))


def load(session: Session, user: User, draft_id: int) -> ZaloTicketDraft:
    check_reader(user)
    row = session.get(ZaloTicketDraft, draft_id)
    if row is None:
        raise BusinessError('NOT_FOUND', 'Không có phiếu nháp này.', status=404)
    return row


def prefill(session: Session, row: ZaloTicketDraft, now=None) -> dict:
    """Dữ liệu điền sẵn cho màn Nhận đơn dịch vụ. Không chọn máy (TUẤN 05/11: không tự gắn máy cũ)."""
    from . import service_appointments
    data = dict(customer_code=row.customer_code, kind=row.kind or '', priority=row.priority, symptom=row.symptom, warranty_of_id='')
    if row.want_date and row.slot:
        value = f'{row.slot}:{row.want_date.isoformat()}'
        if any(c['value'] == value for c in service_appointments.choices(now or _now())):
            data['appointment_choice'] = value
    return data


def _check_open(row: ZaloTicketDraft):
    if row.status != 'CHO':
        raise BusinessError('BAD_STATUS', 'Nháp này đã được xử lý rồi.', status=409)


def check_open(session: Session, user: User, draft_id: int) -> ZaloTicketDraft:
    row = load(session, user, draft_id)
    if row.status == 'DA_TAO' and row.ticket_id:
        ticket = session.get(ServiceTicket, row.ticket_id)
        raise BusinessError('BAD_STATUS', f'Nháp {row.number} đã thành phiếu {ticket.number if ticket else ""}.', status=409)
    _check_open(row)
    return row


def mark_created(session: Session, user: User, draft_id: int, ticket: ServiceTicket) -> ZaloTicketDraft | None:
    """Gọi ngay sau khi phiếu thật đã lưu. Nháp đã xử lý (hai người bấm cùng lúc) thì để nguyên, không lỗi."""
    row = session.scalars(select(ZaloTicketDraft).where(ZaloTicketDraft.id == draft_id).with_for_update()).first()
    if row is None or row.status != 'CHO' or not has_role(user, 'dispatcher'):
        session.rollback()
        return None
    row.status, row.ticket_id, row.decided_by, row.decided_at = 'DA_TAO', ticket.id, user.id, _now()
    row.version += 1
    audit.record(session, user, 'zalo_draft.ticket', 'zalo_ticket_draft', str(row.id), dict(status='CHO'),
                 dict(status='DA_TAO', ticket=ticket.number))
    session.commit()
    _tell_sender(session, row, f'Phiếu nháp {row.number} đã thành phiếu dịch vụ {ticket.number}. Điều phối đã nhận và sẽ giao thợ.')
    return row


def drop(session: Session, user: User, draft_id: int, version: int, reason: str, note: str = '') -> ZaloTicketDraft:
    row = load(session, user, draft_id)
    _check_open(row)
    if row.version != version:
        raise BusinessError('STALE', 'Nháp vừa được người khác sửa, hãy tải lại trang.', status=409)
    if reason not in DROP_REASONS:
        raise BusinessError('BAD_REASON', 'Hãy chọn lý do bỏ nháp.')
    note = str(note or '').strip()[:500]
    if reason == 'KHAC' and len(note) < 3:
        raise BusinessError('BAD_REASON', 'Lý do khác: ghi rõ vài chữ.')
    row.status, row.drop_reason, row.drop_note, row.decided_by, row.decided_at = 'DA_BO', reason, note, user.id, _now()
    row.version += 1
    audit.record(session, user, 'zalo_draft.drop', 'zalo_ticket_draft', str(row.id), dict(status='CHO'), dict(status='DA_BO'),
                 reason=DROP_REASONS[reason] + (': ' + note if note else ''))
    session.commit()
    _tell_sender(session, row, f'Phiếu nháp {row.number} ({row.customer_code}) đã bị bỏ: {DROP_REASONS[reason].lower()}'
                               + (f' ({note})' if note else '') + '.')
    return row


def _tell_sender(session: Session, row: ZaloTicketDraft, text: str) -> None:
    """Báo lại nhân viên đã nhắn (chat riêng đã gắn). Khách nhắn thì không báo: điều phối gọi lại. Lỗi gửi không chặn việc."""
    if row.source != 'NHAN_VIEN' or not row.sender_id:
        return
    chat = session.scalars(select(ZaloChat).where(ZaloChat.user_id == row.sender_id, ZaloChat.chat_type == 'PRIVATE')
                           .order_by(ZaloChat.last_message_at.desc().nulls_last(), ZaloChat.id.desc()).limit(1)).first()
    if chat is None:
        return
    from . import zalo_bot
    try:
        zalo_bot.send_text(session, chat, text, None, 'tro_ly')
    except Exception:  # noqa: BLE001 - chưa cấu hình bot hay Zalo lỗi: việc trên app vẫn xong
        session.rollback()
