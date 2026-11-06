"""Nhắc phiếu sản xuất bị đọng (TUẤN duyệt mẫu 06/11/2026). Chỉ đọc, không tự nhập kho hay xuất vật tư.

Mốc tính theo giờ làm việc (ca sáng + ca chiều, ngày làm việc, bỏ ngày lễ):
- Mới chưa bắt đầu quá TD_SX_STALE_NEW_HOURS (8 giờ = 1 ngày) → thợ; quá gấp đôi → báo thêm điều phối.
- Đang làm quá TD_SX_STALE_WORK_HOURS (16 giờ = 2 ngày) → thợ + điều phối.
- Hoàn thành chưa nhập kho quá TD_SX_STALE_RECEIVE_HOURS (4 giờ) → thủ kho.
- Hoàn thành mà không xuất vật tư (nhập kho sẽ vào giá vốn 0) → thủ kho, ngay khi hoàn thành.
Thợ và thủ kho thấy ở Việc của tôi/chuông (dòng đỏ). Điều phối và thủ kho đã gắn Zalo riêng nhận một tin gộp
lúc TD_SX_STALE_ZALO_TIMES (mặc định 08:00 và 13:45): tin đầu ngày liệt kê hết, các tin sau chỉ phiếu mới đọng.
Mốc trong dispatch_zalo_sent (không cần migration). Chạy trong vòng nền báo điều phối (dispatch_alerts).
"""
from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from .config import get_settings
from .models import AttendanceHoliday, DispatchZaloSent, Product, ProductionOrder, User, ZaloChat
from .security import has_role
from .service_sla import TZ

DAY_MINUTES = 8 * 60
CATCH_UP = timedelta(hours=2)   # app khởi động lại trễ quá 2 giờ sau mốc gửi thì bỏ mốc đó
MAX_DAYS = 120
LABELS = {'CHUA_BAT_DAU': 'Chưa bắt đầu', 'LAM_LAU': 'Làm lâu', 'CHO_NHAP': 'Chờ nhập kho', 'CHUA_VAT_TU': 'Chưa xuất vật tư'}
TONES = {'CHUA_BAT_DAU': 'cho', 'LAM_LAU': 'cho', 'CHO_NHAP': 'huy', 'CHUA_VAT_TU': 'huy'}


def _holidays(session, start: date, end: date) -> set[date]:
    return set(session.scalars(select(AttendanceHoliday.day).where(AttendanceHoliday.day.between(start, end))))


