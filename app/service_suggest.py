"""Gợi ý thợ cho điều phối + nhắc sắp trễ hẹn (TUẤN duyệt 05/11/2026, bản mẫu GoiYTho).

Chỉ SELECT, không ghi gì: app tự chấm điểm trên máy chủ, không gọi API, không tự giao thợ.
Điều phối/giám đốc bấm Giao như bấm tay (service_tickets.assign), ghi chú bước giao cho biết theo gợi ý hay chọn khác.

Điểm xét lần lượt (nặng tới nhẹ):
- Rảnh: nghỉ phép hôm nay thì không gợi ý; trùng giờ hẹn, nhiều việc đang mở thì trừ điểm; chưa vào ca trừ nhẹ.
- Gần khách: vị trí lúc thợ bấm Đã đến khách gần nhất hôm nay so với vị trí lần trước có thợ đến khách/địa điểm này.
  Chưa có vị trí thì so tên quận/huyện trong địa chỉ với nơi thợ đang làm hôm nay.
- Có sẵn hàng: chỉ xét hàng đã ghi trên phiếu (yêu cầu linh kiện còn chờ, dòng vật tư chưa xuất) trong kho thợ.
- Quen máy/khách: đã sửa máy (serial) hoặc đến khách này trong 6 tháng; phiếu bảo hành ưu tiên người sửa lần trước.
"""
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select

from . import service_appointments, service_self_claim, service_tickets
from .config import get_settings
from .models import (AttendancePunch, AttendanceRequest, Customer, CustomerSite, PartRequest,
                     ServiceLine, ServiceStep, ServiceTicket, ServiceVisit, StockBalance, User, Warehouse)
from .service_sla import TZ
from .text import fold

NOTE_FOLLOW = 'Theo gợi ý'
NOTE_OTHER = 'Chọn khác gợi ý'
KNOW_DAYS = 180
CLASH = timedelta(minutes=90)
# Nhắc sắp trễ: hẹn theo giờ thì nhắc thợ trước 30 phút, báo điều phối trước 10 phút.
# Hẹn theo buổi (Sáng/Chiều) thì tính tới hết buổi: nhắc thợ còn 60 phút, báo điều phối còn 30 phút.
TECH_LEAD = {'GIO': timedelta(minutes=30), 'BUOI': timedelta(minutes=60)}
DISPATCH_LEAD = {'GIO': timedelta(minutes=10), 'BUOI': timedelta(minutes=30)}
LATE_WINDOW = timedelta(hours=2)  # quá hẹn hơn 2 giờ thì đã nằm ở ô Quá hạn, thôi nhắc ở đây
WAITING = ('DA_GIAO', 'DA_NHAN', 'DA_HEN')  # thợ chưa bấm Đã đến khách
DEPART = 'depart'
LATE = 'late'
LATE_CHOICES = {'15': 'Trễ 15 phút', '30': 'Trễ 30 phút', '60': 'Trễ hơn 1 giờ'}


def _now():
    return service_tickets._now()


# ---------- Vị trí ----------

def km(a, b) -> float | None:
    if not a or not b:
        return None
    lat1, lng1, lat2, lng2 = map(math.radians, (float(a[0]), float(a[1]), float(b[0]), float(b[1])))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6371 * 2 * math.asin(min(1.0, math.sqrt(h)))


_AREA = re.compile(r'\b(?:quan|q|huyen|h|tp|thanh pho|tx|thi xa)\s*\.?\s*(\d{1,2}|[a-z][a-z ]{1,24}?)\s*(?:,|;|\.|-|$|\btp\b|\bhcm\b|\bho chi minh\b)')
# Quận/huyện TP.HCM cũ viết không kèm chữ "quận" (dữ liệu khách hay ghi "Bình Tân, HCM").
_NAMED = ('binh tan', 'tan phu', 'tan binh', 'binh thanh', 'go vap', 'phu nhuan', 'thu duc', 'binh chanh', 'hoc mon',
          'cu chi', 'nha be', 'can gio', 'di an', 'thuan an', 'bien hoa', 'tan uyen', 'thu dau mot')


