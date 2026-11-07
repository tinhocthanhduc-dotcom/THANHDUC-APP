"""Đơn bán Thợ giao: thợ tự nhận, gợi ý thợ, đi theo phiếu cùng chuyến (TUẤN duyệt 07/11/2026, bản mẫu GiaoDonTuNhan).

- Đơn Thợ giao đã chốt, chưa có thợ: hiện ở Việc chờ nhận trên màn Hôm nay của mọi thợ. Không có giờ hết hạn,
  chỉ rời màn thợ khi đã có người nhận (thợ tự nhận hoặc điều phối giao).
- Đơn nối với phiếu dịch vụ: phiếu có thợ thì đơn tự giao cho thợ đó (lúc chốt đơn hoặc lúc phiếu được giao/tự nhận).
  Phiếu đang mở mà chưa có thợ thì đơn không hiện riêng, đi theo người nhận phiếu.
- Gợi ý thợ cho điều phối: chỉ SELECT, chấm điểm như phiếu dịch vụ (rảnh, gần khách, kho thợ có sẵn hàng của đơn,
  đang có phiếu cùng khách). Không tự giao.
- Không đổi tiền, kho, công nợ: chỉ ghi người giao đơn; kho bàn giao hàng cho thợ như cũ.
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import exists, func, select

from . import sales_orders, service_self_claim, service_suggest, service_tickets
from .errors import BusinessError
from .models import Customer, SalesOrder, ServiceTicket, ServiceVisit, User, Warehouse

NOTE = 'Thợ tự nhận'
OPEN_ORDER = ('DA_CHOT', 'GIAO_MOT_PHAN')
REMIND_AFTER = timedelta(minutes=30)  # chờ quá 30 phút chưa ai nhận: nhắc điều phối một lần, đơn vẫn hiện cho thợ


def enabled() -> bool:
    """Bật/tắt cùng thợ tự nhận phiếu (TD_SELF_CLAIM_MINUTES = 0 là tắt)."""
    return service_self_claim.minutes() > 0


def _ticket_waiting():
    """Đơn nối với phiếu đang mở chưa có thợ: đơn đi theo người nhận phiếu."""
    t = ServiceTicket
    return exists().where(t.id == SalesOrder.service_ticket_id, t.assignee_id.is_(None), t.status.in_(service_tickets.OPEN))


def unassigned():
    o = SalesOrder
    return (o.fulfillment == 'THO_GIAO') & o.status.in_(OPEN_ORDER) & o.delivery_assignee_id.is_(None)


def claimable():
    """Điều kiện đơn đang chờ thợ tự nhận (dùng trong where)."""
    return unassigned() & ~_ticket_waiting()


def waited_since(order):
    return order.confirmed_at or order.created_at


def remind_due(now):
    """Đơn chờ thợ nhận quá 30 phút (để nhắc điều phối; đơn vẫn hiện cho thợ)."""
    o = SalesOrder
    return claimable() & (func.coalesce(o.confirmed_at, o.created_at) <= now - REMIND_AFTER)


def _tech_warehouse(session, user_id):
    return session.scalar(select(Warehouse.code).where(Warehouse.holder_id == user_id, Warehouse.kind == 'THO', Warehouse.active.is_(True)))


def _liquidation(session, order) -> bool:
    from .pending_liquidation import check_fulfillment
    try:
        check_fulfillment(session, order, for_technician=True)
    except BusinessError:
        return True
    return False


def _give(session, actor, order, assignee_id, reason):
    """Ghi thợ giao cho đơn chưa có thợ. Gọi trong transaction, đơn đã khóa."""
    from . import technician_deliveries
    from .pending_liquidation import check_fulfillment
    check_fulfillment(session, order, for_technician=True)
    technician_deliveries.warehouse(session, assignee_id)
    before = sales_orders.snapshot(order)
    order.delivery_assignee_id, order.delivery_assigned_at = assignee_id, datetime.now(UTC)
    order.version += 1
    sales_orders._record(session, actor, order, 'assign_delivery', before, reason)


def follow_ticket(session, actor, ticket) -> list[SalesOrder]:
    """Phiếu vừa có thợ: đơn Thợ giao chưa có thợ nối với phiếu đi theo thợ đó (cùng transaction).
    Thợ chưa có kho thợ hoặc đơn có hàng thanh lý thì để đơn chờ như cũ, không chặn việc giao phiếu."""
    if ticket.assignee_id is None or _tech_warehouse(session, ticket.assignee_id) is None:
        return []
    out = []
    for order in session.scalars(select(SalesOrder).where(SalesOrder.service_ticket_id == ticket.id, unassigned())
                                 .order_by(SalesOrder.id).with_for_update()):
        if _liquidation(session, order):
            continue
        _give(session, actor, order, ticket.assignee_id, f'Đi theo phiếu {ticket.number}')
        out.append(order)
    return out


def follow_on_confirm(session, actor, order):
    """Đơn vừa chốt, nối với phiếu đã có thợ: tự giao cho thợ của phiếu (cùng transaction chốt đơn)."""
    if order.fulfillment != 'THO_GIAO' or order.service_ticket_id is None or order.delivery_assignee_id is not None:
        return
    ticket = session.get(ServiceTicket, order.service_ticket_id)
    if ticket is None or ticket.assignee_id is None or ticket.status not in service_tickets.OPEN:
        return
    if _tech_warehouse(session, ticket.assignee_id) is None or _liquidation(session, order):
        return
    _give(session, actor, order, ticket.assignee_id, f'Đi theo phiếu {ticket.number}')


def claim(session, user: User, order_id: int) -> SalesOrder:
    if 'technician' not in user.roles or not user.active:
        raise BusinessError('FORBIDDEN', 'Chỉ kỹ thuật viên được tự nhận đơn giao.', status=403)
    if not enabled():
        raise BusinessError('NOT_FOUND', 'Không tìm thấy đơn chờ nhận.', status=404)
    with sales_orders._transaction(session):
        # Khóa dòng đơn: hai thợ bấm cùng lúc thì người sau chờ, rồi thấy đơn đã có người nhận.
        order = session.scalar(select(SalesOrder).where(SalesOrder.id == order_id).with_for_update()
                               .execution_options(populate_existing=True))
        if order is None or order.fulfillment != 'THO_GIAO':
            raise BusinessError('NOT_FOUND', 'Không tìm thấy đơn chờ nhận.', status=404)
        if order.delivery_assignee_id is not None:
            name = session.scalar(select(User.full_name).where(User.id == order.delivery_assignee_id)) or 'Thợ khác'
            raise BusinessError('ALREADY_CLAIMED', f'{name} đã nhận đơn {order.number} trước bạn.', status=409)
        if order.status not in OPEN_ORDER:
            raise BusinessError('BAD_STATUS', f'Đơn {order.number} không còn chờ giao.', status=409)
        ticket = session.get(ServiceTicket, order.service_ticket_id) if order.service_ticket_id else None
        if ticket is not None and ticket.assignee_id is None and ticket.status in service_tickets.OPEN:
            raise BusinessError('FOLLOWS_TICKET', f'Đơn {order.number} đi cùng phiếu {ticket.number}. Nhận phiếu đó là nhận luôn đơn.', status=409)
        limit = service_self_claim.max_open()
        if service_self_claim.open_jobs(session, user.id) >= limit:
            raise BusinessError('TOO_MANY_JOBS', f'Bạn đang có {limit} việc mở trở lên. Xong bớt một việc rồi mới nhận thêm được.', status=409)
        if _tech_warehouse(session, user.id) is None:
            raise BusinessError('NO_TECH_WAREHOUSE', 'Bạn chưa có kho thợ nên chưa nhận đơn giao được. Báo giám đốc mở kho thợ.', status=409)
        _give(session, user, order, user.id, NOTE)
    return order


# ---------- Màn thợ ----------

def claim_rows(session, now) -> list[dict]:
    from .web.technician import contact
    out = []
    for order, customer in session.execute(select(SalesOrder, Customer).join(Customer, Customer.code == SalesOrder.customer_code)
                                           .where(claimable()).order_by(func.coalesce(SalesOrder.confirmed_at, SalesOrder.created_at), SalesOrder.id)):
        since = waited_since(order)
        minutes = max(0, int((now - since).total_seconds()) // 60) if since else 0
        goods = ', '.join(f"{_qty(l.qty)} {l.description}" for l in order.lines if l.kind == 'HANG')
        out.append(dict(id=order.id, number=order.number, customer=customer.name, goods=goods or 'Giao đơn bán', since=since,
                        waited=minutes, promised=order.promised_date, **contact(customer, address=order.delivery_address)))
    return out


def linked_orders(session, ticket_ids) -> dict:
    """{ticket_id: [số đơn]} đơn Thợ giao chưa có thợ đi theo phiếu (để thẻ phiếu chờ nhận ghi 'Kèm đơn ...')."""
    if not ticket_ids:
        return {}
    out = {}
    for tid, number in session.execute(select(SalesOrder.service_ticket_id, SalesOrder.number).where(
            SalesOrder.service_ticket_id.in_(ticket_ids), unassigned()).order_by(SalesOrder.id)):
        out.setdefault(tid, []).append(number)
    return out


def _qty(q):
    q = Decimal(q)
    return str(int(q)) if q == q.to_integral_value() else format(q.normalize(), 'f').replace('.', ',')


def is_self_claim(reason: str) -> bool:
    return reason == NOTE


# ---------- Gợi ý thợ ----------

def open_orders(session, ids) -> dict:
    if not ids:
        return {}
    return dict(session.execute(select(SalesOrder.delivery_assignee_id, func.count()).where(
        SalesOrder.delivery_assignee_id.in_(ids), SalesOrder.fulfillment == 'THO_GIAO', SalesOrder.status.in_(OPEN_ORDER))
        .group_by(SalesOrder.delivery_assignee_id)).all())


def rank_orders(session, orders, now=None, techs=None) -> dict:
    """{order_id: [dòng thợ đã xếp]}; dòng có id, name, score, why, free, blocked, best."""
    now = now or service_suggest._now()
    orders = [o for o in orders if o is not None]
    if not orders:
        return {}
    techs = service_suggest.load_techs(session, now) if techs is None else techs
    if not techs:
        return {o.id: [] for o in orders}
    ids = [x.user.id for x in techs]
    delivering = open_orders(session, ids)
    codes = {o.customer_code for o in orders}
    v, t = ServiceVisit, ServiceTicket
    points = {}
    for code, lat, lng in session.execute(select(t.customer_code, v.arrive_lat, v.arrive_lng).join(t, t.id == v.ticket_id)
                                          .where(t.customer_code.in_(codes), v.arrive_lat.is_not(None)).order_by(v.started_at.desc(), v.id.desc())):
        points.setdefault(code, (lat, lng))
    cust_addr = dict(session.execute(select(Customer.code, Customer.address).where(Customer.code.in_(codes))).all())
    same = {}
    for code, uid, number in session.execute(select(t.customer_code, t.assignee_id, t.number).where(
            t.customer_code.in_(codes), t.assignee_id.in_(ids), t.status.in_(service_tickets.OPEN)).order_by(t.id)):
        same.setdefault(code, {}).setdefault(uid, number)
    needs = {}
    for o in orders:
        left = sales_orders.remaining_lines(session, o)
        need = {}
        for line in o.lines:
            if line.kind == 'HANG' and line.product_code and left[line.line_no]['remaining'] > 0:
                need[line.product_code] = need.get(line.product_code, Decimal(0)) + left[line.line_no]['remaining']
        needs[o.id] = need
    stock = service_suggest._stock(session, {x.warehouse for x in techs if x.warehouse}, {c for n in needs.values() for c in n})
    from .config import get_settings
    s = get_settings()
    in_hours = s.work_start <= now.astimezone(service_suggest.TZ).time() <= s.work_end
    out = {}
    for o in orders:
        site = points.get(o.customer_code)
        place = service_suggest.area(o.delivery_address or cust_addr.get(o.customer_code))
        need = needs.get(o.id, {})
        rows = []
        for x in techs:
            score, why, blocked = 0.0, [], ''
            if x.on_leave:
                blocked = x.on_leave
            elif not x.warehouse:
                blocked = 'Chưa có kho thợ'
            jobs = x.open_jobs + delivering.get(x.user.id, 0)
            score -= 12 * jobs
            free = f"{jobs} việc đang mở"
            if jobs <= 1:
                why.append(free)
            if in_hours and not x.clocked_in and not blocked:
                score -= 10
                free += ' · chưa vào ca'
            ticket_no = same.get(o.customer_code, {}).get(x.user.id)
            if ticket_no:
                score += 35
                why.insert(0, f'Đang có phiếu {ticket_no} cùng khách')
            dist = service_suggest.km(x.point, site)
            if dist is not None:
                near = f"{dist:.1f} km".replace('.', ',')
                score += max(0.0, 30 - 3 * dist)
                if dist <= 5:
                    why.insert(1 if ticket_no else 0, near)
            elif place and place in x.areas:
                near = f"cùng {place.title() if not place.isdigit() else 'Quận ' + place}"
                score += 15
                why.insert(1 if ticket_no else 0, near)
            else:
                near = ''
            if need and x.warehouse:
                have = [c for c, q in need.items() if stock.get((x.warehouse, c), Decimal(0)) >= q]
                if len(have) == len(need):
                    score += 20
                    why.append('Kho thợ có sẵn hàng của đơn')
                elif have:
                    score += 5
            if 'dispatcher' in x.user.roles:
                score -= 8
                free += ' · kiêm điều phối'
            if blocked:
                score -= 1000
            rows.append(dict(id=x.user.id, name=x.user.full_name, score=round(score, 1), why=why[:3], near=near, free=free, blocked=blocked))
        rows.sort(key=lambda r: (-r['score'], r['name'], r['id']))
        if rows and not rows[0]['blocked']:
            rows[0]['best'] = True
        out[o.id] = rows
    return out


def assign_note(session, suggested_id, chosen_id) -> str:
    """Lý do ghi vào nhật ký đơn khi điều phối giao: theo gợi ý hay chọn khác (như phiếu dịch vụ)."""
    if not suggested_id:
        return ''
    return service_suggest.assign_note(session, suggested_id, chosen_id)
