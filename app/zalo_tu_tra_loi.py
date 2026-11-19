"""Trả lời tự động Zalo (đợt 3 zalo.quanlythanhduc.com, TUẤN duyệt mẫu 19/11/2026).

Giám đốc bật công tắc chung (mặc định TẮT) và chọn cho từng loại tin khách: Tự gửi, Chờ duyệt, hoặc Tắt.
- Ngoài giờ làm (ngoài 07:30–11:30, 13:30–17:30, ngày nghỉ): báo đã nhận + giờ làm lại. Một lần mỗi 12 giờ mỗi hội thoại.
- Khách đã gắn hỏi tình trạng máy/đơn (nhận theo từ khóa): trả lời bằng trạng thái phiếu/đơn trong app. Không kèm tiền.
- Hỏi giá (Trưởng nhóm AI chia loại): luôn Chờ duyệt, nhờ AI soạn sẵn (đợt 2), không bao giờ tự gửi giá.
- Báo máy hỏng / cần mực: phiếu nháp cho điều phối như cũ; bot báo đã nhận yêu cầu.
- Người lạ chưa gắn khách: báo đã nhận, hỏi tên công ty / số điện thoại. Không trả số liệu.
Chỉ hội thoại riêng của khách hoặc người chưa gắn (không nhóm, không nhân viên). Mỗi tin khách tối đa một tin tự động,
và không gửi nếu đã có tin trả lời sau tin đó (người hoặc Trợ lý Zalo). Chờ duyệt = bản soạn hiện ở Tất cả tin.
Không đụng tiền, kho, công nợ.
"""
import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from . import zalo_bot as bot
from .config import get_settings
from .db import engine
from .errors import BusinessError
from .models import (AttendanceHoliday, Customer, SalesOrder, ServiceTicket, User, ZaloChat, ZaloMessage, ZaloReplyDraft,
                     ZaloTriage, ZaloTroLy)
from .service_sla import TZ
from .text import fold

log = logging.getLogger(__name__)

MODES = {'TU_GUI': 'Tự gửi', 'CHO_DUYET': 'Chờ duyệt', 'TAT': 'Tắt'}
RULES = (
    ('auto_ngoai_gio', 'Khách nhắn ngoài giờ làm', 'Ngoài 07:30–11:30, 13:30–17:30, T2–T7 và ngày nghỉ. Một lần mỗi 12 giờ.', tuple(MODES)),
    ('auto_tinh_trang', 'Khách đã gắn hỏi tình trạng máy / đơn', 'Lấy đúng trạng thái phiếu, đơn trong app. Không kèm tiền.', tuple(MODES)),
    ('auto_hoi_gia', 'Hỏi giá hàng, giá sửa', 'AI soạn sẵn theo tin khách, người duyệt rồi mới gửi. Không bao giờ tự gửi giá.', ('CHO_DUYET', 'TAT')),
    ('auto_bao_hong', 'Báo máy hỏng, cần mực', 'Tạo phiếu nháp cho điều phối như cũ. Bot chỉ báo đã nhận.', tuple(MODES)),
    ('auto_nguoi_la', 'Người lạ chưa gắn khách', 'Chỉ báo đã nhận, hỏi tên công ty / số điện thoại. Không trả số liệu.', tuple(MODES)),
)
ONCE = timedelta(hours=12)
KIND = 'tu_dong'

OFF_HOURS_TEXT = ('Dạ Thành Đức đã nhận tin của anh/chị. Bên em làm việc lại lúc {back}, '
                  'nhân viên sẽ trả lời anh/chị ngay ạ.')
STRANGER_TEXT = ('Dạ Thành Đức đã nhận tin của anh/chị. Anh/chị cho em xin tên công ty (hoặc tên) và số điện thoại '
                 'để nhân viên hỗ trợ đúng ạ.')
BROKEN_TEXT = ('Dạ Thành Đức đã nhận yêu cầu {what} của anh/chị. Điều phối sẽ gọi lại để hẹn giờ trong giờ làm việc ạ.')
STATUS_HEAD = 'Dạ Thành Đức xin báo tình trạng của anh/chị:'
STATUS_TAIL = 'Anh/chị cần gì thêm cứ nhắn, nhân viên sẽ hỗ trợ ạ.'
STATUS_WORDS = ('xong chua', 'toi dau', 'den dau', 'khi nao', 'bao gio', 'sua xong', 'giao chua', 'tinh trang', 'sao roi',
                'duoc chua', 'toi chua', 'den chua', 'lay may', 'lay duoc', 'gui chua', 'xu ly chua', 'xong khong')
OPEN_ORDERS = ('DA_CHOT', 'GIAO_MOT_PHAN')
CLOSED_TICKETS = ('DA_DONG', 'DA_HUY', 'KHONG_LAM_DUOC')


def _now():
    from . import service_tickets
    return service_tickets._now()