def area(address: str | None) -> str:
    """'58 Mã Lò, Q. Bình Tân, HCM' -> 'binh tan'; 'Phường 6, Quận 3' -> '3'. Không đoán được thì ''."""
    text = fold(address)
    if not text:
        return ''
    for m in _AREA.finditer(text):
        value = m.group(1).strip()
        if value and value not in ('hcm', 'ho chi minh', 'hn'):
            return value
    for name in _NAMED:
        if re.search(r'\b' + name + r'\b', text):
            return name
    return ''


def _place_key(t):
    return (t.customer_code, t.site_id)


def _site_points(session, tickets) -> dict:
    """Vị trí đã biết của khách/địa điểm: lần gần nhất thợ bấm Đã đến khách ở đó (bất kỳ phiếu nào)."""
    keys = {_place_key(t) for t in tickets}
    codes = {k[0] for k in keys}
    if not codes:
        return {}
    v, t = ServiceVisit, ServiceTicket
    out = {}
    for code, site, lat, lng in session.execute(select(t.customer_code, t.site_id, v.arrive_lat, v.arrive_lng)
            .join(t, t.id == v.ticket_id).where(t.customer_code.in_(codes), v.arrive_lat.is_not(None))
            .order_by(v.started_at.desc(), v.id.desc())):
        out.setdefault((code, site), (lat, lng))
    return out


def _addresses(session, tickets) -> dict:
    sites = {t.site_id for t in tickets if t.site_id}
    codes = {t.customer_code for t in tickets}
    site_addr = dict(session.execute(select(CustomerSite.id, CustomerSite.address).where(CustomerSite.id.in_(sites))).all()) if sites else {}
    cust_addr = dict(session.execute(select(Customer.code, Customer.address).where(Customer.code.in_(codes))).all()) if codes else {}
    return {t.id: (site_addr.get(t.site_id) or cust_addr.get(t.customer_code) or '') for t in tickets}


# ---------- Thợ hôm nay ----------

@dataclass
class Tech:
    user: User
    open_jobs: int = 0
    appointments: list = field(default_factory=list)   # (ticket_id, appointment_at, slot)
    on_leave: str = ''
    clocked_in: bool = False
    point: tuple | None = None                          # (lat, lng) lần bấm Đã đến khách gần nhất hôm nay
    point_label: str = ''
    areas: set = field(default_factory=set)             # quận/huyện các phiếu đang làm / đã đến hôm nay
    warehouse: str | None = None
    at_ticket: ServiceTicket | None = None              # phiếu đang làm (đã đến khách)


def _technicians(session):
    return list(session.scalars(select(User).where(User.active.is_(True), User.roles.any(service_tickets.TECHNICIAN))
                                .order_by(User.full_name, User.id)))


def _day_bounds(day: date):
    start = datetime.combine(day, time.min, TZ)
    return start, start + timedelta(days=1)


def _leave(session, ids, day) -> dict:
    out = {}
    for uid, part in session.execute(select(AttendanceRequest.user_id, AttendanceRequest.part).where(
            AttendanceRequest.user_id.in_(ids), AttendanceRequest.kind.in_(('NGHI_PHEP', 'NGHI_KHONG_LUONG')),
            AttendanceRequest.status == 'DA_DUYET', AttendanceRequest.from_date <= day, AttendanceRequest.to_date >= day)):
        out[uid] = {'CA_NGAY': 'Nghỉ cả ngày', 'SANG': 'Nghỉ buổi sáng', 'CHIEU': 'Nghỉ buổi chiều'}.get(part, 'Nghỉ')
    return out


