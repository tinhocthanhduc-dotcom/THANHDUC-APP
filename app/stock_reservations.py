"""S1: nơi duy nhất ghi giữ hàng. Mọi thay đổi GIU phải khóa dòng tồn trước.

Dòng đơn bán có PK (order_id, line_no); order_line_id chính là line_no.
Không ghi sổ/tính giá vốn ở đây. Khóa tồn dùng chung với stock_posting.
"""
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select, tuple_

from . import audit
from .errors import BusinessError
from .models import Customer, SalesOrder, StockBalance, StockReservation
from .security import has_role

ZERO = Decimal(0)


def held_query(exclude_order_id=None):
    stmt = select(StockReservation.warehouse_code, StockReservation.product_code,
                  func.sum(StockReservation.qty).label('reserved'), func.min(StockReservation.created_at).label('oldest')).where(StockReservation.status == 'GIU')
    if exclude_order_id is not None:
        stmt = stmt.where(StockReservation.order_id != exclude_order_id)
    return stmt.group_by(StockReservation.warehouse_code, StockReservation.product_code).subquery()


def quantities(session, keys=None, exclude_order_id=None):
    held = held_query(exclude_order_id)
    reserved = func.coalesce(held.c.reserved, 0)
    stmt = select(StockBalance.warehouse_code, StockBalance.product_code, StockBalance.qty,
                  reserved.label('reserved'), (StockBalance.qty-reserved).label('available')).outerjoin(
        held, (held.c.warehouse_code == StockBalance.warehouse_code) & (held.c.product_code == StockBalance.product_code))
    if keys is not None:
        keys = set(keys)
        if not keys:
            return {}
        stmt = stmt.where(tuple_(StockBalance.warehouse_code, StockBalance.product_code).in_(sorted(keys)))
    return {(r.warehouse_code, r.product_code): dict(qty=r.qty, reserved=r.reserved, available=r.available)
            for r in session.execute(stmt)}


def available(session, warehouse_code, product_code):
    return quantities(session, [(warehouse_code, product_code)]).get(
        (warehouse_code, product_code), dict(available=ZERO))['available']


def held_orders(session, warehouse_code, product_code, exclude_order_id=None):
    stmt = select(StockReservation, SalesOrder.number, Customer.name, SalesOrder.promised_date).join(
        SalesOrder, SalesOrder.id == StockReservation.order_id).join(Customer, Customer.code == SalesOrder.customer_code).where(
        StockReservation.status == 'GIU', StockReservation.warehouse_code == warehouse_code,
        StockReservation.product_code == product_code)
    if exclude_order_id is not None:
        stmt = stmt.where(StockReservation.order_id != exclude_order_id)
    return list(session.execute(stmt.order_by(StockReservation.created_at, StockReservation.id)))


def _qty(value):
    text = format(value, 'f')
    return (text.rstrip('0').rstrip('.') if '.' in text else text).replace('.', ',')


def check_out(session, balance, qty, for_order_id=None):
    # Dòng tồn đã được stock_posting khóa. Giữ NEGATIVE_STOCK cho xuất quá tồn thực tế.
    if qty > balance.qty:
        return
    state = quantities(session, [(balance.warehouse_code,balance.product_code)], for_order_id)[(balance.warehouse_code,balance.product_code)]
    held, free = state['reserved'], state['available']
    if held > 0 and qty > free:
        rows = held_orders(session, balance.warehouse_code, balance.product_code, for_order_id)
        orders = list(dict.fromkeys(number for _, number, *_ in rows))
        details = dict(warehouse=balance.warehouse_code, product=balance.product_code, stock=str(balance.qty),
                       reserved=str(held), available=str(free), requested=str(qty), orders=orders)
        raise BusinessError('RESERVED_STOCK',
            f"Mã {balance.product_code} ở kho {balance.warehouse_code}: tồn {_qty(balance.qty)}, đang giữ {_qty(held)}, "
            f"khả dụng {_qty(free)}, cần {_qty(qty)}. Đơn đang giữ: {', '.join(orders)}.", details, status=409)


def snapshot(row):
    result = {}
    for field in ('id','order_id','order_line_id','warehouse_code','product_code','qty','status','reason',
                  'created_by','created_at','closed_by','closed_at'):
        value = getattr(row, field)
        result[field] = value.isoformat() if isinstance(value, datetime) else str(value) if isinstance(value, Decimal) else value
    return result


def _record(session, user, row, action, before=None):
    session.flush()
    audit.record(session, user, 'stock_reservation.'+action, 'stock_reservation', str(row.id), before, snapshot(row), row.reason)


def _close(session, user, row, status, reason=''):
    before = snapshot(row)
    row.status, row.reason = status, reason
    row.closed_by, row.closed_at = user.id, datetime.now(UTC)
    _record(session, user, row, 'export' if status == 'DA_XUAT' else 'release', before)


