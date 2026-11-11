"""Báo cáo sáng qua Zalo cho giám đốc (TUẤN duyệt mẫu 05/11/2026).

Gửi lúc TD_MORNING_REPORT_TIME (mặc định 07:30), các ngày làm việc (TD_WORK_DAYS, bỏ ngày lễ), tới mọi giám đốc
đã được gắn Zalo riêng (Tin nhắn Zalo > Gắn nhân viên). Đầu tin là 5 SỐ CHÍNH (TUẤN duyệt 11/11/2026): doanh thu, lãi gộp,
tiền đã thu, nợ quá hạn, việc trễ; bên dưới giữ phần chi tiết cũ, bỏ những dòng đã có ở 5 số.
Chỉ đọc: dùng lại đúng hàm tính số của Tổng quan, Báo cáo bán hàng, Sổ quỹ, Công nợ và Cảnh báo; không ghi chứng từ.
Doanh thu chưa VAT; tiền thu, quỹ, công nợ đã gồm VAT.
Mỗi ngày một tin cho mỗi người (mốc 'bao-cao-sang:YYYY-MM-DD' trong bảng dispatch_zalo_sent, không cần migration).
Sáng sau ngày nghỉ (thứ Hai, sau lễ) mục Hôm qua gộp từ ngày làm việc trước đến hôm qua.
Chạy nền cùng cờ TD_PUSH_LOOP; nhiều worker uvicorn chỉ một worker chạy nhờ khóa advisory.
"""
import logging
import threading
import time as clock
from datetime import date, datetime, time, timedelta

from sqlalchemy import Date, cast, func, select, text

from . import alerts, config, dashboard, finance_reports, purchase_reports, sales_reports, service_tickets
from .config import get_settings
from .models import (AttendanceHoliday, Customer, DispatchZaloSent, Fund, FundBalance, SalesReceipt, ServiceTicket,
                     User, ZaloChat)
from .security import has_role
from .service_sla import TZ

log = logging.getLogger(__name__)
LOCK_ID = 7_301_029
EVERY = 60
LATEST = time(10, 0)      # app mở lại sau giờ này thì bỏ qua báo cáo hôm đó, không gửi giữa trưa
TOP = 3
DAYS = ('T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN')


def mark(day: date) -> str:
    return f'bao-cao-sang:{day.isoformat()}'


def is_workday(session, day: date) -> bool:
    return day.weekday() in get_settings().work_days and session.get(AttendanceHoliday, day) is None


def period(session, today: date) -> tuple[date, date]:
    """Hôm qua; nếu hôm qua là ngày nghỉ thì lùi tới ngày làm việc gần nhất (gộp cả các ngày nghỉ ở giữa)."""
    end = today - timedelta(days=1)
    start = end
    for _ in range(10):
        if is_workday(session, start):
            break
        start -= timedelta(days=1)
    return start, end


def _money(value) -> str:
    return f'{int(value):,}'.replace(',', '.') + 'đ'


def _short(value) -> str:
    """12.450.000 → 12,5tr; dưới 1 triệu giữ nguyên số đầy đủ."""
    value = int(value)
    if abs(value) < 1_000_000:
        return _money(value)
    text_ = f'{value / 1_000_000:.1f}'.rstrip('0').rstrip('.').replace('.', ',')
    return text_ + 'tr'


def _cut(value: str, n: int = 28) -> str:
    value = ' '.join((value or '').split())
    return value if len(value) <= n else value[:n - 1].rstrip() + '…'


def _day_label(d: date) -> str:
    return f'{DAYS[d.weekday()]} {d.strftime("%d/%m")}'


def _service_revenue(session, start, end):
    t = ServiceTicket
    row = session.execute(select(func.coalesce(func.sum(t.subtotal), 0), func.count())
                          .where(*dashboard._service_revenue(), *sales_reports._window(t.priced_at, start, end))).one()
    return int(row[0]), int(row[1])


def _services_done(session, start, end) -> int:
    t = ServiceTicket
    return int(session.scalar(select(func.count()).where(t.status.in_(('HOAN_THANH', 'DA_DONG')), t.priced_at.is_not(None),
                                                         *sales_reports._window(t.priced_at, start, end))))


def _prev_month_window(start: date, end: date) -> tuple[date, date]:
    prev_end_of_month = start - timedelta(days=1)
    prev_start = prev_end_of_month.replace(day=1)
    return prev_start, prev_start.replace(day=min(end.day, prev_end_of_month.day))