def working_minutes(start: datetime, end: datetime, holidays: set[date]) -> int:
    """Số phút làm việc giữa hai mốc (ca sáng + ca chiều của ngày làm việc, bỏ ngày lễ)."""
    s = get_settings()
    a, b = start.astimezone(TZ), end.astimezone(TZ)
    if b <= a:
        return 0
    total, day = 0, max(a.date(), b.date() - timedelta(days=MAX_DAYS))
    while day <= b.date():
        if day.weekday() in s.work_days and day not in holidays:
            for lo, hi in ((s.work_start, s.lunch_start), (s.lunch_end, s.work_end)):
                x = max(a, datetime.combine(day, lo, TZ))
                y = min(b, datetime.combine(day, hi, TZ))
                if y > x:
                    total += int((y - x).total_seconds() // 60)
        day += timedelta(days=1)
    return total


def age_text(minutes: int) -> str:
    if minutes >= DAY_MINUTES:
        return f'{minutes // DAY_MINUTES} ngày'
    return f'{max(1, minutes // 60)} giờ'


def check(row, now: datetime, holidays: set[date]):
    """dict(code, label, tone, age, minutes, dispatcher, warehouse) nếu phiếu đang đọng, không thì None."""
    s = get_settings()
    code, since, limit = None, None, None
    if row.status == 'MOI':
        code, since, limit = 'CHUA_BAT_DAU', row.created_at, s.sx_stale_new_hours * 60
    elif row.status == 'DANG_LAM':
        code, since, limit = 'LAM_LAU', row.started_at or row.created_at, s.sx_stale_work_hours * 60
    elif row.status == 'HOAN_THANH':
        if row.px_doc_id is None:
            code, since, limit = 'CHUA_VAT_TU', row.finished_at or row.created_at, 0
        else:
            code, since, limit = 'CHO_NHAP', row.finished_at or row.created_at, s.sx_stale_receive_hours * 60
    if code is None or since is None:
        return None
    minutes = working_minutes(since, now, holidays)
    if minutes < limit:
        return None
    age = age_text(minutes) if minutes else ''
    label = LABELS[code] + (f' · {age}' if age and code != 'CHUA_VAT_TU' else '')
    dispatcher = code == 'LAM_LAU' or (code == 'CHUA_BAT_DAU' and (minutes >= 2 * limit or row.technician_id is None))
    return dict(code=code, label=label, tone=TONES[code], age=age, minutes=minutes,
                dispatcher=dispatcher, warehouse=code in ('CHO_NHAP', 'CHUA_VAT_TU'))


def stale_map(session, rows, now: datetime) -> dict:
    rows = [r for r in rows if r.status in ('MOI', 'DANG_LAM', 'HOAN_THANH')]
    if not rows:
        return {}
    local = now.astimezone(TZ).date()
    holidays = _holidays(session, local - timedelta(days=MAX_DAYS), local)
    out = {}
    for r in rows:
        item = check(r, now, holidays)
        if item:
            out[r.id] = item
    return out


def open_rows(session):
    return list(session.scalars(select(ProductionOrder).where(ProductionOrder.status.in_(('MOI', 'DANG_LAM', 'HOAN_THANH')))
                                .order_by(ProductionOrder.id)))


# ---------- Zalo ----------

def recipients(session) -> list[tuple[User, ZaloChat]]:
    out = []
    for user, chat in session.execute(select(User, ZaloChat).join(ZaloChat, ZaloChat.user_id == User.id)
                                      .where(User.active.is_(True), ZaloChat.chat_type == 'PRIVATE').order_by(User.id, ZaloChat.id)):
        if has_role(user, 'dispatcher', 'warehouse') and 'director' not in user.roles and all(u.id != user.id for u, _ in out):
            out.append((user, chat))
    return out


def for_user(user, rows, items) -> list:
    """Phiếu đọng người này phải xử lý: điều phối thấy chưa bắt đầu (mốc 2)/làm lâu, thủ kho thấy chờ nhập/chưa vật tư."""
    dispatcher = 'dispatcher' in user.roles or 'director' in user.roles
    warehouse = has_role(user, 'warehouse')
    out = []
    for r in rows:
        item = items.get(r.id)
        if not item:
            continue
        if item['dispatcher'] and dispatcher:
            out.append((r, item))
        elif item['warehouse'] and warehouse and (not user.warehouse_codes or r.to_warehouse_code in user.warehouse_codes):
            out.append((r, item))
    return out


def _reason(item, tech):
    who = f' (thợ {tech})' if tech else ''
    return {'CHUA_BAT_DAU': f"giao {item['age']} chưa bắt đầu{who}", 'LAM_LAU': f"đang làm {item['age']}{who}",
            'CHO_NHAP': f"hoàn thành {item['age']}, chưa nhập kho",
            'CHUA_VAT_TU': 'chưa xuất vật tư, nhập kho sẽ vào giá vốn 0'}[item['code']]


def render_text(session, pairs, base_url: str) -> str:
    from .zalo_bot import MAX_TEXT
    names = dict(session.execute(select(Product.code, Product.name).where(Product.code.in_({r.product_code for r, _ in pairs}))).all())
    techs = dict(session.execute(select(User.id, User.full_name).where(User.id.in_({r.technician_id for r, _ in pairs if r.technician_id}))).all())
    head = f'🏭 Phiếu sản xuất cần anh xử lý ({len(pairs)}):'
    tail = f'Mở app: {base_url}/san-xuat?dong=1'
    body = []
    for n, (r, item) in enumerate(pairs):
        extra = f' +{len(r.outputs) - 1}' if len(r.outputs) > 1 else ''
        line = f"• {r.number} {names.get(r.product_code, r.product_code)}{extra}: {_reason(item, techs.get(r.technician_id))}"
        more = len(pairs) - n
        if len('\n'.join([head, *body, line, f'+{more} phiếu nữa', tail])) > MAX_TEXT:
            body.append(f'+{more} phiếu nữa, xem trong app')
            break
        body.append(line)
    return '\n'.join([head, *body, tail])


def slot_now(session, now: datetime):
    """Mốc gửi đang tới (time) nếu hôm nay là ngày làm việc và đang trong 2 giờ sau mốc, không thì None."""
    from .morning_report import is_workday
    local = now.astimezone(TZ)
    if not is_workday(session, local.date()):
        return None
    for t in sorted(get_settings().sx_stale_zalo_times, reverse=True):
        at = datetime.combine(local.date(), t, TZ)
        if at <= local < at + CATCH_UP:
            return t
    return None


def _slot_mark(day: date, t: time) -> str:
    return f'sx-dong:{day.isoformat()}:{t.strftime("%H%M")}'


def _order_mark(day: date, row, item) -> str:
    return f"sx-dong-phieu:{day.isoformat()}:{row.id}:{item['code']}"


def run_once(session, now=None, sender=None) -> int:
    from . import dispatch_alerts, zalo_bot
    from .service_tickets import _now
    s = get_settings()
    if not s.sx_stale_zalo or not s.zalo_bot_token.strip():
        return 0
    now = now or _now()
    t = slot_now(session, now)
    if t is None:
        return 0
    day = now.astimezone(TZ).date()
    first = t == min(s.sx_stale_zalo_times)
    people = [(u, c) for u, c in recipients(session) if session.get(DispatchZaloSent, (_slot_mark(day, t), u.id)) is None]
    if not people:
        return 0
    rows = open_rows(session)
    items = stale_map(session, rows, now)
    send = sender or zalo_bot.send_text
    count = 0
    for user, chat in people:
        pairs = for_user(user, rows, items)
        if not first:
            pairs = [(r, i) for r, i in pairs if session.get(DispatchZaloSent, (_order_mark(day, r, i), user.id)) is None]
        # Ghi mốc trước rồi mới gửi: gửi lỗi vẫn không gửi lại (tránh 2 tin), lỗi hiện ở Tin nhắn Zalo.
        slot = DispatchZaloSent(mark=_slot_mark(day, t), user_id=user.id)
        session.add(slot)
        for r, i in pairs:
            if session.get(DispatchZaloSent, (_order_mark(day, r, i), user.id)) is None:
                session.add(DispatchZaloSent(mark=_order_mark(day, r, i), user_id=user.id))
        session.commit()
        if not pairs:
            continue
        msg = send(session, chat, render_text(session, pairs, dispatch_alerts.base_url()), None, 'san_xuat')
        slot.message_id = getattr(msg, 'id', None)
        session.commit()
        count += 1
    return count
