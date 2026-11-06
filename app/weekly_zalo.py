"""Tin Zalo sáng đầu tuần (TUẤN duyệt mẫu 06/11/2026). Chỉ đọc, không ghi chứng từ.

- Khách lâu chưa gọi mực → nhân viên kinh doanh (không phải giám đốc) đã gắn Zalo riêng, lúc TD_INK_CALL_TIME (08:00).
- Báo cáo sản xuất tuần trước → giám đốc đã gắn Zalo riêng, lúc TD_SX_WEEKLY_TIME (07:40).
Ngày làm việc đầu tuần (thứ Hai; nghỉ lễ thì ngày làm việc kế tiếp), bỏ qua nếu app mở lại sau 10:00.
Danh sách trống / tuần không có phiếu thì không gửi. Mốc 'nhac-muc:<thứ Hai>' / 'bc-sx:<thứ Hai>' trong dispatch_zalo_sent.
Chạy trong vòng nền báo điều phối (dispatch_alerts).
"""
from datetime import timedelta

from sqlalchemy import select

from . import ink_rhythm, production_report
from .config import get_settings
from .debt_reminder import first_workday_of_week, week_start
from .models import DispatchZaloSent, User, ZaloChat
from .morning_report import LATEST
from .security import has_role
from .service_sla import TZ

TOP = 8


def _people(session, want):
    out = []
    for user, chat in session.execute(select(User, ZaloChat).join(ZaloChat, ZaloChat.user_id == User.id)
                                      .where(User.active.is_(True), ZaloChat.chat_type == 'PRIVATE').order_by(User.id, ZaloChat.id)):
        if want(user) and all(u.id != user.id for u, _ in out):
            out.append((user, chat))
    return out


def ink_recipients(session):
    return _people(session, lambda u: 'sales' in u.roles and 'director' not in u.roles)


def director_recipients(session):
    return _people(session, lambda u: 'director' in u.roles)


def ink_text(data, base_url: str) -> str:
    rows = data['goi']
    lines = [f'📞 Khách lâu chưa gọi mực: {len(rows)} khách']
    for r in rows[:TOP]:
        lines.append(f"• {r['name'][:40]}: quá {r['over']} ngày (nhịp {r['rhythm']} ngày)" + (f" · {r['phone']}" if r['phone'] else ''))
    if len(rows) > TOP:
        lines.append(f'+{len(rows) - TOP} khách nữa')
    lines.append(f'Mở app: {base_url}/khach-hang/nhac-muc')
    return '\n'.join(lines)


def _due(session, now, at):
    local = now.astimezone(TZ)
    return at <= local.time() < LATEST and first_workday_of_week(session, local.date())


def _send(session, people, mark, body, kind, sender):
    from . import zalo_bot
    send = sender or zalo_bot.send_text
    count = 0
    for user, chat in people:
        if session.get(DispatchZaloSent, (mark, user.id)) is not None:
            continue
        # Ghi mốc trước rồi mới gửi: gửi lỗi vẫn không gửi lại (tránh 2 tin), lỗi hiện ở Tin nhắn Zalo.
        row = DispatchZaloSent(mark=mark, user_id=user.id)
        session.add(row)
        session.commit()
        msg = send(session, chat, body, None, kind)
        row.message_id = getattr(msg, 'id', None)
        session.commit()
        count += 1
    return count


def run_once(session, now=None, sender=None) -> int:
    from . import dispatch_alerts
    from .service_tickets import _now
    s = get_settings()
    if not s.zalo_bot_token.strip():
        return 0
    now = now or _now()
    today = now.astimezone(TZ).date()
    monday = week_start(today)
    count = 0
    if s.sx_weekly_zalo and _due(session, now, s.sx_weekly_time):
        people = [(u, c) for u, c in director_recipients(session) if session.get(DispatchZaloSent, (f'bc-sx:{monday}', u.id)) is None]
        if people:
            start, end = production_report.week_of(monday - timedelta(days=7))
            d = production_report.build(session, start, end, now)
            if d['orders']:
                count += _send(session, people, f'bc-sx:{monday}', production_report.render_text(d, dispatch_alerts.base_url()), 'san_xuat', sender)
    if s.ink_call_zalo and _due(session, now, s.ink_call_time):
        people = [(u, c) for u, c in ink_recipients(session) if session.get(DispatchZaloSent, (f'nhac-muc:{monday}', u.id)) is None]
        if people:
            data = ink_rhythm.collect(session, today)
            if data['goi']:
                count += _send(session, people, f'nhac-muc:{monday}', ink_text(data, dispatch_alerts.base_url()), 'nhac_muc', sender)
    return count
