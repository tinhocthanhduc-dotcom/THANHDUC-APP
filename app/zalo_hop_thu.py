"""Tất cả tin Zalo (TUẤN duyệt mẫu zalo.quanlythanhduc.com 19/11/2026, đợt 1).

Xem lại mọi hội thoại khách nhắn vào Zalo Bot theo từng người, kèm khách là ai và đang có việc gì.
- Giám đốc thấy mọi hội thoại (cả nhân viên, nhóm). Kinh doanh, điều phối chỉ thấy hội thoại riêng
  của khách đã gắn hoặc người chưa gắn; không thấy tin nhân viên hỏi bot.
- Trả lời đi ra từ Zalo Bot (zalo_bot.send_text), giới hạn như màn Tin nhắn Zalo.
- Gắn hội thoại với khách/nhân viên vẫn chỉ giám đốc, ở màn Tin nhắn Zalo.
- Công nợ chỉ hiện cho kế toán/giám đốc. Không đổi tiền, kho, công nợ.
- Đợt 2: "AI soạn câu trả lời" nhờ Codex/Claude trên Z2 soạn từ tin gần nhất + trạng thái phiếu/đơn (không đưa tiền,
  công nợ cho AI). AI không tự gửi: bản soạn hiện trong ô trả lời, người xem sửa rồi bấm Gửi khách. Số lạ thì cảnh báo.
"""
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import zalo_bot as bot
from .errors import BusinessError
from . import audit
from .models import Customer, SalesOrder, ServiceTicket, User, ZaloAiJob, ZaloChat, ZaloMessage, ZaloReplyDraft, ZaloTriage
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


def reply(session: Session, user: User, chat_pk: int, text: str, draft_id: int | None = None) -> ZaloMessage:
    chat = get_chat(session, user, chat_pk)
    text = bot._no_nul(text or '').strip()
    if not text:
        raise BusinessError('VALIDATION', 'Hãy nhập nội dung trả lời.', status=422)
    if len(text) > bot.MAX_TEXT:
        raise BusinessError('VALIDATION', f'Tin nhắn Zalo tối đa {bot.MAX_TEXT} ký tự.', status=422)
    draft = session.get(ZaloReplyDraft, draft_id, with_for_update=True) if draft_id else None
    if draft is not None and (draft.chat_pk != chat.id or draft.status != 'XONG'):
        draft = None  # bản soạn của hội thoại khác hoặc đã đóng: gửi như tin gõ tay
    row = bot.send_text(session, chat, text, user)
    if draft is not None:
        draft.status, draft.closed_by, draft.closed_at, draft.sent_message_id = 'DA_GUI', user.id, row.message_at, row.id
        audit.record(session, user, 'zalo.ai_reply_sent', 'zalo_chat', str(chat.id), None,
                     dict(draft=draft.id, edited=text != draft.text.strip()))
        session.commit()
    return row


# ---------- Đợt 2: AI soạn sẵn câu trả lời ----------

DRAFT_WAIT = 240   # giây: quá lâu máy Z2 chưa trả lời thì báo lỗi, bấm soạn lại được
DRAFT_KEEP = timedelta(hours=12)   # bản soạn cũ hơn không hiện nữa
HISTORY = 10
DRAFT_PROMPT = """Bạn là nhân viên chăm sóc khách của Công ty Tin Học Thành Đức (bán, sửa máy in, giao/nạp mực, TP.HCM).
Soạn MỘT tin Zalo trả lời tin mới nhất của khách dưới đây. Nhân viên sẽ đọc, sửa rồi mới gửi.
Quy tắc bắt buộc:
- Chỉ dùng thông tin trong phần "Số liệu của công ty" và trong cuộc trò chuyện. Không bịa trạng thái, giờ hẹn, giá.
- Không viết con số, số tiền, ngày giờ, mã phiếu/đơn nào không có trong số liệu hoặc cuộc trò chuyện.
- Khách hỏi giá mà số liệu không có giá: nói bên em sẽ gửi báo giá ngay, không đoán giá.
- Không nhắc công nợ, không hứa giảm giá, không hứa giờ giao nếu số liệu không có.
- Xưng "em", gọi khách "anh/chị" (hoặc theo tên nếu biết). Lịch sự, ngắn gọn, dưới 600 ký tự. Văn bản thường, không markdown.
- Chỉ trả về nội dung tin, không giải thích, không bọc trong dấu ngoặc.
Hôm nay: {today}.
Số liệu của công ty:
<<<
{facts}
>>>
Cuộc trò chuyện gần đây (cũ → mới):
<<<
{history}
>>>"""