def config(session: Session) -> ZaloTroLy:
    from . import zalo_agent
    return zalo_agent.config(session)


# ---------- giờ làm ----------

def back_at(session: Session, at: datetime) -> datetime | None:
    """None nếu `at` trong giờ làm; không thì lúc làm lại gần nhất (kể cả sau giờ nghỉ trưa)."""
    s = get_settings()
    local = at.astimezone(TZ)
    day = local.date()
    workday = day.weekday() in s.work_days and session.get(AttendanceHoliday, day) is None
    if workday:
        start, lunch, after, end = (datetime.combine(day, t, TZ) for t in (s.work_start, s.lunch_start, s.lunch_end, s.work_end))
        if start <= local < lunch or after <= local < end:
            return None
        if lunch <= local < after:
            return after
    from .zalo_doc_tin import work_start_after
    return work_start_after(session, local)


def _back_label(at: datetime, now: datetime) -> str:
    local, today = at.astimezone(TZ), now.astimezone(TZ).date()
    if local.date() == today:
        return local.strftime('%H:%M') + ' hôm nay'
    if local.date() == today + timedelta(days=1):
        return local.strftime('%H:%M') + ' sáng mai' if local.hour < 12 else local.strftime('%H:%M') + ' mai'
    names = ('Thứ Hai', 'Thứ Ba', 'Thứ Tư', 'Thứ Năm', 'Thứ Sáu', 'Thứ Bảy', 'Chủ nhật')
    return f"{local.strftime('%H:%M')} {names[local.weekday()]} {local.strftime('%d/%m')}"


# ---------- nội dung ----------

def asks_status(text: str) -> bool:
    t = ' ' + re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]+', ' ', fold(text or '').lower())) + ' '
    return any(f' {w} ' in t for w in STATUS_WORDS)


def status_text(session: Session, customer_code: str) -> str | None:
    """Trạng thái phiếu dịch vụ đang mở + đơn chưa giao xong của khách. Không có gì thì None (để người trả lời)."""
    from .zalo_tra_cuu import KINDS, ORDER_STATUSES, TICKET_STATUSES
    lines = []
    for t in session.scalars(select(ServiceTicket).where(ServiceTicket.customer_code == customer_code,
                                                         ServiceTicket.status.not_in(CLOSED_TICKETS))
                             .order_by(ServiceTicket.created_at.desc()).limit(5)):
        when = f", hẹn {t.appointment_at.astimezone(TZ).strftime('%H:%M %d/%m')}" if t.appointment_at and t.status not in ('HOAN_THANH',) else ''
        lines.append(f'- {t.number} ({KINDS.get(t.kind or "", "dịch vụ")}): {TICKET_STATUSES.get(t.status, t.status)}{when}.')
    for o in session.scalars(select(SalesOrder).where(SalesOrder.customer_code == customer_code, SalesOrder.status.in_(OPEN_ORDERS))
                             .order_by(SalesOrder.created_at.desc()).limit(3)):
        due = f", hẹn giao {o.promised_date.strftime('%d/%m')}" if o.promised_date else ''
        lines.append(f'- Đơn {o.number}: {ORDER_STATUSES.get(o.status, o.status)}{due}.')
    if not lines:
        return None
    return '\n'.join([STATUS_HEAD, *lines, STATUS_TAIL])


# ---------- gửi / chờ duyệt ----------

def _eligible(session: Session, chat: ZaloChat | None) -> bool:
    return chat is not None and chat.chat_type == 'PRIVATE' and not chat.user_id


def _answered_after(session: Session, msg: ZaloMessage) -> bool:
    """Đã có tin đi ra sau tin khách này (người trả lời, Trợ lý Zalo, hoặc tin tự động khác)."""
    return session.scalar(select(ZaloMessage.id).where(
        ZaloMessage.chat_pk == msg.chat_pk, ZaloMessage.direction == 'OUT', ZaloMessage.kind != 'thong_bao',
        ZaloMessage.id > msg.id).limit(1)) is not None


def _sent_recently(session: Session, chat: ZaloChat, text_start: str) -> bool:
    return session.scalar(select(ZaloMessage.id).where(
        ZaloMessage.chat_pk == chat.id, ZaloMessage.direction == 'OUT', ZaloMessage.kind == KIND,
        ZaloMessage.text.startswith(text_start), ZaloMessage.message_at >= _now() - ONCE).limit(1)) is not None