def keep_in_tx(session, user, order):
    from .stock_posting import _lock_balances
    lines = sorted((l for l in order.lines if l.kind == 'HANG'), key=lambda l: (l.warehouse_code,l.product_code,l.line_no))
    _lock_balances(session, {(l.warehouse_code,l.product_code) for l in lines})
    for line in lines:
        old = session.scalar(select(StockReservation).where(StockReservation.order_id == order.id,
            StockReservation.order_line_id == line.line_no, StockReservation.status == 'GIU'))
        held = old.qty if old else ZERO
        more = min(line.qty-held, max(ZERO, available(session, line.warehouse_code, line.product_code)))
        if more <= 0:
            continue
        if old:
            _close(session, user, old, 'DA_NHA', 'Giữ thêm')
        row = StockReservation(order_id=order.id, order_line_id=line.line_no, warehouse_code=line.warehouse_code,
                               product_code=line.product_code, qty=held+more, status='GIU', reason='', created_by=user.id)
        session.add(row)
        _record(session, user, row, 'keep')


def close_in_tx(session, user, order, *, warehouse_code=None, exported=False, reason=''):
    from .stock_posting import _lock_balances
    stmt = select(StockReservation).where(StockReservation.order_id == order.id, StockReservation.status == 'GIU')
    if warehouse_code is not None:
        stmt = stmt.where(StockReservation.warehouse_code == warehouse_code)
    rows = list(session.scalars(stmt.order_by(StockReservation.id)))
    _lock_balances(session, {(r.warehouse_code,r.product_code) for r in rows})
    for row in rows:
        _close(session, user, row, 'DA_XUAT' if exported else 'DA_NHA', reason)


def for_order(session, order):
    rows = list(session.scalars(select(StockReservation).where(StockReservation.order_id == order.id).order_by(StockReservation.id)))
    result = {}
    for line in order.lines:
        if line.kind != 'HANG':
            continue
        current = next((r for r in reversed(rows) if r.order_line_id == line.line_no and r.status in ('GIU','DA_XUAT')), None)
        held = current.qty if current else ZERO
        result[line.line_no] = dict(reservation=current, held=held, missing=max(ZERO,line.qty-held))
    return result


def can_keep(user, order):
    return has_role(user, 'warehouse') or (has_role(user, 'sales') and order.created_by == user.id)


def keep_more(session, user, order_id, version):
    from . import sales_orders
    with sales_orders._transaction(session):
        order = sales_orders._load(session, order_id, lock=True)
        if not can_keep(user, order):
            raise BusinessError('FORBIDDEN', 'Chỉ người lập đơn, kho hoặc giám đốc được giữ thêm hàng.', status=403)
        sales_orders._version(order, version)
        sales_orders._status(order, ('DA_CHOT',))
        sales_orders._lines(session, [sales_orders.SalesOrderLineIn.model_validate(l, from_attributes=True) for l in order.lines], require_warehouse=True)
        before = sales_orders.snapshot(order)
        keep_in_tx(session, user, order)
        order.version += 1
        sales_orders._record(session, user, order, 'keep_stock', before)
    return order


def release(session, user, order_id, reservation_id, reason, version):
    from . import sales_orders
    if not has_role(user, 'warehouse'):
        raise BusinessError('FORBIDDEN', 'Chỉ kho hoặc giám đốc được nhả hàng.', status=403)
    with sales_orders._transaction(session):
        order = sales_orders._load(session, order_id, lock=True)
        sales_orders._version(order, version)
        sales_orders._status(order, ('DA_CHOT',))
        reason = (reason or '').strip()
        if not reason:
            raise BusinessError('REASON_REQUIRED', 'Phải nhập lý do nhả hàng.')
        row = session.scalar(select(StockReservation).where(StockReservation.id == reservation_id,
            StockReservation.order_id == order.id).execution_options(populate_existing=True))
        if row is None:
            raise BusinessError('NOT_FOUND', 'Không tìm thấy dòng giữ của đơn.', status=404)
        if row.status != 'GIU':
            raise BusinessError('BAD_STATUS', 'Dòng hàng này không còn được giữ.', status=409)
        from .stock_posting import _lock_balances
        _lock_balances(session, {(row.warehouse_code,row.product_code)})
        before = sales_orders.snapshot(order)
        _close(session, user, row, 'DA_NHA', reason)
        order.version += 1
        sales_orders._record(session, user, order, 'release_stock', before, reason)
    return order


def picker(session, products, warehouse_code):
    if not warehouse_code:
        return {}
    values = quantities(session, [(warehouse_code,p.code) for p in products])
    return {p.code: values.get((warehouse_code,p.code), dict(qty=ZERO,reserved=ZERO,available=ZERO)) for p in products}