def load_techs(session, now=None) -> list[Tech]:
    now = now or _now()
    day = now.astimezone(TZ).date()
    start, end = _day_bounds(day)
    users = _technicians(session)
    if not users:
        return []
    ids = [u.id for u in users]
    techs = {u.id: Tech(user=u) for u in users}
    t = ServiceTicket
    open_rows = list(session.scalars(select(t).where(t.assignee_id.in_(ids), t.status.in_(service_tickets.OPEN))))
    addr = _addresses(session, open_rows)
    for row in open_rows:
        tech = techs[row.assignee_id]
        tech.open_jobs += 1
        if row.appointment_at is not None and row.status in WAITING:
            tech.appointments.append((row.id, row.appointment_at, row.appointment_slot))
        if row.status == 'DANG_LAM':
            tech.at_ticket = row
            a = area(addr.get(row.id))
            if a:
                tech.areas.add(a)
    for uid, label in _leave(session, ids, day).items():
        techs[uid].on_leave = label
    for uid in session.scalars(select(AttendancePunch.user_id).where(AttendancePunch.user_id.in_(ids), AttendancePunch.kind == 'IN',
                                                                     AttendancePunch.at >= start, AttendancePunch.at < end)):
        techs[uid].clocked_in = True
    v = ServiceVisit
    visited = list(session.execute(select(v.technician_id, v.arrive_lat, v.arrive_lng, v.started_at, t).join(t, t.id == v.ticket_id)
                                   .where(v.technician_id.in_(ids), v.started_at >= start, v.started_at < end)
                                   .order_by(v.started_at.desc(), v.id.desc())))
    visit_addr = _addresses(session, [row[4] for row in visited])
    names = dict(session.execute(select(Customer.code, Customer.name).where(Customer.code.in_({row[4].customer_code for row in visited}))).all()) if visited else {}
    for uid, lat, lng, at, ticket in visited:
        tech = techs[uid]
        a = area(visit_addr.get(ticket.id))
        if a:
            tech.areas.add(a)
        if tech.point is None and lat is not None:
            tech.point = (lat, lng)
            tech.point_label = f"ở {names.get(ticket.customer_code, ticket.customer_code)} lúc {at.astimezone(TZ):%H:%M}"
    for uid, code in session.execute(select(Warehouse.holder_id, Warehouse.code).where(
            Warehouse.kind == 'THO', Warehouse.active.is_(True), Warehouse.holder_id.in_(ids))):
        techs[uid].warehouse = code
    return list(techs.values())


# ---------- Chấm điểm ----------

def _needs(session, ticket_ids) -> dict:
    """Hàng đã ghi trên phiếu mà thợ cần mang: yêu cầu linh kiện còn chờ cấp + dòng vật tư chưa xuất."""
    out = {}
    if not ticket_ids:
        return out
    for tid, code, qty in session.execute(select(PartRequest.ticket_id, PartRequest.product_code, PartRequest.qty).where(
            PartRequest.ticket_id.in_(ticket_ids), PartRequest.status == 'CHO_CAP')):
        out.setdefault(tid, {})
        out[tid][code] = out[tid].get(code, Decimal(0)) + qty
    for tid, code, qty in session.execute(select(ServiceLine.ticket_id, ServiceLine.product_code, ServiceLine.qty).where(
            ServiceLine.ticket_id.in_(ticket_ids), ServiceLine.kind == 'VAT_TU', ServiceLine.stock_doc_id.is_(None))):
        out.setdefault(tid, {})
        out[tid][code] = out[tid].get(code, Decimal(0)) + qty
    return out


def _stock(session, warehouses, codes) -> dict:
    if not warehouses or not codes:
        return {}
    return {(w, p): q for w, p, q in session.execute(select(StockBalance.warehouse_code, StockBalance.product_code, StockBalance.qty)
                                                      .where(StockBalance.warehouse_code.in_(warehouses), StockBalance.product_code.in_(codes)))}


def _history(session, tickets, now) -> dict:
    """Ai đã sửa máy / đến khách này trong 6 tháng: {ticket_id: {user_id: (máy, khách)}}."""
    serials = {t.serial for t in tickets if t.serial}
    codes = {t.customer_code for t in tickets}
    since = now - timedelta(days=KNOW_DAYS)
    t = ServiceTicket
    who = func.coalesce(t.repaired_by, t.assignee_id)
    by_serial, by_customer = {}, {}
    match = or_(t.serial.in_(serials), t.customer_code.in_(codes)) if serials else t.customer_code.in_(codes)
    rows = session.execute(select(t.id, t.serial, t.customer_code, who).where(
        t.status.in_(('HOAN_THANH', 'DA_DONG')), t.created_at >= since, who.is_not(None), match)) if codes else []
    for tid, serial, code, uid in rows:
        if serial:
            by_serial.setdefault(serial, {}).setdefault(uid, set()).add(tid)
        by_customer.setdefault(code, {}).setdefault(uid, set()).add(tid)
    prev = {}
    warranty_of = {t.warranty_of_id for t in tickets if t.warranty_of_id}
    if warranty_of:
        prev = dict(session.execute(select(ServiceTicket.id, func.coalesce(ServiceTicket.repaired_by, ServiceTicket.assignee_id))
                                    .where(ServiceTicket.id.in_(warranty_of))).all())
    out = {}
    for tk in tickets:
        row = {}
        for uid, ids in by_serial.get(tk.serial, {}).items():
            row.setdefault(uid, [0, 0])[0] = len(ids - {tk.id})
        for uid, ids in by_customer.get(tk.customer_code, {}).items():
            row.setdefault(uid, [0, 0])[1] = len(ids - {tk.id})
        out[tk.id] = (row, prev.get(tk.warranty_of_id))
    return out


