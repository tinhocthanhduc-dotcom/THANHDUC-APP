"""Tin gộp giám đốc (đợt 2 rà soát, TUẤN duyệt mẫu 16/11/2026).

Tin tự động theo giờ gửi giám đốc (báo cáo sáng, sản xuất treo, Nhân sự AI báo chấm công, tóm tắt Đội AI cuối ngày)
không gửi ngay mà giữ lại, rồi gửi gộp một tin ở các mốc (mặc định 08:30, 13:45, 17:45).
Tin gấp (trễ hẹn lâu, quá tải, việc treo báo lên, trả lời lệnh) không đi qua đây nên vẫn gửi ngay.
Tin gửi nhân viên giữ nguyên. Tắt "Tin gộp giám đốc" ở Đội AI › Thành viên thì tin đang giữ gửi ngay ở phút kế tiếp.

Tin giữ là dòng zalo_messages trạng thái CHO_GOP; gửi xong đổi DA_GOP. Mốc chống gửi trùng trong dispatch_zalo_sent.
Không cần migration.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .models import DispatchZaloSent, User, ZaloChat, ZaloMessage

HELD = {'bao_cao_sang': 'Báo cáo sáng', 'san_xuat': 'Sản xuất treo', 'nhan_su_bao': 'Chấm công', 'tom_tat_doi': 'Tóm tắt Đội AI'}
CODE = 'TIN_GOP'
GRACE = timedelta(minutes=2)     # chờ 2 phút sau mốc để tin cùng giờ (vd chấm công 17:45) kịp vào tin gộp
CATCH_UP = timedelta(hours=2)    # máy chủ khởi động lại trễ quá 2 giờ thì đợi mốc sau
SPLIT = '\n\n— — —\n\n'


def _now():
    return datetime.now(timezone.utc)


def _active(session):
    from . import nhan_vien_ao as nva
    return nva.active(session, CODE)


def _director_chat(session, chat) -> bool:
    if chat is None or chat.user_id is None:
        return False
    user = session.get(User, chat.user_id)
    return user is not None and user.active and 'director' in (user.roles or [])


def should_hold(session, chat, kind, user=None) -> bool:
    return user is None and kind in HELD and _director_chat(session, chat) and _active(session) is not None


def hold(session, chat, text, kind):
    row = ZaloMessage(chat_pk=chat.id, direction='OUT', kind=kind, text=text, sender_name='Tự động', status='CHO_GOP',
                      raw={}, message_at=_now())
    session.add(row)
    session.commit()
    return row


def slot(now, times):
    """Mốc vừa qua (đã đủ 2 phút, chưa quá 2 giờ) theo giờ Việt Nam, không thì None."""
    from .nhan_vien_ao import TZ
    local = now.astimezone(TZ)
    for raw in sorted(times, reverse=True):
        at = datetime.combine(local.date(), datetime.strptime(raw, '%H:%M').time(), TZ)
        if at + GRACE <= local < at + CATCH_UP:
            return raw
    return None


def compose(rows, at, day) -> list[str]:
    """Ghép các tin giữ thành một hoặc vài tin (mỗi tin tối đa 2000 ký tự của Zalo)."""
    from .zalo_bot import MAX_TEXT
    head = f'TIN GỘP {at} · {day.strftime("%d/%m")} ({len(rows)} mục)'
    parts, current = [], head
    for r in rows:
        piece = r.text.strip()
        if len(piece) > MAX_TEXT - 40:
            piece = piece[:MAX_TEXT - 41] + '…'
        if len(current) + len(SPLIT) + len(piece) > MAX_TEXT:
            parts.append(current)
            current = piece
        else:
            current = current + SPLIT + piece
    parts.append(current)
    return parts


def run_once(session, now=None, sender=None) -> int:
    """Gọi mỗi phút từ nhan_vien_ao.run_once. Trả về số tin đã gửi."""
    from . import nhan_vien_ao as nva
    from .zalo_bot import send_text
    now = now or _now()
    send = sender or send_text
    held = list(session.scalars(select(ZaloMessage).where(ZaloMessage.status == 'CHO_GOP', ZaloMessage.direction == 'OUT')
                                .order_by(ZaloMessage.message_at, ZaloMessage.id)))
    if not held:
        return 0
    row = _active(session)
    at = slot(now, nva.cfg(row)['times']) if row is not None else 'ngay'
    if at is None:
        return 0
    day = now.astimezone(nva.TZ).date()
    by_chat = {}
    for m in held:
        by_chat.setdefault(m.chat_pk, []).append(m)
    sent = 0
    for chat_pk, rows in by_chat.items():
        chat = session.get(ZaloChat, chat_pk)
        uid = chat.user_id or 0
        mark = f'gop:{day.isoformat()}:{at.replace(":", "")}'
        if at != 'ngay':
            if session.get(DispatchZaloSent, (mark, uid)) is not None:
                continue
            session.add(DispatchZaloSent(mark=mark, user_id=uid))
        for m in rows:
            m.status = 'DA_GOP'
        session.commit()
        label = 'lúc ' + at if at != 'ngay' else 'đang giữ'
        for text in compose(rows, label, day):
            send(session, chat, text, None, 'tin_gop')
            sent += 1
    return sent
