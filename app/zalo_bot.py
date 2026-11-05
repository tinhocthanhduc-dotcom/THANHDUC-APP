"""Tin nhắn Zalo Bot (TUẤN 05/11/2026): lưu tin Zalo gửi về qua webhook, giám đốc xem và trả lời trên app.

Zalo không có lệnh xem lịch sử và webhook chỉ báo tin nhận được, nên app tự lưu cả tin nhận lẫn tin gửi từ màn này.
Token bot không bao giờ được ghi vào log, nhật ký hay thông báo lỗi. Đợt 1 chưa lưu tin n8n gọi thẳng Zalo.
"""
import hmac
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import audit
from .config import get_settings
from .db import engine
from .errors import BusinessError
from .models import User, ZaloChat, ZaloMessage

API_BASE = 'https://bot-api.zaloplatforms.com/bot'
MAX_TEXT = 2000  # giới hạn của Zalo cho một tin sendMessage
PREVIEW = 200
# Điều khoản Zalo: phải báo người dùng họ đang chat với phần mềm tự động và có thu thập dữ liệu (TUẤN duyệt 05/11/2026).
NOTICE = 'Đây là tài khoản tự động của Công ty Tin Học Thành Đức. Tin nhắn của anh/chị sẽ được lưu lại để nhân viên hỗ trợ.'
KINDS = {'message.text.received': 'text', 'message.image.received': 'image', 'message.sticker.received': 'sticker',
         'message.voice.received': 'voice', 'message.unsupported.received': 'unsupported'}
KIND_LABELS = {'image': 'Hình ảnh', 'sticker': 'Sticker', 'voice': 'Tin thoại', 'unsupported': 'Tin không hỗ trợ'}


class ZaloError(Exception):
    """Lỗi gọi Zalo, nội dung đã bỏ token nên hiện được cho giám đốc."""


def _director(user: User) -> None:
    if not user.active or 'director' not in user.roles:
        raise BusinessError('FORBIDDEN', 'Chỉ giám đốc được xem tin nhắn Zalo.', status=403)


def _clean(text: str) -> str:
    token = get_settings().zalo_bot_token.strip()
    text = str(text)
    return (text.replace(token, '***') if token else text)[:500]


def call(method: str, payload: dict | None = None, timeout: int = 15):
    """Gọi Zalo Bot API. Lỗi mạng dùng `from None` để traceback không mang địa chỉ có token."""
    token = get_settings().zalo_bot_token.strip()
    if not token:
        raise ZaloError('Chưa cấu hình token bot (TD_ZALO_BOT_TOKEN).')
    req = urllib.request.Request(f'{API_BASE}{token}/{method}', data=json.dumps(payload or {}).encode(), method='POST',
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b'{}')
        except ValueError:
            raise ZaloError(f'Zalo trả lỗi HTTP {exc.code}.') from None
    except (OSError, ValueError):  # URLError, hết giờ và mất kết nối đều là OSError
        raise ZaloError('Không kết nối được Zalo, hãy thử lại sau.') from None
    if not isinstance(body, dict) or not body.get('ok'):
        reason = body.get('description') if isinstance(body, dict) else ''
        raise ZaloError(_clean(reason or 'Zalo từ chối yêu cầu.'))
    return body.get('result')


def valid_secret(header: str) -> bool:
    """Chưa đặt khóa bí mật thì từ chối mọi yêu cầu, không mở cửa."""
    secret = get_settings().zalo_webhook_secret
    return bool(secret) and hmac.compare_digest(header.encode(), secret.encode())


def _no_nul(value):
    """PostgreSQL không nhận ký tự NUL và surrogate lẻ (JSON cho phép cả hai) trong text và JSONB."""
    if isinstance(value, str):
        return value.replace('\x00', '').encode('utf-8', 'replace').decode('utf-8')
    if isinstance(value, list):
        return [_no_nul(v) for v in value]
    if isinstance(value, dict):
        return {_no_nul(k): _no_nul(v) for k, v in value.items()}
    return value