def _hm(value) -> str:
    from .service_sla import TZ
    return value.astimezone(TZ).strftime('%H:%M %d/%m/%Y') if value else ''


def draft_facts(session: Session, chat: ZaloChat) -> str:
    """Số liệu đưa cho AI: khách, phiếu dịch vụ đang mở, đơn gần nhất. Không tiền, không công nợ."""
    lines = []
    customer = session.get(Customer, chat.customer_code) if chat.customer_code else None
    if customer is None:
        lines.append('Người nhắn chưa được gắn với khách nào trong hệ thống (chưa biết là khách nào).')
        return '\n'.join(lines)
    lines.append(f'Khách: {customer.name} (mã {customer.code}).')
    tickets = list(session.scalars(select(ServiceTicket).where(ServiceTicket.customer_code == customer.code,
                                                               ServiceTicket.status.not_in(CLOSED_TICKETS))
                                   .order_by(ServiceTicket.created_at.desc()).limit(5)))
    for t in tickets:
        when = f', hẹn {_hm(t.appointment_at)}' if t.appointment_at else ''
        lines.append(f'Phiếu {t.number}: {KINDS.get(t.kind or "", "dịch vụ")}, {TICKET_STATUSES.get(t.status, t.status)}{when}.'
                     + (f' Lỗi khách báo: {(t.symptom or "")[:120]}' if t.symptom else ''))
    if not tickets:
        lines.append('Không có phiếu dịch vụ nào đang mở.')
    for o in session.scalars(select(SalesOrder).where(SalesOrder.customer_code == customer.code, SalesOrder.status.not_in(('DA_HUY', 'NHAP')))
                             .order_by(SalesOrder.created_at.desc()).limit(3)):
        due = f', hẹn giao {o.promised_date.strftime("%d/%m/%Y")}' if o.promised_date else ''
        lines.append(f'Đơn {o.number}: {ORDER_STATUSES.get(o.status, o.status)}{due}.')
    return '\n'.join(lines)


def _history(session: Session, chat: ZaloChat) -> str:
    rows = bot.list_messages(session, chat.id, 0, HISTORY)
    out = []
    for m in rows:
        who = 'Khách' if m.direction == 'IN' else 'Công ty'
        body = (m.text or f'[{bot.KIND_LABELS.get(m.kind, m.kind)}]').replace('\n', ' ')[:400]
        out.append(f'{who} ({_hm(m.message_at)}): {body}')
    return '\n'.join(out)