def _clash(tech: Tech, ticket: ServiceTicket):
    if ticket.appointment_at is None:
        return None
    for tid, at, slot in tech.appointments:
        if tid == ticket.id:
            continue
        if slot in service_appointments.LABELS or ticket.appointment_slot in service_appointments.LABELS:
            same = (at.astimezone(TZ).date() == ticket.appointment_at.astimezone(TZ).date()
                    and (slot or 'GIO') == (ticket.appointment_slot or 'GIO') and slot in service_appointments.LABELS)
            if same:
                return f"đã có hẹn {service_appointments.LABELS[slot].lower()} cùng ngày"
            continue
        if abs(at - ticket.appointment_at) < CLASH:
            return f"trùng hẹn {at.astimezone(TZ):%H:%M}"
    return None


def _is_today(ticket, now):
    when = ticket.appointment_at or now
    return when.astimezone(TZ).date() == now.astimezone(TZ).date()


def rank_many(session, tickets, now=None, techs=None) -> dict:
    """{ticket_id: [dòng thợ đã xếp]}; mỗi dòng có id, name, score, why (chip ngắn), cột near/free/stock/know, blocked."""
    now = now or _now()
    tickets = [t for t in tickets if t is not None]
    if not tickets:
        return {}
    techs = load_techs(session, now) if techs is None else techs
    if not techs:
        return {t.id: [] for t in tickets}
    points = _site_points(session, tickets)
    addr = _addresses(session, tickets)
    needs = _needs(session, [t.id for t in tickets])
    stock = _stock(session, {x.warehouse for x in techs if x.warehouse}, {c for n in needs.values() for c in n})
    hist = _history(session, tickets, now)
    work_start, work_end = get_settings().work_start, get_settings().work_end
    in_hours = work_start <= now.astimezone(TZ).time() <= work_end
    out = {}
    for t in tickets:
        site = points.get(_place_key(t))
        place = area(addr.get(t.id))
        need = needs.get(t.id, {})
        known, previous = hist.get(t.id, ({}, None))
        rows = []
        for x in techs:
            score, why = 0.0, []
            today = _is_today(t, now)
            blocked = ''
            if x.on_leave and today:
                blocked = x.on_leave
            # Rảnh
            clash = _clash(x, t)
            free = f"{x.open_jobs} việc đang mở"
            score -= 12 * x.open_jobs
            if x.open_jobs == 0:
                why.append('0 việc đang mở')
            elif x.open_jobs == 1:
                why.append('1 việc đang mở')
            if clash:
                score -= 45
                free += ' · ' + clash
            if today and in_hours and not x.clocked_in and not blocked:
                score -= 10
                free += ' · chưa vào ca'
            # Gần khách
            dist = km(x.point, site)
            if dist is not None:
                near = f"{dist:.1f} km".replace('.', ',')
                score += max(0.0, 30 - 3 * dist)
                if dist <= 5:
                    why.insert(0, near)
            elif place and place in x.areas:
                near = f"cùng {place.title() if not place.isdigit() else 'Quận ' + place}"
                score += 15
                why.insert(0, near)
            else:
                near = ''  # chưa biết vị trí: không cộng, không trừ
            # Có sẵn hàng
            if need:
                have = [c for c, q in need.items() if x.warehouse and stock.get((x.warehouse, c), Decimal(0)) >= q]
                if len(have) == len(need):
                    stock_label = 'Có đủ hàng trên phiếu'
                    score += 20
                    why.append('Kho có ' + ', '.join(sorted(have))[:40])
                elif have:
                    stock_label = f"Có {len(have)}/{len(need)} mã"
                    score += 5
                else:
                    stock_label = 'Thiếu hàng trên phiếu'
                    score -= 8
            else:
                stock_label = 'Không cần hàng'
            # Quen máy / khách
            serial_n, cust_n = known.get(x.user.id, (0, 0))
            if previous == x.user.id:
                know = 'Người sửa lần trước'
                score += 40
                why.append(know)
            elif serial_n:
                know = f"Đã sửa máy này {serial_n} lần"
                score += 10 + 5 * min(serial_n, 3)
                why.append(know)
            elif cust_n:
                know = f"Đã đến khách này {cust_n} lần"
                score += 4 + 2 * min(cust_n, 3)
                why.append(know)
            else:
                know = 'Chưa'
            if 'dispatcher' in x.user.roles or 'director' in x.user.roles:
                score -= 8  # thợ kiêm điều phối/giám đốc: ưu tiên để thợ khác đi trước khi ngang điểm
                free += ' · kiêm điều phối'
            if blocked:
                score -= 1000
            rows.append(dict(id=x.user.id, name=x.user.full_name, score=round(score, 1), why=why[:3], near=near, free=free,
                             stock=stock_label, know=know, blocked=blocked, where=x.point_label))
        rows.sort(key=lambda r: (-r['score'], r['name'], r['id']))
        if rows and not rows[0]['blocked']:
            rows[0]['best'] = True
        out[t.id] = rows
    return out