def _time(raw) -> datetime:
    """Zalo gửi mili giây. Sai hoặc thiếu thì lấy giờ nhận."""
    try:
        return datetime.fromtimestamp(int(raw) / 1000, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.now(timezone.utc)


def _preview(row_text: str, kind: str) -> str:
    return (row_text or KIND_LABELS.get(kind, ''))[:PREVIEW]


def receive(session: Session, payload: dict) -> int | None:
    """Lưu một sự kiện Zalo, gửi trùng thì bỏ qua. Trả mã hội thoại nếu cần gửi lời báo lần đầu."""
    payload = _no_nul(payload)
    event = payload.get('result') if isinstance(payload.get('result'), dict) else payload
    msg = event.get('message')
    if not isinstance(msg, dict) or not isinstance(msg.get('chat'), dict):
        return None
    chat_id = str(msg['chat'].get('id') or '').strip()
    if not chat_id or len(chat_id) > 64:
        return None
    sender = msg.get('from') if isinstance(msg.get('from'), dict) else {}
    chat_type = 'GROUP' if msg['chat'].get('chat_type') == 'GROUP' else 'PRIVATE'
    sender_name = str(sender.get('display_name') or '')[:200]
    name = str(msg['chat'].get('title') or msg['chat'].get('display_name') or '')[:200]
    if not name and chat_type == 'PRIVATE':
        name = sender_name
    stmt = insert(ZaloChat).values(chat_id=chat_id, chat_type=chat_type, display_name=name)
    pk = session.execute(stmt.on_conflict_do_update(
        index_elements=['chat_id'],
        set_=dict(display_name=func.coalesce(func.nullif(stmt.excluded.display_name, ''), ZaloChat.display_name)),
    ).returning(ZaloChat.id)).scalar_one()

    kind = KINDS.get(str(event.get('event_name') or ''), 'unsupported')
    text = str(msg.get('text') or msg.get('caption') or '')
    at = _time(msg.get('date'))
    zalo_id = str(msg.get('message_id') or '')[:64] or None
    inserted = session.execute(insert(ZaloMessage).values(
        chat_pk=pk, zalo_message_id=zalo_id, direction='IN', kind=kind, text=text, sender_id=str(sender.get('id') or '')[:64],
        sender_name=sender_name, status='DA_NHAN', raw=event, message_at=at,
    ).on_conflict_do_nothing(index_elements=['zalo_message_id']).returning(ZaloMessage.id)).scalar_one_or_none()
    if inserted is None:
        session.commit()
        return None
    session.execute(update(ZaloChat).where(ZaloChat.id == pk).values(
        last_message_at=at, last_text=_preview(text, kind), unread=ZaloChat.unread + 1))
    notice = None
    if not sender.get('is_bot'):  # cột notice_sent_at đảm bảo chỉ một yêu cầu được gửi lời báo
        notice = session.execute(update(ZaloChat).where(ZaloChat.id == pk, ZaloChat.notice_sent_at.is_(None)).values(
            notice_sent_at=func.now()).returning(ZaloChat.id)).scalar_one_or_none()
    session.commit()
    return notice


def receive_event(payload: dict) -> int | None:
    with Session(engine()) as session:
        return receive(session, payload)


def send_text(session: Session, chat: ZaloChat, text: str, user: User | None = None, kind: str = 'text') -> ZaloMessage:
    """Ghi tin trước rồi mới gửi, để gửi lỗi vẫn thấy trên màn. Không tự gửi lại vì có thể làm người nhận nhận 2 tin."""
    row = ZaloMessage(chat_pk=chat.id, direction='OUT', kind=kind, text=text, sent_by=user.id if user else None,
                      sender_name=user.full_name if user else 'Tự động', status='DANG_GUI', raw={},
                      message_at=datetime.now(timezone.utc))
    session.add(row)
    session.flush()
    if user:  # Nhật ký không chứa nội dung tin của khách.
        audit.record(session, user, 'zalo.reply', 'zalo_chat', str(chat.id), None, dict(message_id=row.id, length=len(text)))
    session.commit()
    try:
        result = call('sendMessage', dict(chat_id=chat.chat_id, text=text))
    except ZaloError as exc:
        row.status, row.error = 'LOI', str(exc)
    else:
        row.status = 'DA_GUI'
        row.zalo_message_id = str(result.get('message_id') or '')[:64] or None if isinstance(result, dict) else None
        if kind != 'thong_bao':  # lời báo tự động không che tin mới nhất của khách ở danh sách
            chat.last_message_at, chat.last_text = row.message_at, text[:PREVIEW]
    session.commit()
    return row


def send_notice(chat_pk: int) -> None:
    """Lời báo lần đầu, chạy nền sau khi đã trả lời Zalo. Gửi lỗi vẫn ghi trên màn, không làm hỏng webhook."""
    with Session(engine()) as session:
        chat = session.get(ZaloChat, chat_pk)
        if chat is not None:
            send_text(session, chat, NOTICE, None, 'thong_bao')


def reply(session: Session, user: User, chat_pk: int, text: str) -> ZaloMessage:
    _director(user)
    chat = session.get(ZaloChat, chat_pk)
    if chat is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy hội thoại.', status=404)
    text = _no_nul(text).strip()
    if not text:
        raise BusinessError('VALIDATION', 'Hãy nhập nội dung trả lời.', status=422)
    if len(text) > MAX_TEXT:
        raise BusinessError('VALIDATION', f'Tin nhắn Zalo tối đa {MAX_TEXT} ký tự.', status=422)
    return send_text(session, chat, text, user)


def install_webhook(session: Session, user: User) -> tuple[bool, str]:
    """Cài webhook cho bot. Trả (được hay không, lời nhắn kèm kết quả kiểm tra của Zalo)."""
    _director(user)
    cfg = get_settings()
    if not 8 <= len(cfg.zalo_webhook_secret) <= 256:
        return False, 'Khóa bí mật (TD_ZALO_WEBHOOK_SECRET) phải dài từ 8 đến 256 ký tự.'
    try:
        result = call('setWebhook', dict(url=cfg.zalo_webhook_url, secret_token=cfg.zalo_webhook_secret), timeout=30)
    except ZaloError as exc:
        return False, str(exc)
    audit.record(session, user, 'zalo.webhook_set', 'zalo_bot', 'webhook', None, dict(url=cfg.zalo_webhook_url))
    session.commit()
    check = result.get('verification') if isinstance(result, dict) else None
    # Zalo vẫn lưu địa chỉ khi kiểm tra thất bại, nên hiện nguyên kết quả cho giám đốc xem.
    return True, _clean(json.dumps(check, ensure_ascii=False)) if check is not None else 'Zalo không trả kết quả kiểm tra.'


def _matching(q: str):
    q = (q or '').strip()
    query = select(ZaloChat)
    if q:
        found = select(ZaloMessage.chat_pk).where(ZaloMessage.text.icontains(q, autoescape=True))
        query = query.where(ZaloChat.display_name.icontains(q, autoescape=True) | ZaloChat.chat_id.icontains(q, autoescape=True)
                            | ZaloChat.id.in_(found))
    return query


def count_chats(session: Session, q: str = '') -> int:
    return session.scalar(select(func.count()).select_from(_matching(q).subquery())) or 0


def list_chats(session: Session, q: str, offset: int, size: int) -> list[ZaloChat]:
    query = _matching(q).order_by(ZaloChat.last_message_at.desc().nulls_last(), ZaloChat.id.desc())
    return list(session.scalars(query.offset(offset).limit(size)))


def count_messages(session: Session, chat_pk: int) -> int:
    return session.scalar(select(func.count()).select_from(ZaloMessage).where(ZaloMessage.chat_pk == chat_pk)) or 0


def list_messages(session: Session, chat_pk: int, offset: int, size: int) -> list[ZaloMessage]:
    """Trang 1 là tin mới nhất; trong một trang xếp cũ trước, mới sau như khung chat."""
    rows = session.scalars(select(ZaloMessage).where(ZaloMessage.chat_pk == chat_pk)
                           .order_by(ZaloMessage.message_at.desc(), ZaloMessage.id.desc()).offset(offset).limit(size))
    return list(rows)[::-1]


def mark_read(session: Session, chat_pk: int) -> None:
    session.execute(update(ZaloChat).where(ZaloChat.id == chat_pk, ZaloChat.unread > 0).values(unread=0))
    session.commit()