def build(session, user, today: date, now: datetime | None = None) -> dict:
    """Số liệu báo cáo sáng cho giám đốc. today = ngày gửi (giờ VN)."""
    with session.no_autoflush:
        dashboard.check_reader(user)
        now = now or dashboard._now()
        start, end = period(session, today)
        sales = sales_reports.totals(session, user, start, end)
        svc_amount, _ = _service_revenue(session, start, end)
        cash = finance_reports.category_totals(session, user, start, end)

        month_start = end.replace(day=1)
        month_sales = sales_reports.totals(session, user, month_start, end)['subtotal']
        month_svc, _ = _service_revenue(session, month_start, end)
        prev_start, prev_end = _prev_month_window(month_start, end)
        prev = sales_reports.totals(session, user, prev_start, prev_end)['subtotal'] + _service_revenue(session, prev_start, prev_end)[0]
        month = month_sales + month_svc
        margin = margin_rate = margin_day = margin_day_rate = None
        if has_role(user, 'director'):
            m = purchase_reports.gross_margin(session, user, month_start, end)['totals']
            margin, margin_rate = int(m['profit']), _rate(m['rate'])
            m = purchase_reports.gross_margin(session, user, start, end)['totals']
            margin_day, margin_day_rate = int(m['profit']), _rate(m['rate'])

        funds = {k: int(v) for k, v in session.execute(select(Fund.kind, func.coalesce(func.sum(FundBalance.balance), 0))
                                                        .join(FundBalance, FundBalance.fund_code == Fund.code).group_by(Fund.kind))}
        pending = session.execute(select(func.count(), func.coalesce(func.sum(SalesReceipt.amount), 0))
                                  .where(SalesReceipt.status == 'CHO_XAC_NHAN')).one()
        debt_total = int(finance_reports.debt_totals(session, user)['remaining'])
        overdue = alerts.customer_overdue(user, today)
        # Chỉ tổng nợ quá hạn, không liệt kê tên khách (TUẤN 05/11/2026).
        overdue_total = int(session.scalar(select(func.coalesce(func.sum(overdue.c.remaining), 0)).where(overdue.c.days > 0)))

        ncc_days = config.ALERT_DEBT_DAYS   # cùng mốc với cảnh báo 'NCC nợ quá hạn'

        def ncc_overdue(as_of):
            q = purchase_reports._aging(as_of, overdue_days=ncc_days).subquery()
            return int(session.scalar(select(func.coalesce(func.sum(q.c.overdue), 0))))
        ncc_now = ncc_overdue(today)
        ncc_week = max(0, ncc_overdue(today + timedelta(days=7)) - ncc_now)

        t = ServiceTicket
        opened = t.status.in_(service_tickets.OPEN)
        open_count, late_count = session.execute(select(func.count().filter(opened), func.count().filter(opened, t.due_at < now))).one()
        late_rows = session.execute(select(t.number, t.due_at, Customer.name, User.full_name)
                                    .join(Customer, Customer.code == t.customer_code).outerjoin(User, User.id == t.assignee_id)
                                    .where(opened, t.due_at < now).order_by(t.due_at, t.id).limit(TOP)).all()
        appt_day = cast(func.timezone('Asia/Ho_Chi_Minh', t.appointment_at), Date)
        appts, appts_free = session.execute(select(func.count(), func.count().filter(t.assignee_id.is_(None)))
                                            .where(opened, t.appointment_at.is_not(None), appt_day == today)).one()

        approvals = _approvals(session, user, start, end)

    from . import below_cost
    below_rows = below_cost.recent(session, start, end) if has_role(user, 'director') else []
    return dict(
        today=today, start=start, end=end, **approvals,
        revenue=int(sales['subtotal']) + svc_amount, sales_revenue=int(sales['subtotal']), service_revenue=svc_amount,
        orders=int(sales['orders']), services_done=_services_done(session, start, end),
        income=int(cash['income']), expense=int(cash['expense']),
        month_start=month_start, month_revenue=month, prev_revenue=prev, prev_month=prev_start, margin=margin,
        margin_rate=margin_rate, margin_day=margin_day, margin_day_rate=margin_day_rate,
        fund_cash=funds.get('TIEN_MAT', 0), fund_bank=funds.get('NGAN_HANG', 0),
        pending_count=int(pending[0]), pending_amount=int(pending[1]),
        debt_total=debt_total, overdue_total=overdue_total,
        ncc_overdue=ncc_now, ncc_week=ncc_week,
        open=int(open_count), late=int(late_count),
        late_rows=[dict(number=r.number, customer=r.name, tech=r.full_name, minutes=int((now - r.due_at).total_seconds() // 60))
                   for r in late_rows],
        appointments=int(appts), appointments_free=int(appts_free),
        below_cost=below_rows)


def _approvals(session, user, start: date, end: date) -> dict:
    """Hạn mức duyệt (TUẤN duyệt 11/11/2026): phiếu chờ giám đốc (trên mức/không giao/của kế toán) và phiếu kế toán đã duyệt."""
    from . import approval_limits
    if not has_role(user, 'director'):
        return dict(boss_waiting=[], delegated=None)
    waiting = [r for r in approval_limits.queue(session) if not r['delegable']]
    lo = datetime.combine(start, time.min, TZ)
    hi = datetime.combine(end + timedelta(days=1), time.min, TZ)
    return dict(boss_waiting=waiting, delegated=approval_limits.delegated_between(session, lo, hi))


def approval_lines(d: dict, base_url: str) -> list[str]:
    lines = []
    waiting = d.get('boss_waiting') or []
    if waiting:
        lines += ['', f'✍️ CHỜ ANH DUYỆT · {len(waiting)}']
        for r in waiting[:5]:
            money = f" {_money(r['amount'])}" if r['amount'] else ''
            lines.append(f"· {r['label']} · {_first_name(r['who'])}{money}")
        if len(waiting) > 5:
            lines.append(f'· … và {len(waiting) - 5} phiếu nữa')
        lines.append(f'→ {base_url}/viec-cua-toi')
    k = d.get('delegated')
    if k:
        parts = []
        if k['advance']:
            parts.append(f"{k['advance']} tạm ứng {_short(k['advance_amount'])}")
        if k['punch']:
            parts.append(f"{k['punch']} bổ sung giờ")
        if k['leave']:
            parts.append(f"{k['leave']} xin nghỉ")
        if k['disposal']:
            parts.append(f"{k['disposal']} hủy hàng {_short(k['disposal_amount'])}")
        if parts:
            lines += ['', 'Kế toán đã duyệt hôm qua: ' + ' · '.join(parts)]
    return lines


def _rate(value):
    return None if value is None else float(value)


def _pct(rate) -> str:
    return '' if rate is None else f" ({f'{rate:.0f}'}%)"


def period_label(d: dict) -> str:
    start, end = d['start'], d['end']
    return _day_label(end) if start == end else f'{_day_label(start)}–{_day_label(end)}'


def month_change(d: dict) -> int | None:
    if d['prev_revenue'] <= 0:
        return None
    return round((d['month_revenue'] - d['prev_revenue']) * 100 / d['prev_revenue'])


def five_lines(d: dict) -> list[str]:
    """5 số chính đầu tin (TUẤN duyệt 11/11/2026). Doanh thu, lãi gộp chưa VAT; tiền thu, công nợ đã gồm VAT."""
    month = f"   Tháng {d['end'].month}: {_short(d['month_revenue'])}"
    pct = month_change(d)
    if pct is not None:
        month += f" ({'+' if pct >= 0 else ''}{pct}% cùng kỳ T{d['prev_month'].month})"
    lines = [f"1️⃣ Doanh thu: {_money(d['revenue'])}", month]
    if d.get('margin_day') is not None:
        lines += [f"2️⃣ Lãi gộp: {_money(d['margin_day'])}{_pct(d.get('margin_day_rate'))}",
                  f"   Tháng {d['end'].month}: {_short(d['margin'])}{_pct(d.get('margin_rate'))}"]
    lines += [f"3️⃣ Tiền đã thu: {_money(d['income'])}",
              (f"   Chờ KT xác nhận: {d['pending_count']} phiếu · {_short(d['pending_amount'])}" if d['pending_count']
               else '   Chờ KT xác nhận: không có'),
              f"4️⃣ Nợ quá hạn: {_money(d['overdue_total'])}" if d['overdue_total'] else '4️⃣ Nợ quá hạn: không có',
              f"   Tổng khách nợ: {_short(d['debt_total'])}"]
    if d['late']:
        r = d['late_rows'][0]
        lines += [f"5️⃣ Việc trễ: {d['late']} phiếu quá hẹn",
                  f"   Cũ nhất: {r['number']} {_cut(r['customer'], 22)} {_late(r['minutes'])} ({_first_name(r['tech'])})"]
    else:
        lines.append('5️⃣ Việc trễ: không có phiếu quá hẹn')
    return lines


def _late(minutes: int) -> str:
    if minutes >= 24 * 60:
        return f'trễ {minutes // (24 * 60)} ngày'
    return f'trễ {max(1, minutes // 60)} giờ' if minutes >= 60 else f'trễ {max(1, minutes)} phút'


def _first_name(full: str | None) -> str:
    return full.split()[-1] if full else 'chưa giao thợ'


def render_text(d: dict, base_url: str = 'https://quanlythanhduc.com') -> str:
    start, end = d['start'], d['end']
    when = period_label(d)
    lines = [f"☀️ 5 SỐ SÁNG {_day_label(d['today'])} · số {when}", '', *five_lines(d), '──────────',
             '💰 HÔM QUA' if start == end and (d['today'] - end).days == 1 else f'💰 {when}',
             f"· Bán hàng {_money(d['sales_revenue'])[:-1]} · Dịch vụ {_money(d['service_revenue'])[:-1]}",
             f"· {d['orders']} đơn giao, {d['services_done']} phiếu DV xong",
             f"· Thực chi: {_money(d['expense'])}", '',
             '🏦 TIỀN & CÔNG NỢ',
             f"Quỹ: {_money(d['fund_cash'] + d['fund_bank'])} (TM {_short(d['fund_cash'])} · NH {_short(d['fund_bank'])})"]
    ncc = f"Nợ NCC đến hạn 7 ngày tới: {_money(d['ncc_week'])}" if d['ncc_week'] else 'Nợ NCC đến hạn 7 ngày tới: không có'
    if d['ncc_overdue']:
        ncc += f" (đã quá hạn {_short(d['ncc_overdue'])})"
    lines += [ncc, '', '🔧 DỊCH VỤ', f"Đang mở {d['open']} · Quá hẹn {d['late']}"]
    lines += [f"· {r['number']} {_cut(r['customer'], 24)} – {_late(r['minutes'])} ({_first_name(r['tech'])})" for r in d['late_rows']]
    appt = f"Lịch hẹn hôm nay: {d['appointments']} lượt" if d['appointments'] else 'Lịch hẹn hôm nay: không có'
    if d['appointments_free']:
        appt += f" ({d['appointments_free']} chưa giao thợ)"
    lines += [appt, *approval_lines(d, base_url)]
    below = d.get('below_cost') or []
    if below:
        # Đơn chốt dưới giá vốn (TUẤN 11/11/2026): số đơn, người chốt, lỗ, lý do.
        lines += ['', f"⚠️ ĐƠN DƯỚI GIÁ VỐN: {len(below)} đơn · lỗ {_money(sum(r['loss'] for r in below))}"]
        lines += [f"· {r['number']} {_first_name(r['by'])} lỗ {_short(r['loss'])}" + (f" – {_cut(r['reason'], 40)}" if r['reason'] else '')
                  for r in below[:5]]
    lines += ['', f"Xem đủ: {base_url}/tong-quan"]
    return '\n'.join(lines)


def recipients(session) -> list[tuple[User, ZaloChat]]:
    out = []
    for user, chat in session.execute(select(User, ZaloChat).join(ZaloChat, ZaloChat.user_id == User.id)
                                      .where(User.active.is_(True), ZaloChat.chat_type == 'PRIVATE').order_by(User.id, ZaloChat.id)):
        if 'director' in user.roles and all(u.id != user.id for u, _ in out):
            out.append((user, chat))
    return out


def due(session, now: datetime) -> bool:
    s = get_settings()
    local = now.astimezone(TZ)
    return s.morning_report_time <= local.time() < LATEST and is_workday(session, local.date())


def run_once(session, now=None, sender=None) -> int:
    from . import dispatch_alerts, zalo_bot
    s = get_settings()
    if not s.morning_report or not s.zalo_bot_token.strip():
        return 0
    now = now or dashboard._now()
    if not due(session, now):
        return 0
    today = now.astimezone(TZ).date()
    people = [(u, c) for u, c in recipients(session)
              if session.get(DispatchZaloSent, (mark(today), u.id)) is None]
    send = sender or zalo_bot.send_text
    count = 0
    for user, chat in people:
        body = render_text(build(session, user, today, now), dispatch_alerts.base_url())
        # Ghi mốc trước rồi mới gửi: gửi lỗi vẫn không gửi lại (tránh 2 tin), lỗi hiện ở Tin nhắn Zalo.
        row = DispatchZaloSent(mark=mark(today), user_id=user.id)
        session.add(row)
        session.commit()
        msg = send(session, chat, body, None, 'bao_cao_sang')
        row.message_id = getattr(msg, 'id', None)
        session.commit()
        count += 1
    return count


def send_test(session, user, now=None):
    """Nút Gửi thử: gửi ngay bản báo cáo hôm nay cho chính giám đốc đang bấm; không ghi mốc nên 7:30 vẫn gửi."""
    from . import dispatch_alerts, zalo_bot
    now = now or dashboard._now()
    chat = next((c for u, c in recipients(session) if u.id == user.id), None)
    if chat is None:
        return None
    body = render_text(build(session, user, now.astimezone(TZ).date(), now), dispatch_alerts.base_url())
    return zalo_bot.send_text(session, chat, body, None, 'bao_cao_sang')


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
            log.exception('morning report loop')
        clock.sleep(EVERY)


def start():
    threading.Thread(target=_loop, name='morning-report', daemon=True).start()