def best(rows) -> dict | None:
    return rows[0] if rows and rows[0].get('best') else None


def rank(session, ticket, now=None) -> list:
    return rank_many(session, [ticket], now).get(ticket.id, [])


def needs_dispatch(t: ServiceTicket, now) -> bool:
    """Phiếu điều phối cần giao: mới, chưa thợ, không đang chờ thợ tự nhận."""
    return (t.status == 'MOI' and t.assignee_id is None and not t.counter_intake
            and not (t.self_claim_until is not None and t.self_claim_until > now))


def assign_note(session, suggested_id: int, chosen_id: int, reason: str = '') -> str:
    """Ghi chú bước giao cho thống kê: 'Theo gợi ý' hoặc 'Chọn khác gợi ý (X) · lý do'.
    suggested_id là thợ app đã gợi ý trên màn lúc điều phối bấm (gửi kèm form), không tính lại."""
    if suggested_id == chosen_id:
        return NOTE_FOLLOW
    name = session.scalar(select(User.full_name).where(User.id == suggested_id))
    if name is None:
        return ''
    note = f"{NOTE_OTHER} ({name})"
    reason = ' '.join(str(reason or '').split())[:200]
    return f"{note} · {reason}" if reason else note


def month_stats(session, start, end) -> dict:
    s = ServiceStep
    rows = session.execute(select(s.note).where(s.action.in_(('assign', 'reassign')), s.at >= start, s.at < end,
                                                (s.note.startswith(NOTE_FOLLOW) | s.note.startswith(NOTE_OTHER)))).scalars().all()
    follow = sum(1 for n in rows if n.startswith(NOTE_FOLLOW))
    other = len(rows) - follow
    reasons = {}
    for n in rows:
        if n.startswith(NOTE_OTHER):
            r = n.split(' · ', 1)[1].split('. ', 1)[0].strip() if ' · ' in n else 'Không ghi'
            reasons[r] = reasons.get(r, 0) + 1
    late_steps = session.scalar(select(func.count()).select_from(s).where(s.action == LATE, s.at >= start, s.at < end)) or 0
    departs = session.scalar(select(func.count()).select_from(s).where(s.action == DEPART, s.at >= start, s.at < end)) or 0
    total = follow + other
    return dict(total=total, follow=follow, other=other, pct=round(follow * 100 / total) if total else 0,
                reasons=sorted(reasons.items(), key=lambda kv: -kv[1])[:8], late=late_steps, departs=departs)


# ---------- Sắp trễ hẹn ----------

def deadline(t: ServiceTicket):
    if t.appointment_at is None:
        return None
    return service_appointments.end(t.appointment_at, t.appointment_slot)


def _kind(t):
    return 'BUOI' if t.appointment_slot in service_appointments.LABELS else 'GIO'