def request_draft(session: Session, user: User | None, chat_pk: int) -> ZaloReplyDraft:
    """user=None: Trả lời tự động (đợt 3) nhờ AI soạn khi khách hỏi giá; vẫn chỉ chờ duyệt, không tự gửi."""
    from . import zalo_agent
    from .tin_gui_khach import _now
    chat = get_chat(session, user, chat_pk) if user is not None else session.get(ZaloChat, chat_pk)
    if chat is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy hội thoại.', status=404)
    if chat.chat_type != 'PRIVATE' or chat.user_id:
        raise BusinessError('VALIDATION', 'AI chỉ soạn trả lời cho hội thoại riêng của khách.', status=422)
    if not session.scalar(select(func.count()).select_from(ZaloMessage).where(ZaloMessage.chat_pk == chat.id, ZaloMessage.direction == 'IN')):
        raise BusinessError('VALIDATION', 'Khách chưa nhắn tin nào để trả lời.', status=422)
    cfg = zalo_agent.config(session)
    if cfg.staff_engine not in ('codex', 'claude') or not zalo_agent.z2_alive(session):
        raise BusinessError('NO_Z2', 'Máy Z2 (Codex/Claude) đang không chạy nên AI chưa soạn được. Hãy gõ tay.', status=409)
    for old in session.scalars(select(ZaloReplyDraft).where(ZaloReplyDraft.chat_pk == chat.id, ZaloReplyDraft.status.in_(('DANG', 'XONG', 'LOI')))):
        old.status, old.closed_by, old.closed_at = 'BO', user.id if user else None, _now()
    facts = draft_facts(session, chat)
    from .service_sla import TZ
    job = ZaloAiJob(log_id=None, engine=cfg.staff_engine,
                    prompt=DRAFT_PROMPT.format(today=_now().astimezone(TZ).strftime('%d/%m/%Y'), facts=facts, history=_history(session, chat)))
    session.add(job)
    session.flush()
    draft = ZaloReplyDraft(chat_pk=chat.id, job_id=job.id, status='DANG', facts=facts, created_by=user.id if user else None)
    session.add(draft)
    session.flush()
    if user is not None:
        audit.record(session, user, 'zalo.ai_reply_draft', 'zalo_chat', str(chat.id), None, dict(draft=draft.id, job=job.id))
    session.commit()
    return draft


def draft_state(session: Session, chat: ZaloChat) -> dict | None:
    """Bản AI soạn đang mở của hội thoại: DANG (chờ Z2), LOI, hoặc XONG kèm chữ và số lạ (không có trong số liệu/tin)."""
    from .tin_gui_khach import _now, clean_answer, tokens
    draft = session.scalars(select(ZaloReplyDraft).where(ZaloReplyDraft.chat_pk == chat.id, ZaloReplyDraft.status.in_(('DANG', 'XONG', 'LOI')))
                            .order_by(ZaloReplyDraft.id.desc()).limit(1)).first()
    if draft is None or _now() - draft.created_at > DRAFT_KEEP:
        return None
    if draft.status == 'DANG':
        job = session.get(ZaloAiJob, draft.job_id) if draft.job_id else None
        if job is None or job.status == 'LOI':
            draft.status = 'LOI'
        elif job.status == 'XONG':
            text = clean_answer(job.answer)[:bot.MAX_TEXT]
            draft.status, draft.text = ('XONG', text) if text else ('LOI', '')
        elif _now() - job.created_at > timedelta(seconds=DRAFT_WAIT):
            job.status, job.error, job.done_at = 'LOI', f'Quá {DRAFT_WAIT} giây máy Z2 chưa trả lời', _now()
            draft.status = 'LOI'
        session.commit()
    if draft.status == 'DANG':
        return dict(id=draft.id, status='DANG', auto=draft.created_by is None)
    if draft.status == 'LOI':
        return dict(id=draft.id, status='LOI', auto=draft.created_by is None)
    known = tokens(draft.facts) | tokens(_history(session, chat))
    return dict(id=draft.id, status='XONG', text=draft.text, odd=sorted(tokens(draft.text) - known), auto=draft.created_by is None,
                ai=draft.job_id is not None)


def drop_draft(session: Session, user: User, chat_pk: int, draft_id: int) -> None:
    from .tin_gui_khach import _now
    chat = get_chat(session, user, chat_pk)
    draft = session.get(ZaloReplyDraft, draft_id, with_for_update=True)
    if draft is None or draft.chat_pk != chat.id:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy bản soạn.', status=404)
    if draft.status in ('DANG', 'XONG', 'LOI'):
        draft.status, draft.closed_by, draft.closed_at = 'BO', user.id, _now()
        session.commit()


def unread_chats(session: Session, user: User) -> int:
    """Số hội thoại có tin chưa xem (số trên tab Tất cả tin)."""
    return session.scalar(select(func.count()).select_from(_scope(select(ZaloChat).where(ZaloChat.unread > 0), user).subquery())) or 0
