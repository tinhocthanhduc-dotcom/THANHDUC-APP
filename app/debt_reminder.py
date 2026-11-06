"""Nhắc nợ khách quá hạn qua Zalo cho kế toán, sáng thứ Hai (TUẤN duyệt mẫu 06/11/2026).

Gửi lúc TD_DEBT_REMINDER_TIME (mặc định 07:45) ngày làm việc đầu tuần (thứ Hai; thứ Hai nghỉ lễ thì ngày làm việc kế tiếp),
mỗi tuần một tin cho mỗi người. Người nhận: nhân viên có quyền kế toán, không phải giám đốc, đã gắn Zalo riêng.
Quá hạn = đúng tab "Quá 30 ngày" của trang Công nợ khách hàng (cùng hàm _rows), số tiền là phần khách chưa trả (gồm VAT).
Chỉ đọc, không ghi chứng từ. Mốc 'nhac-no:<thứ Hai>' trong dispatch_zalo_sent (không cần migration).
Chạy nền cùng cờ TD_PUSH_LOOP; nhiều worker uvicorn chỉ một worker chạy nhờ khóa advisory.
"""
import logging
import threading
import time as clock
from datetime import date, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import select, text

from . import dashboard, finance_reports
from .config import get_settings
from .models import DispatchZaloSent, User, ZaloChat
from .morning_report import LATEST, _cut, _day_label, _money, is_workday
from .security import has_role
from .service_sla import TZ

log = logging.getLogger(__name__)
LOCK_ID = 7_301_031
EVERY = 60
TOP = 10
DOCS = 3


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def mark(day: date) -> str:
    return f'nhac-no:{week_start(day).isoformat()}'


def first_workday_of_week(session, day: date) -> bool:
    """Hôm nay là ngày làm việc và từ thứ Hai đến hôm qua đều nghỉ."""
    if not is_workday(session, day):
        return False
    d = week_start(day)
    while d < day:
        if is_workday(session, d):
            return False
        d += timedelta(days=1)
    return True


def build(session, user, today: date) -> dict:
    from .web.customer_debts import _rows
    from .web.lists import OLD_DEBT_DAYS
    with session.no_autoflush:
        finance_reports.check_reader(user)
        rows = _rows(session, user, today)
    old = sorted((r for r in rows if r['old']), key=lambda r: (-r['unpaid'], r['code']))
    items = [dict(name=r['name'], code=r['code'], amount=r['unpaid'], days=r['days'], phone=(r['phones'] or [''])[0],
                  docs=[doc['number'] or doc['kind'] for doc in r['docs'] if doc['unpaid']]) for r in old]
    return dict(today=today, days=OLD_DEBT_DAYS, items=items, total=sum(i['amount'] for i in items),
                oldest=max((i['days'] for i in items), default=0), pending=sum(r['pending'] for r in rows))


def render_text(d: dict, base_url: str = 'https://quanlythanhduc.com') -> str:
    from .zalo_bot import MAX_TEXT
    link = f"Báo thu: {base_url}/cong-no-khach-hang?tab=qua-han"
    head = [f"📒 NHẮC NỢ QUÁ HẠN · {_day_label(d['today'])}"]
    if not d['items']:
        head.append(f"Không có khách chưa trả quá {d['days']} ngày.")
        tail = [f"Đã thu, đang ở nhân viên: {_money(d['pending'])}"] if d['pending'] else []
        return '\n'.join(head + tail + ['', link])
    head += [f"Khách chưa trả quá {d['days']} ngày: {len(d['items'])} khách",
             f"Tổng: {_money(d['total'])} · cũ nhất {d['oldest']} ngày", '']
    tail = ['']
    if d['pending']:
        tail.append(f"Đã thu, đang ở nhân viên: {_money(d['pending'])}")
    tail.append(link)
    body, shown = [], 0
    for n, i in enumerate(d['items'][:TOP], 1):
        docs = ', '.join(i['docs'][:DOCS]) + (f' +{len(i["docs"]) - DOCS}' if len(i['docs']) > DOCS else '')
        detail = ' · '.join(x for x in (f"{i['days']} ngày", i['phone'], docs) if x)
        lines = [f"{n}. {_cut(i['name'], 32)} – {_money(i['amount'])}", f"   {detail}"]
        if i.get('code'):  # đợt 4 (Q10): mở thẳng màn soạn tin nhắc nợ cho khách này
            lines.append(f"   Soạn tin: {base_url}/tin-gui-khach/moi?loai=NHAC_NO&khach={quote(i['code'])}")
        more = len(d['items']) - n
        extra = [f'+{more} khách nữa, xem đủ trong app'] if more else []
        if len('\n'.join(head + body + lines + extra + tail)) > MAX_TEXT:
            break
        body += lines
        shown = n
    if len(d['items']) > shown:
        body.append(f"+{len(d['items']) - shown} khách nữa, xem đủ trong app")
    return '\n'.join(head + body + tail)