def _steps_since(session, ids, actions, since):
    out = {}
    if not ids:
        return out
    for tid, action, at, note, sid in session.execute(select(ServiceStep.ticket_id, ServiceStep.action, ServiceStep.at, ServiceStep.note, ServiceStep.id)
            .where(ServiceStep.ticket_id.in_(ids), ServiceStep.action.in_(actions), ServiceStep.at >= since).order_by(ServiceStep.id)):
        out.setdefault(tid, []).append(dict(action=action, at=at, note=note, id=sid))
    return out


def at_risk(session, now=None, level='tech', assignee_id=None) -> list[dict]:
    """Phiếu đã hẹn, thợ chưa đến khách, tới mốc nhắc. level='tech' (mốc nhắc thợ) hoặc 'dispatch' (mốc báo điều phối).
    Thợ đã bấm Đi sau lần đổi hẹn cuối thì thôi nhắc; thợ đã Báo trễ thì vẫn hiện cho điều phối kèm số phút."""
    now = now or _now()
    t = ServiceTicket
    q = select(t).where(t.status.in_(WAITING), t.assignee_id.is_not(None), t.appointment_at.is_not(None),
                        t.appointment_at < now + timedelta(hours=6), t.appointment_at > now - timedelta(hours=12))
    if assignee_id is not None:
        q = q.where(t.assignee_id == assignee_id)
    tickets = list(session.scalars(q.order_by(t.appointment_at, t.id)))
    leads = TECH_LEAD if level == 'tech' else DISPATCH_LEAD
    out = []
    picked = []
    for row in tickets:
        end = deadline(row)
        if end is None or now < end - leads[_kind(row)] or now > end + LATE_WINDOW:
            continue
        picked.append((row, end))
    steps = _steps_since(session, [r.id for r, _ in picked], (DEPART, LATE, 'reschedule', 'assign', 'reassign'), now - timedelta(hours=12))
    for row, end in picked:
        last_change = None
        departed = late = None
        for s in steps.get(row.id, []):
            if s['action'] in ('reschedule', 'assign', 'reassign'):
                last_change, departed, late = s['at'], None, None
            elif s['action'] == DEPART:
                departed = s
            elif s['action'] == LATE:
                late = s
        if departed:
            continue
        minutes = int((end - now).total_seconds() // 60)
        out.append(dict(ticket=row, deadline=end, minutes=minutes, late=late, mark=f"{row.id}:{end.isoformat(timespec='minutes')}"))
    return out


def risk_label(item) -> str:
    m = item['minutes']
    if m < 0:
        return f"Trễ {-m} phút"
    if item['ticket'].appointment_slot in service_appointments.LABELS:
        return f"Còn {m} phút hết buổi"
    return f"Còn {m} phút"


def depart(session, user, ticket_id: int):
    """Thợ bấm Đi ngay: chỉ ghi một bước nhật ký, không đổi trạng thái phiếu."""
    with service_tickets._transaction(session):
        t = service_tickets._get(session, user, ticket_id, lock=True)
        _own(user, t)
        end = deadline(t)
        label = f"Đi tới khách · hẹn {end.astimezone(TZ):%H:%M %d/%m}" if end else 'Đi tới khách'
        service_tickets._record(session, user, t, DEPART, service_tickets.snapshot(t), t.status, label)
    return t


def report_late(session, user, ticket_id: int, choice: str):
    from .errors import BusinessError
    if choice not in LATE_CHOICES:
        raise BusinessError('VALIDATION', 'Chọn trễ 15 phút, 30 phút hoặc hơn 1 giờ.')
    with service_tickets._transaction(session):
        t = service_tickets._get(session, user, ticket_id, lock=True)
        _own(user, t)
        service_tickets._record(session, user, t, LATE, service_tickets.snapshot(t), t.status, LATE_CHOICES[choice])
    return t


def _own(user, t):
    from .errors import BusinessError
    if t.assignee_id != user.id:
        raise BusinessError('FORBIDDEN', 'Chỉ thợ đang giữ phiếu được báo đi hoặc báo trễ.', status=403)
    if t.status not in WAITING or t.appointment_at is None:
        raise BusinessError('BAD_STATUS', 'Phiếu chưa có hẹn hoặc bạn đã đến khách.', status=409)