def _act(session: Session, chat: ZaloChat, mode: str, text: str, rule: str) -> str:
    """Tự gửi qua bot, hoặc để bản soạn ở Tất cả tin (Chờ duyệt). Trả về việc đã làm."""
    if mode == 'TU_GUI':
        bot.send_text(session, chat, text, None, kind=KIND, hold=False)
        done = 'gui'
    elif mode == 'CHO_DUYET':
        for old in session.scalars(select(ZaloReplyDraft).where(ZaloReplyDraft.chat_pk == chat.id, ZaloReplyDraft.status.in_(('DANG', 'XONG', 'LOI')))):
            old.status, old.closed_at = 'BO', _now()
        session.add(ZaloReplyDraft(chat_pk=chat.id, status='XONG', facts=text, text=text, created_by=None))
        session.commit()
        done = 'cho_duyet'
    else:
        return ''
    log.info('Trả lời tự động %s (%s) cho hội thoại %s', rule, done, chat.id)
    return done


def on_message(session: Session, message_id: int) -> str:
    """Sau khi nhận tin khách (chạy nền, sau Trợ lý Zalo). Trả về việc đã làm ('' nếu không làm gì)."""
    cfg = config(session)
    msg = session.get(ZaloMessage, message_id)
    if not cfg.auto_on or msg is None or msg.direction != 'IN':
        return ''
    if isinstance(msg.raw, dict) and ((msg.raw.get('message') or {}).get('from') or {}).get('is_bot'):
        return ''
    chat = session.get(ZaloChat, msg.chat_pk)
    if not _eligible(session, chat) or _answered_after(session, msg):
        return ''
    back = back_at(session, msg.message_at)
    if back is not None:
        if cfg.auto_ngoai_gio == 'TAT' or _sent_recently(session, chat, OFF_HOURS_TEXT[:20]):
            return ''
        return _act(session, chat, cfg.auto_ngoai_gio, OFF_HOURS_TEXT.format(back=_back_label(back, _now())), 'ngoai_gio')
    customer = session.get(Customer, chat.customer_code) if chat.customer_code else None
    if customer is None:
        if cfg.auto_nguoi_la == 'TAT' or _sent_recently(session, chat, STRANGER_TEXT[:20]):
            return ''
        return _act(session, chat, cfg.auto_nguoi_la, STRANGER_TEXT, 'nguoi_la')
    if cfg.auto_tinh_trang != 'TAT' and msg.kind == 'text' and asks_status(msg.text):
        text = status_text(session, customer.code)
        if text:
            return _act(session, chat, cfg.auto_tinh_trang, text, 'tinh_trang')
    return ''


def handle_message(message_id: int) -> None:
    """Chạy nền sau webhook. Không ném lỗi: webhook không được hỏng."""
    try:
        with Session(engine()) as session:
            on_message(session, message_id)
    except Exception:  # noqa: BLE001
        log.exception('Trả lời tự động lỗi ở tin %s', message_id)


def on_triage(session: Session, row: ZaloTriage) -> str:
    """Sau khi Trưởng nhóm AI chia loại tin: hỏi giá → AI soạn chờ duyệt; báo hỏng/cần mực → báo đã nhận."""
    cfg = config(session)
    if not cfg.auto_on or row.status != 'XONG':
        return ''
    msg = session.get(ZaloMessage, row.message_id)
    chat = session.get(ZaloChat, msg.chat_pk) if msg else None
    if msg is None or not _eligible(session, chat) or _answered_after(session, msg) or back_at(session, msg.message_at) is not None:
        return ''  # ngoài giờ đã có lời báo ngoài giờ
    if row.intent == 'HOI_GIA' and cfg.auto_hoi_gia == 'CHO_DUYET':
        from . import zalo_hop_thu
        try:
            zalo_hop_thu.request_draft(session, None, chat.id)
        except BusinessError:
            session.rollback()  # Z2 tắt: nhân viên tự trả lời như cũ
            return ''
        return 'ai_soan'
    if row.intent in ('SUA', 'MUC') and cfg.auto_bao_hong != 'TAT':
        what = 'sửa máy' if row.intent == 'SUA' else 'giao mực'
        return _act(session, chat, cfg.auto_bao_hong, BROKEN_TEXT.format(what=what), 'bao_hong')
    return ''


# ---------- màn cài đặt (chỉ giám đốc) ----------

def save(session: Session, user: User, form: dict) -> ZaloTroLy:
    from .zalo_bot import _director
    _director(user)
    row = session.get(ZaloTroLy, 1, with_for_update=True)
    if row is None:
        row = ZaloTroLy(id=1, enabled=False)
        session.add(row)
    before = {k: getattr(row, k) for k in ('auto_on', *(r[0] for r in RULES))}
    row.auto_on = str(form.get('auto_on') or '') == '1'
    for key, _, _, allowed in RULES:
        value = str(form.get(key) or '')
        if value not in allowed:
            raise BusinessError('VALIDATION', 'Mức trả lời không hợp lệ.', status=422)
        setattr(row, key, value)
    row.updated_by, row.updated_at = user.id, _now()
    after = {k: getattr(row, k) for k in before}
    audit.record(session, user, 'zalo.auto_reply', 'zalo_tro_ly', '1', before, after)
    session.commit()
    return row