def recipients(session) -> list[tuple[User, ZaloChat]]:
    out = []
    for user, chat in session.execute(select(User, ZaloChat).join(ZaloChat, ZaloChat.user_id == User.id)
                                      .where(User.active.is_(True), ZaloChat.chat_type == 'PRIVATE').order_by(User.id, ZaloChat.id)):
        if has_role(user, 'finance') and 'director' not in user.roles and all(u.id != user.id for u, _ in out):
            out.append((user, chat))
    return out


def due(session, now: datetime) -> bool:
    local = now.astimezone(TZ)
    return get_settings().debt_reminder_time <= local.time() < LATEST and first_workday_of_week(session, local.date())


def run_once(session, now=None, sender=None) -> int:
    from . import dispatch_alerts, zalo_bot
    s = get_settings()
    if not s.debt_reminder or not s.zalo_bot_token.strip():
        return 0
    now = now or dashboard._now()
    if not due(session, now):
        return 0
    today = now.astimezone(TZ).date()
    people = [(u, c) for u, c in recipients(session) if session.get(DispatchZaloSent, (mark(today), u.id)) is None]
    send = sender or zalo_bot.send_text
    count = 0
    for user, chat in people:
        body = render_text(build(session, user, today), dispatch_alerts.base_url())
        # Ghi mốc trước rồi mới gửi: gửi lỗi vẫn không gửi lại (tránh 2 tin), lỗi hiện ở Tin nhắn Zalo.
        row = DispatchZaloSent(mark=mark(today), user_id=user.id)
        session.add(row)
        session.commit()
        msg = send(session, chat, body, None, 'nhac_no')
        row.message_id = getattr(msg, 'id', None)
        session.commit()
        count += 1
    return count


def preview(session, user, now=None) -> str:
    from . import dispatch_alerts
    now = now or dashboard._now()
    return render_text(build(session, user, now.astimezone(TZ).date()), dispatch_alerts.base_url())


def send_test(session, user, chat_pk: int, now=None):
    """Nút Gửi thử của giám đốc: gửi ngay bản hôm nay tới một người nhận đã gắn Zalo; không ghi mốc nên thứ Hai vẫn gửi."""
    from . import dispatch_alerts, zalo_bot
    now = now or dashboard._now()
    pair = next(((u, c) for u, c in recipients(session) if c.id == chat_pk), None)
    if pair is None:
        return None
    body = render_text(build(session, pair[0], now.astimezone(TZ).date()), dispatch_alerts.base_url())
    return zalo_bot.send_text(session, pair[1], body, None, 'nhac_no')


def _loop():
    from sqlalchemy.orm import Session
    from .db import engine
    eng = engine()
    while True:
        try:
            with eng.connect() as conn:
                locked = conn.execute(text('select pg_try_advisory_lock(:k)'), dict(k=LOCK_ID)).scalar()
                conn.commit()
                if locked:
                    while True:
                        with Session(bind=eng, expire_on_commit=False) as session:
                            run_once(session)
                        clock.sleep(EVERY)
        except Exception:  # noqa: BLE001 - vòng nền không được làm sập app; thử lại sau.
            log.exception('debt reminder loop')
        clock.sleep(EVERY)


def start():
    threading.Thread(target=_loop, name='debt-reminder', daemon=True).start()
