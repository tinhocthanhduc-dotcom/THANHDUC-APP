"""Trưởng nhóm AI (Đội AI đợt 1, TUẤN duyệt 15/11/2026, mẫu 9rRchbgXNR4sY5jurdjQMq).

Chạy bằng quy tắc trong app (không gọi API), mỗi 5 phút:
1. Gom việc: đọc các nhóm "Việc của tôi" của mọi người.
2. Chia việc: mỗi việc một người phụ trách (giữ người cũ nếu vẫn thấy việc đó; không thì người ít việc nhất,
   ưu tiên người không phải giám đốc) và một hạn (giờ hẹn / hạn phiếu nếu có; việc gấp 2 giờ; việc thường hết
   1 ngày làm việc; nhóm mua hàng, báo giá chờ khách không có hạn).
3. Theo dõi: quá hạn thì nhắc người đó (gộp một tin), sau 30 phút vẫn treo thì báo điều phối (việc dịch vụ,
   giao hàng, kho), quá hạn 2 giờ (việc gấp) hoặc 1 ngày làm việc (việc thường) thì báo giám đốc (Q2).
4. Đổi người (Q1): phiếu dịch vụ đã giao mà thợ chưa nhận, thợ đang quá tải, có thợ khác đã vào ca ít việc hơn
   hẳn và phiếu chưa cấp hàng thì chuyển sang thợ đó; điều phối hoàn tác được trong undo_minutes phút.
   Việc có tiền, đơn bán (có hàng đi theo) không bao giờ tự đổi.
5. Tin Zalo 07:45 và 13:30 (Q4): mỗi người 3 việc làm tiếp + việc còn treo, gộp thay Thư ký phiếu.

Ranh giới cứng như nhân viên AI: không đụng tiền, kho, công nợ, giá; không hủy, không đóng; không nhắn khách.
team_tasks chỉ là bảng theo dõi, không đổi quyền hay trạng thái chứng từ.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from . import nhan_vien_ao as nva
from . import service_suggest, service_tickets
from .errors import BusinessError
from .models import DispatchZaloSent, ServiceStep, ServiceTicket, TeamTask, User, VirtualAgentLog
from .service_sla import TZ

log = logging.getLogger(__name__)

CODE = 'TRUONG_NHOM'
EVERY = timedelta(minutes=5)
URGENT_HOURS = 2           # Q2: việc gấp quá hạn 2 giờ thì báo giám đốc; việc gấp không có hạn riêng thì hạn 2 giờ
DISPATCH_AFTER = timedelta(minutes=30)
MAX_NUDGES = 4             # tối đa tin nhắc quá hạn mỗi người mỗi ngày
# Nhóm chỉ để biết, không tính là việc phải làm.
SKIP = ('machines_held', 'service_working', 'service_workshop')
# Nhóm không có hạn (không nhắc, không báo lên): mua hàng chưa gấp, báo giá chờ khách trả lời.
NO_DUE = ('stock_low', 'purchases_waiting', 'pending_supplier_returns', 'quotes_open')
# Nhắc hẹn AI đã nhắc thợ và báo giám đốc các nhóm này: trưởng nhóm chỉ theo dõi, không nhắn thêm.
NUDGED_ELSEWHERE = ('service_assigned', 'service_accepted', 'service_today', 'service_appointment_overdue')
# Treo lâu thì báo điều phối (việc liên quan thợ, giao hàng, kho).
DISPATCH_TRACK = ('service_', 'delivery_', 'technician_deliveries', 'orders_waiting', 'orders_pickup_late', 'parts_pending', 'machines_')
# Việc gấp (cùng danh sách khung "Làm ngay" của Việc của tôi).
URGENT = ('service_appointment_overdue', 'service_goods_short', 'service_today', 'service_unassigned', 'service_assigned', 'service_return_ready',
          'orders_pickup_late', 'orders_cancel_requested', 'technician_deliveries', 'delivery_handover', 'delivery_unclaimed', 'orders_waiting',
          'parts_pending', 'machines_pending', 'machines_warehouse')
AI_KEYS = ('zalo_ticket_drafts', 'customer_messages_waiting')   # AI đã soạn sẵn, chờ người bấm
ROLE_LABELS = {'director': 'Giám đốc', 'dispatcher': 'Điều phối', 'technician': 'Kỹ thuật', 'warehouse': 'Kho', 'sales': 'Bán hàng',
               'finance': 'Kế toán', 'reception': 'Lễ tân', 'admin': 'Quản trị'}
_last = {'sync': None}


def _now():
    return nva._now()


def cfg(session) -> dict:
    return nva.cfg(nva.rows(session)[CODE])


def active(session):
    return nva.active(session, CODE)


# ---------- gom việc + chia việc ----------

def _people(session) -> list[User]:
    return [u for u in session.scalars(select(User).where(User.active.is_(True), User.username != nva.SYSTEM_USERNAME).order_by(User.id))
            if set(u.roles or []) - {'admin'}]


def _is_director(user) -> bool:
    return 'director' in (user.roles or [])


def _add_workdays(session, at, days=1):
    from .morning_report import is_workday
    local = at.astimezone(TZ)
    day, left = local.date(), days
    while left:
        day += timedelta(days=1)
        if is_workday(session, day):
            left -= 1
    return datetime.combine(day, local.time(), TZ)


def _due(session, key, row, first_seen):
    if key in NO_DUE:
        return None
    for field in ('appointment_at', 'due_at'):
        value = row.get(field)
        if value is not None and key != 'service_appointment_overdue':
            return value
    if key in URGENT:
        return first_seen + timedelta(hours=URGENT_HOURS)
    return _add_workdays(session, first_seen)


def _title(group_title, row):
    who = str(row.get('customer') or '').strip()
    return f'{group_title} · {who}' if who else group_title


def gather(session) -> dict:
    """{(key, ref): dict(row, group_title, viewers=[user...])} từ Việc của tôi của mọi người."""
    from . import my_work
    items = {}
    for user in _people(session):
        try:
            groups = my_work.collect(session, user)
        except Exception:  # noqa: BLE001 - một người lỗi không chặn cả đội
            session.rollback()
            log.exception('truong nhom gom viec %s', user.id)
            continue
        for g in groups:
            if g['key'] in SKIP:
                continue
            for row in g['rows']:
                ref = str(row.get('id'))
                item = items.setdefault((g['key'], ref), dict(key=g['key'], ref=ref, row=row, group_title=g['title'], viewers=[]))
                item['viewers'].append(user)
    return items


def sync(session, now=None) -> dict:
    """Cập nhật team_tasks theo việc đang có. Trả số việc mới, đã xong, đang mở."""
    now = now or _now()
    items = gather(session)
    open_tasks = {(t.item_key, t.ref): t for t in session.scalars(select(TeamTask).where(TeamTask.done_at.is_(None)))}
    load = {}
    for (key, ref), t in open_tasks.items():
        if (key, ref) in items and key not in NO_DUE:
            load[t.owner_id] = load.get(t.owner_id, 0) + 1
    new = done = 0
    for (key, ref), t in open_tasks.items():
        if (key, ref) not in items:
            t.done_at = now
            done += 1
    for (key, ref), item in items.items():
        row, viewers = item['row'], item['viewers']
        t = open_tasks.get((key, ref))
        ids = {u.id for u in viewers}
        if t is not None and t.owner_id in ids:
            owner_id = t.owner_id
        else:
            staff = [u for u in viewers if not _is_director(u)] or viewers
            owner_id = min(staff, key=lambda u: (load.get(u.id, 0), u.id)).id
            if key not in NO_DUE:
                load[owner_id] = load.get(owner_id, 0) + 1
                if t is not None:
                    load[t.owner_id] = max(0, load.get(t.owner_id, 0) - 1)
        title = _title(item['group_title'], row)[:400]
        if t is None:
            t = TeamTask(item_key=key, ref=ref, owner_id=owner_id, first_seen_at=now, last_seen_at=now)
            session.add(t)
            new += 1
        t.owner_id = owner_id
        t.number = str(row.get('number') or '')[:80]
        t.title, t.link = title, str(row.get('link') or '')
        t.urgent = key in URGENT or bool(row.get('overdue') and key not in NO_DUE)
        t.due_at = _due(session, key, row, t.first_seen_at or now)
        t.last_seen_at = now
    session.commit()
    return dict(new=new, done=done, open=len(items))


# ---------- 3 việc làm tiếp ----------

def _rank(t: TeamTask, now):
    late = t.due_at is not None and t.due_at < now
    return (not late, not t.urgent, t.due_at or now + timedelta(days=30), t.first_seen_at, t.id)


def open_tasks(session, owner_id=None) -> list[TeamTask]:
    q = select(TeamTask).where(TeamTask.done_at.is_(None))
    if owner_id is not None:
        q = q.where(TeamTask.owner_id == owner_id)
    return list(session.scalars(q))


def next_for(session, user, limit=3, now=None) -> list[dict]:
    """3 việc làm tiếp của một người, cho Việc của tôi. Rỗng khi Trưởng nhóm AI tắt."""
    if active(session) is None:
        return []
    now = now or _now()
    tasks = sorted((t for t in open_tasks(session, user.id) if t.item_key not in NO_DUE), key=lambda t: _rank(t, now))[:limit]
    return [dict(id=t.id, number=t.number, title=t.title, link=t.link, when=when(t, now), late=t.due_at is not None and t.due_at < now)
            for t in tasks]


def when(t: TeamTask, now) -> str:
    if t.due_at is None:
        return 'Không có hạn'
    if t.due_at < now:
        mins = int((now - t.due_at).total_seconds() // 60)
        return f'Trễ {mins} phút' if mins < 60 else f'Trễ {mins // 60} giờ' if mins < 24 * 60 else f'Trễ {mins // 1440} ngày'
    local, today = t.due_at.astimezone(TZ), now.astimezone(TZ).date()
    return 'Hạn ' + local.strftime('%H:%M' if local.date() == today else '%d/%m %H:%M')


# ---------- nhắc, báo lên ----------

def _past(t, now, hours=None):
    return t.due_at is not None and now >= t.due_at + (timedelta(hours=hours) if hours else timedelta(0))


def _director_due(session, t, now):
    if t.due_at is None:
        return False
    if t.urgent:
        return _past(t, now, URGENT_HOURS)
    return now >= _add_workdays(session, t.due_at)


def _lines(tasks, now, limit=6):
    out = [f'• {t.number} {t.title} ({when(t, now).lower()})' for t in tasks[:limit]]
    if len(tasks) > limit:
        out.append(f'• … và {len(tasks) - limit} việc nữa')
    return out


def _nudges_today(session, uid, now):
    return session.scalar(select(func.count()).select_from(VirtualAgentLog).where(
        VirtualAgentLog.agent == CODE, VirtualAgentLog.action == 'NHAC', VirtualAgentLog.target_id == uid,
        VirtualAgentLog.at >= nva._day_start(now))) or 0


def escalate(session, now=None, sender=None) -> int:
    """Nhắc người phụ trách, báo điều phối, báo giám đốc. Chỉ trong giờ làm việc."""
    now = now or _now()
    if not nva._working(session, now):
        return 0
    users = {u.id: u for u in _people(session)}
    tasks = [t for t in open_tasks(session) if t.item_key not in NO_DUE and t.item_key not in NUDGED_ELSEWHERE and _past(t, now)]
    if not tasks:
        return 0
    zalo = nva._zalo_ready()
    people = nva.chats(session) if zalo else {}
    send = nva._sender(sender)
    base = nva._base()
    stamp = now.astimezone(TZ).strftime('%Y%m%d%H%M')
    count = 0
    # 1) Nhắc người phụ trách: gộp các việc vừa quá hạn thành một tin.
    by_owner = {}
    for t in tasks:
        if t.reminded_at is None:
            by_owner.setdefault(t.owner_id, []).append(t)
    for uid, mine in by_owner.items():
        user = users.get(uid)
        for t in mine:
            t.reminded_at = now
        session.commit()
        if user is None or _is_director(user) or uid not in people or _nudges_today(session, uid, now) >= MAX_NUDGES:
            continue
        mine.sort(key=lambda t: _rank(t, now))
        body = '\n'.join([f'Trưởng nhóm AI nhắc: {len(mine)} việc của bạn đã quá hạn', *_lines(mine, now), f'Mở app: {base}/viec-cua-toi'])
        if nva._send(session, people[uid], uid, f'tn-nhac:{stamp}', body, send):
            session.add(VirtualAgentLog(agent=CODE, action='NHAC', target_id=uid, text=f'Nhắc {user.full_name} {len(mine)} việc quá hạn',
                                        reason=', '.join(t.number for t in mine)[:500]))
            session.commit()
            count += 1
    # 2) Nhắc rồi 30 phút vẫn treo: báo điều phối (việc thợ, giao hàng, kho).
    from .dispatch_alerts import recipients
    late = [t for t in tasks if t.reminded_at is not None and t.dispatch_at is None and now - t.reminded_at >= DISPATCH_AFTER
            and t.item_key.startswith(DISPATCH_TRACK)]
    if late:
        for t in late:
            t.dispatch_at = now
        session.commit()
        names = {uid: u.full_name for uid, u in users.items()}
        for user, chat in (recipients(session) if zalo else []):
            mine = [t for t in late if t.owner_id != user.id]
            if not mine:
                continue
            lines = [f'• {t.number} {t.title} · {names.get(t.owner_id, "")} ({when(t, now).lower()})' for t in mine[:6]]
            body = '\n'.join([f'Trưởng nhóm AI báo điều phối: {len(mine)} việc đã nhắc mà vẫn treo', *lines, f'Xem: {base}/viec-cua-toi'])
            if nva._send(session, chat, user.id, f'tn-dp:{stamp}', body, send):
                session.add(VirtualAgentLog(agent=CODE, action='BAO', target_id=user.id, text=f'Báo điều phối {len(mine)} việc vẫn treo',
                                            reason=', '.join(t.number for t in mine)[:500]))
                session.commit()
                count += 1
    # 3) Quá lâu: báo giám đốc (Q2: việc gấp 2 giờ, việc thường 1 ngày làm việc).
    boss = [t for t in tasks if t.director_at is None and t.reminded_at is not None and _director_due(session, t, now)
            and not (t.owner_id in users and _is_director(users[t.owner_id]))]
    if boss:
        names = {uid: u.full_name for uid, u in users.items()}
        for t in boss:
            t.director_at = now
            session.add(VirtualAgentLog(agent=CODE, action='BAO', ticket_id=_ticket_id(t), target_id=t.owner_id,
                                        text=f'Báo giám đốc: {t.number} {t.title} · {names.get(t.owner_id, "")} {when(t, now).lower()}'))
        session.commit()
        lines = [f'• {t.number} {t.title} · {names.get(t.owner_id, "")} ({when(t, now).lower()})' for t in boss[:6]]
        body = '\n'.join([f'Trưởng nhóm AI báo giám đốc: {len(boss)} việc treo lâu, đã nhắc người làm và điều phối', *lines, f'Xem: {base}/doi-ai'])
        for uid, chat in people.items():
            user = users.get(uid)
            if user is not None and _is_director(user) and nva._send(session, chat, uid, f'tn-gd:{stamp}', body, send):
                count += 1
    return count


def _ticket_id(t: TeamTask):
    if t.item_key.startswith('service_') and t.ref.isdecimal():
        return int(t.ref)
    return None


# ---------- đổi người khi quá tải (Q1) ----------

def _load(session):
    out = {}
    for t in open_tasks(session):
        if t.item_key not in NO_DUE:
            out[t.owner_id] = out.get(t.owner_id, 0) + 1
    return out


def _has_stock(session, t: ServiceTicket) -> bool:
    from .technician_reassign import ticket_need
    from .technician_stock import own_warehouse
    old = own_warehouse(session, session.get(User, t.assignee_id))
    return bool(old is not None and ticket_need(session, t, old.code))


def rebalance(session, now=None, sender=None) -> int:
    """Chuyển tối đa một phiếu mỗi lượt từ thợ quá tải sang thợ đã vào ca ít việc hơn hẳn."""
    row = active(session)
    now = now or _now()
    if row is None or not nva._working(session, now):
        return 0
    c = nva.cfg(row)
    if not c.get('reassign'):
        return 0
    over, gap = int(c['overload']), int(c['gap'])
    load = _load(session)
    busy = {uid for uid, n in load.items() if n >= over}
    if not busy:
        return 0
    accept = timedelta(minutes=int(nva.cfg(nva.rows(session)['NHAC_HEN'])['accept_minutes']))
    t, s = ServiceTicket, ServiceStep
    last = select(s.ticket_id, func.max(s.id).label('sid')).where(s.action.in_(('assign', 'reassign'))).group_by(s.ticket_id).subquery()
    moved = select(VirtualAgentLog.id).where(VirtualAgentLog.ticket_id == t.id, VirtualAgentLog.agent == CODE,
                                             VirtualAgentLog.action == 'DOI_NGUOI').exists()
    rows_ = list(session.execute(select(t, s).join(last, last.c.ticket_id == t.id).join(s, s.id == last.c.sid).where(
        t.status == 'DA_GIAO', t.assignee_id.in_(busy), s.assignee_id == t.assignee_id, s.at <= now - accept,
        t.priority != 'KHAN', t.warranty_of_id.is_(None), t.counter_intake.is_(False), ~moved).order_by(t.id).limit(10)))
    if not rows_:
        return 0
    techs = service_suggest.load_techs(session, now)
    by_id = {x.user.id: x for x in techs}
    actor = nva.system_user(session)
    names = dict(session.execute(select(User.id, User.full_name)).all())
    for ticket, step in rows_:
        if _has_stock(session, ticket):
            continue
        old_id, old_load = ticket.assignee_id, load.get(ticket.assignee_id, 0)
        ranks = service_suggest.rank_many(session, [ticket], now, techs).get(ticket.id, [])
        pick = next((r for r in ranks if r['id'] != old_id and not r['blocked'] and r['id'] in by_id
                     and not by_id[r['id']].on_leave and by_id[r['id']].clocked_in and load.get(r['id'], 0) <= old_load - gap), None)
        if pick is None:
            continue
        why = f"{names.get(old_id, '')} đang {old_load} việc, {pick['name']} {load.get(pick['id'], 0)} việc" + (
            f", {', '.join(pick['why'])}" if pick['why'] else '')
        try:
            with service_tickets._transaction(session):
                service_tickets.assign_in_tx(session, actor, ticket.id, pick['id'], ticket.version, f'Trưởng nhóm AI · {why}')
                entry = VirtualAgentLog(agent=CODE, action='DOI_NGUOI', ticket_id=ticket.id, target_id=pick['id'],
                                        text=f"Chuyển {ticket.number} từ {names.get(old_id, '')} sang {pick['name']}", reason=why)
                session.add(entry)
        except BusinessError as exc:
            session.rollback()
            log.info('truong nhom bo qua %s: %s', ticket.id, exc)
            continue
        _tell(session, entry, ticket, old_id, pick, why, int(c['undo_minutes']), sender)
        return 1
    return 0


def _tell(session, entry, ticket, old_id, pick, why, undo, sender):
    from .dispatch_alerts import recipients
    from .config import get_settings
    if not nva._zalo_ready() or not get_settings().dispatch_zalo:
        return
    send = nva._sender(sender)
    base = nva._base()
    tail = f'Muốn trả về thợ cũ, mở phiếu bấm Giao thợ khác (giám đốc có nút Hoàn tác {undo} phút ở màn Đội AI): {base}/dich-vu/{ticket.id}'
    body = f'Trưởng nhóm AI vừa chuyển {ticket.number} sang {pick["name"]}. Lý do: {why}.\n{tail}'
    for user, chat in recipients(session):
        if user.id not in (pick['id'],):
            nva._send(session, chat, user.id, f'tn-doi:{entry.id}', body, send)
    chat = nva.chats(session).get(old_id)
    if chat is not None:
        nva._send(session, chat, old_id, f'tn-doi-cu:{entry.id}', f'Phiếu {ticket.number} đã chuyển sang {pick["name"]} cho đỡ việc của bạn. Không cần đi phiếu này nữa.', send)


def can_undo(session, entry: VirtualAgentLog, now=None) -> bool:
    if entry.action != 'DOI_NGUOI' or entry.undone_at is not None or entry.ticket_id is None:
        return False
    now = now or _now()
    undo = int(cfg(session)['undo_minutes'])
    if now - entry.at > timedelta(minutes=undo):
        return False
    t = session.get(ServiceTicket, entry.ticket_id)
    return t is not None and t.status == 'DA_GIAO' and t.assignee_id == entry.target_id


def undo(session, user, log_id: int) -> ServiceTicket:
    """Điều phối/giám đốc trả phiếu Trưởng nhóm AI vừa chuyển về thợ cũ (khi thợ mới chưa bấm Nhận)."""
    from .security import has_role
    if not has_role(user, *service_tickets.DISPATCHERS):
        raise BusinessError('FORBIDDEN', 'Chỉ điều phối hoặc giám đốc được hoàn tác.', status=403)
    entry = session.get(VirtualAgentLog, log_id)
    if entry is None or entry.agent != CODE or entry.action != 'DOI_NGUOI':
        raise BusinessError('NOT_FOUND', 'Không tìm thấy lần đổi người của Trưởng nhóm AI.', status=404)
    if not can_undo(session, entry):
        raise BusinessError('UNDO_CLOSED', 'Hết giờ hoàn tác hoặc thợ mới đã nhận việc. Dùng Giao thợ khác trên phiếu.', status=409)
    steps = list(session.scalars(select(ServiceStep).where(ServiceStep.ticket_id == entry.ticket_id, ServiceStep.action.in_(('assign', 'reassign')))
                                 .order_by(ServiceStep.id.desc()).limit(2)))
    if len(steps) < 2 or steps[0].assignee_id != entry.target_id or steps[1].assignee_id is None:
        raise BusinessError('UNDO_CLOSED', 'Không tìm thấy thợ cũ của phiếu. Dùng Giao thợ khác trên phiếu.', status=409)
    old_id = steps[1].assignee_id
    with service_tickets._transaction(session):
        t = service_tickets._get(session, user, entry.ticket_id, lock=True)
        service_tickets.assign_in_tx(session, user, t.id, old_id, t.version, 'Hoàn tác Trưởng nhóm AI')
        entry.undone_by, entry.undone_at = user.id, _now()
        old = session.scalar(select(User.full_name).where(User.id == old_id)) or ''
        session.add(VirtualAgentLog(agent=CODE, action='HOAN_TAC', ticket_id=t.id, target_id=user.id,
                                    text=f'{user.full_name} trả {t.number} về {old}'))
    return t


# ---------- tin Zalo 07:45 / 13:30 (Q4, gộp Thư ký phiếu) ----------

def brief_text(user, nexts, groups, at, base) -> str:
    lines = [f'Chào {user.full_name}, việc nên làm tiếp lúc {at}:' if nexts else f'Việc còn treo lúc {at}:']
    for i, n in enumerate(nexts, 1):
        lines.append(f"{i}. {n['number']} {n['title']} ({n['when'].lower()})")
    if groups:
        if nexts:
            lines += ['', 'Còn treo:']
        for g in groups:
            numbers = ', '.join(str(r.get('number')) for r in g['rows'][:3] if r.get('number'))
            more = f' +{g["count"] - 3}' if g['count'] > 3 and numbers else ''
            line = f"• {g['title']}: {g['count']}" + (f' ({numbers}{more})' if numbers else '')
            if len('\n'.join(lines + [line])) > nva.MAX_TEXT - 80:
                lines.append('• … xem thêm trong app')
                break
            lines.append(line)
    lines.append(f'Mở app: {base}/viec-cua-toi')
    return '\n'.join(lines)


def brief_once(session, now=None, sender=None) -> int:
    from . import my_work
    row = active(session)
    now = now or _now()
    if row is None or not nva._zalo_ready():
        return 0
    at = nva.clerk_slot(session, now, nva.cfg(row)['times'])
    if at is None:
        return 0
    if _last['sync'] is None or now - _last['sync'] > EVERY:
        sync(session, now)
        _last['sync'] = now
    day = now.astimezone(TZ).date()
    mark = f"tn-sang:{day.isoformat()}:{at.replace(':', '')}"
    send = nva._sender(sender)
    base = nva._base()
    count = 0
    for uid, chat in nva.chats(session).items():
        if session.get(DispatchZaloSent, (mark, uid)) is not None:
            continue
        user = session.get(User, uid)
        if user is None or _is_director(user):
            continue
        nexts = next_for(session, user, 3, now)
        groups = [g for g in my_work.collect(session, user, keys=nva.CLERK_KEYS) if g['count']]
        if not nexts and not groups:
            session.add(DispatchZaloSent(mark=mark, user_id=uid))
            session.commit()
            continue
        if nva._send(session, chat, uid, mark, brief_text(user, nexts, groups, at, base), send):
            session.add(VirtualAgentLog(agent=CODE, action='BAO', target_id=uid,
                                        text=f'Gửi {user.full_name} {len(nexts)} việc làm tiếp lúc {at}',
                                        reason=', '.join(n['number'] for n in nexts)[:500]))
            session.commit()
            count += 1
    return count


# ---------- vòng nền ----------

def run_once(session, now=None, sender=None) -> int:
    """Gọi mỗi phút từ nhan_vien_ao.run_once; tự giãn ra mỗi 5 phút."""
    if active(session) is None:
        return 0
    now = now or _now()
    total = brief_once(session, now, sender)
    if _last['sync'] is not None and now - _last['sync'] < EVERY:
        return total
    sync(session, now)
    _last['sync'] = now
    total += rebalance(session, now, sender)
    total += escalate(session, now, sender)
    return total


# ---------- màn Đội AI ----------

def board(session, now=None) -> dict:
    now = now or _now()
    start = nva._day_start(now)
    tasks = open_tasks(session)
    people = _people(session)
    c = cfg(session)
    over = int(c['overload'])
    lanes = []
    for u in people:
        mine = sorted((t for t in tasks if t.owner_id == u.id and t.item_key not in NO_DUE), key=lambda t: _rank(t, now))
        late = sum(1 for t in mine if _past(t, now))
        pct = min(100, round(len(mine) * 100 / max(over, 1)))
        lanes.append(dict(user=u, roles=' · '.join(ROLE_LABELS.get(r, r) for r in u.roles if r in ROLE_LABELS and r != 'admin'),
                          count=len(mine), late=late, pct=pct, tone='over' if len(mine) >= over else 'hi' if pct >= 60 else 'ok',
                          top=[dict(t=t, when=when(t, now), late=_past(t, now)) for t in mine[:3]],
                          director=_is_director(u)))
    lanes.sort(key=lambda x: (x['director'], -x['count'], x['user'].full_name))
    stats = nva.today_stats(session, now)
    done_by_ai = sum(n for agent in stats.values() for a, n in agent.items() if a in ('GIAO', 'NHAC', 'BAO', 'DOI_NGUOI'))
    feed = list(session.scalars(select(VirtualAgentLog).where(VirtualAgentLog.at >= start, VirtualAgentLog.action != 'CAI_DAT')
                                .order_by(VirtualAgentLog.id.desc()).limit(20)))
    late_tasks = sorted((t for t in tasks if t.item_key not in NO_DUE and _past(t, now)), key=lambda t: _rank(t, now))
    names = dict(session.execute(select(User.id, User.full_name)).all())
    last = {code: session.scalar(select(VirtualAgentLog).where(VirtualAgentLog.agent == code, VirtualAgentLog.action != 'CAI_DAT')
                                 .order_by(VirtualAgentLog.id.desc()).limit(1)) for code in nva.AGENTS}
    return dict(
        tiles=dict(open=sum(1 for t in tasks if t.item_key not in NO_DUE), ai=done_by_ai,
                   waiting=sum(1 for t in tasks if t.item_key in AI_KEYS), late=len(late_tasks),
                   boss=sum(1 for t in tasks if t.director_at is not None),
                   new_today=session.scalar(select(func.count()).select_from(TeamTask).where(TeamTask.first_seen_at >= start)) or 0),
        lanes=lanes, feed=feed, can_undo={e.id: can_undo(session, e, now) for e in feed if e.action == 'DOI_NGUOI'},
        late=[dict(t=t, when=when(t, now), owner=names.get(t.owner_id, '')) for t in late_tasks],
        escalated=[dict(t=t, when=when(t, now), owner=names.get(t.owner_id, '')) for t in sorted(
            (t for t in tasks if t.director_at is not None), key=lambda t: _rank(t, now))],
        stats=stats, last=last, names=names, overload=over, synced=_last['sync'])
