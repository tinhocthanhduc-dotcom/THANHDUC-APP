"""Tất cả tin Zalo (TUẤN duyệt mẫu zalo.quanlythanhduc.com 19/11/2026, đợt 1).

Xem lại mọi hội thoại khách nhắn vào Zalo Bot theo từng người, kèm khách là ai và đang có việc gì.
- Giám đốc thấy mọi hội thoại (cả nhân viên, nhóm). Kinh doanh, điều phối chỉ thấy hội thoại riêng
  của khách đã gắn hoặc người chưa gắn; không thấy tin nhân viên hỏi bot.
- Trả lời đi ra từ Zalo Bot (zalo_bot.send_text), giới hạn như màn Tin nhắn Zalo.
- Gắn hội thoại với khách/nhân viên vẫn chỉ giám đốc, ở màn Tin nhắn Zalo.
- Công nợ chỉ hiện cho kế toán/giám đốc. Không đổi tiền, kho, công nợ.
"""
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import zalo_bot as bot
from .errors import BusinessError
from .models import Customer, SalesOrder, ServiceTicket, User, ZaloChat, ZaloMessage, ZaloTriage
from .security import has_role
from .zalo_tra_cuu import KINDS, ORDER_STATUSES, TICKET_STATUSES

VIEWERS = ('sales', 'dispatcher')
TABS = (('tat-ca', 'Tất cả'), ('khach', 'Khách'), ('chua-gan', 'Chưa gắn khách'), ('nhan-vien', 'Nhân viên'))
DIRECTOR_TABS = ('nhan-vien',)
CLOSED_TICKETS = ('DA_DONG', 'DA_HUY', 'KHONG_LAM_DUOC')


def is_director(user: User) -> bool:
    return user.active and 'director' in user.roles


def can_view(user: User) -> bool:
    return bool(user.active) and has_role(user, *VIEWERS)


def tabs(user: User) -> tuple:
    return tuple(t for t in TABS if t[0] not in DIRECTOR_TABS or is_director(user))


def _scope(stmt, user: User):
    """Nhân viên không phải giám đốc: chỉ hội thoại riêng chưa gắn nhân viên (khách hoặc người lạ)."""
    if is_director(user):
        return stmt
    return stmt.where(ZaloChat.chat_type == 'PRIVATE', ZaloChat.user_id.is_(None))


def _tab(stmt, tab: str):
    if tab == 'khach':
        return stmt.where(ZaloChat.customer_code.is_not(None))
    if tab == 'chua-gan':
        return stmt.where(ZaloChat.chat_type == 'PRIVATE', ZaloChat.customer_code.is_(None), ZaloChat.user_id.is_(None))
    if tab == 'nhan-vien':
        return stmt.where(ZaloChat.user_id.is_not(None))
    return stmt


def _search(stmt, q: str):
    q = (q or '').strip()
    if not q:
        return stmt
    like = f'%{q}%'
    named = select(Customer.code).where(or_(Customer.name.ilike(like), Customer.code.ilike(like)))
    return stmt.where(or_(ZaloChat.display_name.ilike(like), ZaloChat.last_text.ilike(like), ZaloChat.chat_id.ilike(like),
                          ZaloChat.customer_code.in_(named)))


def _base(user: User, tab: str, q: str):
    if tab not in dict(tabs(user)):
        tab = 'tat-ca'
    return _search(_tab(_scope(select(ZaloChat), user), tab), q)


def count_chats(session: Session, user: User, tab: str = 'tat-ca', q: str = '') -> int:
    return session.scalar(select(func.count()).select_from(_base(user, tab, q).subquery())) or 0


def counts(session: Session, user: User, q: str = '') -> dict:
    return {key: count_chats(session, user, key, q) for key, _ in tabs(user)}


def list_chats(session: Session, user: User, tab: str, q: str, offset: int, size: int) -> list[ZaloChat]:
    stmt = _base(user, tab, q).order_by(ZaloChat.last_message_at.desc().nulls_last(), ZaloChat.id.desc())
    return list(session.scalars(stmt.offset(offset).limit(size)))


