"""Nhân sự AI (TUẤN duyệt 15/11/2026, mẫu NVFiiyXwNrZivXwA5Ft2Ht): một Nhân viên AI trong trang /nhan-vien-ao.

Chạy bằng code trên máy chủ (gọi mỗi phút từ nhan_vien_ao.run_once), không gọi API. Bốn việc:
1. Nhắc quên vào ca: lúc remind_at (07:45) ngày làm việc, ai chưa bấm Vào ca và không nghỉ phép nhận tin Zalo riêng.
   Ra ca đã có attendance.auto_clock_out nên không nhắc.
2. Báo giám đốc chấm công lúc report_times: mốc trước giờ tan ca báo đi trễ, chưa vào ca, nghỉ phép;
   mốc sau giờ tan ca báo về sớm, app tự chốt ra ca, còn trong ca, không chấm công cả ngày. Không có gì thì không nhắn.
3. Tin tuần (weekly_day lúc weekly_time): mỗi người nhận công, đi trễ, KPI so mục tiêu và hoa hồng tạm tính tháng của
   chính mình (không có lãi gộp). Giám đốc nhận bảng cả đội (có lãi gộp) nếu weekly_director.
4. Soát bảng lương: ngày payroll_day lúc payroll_time nhắn giám đốc các điểm cần xem; trang /luong hiện cùng kết quả.

Ranh giới cứng: chỉ đọc và nhắn nội bộ. Không sửa tiền, công, hoa hồng, lương; không duyệt, khóa, chi.
Giám đốc không bị nhắc chấm công. Chỉ nhắn người đã gắn Zalo riêng. Mốc chống gửi lặp ở dispatch_zalo_sent.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select

from . import attendance, kpi
from . import nhan_vien_ao as nva
from .config import get_settings
from .models import (AttendanceMonthLock, AttendancePunch, AttendanceRequest, CommissionMonth, DispatchZaloSent, Employee,
                     PayrollLine, PayrollMonth, User, VirtualAgent, VirtualAgentLog)
from .service_sla import TZ

CODE = 'NHAN_SU'
WINDOW = timedelta(hours=1)        # máy chủ khởi động lại trễ quá 1 giờ sau mốc thì bỏ mốc đó
WEEKDAYS = ('Thứ 2', 'Thứ 3', 'Thứ 4', 'Thứ 5', 'Thứ 6', 'Thứ 7', 'Chủ nhật')
GONE = ('CHUA_VAO_LAM', 'DA_NGHI_VIEC')
# Chỉ số giám đốc thấy trong bảng cả đội, theo vai trò.
TEAM_KEYS = ('tho_hoan_thanh', 'tho_giao_don', 'kd_doanh_thu', 'kd_lai_gop')


def money(value) -> str:
    return f'{int(value or 0):,}'.replace(',', '.')


def _short_money(value) -> str:
    value = int(value or 0)
    if abs(value) >= 1_000_000:
        return f'{value / 1_000_000:.1f}'.rstrip('0').rstrip('.').replace('.', ',') + 'tr'
    return money(value)


def _num(value) -> str:
    value = Decimal(str(value or 0))
    return f'{value.normalize():f}'.replace('.', ',') if value != value.to_integral_value() else str(int(value))


def _due(now, raw: str) -> bool:
    if not raw:
        return False
    local = now.astimezone(TZ)
    at = datetime.combine(local.date(), datetime.strptime(raw, '%H:%M').time(), TZ)
    return at <= local < at + WINDOW


def _workday(session, day) -> bool:
    from .morning_report import is_workday
    return is_workday(session, day)


def _sent(session, mark, uid) -> bool:
    return session.get(DispatchZaloSent, (mark, uid)) is not None


def _mark(session, mark, uid):
    session.add(DispatchZaloSent(mark=mark, user_id=uid))
    session.commit()


def _log(session, action, text, target_id=None, reason=''):
    session.add(VirtualAgentLog(agent=CODE, action=action, text=text[:2000], target_id=target_id, reason=reason[:500]))
    session.commit()


def staff(session, day) -> list[tuple[User, Employee]]:
    """Nhân viên có hồ sơ nhân sự còn làm trong ngày, tài khoản đang mở, không phải giám đốc."""
    q = select(User, Employee).join(Employee, Employee.user_id == User.id).where(
        User.active.is_(True), Employee.start_date <= day, or_(Employee.end_date.is_(None), Employee.end_date >= day)).order_by(Employee.code)
    return [(u, e) for u, e in session.execute(q) if 'director' not in (u.roles or []) and u.username != nva.SYSTEM_USERNAME]


def short_names(people) -> dict[int, str]:
    """Tên gọi (chữ cuối); trùng thì dùng họ tên đầy đủ."""
    last = {u.id: (u.full_name or u.username).split()[-1] for u in people}
    seen = {}
    for name in last.values():
        seen[name] = seen.get(name, 0) + 1
    return {u.id: last[u.id] if seen[last[u.id]] == 1 else u.full_name for u in people}


def _directors(session) -> list:
    return [(uid, chat) for uid, chat in nva.chats(session).items()
            if 'director' in (session.get(User, uid).roles or [])]


def _days(session, ids, day) -> dict:
    with session.no_autoflush:
        data = attendance._dataset(session, ids, day, day)
        return {i: attendance._day(data, i, day) for i in ids}


def _hm(stamp) -> str:
    return stamp.astimezone(TZ).strftime('%H:%M')


# ---------- 1. Nhắc quên vào ca ----------

def remind_once(session, now=None, sender=None) -> int:
    row = nva.active(session, CODE)
    now = now or nva._now()
    if row is None or not nva._zalo_ready():
        return 0
    raw = nva.cfg(row)['remind_at']
    day = now.astimezone(TZ).date()
    if not raw or not _due(now, raw) or not _workday(session, day):
        return 0
    people = nva.chats(session)
    mark = f'nsa-vao:{day.isoformat()}'
    todo = [(u, e) for u, e in staff(session, day) if u.id in people and not _sent(session, mark, u.id)]
    if not todo:
        return 0
    days = _days(session, [u.id for u, _ in todo], day)
    send = nva._sender(sender)
    base = nva._base()
    count = 0
    for user, _ in todo:
        d = days[user.id]
        if d['punches'] or d['paid_leave'] or d['unpaid_leave'] or d['status'] in GONE:
            continue
        path = '/tho' if 'technician' in (user.roles or []) else '/cham-cong'
        body = (f'Nhắc chấm công: {raw} rồi bạn chưa bấm Vào ca hôm nay.\nMở app: {base}{path}\n'
                f'Nếu nghỉ, bấm Xin nghỉ trong Chấm công.')
        if nva._send(session, people[user.id], user.id, mark, body, send):
            _log(session, 'NHAC', f'Nhắc {user.full_name}: chưa bấm Vào ca lúc {raw}', user.id)
            count += 1
    return count


# ---------- 2. Báo giám đốc chấm công ----------

def attendance_text(session, day, at: str, morning: bool) -> str | None:
    people = [u for u, _ in staff(session, day)]
    if not people:
        return None
    names = short_names(people)
    days = _days(session, [u.id for u in people], day)
    here = [u for u in people if days[u.id]['status'] not in GONE]
    lines = []
    if morning:
        late = [f"{names[u.id]} {_hm(days[u.id]['first_in'])} ({days[u.id]['late_minutes']} phút)" for u in here if days[u.id]['late_minutes']]
        leave = [names[u.id] + (' (không lương)' if days[u.id]['unpaid_leave'] and not days[u.id]['paid_leave'] else '')
                 for u in here if not days[u.id]['punches'] and (days[u.id]['paid_leave'] or days[u.id]['unpaid_leave'])]
        absent = [names[u.id] for u in here if not days[u.id]['punches'] and not days[u.id]['paid_leave'] and not days[u.id]['unpaid_leave']]
        if not (late or leave or absent):
            return None
        came = sum(1 for u in here if days[u.id]['first_in'])
        lines.append(f'Đã vào ca {came}/{len(here)}.')
        lines.append('• Đi trễ: ' + (', '.join(late) if late else 'không có'))
        lines.append('• Chưa vào ca: ' + (', '.join(absent) if absent else 'không có'))
        lines.append('• Nghỉ phép: ' + (', '.join(leave) if leave else 'không có'))
        head = f"Chấm công sáng {day.strftime('%d/%m')} (Nhân sự AI)"
    else:
        auto = {r.user_id: r for r in session.scalars(select(AttendancePunch).where(
            AttendancePunch.user_id.in_([u.id for u in here]), AttendancePunch.kind == 'OUT', AttendancePunch.source == 'TU_DONG',
            AttendancePunch.at >= attendance._bounds(day)[0], AttendancePunch.at < attendance._bounds(day)[1]))}
        early, still, none_ = [], [], []
        for u in here:
            d = days[u.id]
            if d['missing_out']:
                still.append(names[u.id])
            elif d['last_out'] and d['early_minutes'] and u.id not in auto:
                early.append(f"{names[u.id]} ra {_hm(d['last_out'])} (sớm {d['early_minutes']} phút)")
            elif not d['punches'] and not d['paid_leave'] and not d['unpaid_leave']:
                none_.append(names[u.id])
        autos = [f'{names[uid]} {_hm(r.at)}' for uid, r in auto.items()]
        if not (early or still or none_ or autos):
            return None
        if early:
            lines.append('• Về sớm: ' + ', '.join(early))
        if autos:
            lines.append('• App tự chốt ra ca (quên bấm): ' + ', '.join(autos))
        if still:
            lines.append('• Còn trong ca (đang làm việc): ' + ', '.join(still))
        if none_:
            lines.append('• Không chấm công cả ngày: ' + ', '.join(none_))
        head = f"Chấm công chiều {day.strftime('%d/%m')} (Nhân sự AI)"
    return '\n'.join([head, *lines, f'Mở bảng công: {nva._base()}/cham-cong/bang'])


def report_once(session, now=None, sender=None) -> int:
    row = nva.active(session, CODE)
    now = now or nva._now()
    if row is None or not nva._zalo_ready():
        return 0
    day = now.astimezone(TZ).date()
    times = [t for t in nva.cfg(row)['report_times'] if _due(now, t)]
    if not times or not _workday(session, day):
        return 0
    bosses = _directors(session)
    if not bosses:
        return 0
    send = nva._sender(sender)
    end = get_settings().work_end
    count = 0
    for at in times:
        mark = f"nsa-cc:{day.isoformat()}:{at.replace(':', '')}"
        todo = [(uid, chat) for uid, chat in bosses if not _sent(session, mark, uid)]
        if not todo:
            continue
        body = attendance_text(session, day, at, datetime.strptime(at, '%H:%M').time() < end)
        for uid, chat in todo:
            if body is None:
                _mark(session, mark, uid)
            elif nva._send(session, chat, uid, mark, body, send):
                _log(session, 'BAO', 'Báo giám đốc: ' + ' · '.join(body.split('\n')[1:-1]), uid)
                count += 1
    return count


# ---------- 3. Tin tuần KPI + hoa hồng ----------

def _fmt(value, unit) -> str:
    if value is None:
        return 'chưa có'
    if unit == 'đồng':
        return money(value) + ' đ'
    if unit == '%':
        return f'{int(Decimal(str(value)).quantize(Decimal(1)))}%'
    if unit == 'giờ':
        return _num(Decimal(str(value)).quantize(Decimal('0.1'))) + ' giờ'
    return f'{int(Decimal(str(value)).quantize(Decimal(1)))} {unit}'


def _commission_on(month) -> bool:
    return month >= kpi.month_day(get_settings().commission_start)


def commission_draft(session, month, ids) -> dict[int, int]:
    from . import commissions
    if not ids or not _commission_on(month):
        return {}
    out = {}
    for r in commissions.draft(session, month, list(ids)):
        out[r['user_id']] = out.get(r['user_id'], 0) + int(r['amount'] or 0)
    session.commit()   # nhả khóa đọc chung của hoa hồng
    return out


def _standard_days(session, emp, month) -> int:
    from . import employees
    salary = employees.salary_on(session, emp.id, kpi.month_end(month))
    return int(salary.standard_days) if salary and salary.standard_days else 26


def staff_text(session, user, emp, month, day, base: str) -> str:
    total = attendance.month_summary(session, user.id, month)
    lines = [f"Tháng {month.strftime('%m')} tới hôm nay:",
             f"• Công: {_num(total['cong'])}/{_standard_days(session, emp, month)}"
             + (f" · đi trễ {total['late_count']} lần ({total['late_minutes']} phút)" if total['late_count'] else ' · không đi trễ')]
    values = kpi._compute(session, month, [user.id], sensitive=False).get(user.id, {})
    goals = kpi.targets(session, month)
    for m in kpi.METRICS:
        if m['key'] not in values:
            continue
        actual, target = values[m['key']], kpi.target_for(goals, user.id, m['key'])
        line = f"• {m['label']}: {_fmt(actual, m['unit'])}"
        if target is not None:
            pct = kpi.achievement(actual, target, m['key'])
            line += f" · mục tiêu {_fmt(target, m['unit'])}" + (f' ({int(pct)}%)' if pct is not None else '') + (' ✓' if pct is not None and pct >= 100 else '')
        lines.append(line)
    if set(user.roles or []) & {'sales', 'technician'} and _commission_on(month):
        amount = commission_draft(session, month, [user.id]).get(user.id, 0)
        lines.append(f"Hoa hồng tạm tính tháng {month.strftime('%m')}: {money(amount)} đ")
        lines.append('(chưa chốt, còn đổi khi khách trả thêm hoặc trả hàng)')
    return '\n'.join(['Tuần của bạn (Nhân sự AI)', *lines, f'Xem chi tiết: {base}/kpi'])


def team_text(session, people, month, base: str) -> str:
    names = short_names([u for u, _ in people])
    ids = [u.id for u, _ in people]
    values = kpi.compute(session, month, ids)
    comm = commission_draft(session, month, ids)
    totals = attendance.month_summary(session, ids, month) if ids else {}
    lines = []
    for user, _ in people:
        parts = [f"công {_num(totals[user.id]['cong'])}"]
        v = values.get(user.id, {})
        if v.get('tho_hoan_thanh') is not None and 'tho_hoan_thanh' in v:
            parts.append(f"{int(v['tho_hoan_thanh'])} phiếu xong")
        if 'kd_doanh_thu' in v:
            parts.append(f"DT {_short_money(v['kd_doanh_thu'])}")
        if 'kd_lai_gop' in v:
            parts.append(f"lãi gộp {_short_money(v['kd_lai_gop'])}")
        if user.id in comm or set(user.roles or []) & {'sales', 'technician'} and _commission_on(month):
            parts.append(f'HH {money(comm.get(user.id, 0))}')
        lines.append(f'• {names[user.id]}: ' + ' · '.join(parts))
    tail = [f'Tổng hoa hồng tạm tính: {money(sum(comm.values()))} đ'] if _commission_on(month) else []
    return '\n'.join([f"KPI và hoa hồng cả đội · tháng {month.strftime('%m')} tới hôm nay", *lines, *tail, f'Mở KPI tổng: {base}/kpi/tong'])


def weekly_once(session, now=None, sender=None) -> int:
    row = nva.active(session, CODE)
    now = now or nva._now()
    if row is None or not nva._zalo_ready():
        return 0
    c = nva.cfg(row)
    local = now.astimezone(TZ)
    if local.weekday() != int(c['weekly_day']) or not _due(now, c['weekly_time']):
        return 0
    day = local.date()
    month = day.replace(day=1)
    mark = f'nsa-tuan:{day.isoformat()}'
    chats = nva.chats(session)
    people = staff(session, day)
    send = nva._sender(sender)
    base = nva._base()
    count = 0
    for user, emp in people:
        if user.id not in chats or _sent(session, mark, user.id):
            continue
        body = staff_text(session, user, emp, month, day, base)
        if nva._send(session, chats[user.id], user.id, mark, body, send):
            _log(session, 'BAO', f'Gửi {user.full_name} tin tuần KPI và hoa hồng tạm tính', user.id)
            count += 1
    if c['weekly_director'] and people:
        bosses = [(uid, chat) for uid, chat in _directors(session) if not _sent(session, mark, uid)]
        if bosses:
            body = team_text(session, people, month, base)
            for uid, chat in bosses:
                if nva._send(session, chat, uid, mark, body, send):
                    _log(session, 'BAO', f'Gửi giám đốc bảng KPI và hoa hồng cả đội ({len(people)} người)', uid)
                    count += 1
    return count


# ---------- 4. Soát bảng lương ----------

def _next_month(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1)


def payroll_check(session, month, now=None) -> dict:
    """Chỉ đọc: các điểm giám đốc cần xem trước khi duyệt bảng lương tháng `month`. Không ghi gì."""
    from . import payroll
    now = now or nva._now()
    month = month.replace(day=1)
    thang = month.strftime('%Y-%m')
    out = dict(month=month, at=now, items=[], ok=[], approved=session.get(PayrollMonth, month) is not None)
    if out['approved']:
        return out
    with session.no_autoflush:
        rows = payroll._draft(session, month)
    items, ok = out['items'], out['ok']

    def add(level, title, detail, who, href):
        items.append(dict(level=level, title=title, detail=detail, who=who, href=href))

    # Hoa hồng
    if _commission_on(month):
        if session.get(CommissionMonth, month) is None:
            from . import commissions
            draft = commissions.draft(session, month)
            session.commit()
            total = sum(int(r['amount'] or 0) for r in draft)
            nxt = _next_month(month)
            when = f" Khóa được từ {nxt.strftime('%d/%m')}." if now.astimezone(TZ).date() < nxt else ''
            add('hot', f"Hoa hồng tháng {month.strftime('%m')} chưa khóa",
                f'Bảng lương chỉ lấy hoa hồng đã khóa nên đang là 0 đ. Hoa hồng tạm tính: {money(total)} đ.{when}',
                'Giám đốc', f'/hoa-hong/thang?thang={thang}')
            missing = sum(1 for r in draft if r.get('missing_rule'))
            if missing:
                add('cho', f'{missing} dòng hoa hồng chưa có quy tắc', 'Các dòng này đang tính 0 đ cho đến khi có quy tắc phù hợp.',
                    'Giám đốc', '/hoa-hong/quy-tac')
            else:
                ok.append('dòng hoa hồng đều có quy tắc')
    # Bảng công
    if not attendance.is_locked(session.get(AttendanceMonthLock, month)):
        add('cho', f"Bảng công tháng {month.strftime('%m')} chưa khóa", 'Duyệt lương cần khóa bảng công. Khóa sau khi hết tháng và đã duyệt hết yêu cầu chấm công.',
            'Giám đốc', f'/cham-cong/bang?thang={thang}')
    uids = [r['user_id'] for r in rows]
    names = {r['user_id']: r['full_name'] for r in rows}
    start, end = month, attendance.month_end(month)
    lo, hi = attendance._bounds(start)[0], attendance._bounds(end)[1]
    pending = list(session.scalars(select(AttendanceRequest).where(
        AttendanceRequest.status == 'CHO_DUYET', AttendanceRequest.user_id.in_(uids or [0]),
        or_((AttendanceRequest.punch_at >= lo) & (AttendanceRequest.punch_at < hi),
            (AttendanceRequest.from_date <= end) & (AttendanceRequest.to_date >= start))).order_by(AttendanceRequest.id)))
    if pending:
        sample = ', '.join(f"{names[r.user_id]} ({attendance.KINDS.get(r.kind, r.kind).lower()} "
                           f"{(r.punch_at.astimezone(TZ).date() if r.punch_at else r.from_date).strftime('%d/%m')})" for r in pending[:3])
        add('cho', f'{len(pending)} yêu cầu chấm công chờ duyệt', sample + (' …' if len(pending) > 3 else '') + '.',
            'Giám đốc duyệt', '/cham-cong/duyet')
    else:
        ok.append('không còn yêu cầu chấm công chờ duyệt')
    today = now.astimezone(TZ).date()
    prev = {r.employee_id: r for r in session.scalars(select(PayrollLine).where(PayrollLine.month == (month - timedelta(days=1)).replace(day=1)))}
    agent = session.get(VirtualAgent, CODE)   # chỉ đọc, không tạo dòng cấu hình khi mở trang Lương
    pct = int({**nva.AGENTS[CODE]['defaults'], **((agent.config if agent else None) or {})}['change_pct'])
    with session.no_autoflush:   # một lần đọc chấm công cho cả bảng, không đọc từng người
        data = attendance._dataset(session, uids, start, min(end, today - timedelta(days=1))) if uids and start < today else None
    flags = dict(salary=False, missing=False, absent=False, net=False, advance=False, change=False)
    for r in rows:
        name = r['full_name']
        if r['salary_version_id'] is None:
            flags['salary'] = True
            add('hot', f'{name} chưa có mức lương', 'Lương công và phụ cấp đang là 0 đ. Duyệt bảng lương sẽ bị chặn.', 'Giám đốc', '/nhan-su')
        days = [attendance._day(data, r['user_id'], d) for d in attendance._days(start, min(end, today - timedelta(days=1)))] if data else []
        miss = [d['day'].strftime('%d/%m') for d in days if d['missing_out']]
        if miss:
            flags['missing'] = True
            add('hot', f'{name} thiếu giờ ra {len(miss)} ngày', f"{', '.join(miss)} không có giờ ra nên tính 0 công.",
                f"{name.split()[-1]} gửi bổ sung", f'/cham-cong/bang?thang={thang}')
        absent = [d['day'].strftime('%d/%m') for d in days if d['status'] == 'NGHI_KHONG_PHEP']
        if absent:
            flags['absent'] = True
            add('cho', f'{name} nghỉ không phép {len(absent)} ngày', ', '.join(absent) + ' không chấm công, không có đơn nghỉ.',
                'Giám đốc xem', f'/cham-cong/bang?thang={thang}')
        if r['net'] < 0:
            flags['net'] = True
            add('hot', f'{name} thực nhận âm', f"Thực nhận {money(r['net'])} đ. Kiểm tra tạm ứng và khấu trừ.", 'Giám đốc', f'/luong?thang={thang}')
        elif r['advance'] and r['gross'] and r['advance'] * 2 > r['gross']:
            flags['advance'] = True
            add('cho', f'{name} tạm ứng hơn nửa thu nhập', f"Tạm ứng {money(r['advance'])} đ trên thu nhập {money(r['gross'])} đ.", 'Giám đốc', '/tam-ung')
        old = prev.get(r['employee_id'])
        if old is not None and old.gross > 0 and abs(r['gross'] - old.gross) * 100 > pct * old.gross:
            flags['change'] = True
            diff = (r['gross'] - old.gross) * 100 // old.gross
            add('cho', f"Thu nhập {name} {'tăng' if diff > 0 else 'giảm'} {abs(diff)}% so tháng trước",
                f"Tháng này {money(r['gross'])} đ, tháng trước {money(old.gross)} đ.", 'Giám đốc xem', f'/luong?thang={thang}')
    texts = dict(salary='ai cũng có mức lương', missing='không ai thiếu giờ ra', absent='không ngày nghỉ không phép',
                 net='không ai thực nhận âm', advance='tạm ứng dưới nửa thu nhập', change=f'thu nhập không đổi quá {pct}% so tháng trước')
    if rows:
        ok[:0] = [texts[k] for k, hit in flags.items() if not hit]
    items.sort(key=lambda i: i['level'] != 'hot')
    return out


def payroll_text(check: dict, base: str) -> str:
    month = check['month']
    head = f"Soát bảng lương tháng {month.strftime('%m')} (nháp, Nhân sự AI)"
    tail = f"Mở: {base}/luong?thang={month.strftime('%Y-%m')}"
    if not check['items']:
        return '\n'.join([head, 'Không thấy điểm nào cần xem.', tail])
    lines = [f"{n}. {i['title']}" for n, i in enumerate(check['items'][:8], 1)]
    if len(check['items']) > 8:
        lines.append(f"… và {len(check['items']) - 8} điểm khác trong app")
    return '\n'.join([head, f"{len(check['items'])} điểm cần xem trước khi duyệt:", *lines, tail])


def payroll_once(session, now=None, sender=None) -> int:
    row = nva.active(session, CODE)
    now = now or nva._now()
    if row is None or not nva._zalo_ready():
        return 0
    c = nva.cfg(row)
    local = now.astimezone(TZ)
    if local.day != int(c['payroll_day']) or not _due(now, c['payroll_time']):
        return 0
    month = local.date().replace(day=1)
    mark = f'nsa-luong:{month.isoformat()}'
    bosses = [(uid, chat) for uid, chat in _directors(session) if not _sent(session, mark, uid)]
    if not bosses:
        return 0
    check = payroll_check(session, month, now)
    if check['approved']:
        for uid, _ in bosses:
            _mark(session, mark, uid)
        return 0
    body = payroll_text(check, nva._base())
    send = nva._sender(sender)
    count = 0
    for uid, chat in bosses:
        if nva._send(session, chat, uid, mark, body, send):
            _log(session, 'BAO', f"Soát bảng lương tháng {month.strftime('%m/%Y')}: {len(check['items'])} điểm cần xem", uid,
                 '; '.join(i['title'] for i in check['items']))
            count += 1
    return count


def run_once(session, now=None, sender=None) -> int:
    now = now or nva._now()
    if nva.active(session, CODE) is None:
        return 0
    total = 0
    for job in (remind_once, report_once, weekly_once, payroll_once):
        try:
            total += job(session, now, sender)
        except Exception:  # noqa: BLE001 - lỗi một việc không chặn việc khác
            session.rollback()
            nva.log.exception('nhan su ai %s', job.__name__)
    return total