def get_chat(session: Session, user: User, chat_pk: int) -> ZaloChat:
    """Hội thoại người xem được mở; ngoài phạm vi thì báo không thấy (không lộ là có)."""
    if not can_view(user):
        raise BusinessError('FORBIDDEN', 'Bạn không có quyền xem tin Zalo.', status=403)
    chat = session.scalars(_scope(select(ZaloChat).where(ZaloChat.id == chat_pk), user)).first()
    if chat is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy hội thoại.', status=404)
    return chat


def waiting(session: Session, chat_ids) -> set[int]:
    """Hội thoại còn tin khách AI đã chia mà chưa ai xử lý (như hộp Cần trả lời)."""
    ids = [i for i in chat_ids if i]
    if not ids:
        return set()
    rows = session.scalars(select(ZaloMessage.chat_pk).join(ZaloTriage, ZaloTriage.message_id == ZaloMessage.id).where(
        ZaloMessage.chat_pk.in_(ids), ZaloTriage.done_at.is_(None), ZaloTriage.status.in_(('XONG', 'LOI'))).distinct())
    return set(rows)


def customers(session: Session, chats) -> dict:
    codes = {c.customer_code for c in chats if c.customer_code}
    return {c.code: c for c in session.scalars(select(Customer).where(Customer.code.in_(codes)))} if codes else {}


def panel(session: Session, user: User, chat: ZaloChat) -> dict:
    """Khung phải: khách là ai, phiếu dịch vụ đang mở, đơn gần nhất, còn nợ (chỉ kế toán/giám đốc)."""
    from .web import nav
    out = dict(customer=None, tickets=[], orders=[], debt=None, staff=None)
    if chat.user_id:
        out['staff'] = session.get(User, chat.user_id)
    if not chat.customer_code:
        return out
    customer = session.get(Customer, chat.customer_code)
    out['customer'] = customer
    if customer is None:
        return out
    rows = session.scalars(select(ServiceTicket).where(ServiceTicket.customer_code == customer.code,
                                                       ServiceTicket.status.not_in(CLOSED_TICKETS))
                           .order_by(ServiceTicket.created_at.desc()).limit(5))
    out['tickets'] = [dict(number=t.number, kind=KINDS.get(t.kind or '', 'Phiếu dịch vụ'), status=TICKET_STATUSES.get(t.status, t.status),
                           done=t.status == 'HOAN_THANH', url=nav.ticket_url(user, t.id)) for t in rows]
    if nav.allowed(user, '/don-ban'):
        rows = session.scalars(select(SalesOrder).where(SalesOrder.customer_code == customer.code, SalesOrder.status != 'DA_HUY')
                               .order_by(SalesOrder.created_at.desc()).limit(3))
        out['orders'] = [dict(number=o.number, status=ORDER_STATUSES.get(o.status, o.status), url=f'/don-ban/{o.id}') for o in rows]
    if has_role(user, 'finance'):
        from . import finance_reports
        out['debt'] = sum(int(d['remaining']) for d in finance_reports.customer_docs(session, user, customer.code))
    return out


def reply(session: Session, user: User, chat_pk: int, text: str) -> ZaloMessage:
    chat = get_chat(session, user, chat_pk)
    text = bot._no_nul(text or '').strip()
    if not text:
        raise BusinessError('VALIDATION', 'Hãy nhập nội dung trả lời.', status=422)
    if len(text) > bot.MAX_TEXT:
        raise BusinessError('VALIDATION', f'Tin nhắn Zalo tối đa {bot.MAX_TEXT} ký tự.', status=422)
    return bot.send_text(session, chat, text, user)


def unread_chats(session: Session, user: User) -> int:
    """Số hội thoại có tin chưa xem (số trên tab Tất cả tin)."""
    return session.scalar(select(func.count()).select_from(_scope(select(ZaloChat).where(ZaloChat.unread > 0), user).subquery())) or 0
