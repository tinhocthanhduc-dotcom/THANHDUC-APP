"""Hàng mang theo phiếu dịch vụ (TUẤN 30/10/2026, mẫu DuHangChiPhi).

Phiếu có linh kiện cấp cho phiếu hoặc đơn giao cùng chuyến thì thợ phải bấm Đã nhận đủ hàng
hoặc Báo thiếu hàng trước khi Đã đến khách. Xác nhận ghi một dòng service_steps (không bảng mới);
danh sách hàng đổi (thêm mã, đổi số lượng) thì phải xác nhận lại.
"""
from decimal import Decimal

from sqlalchemy import select

from . import service_tickets as svc
from .errors import BusinessError
from .models import PartRequest, Product, SalesOrder, ServiceStep, StockReservation, Warehouse

BEFORE_ARRIVAL = ('DA_NHAN', 'DA_HEN')
ACTIONS = ('goods_ok', 'goods_short')
SHORT_REASONS = ('Kho chưa đưa', 'Thiếu số lượng', 'Sai mã hàng', 'Hàng lỗi')
PART_LABELS = {'CHO_CAP': 'Chờ kho', 'DA_CAP': 'Đã cấp', 'HET_HANG': 'Kho hết hàng'}
SIGN = 'Hàng: '


def _qty(value):
    value = Decimal(value).normalize()
    return format(value if value != value.to_integral() else value.quantize(Decimal(1)), 'f')


def items(session, t):
    """Hàng thợ mang theo cho phiếu: linh kiện kho cấp cho phiếu + đơn giao cùng chuyến còn phải giao."""
    rows = []
    for r, name, kind in session.execute(select(PartRequest, Product.name, Warehouse.kind).join(Product, Product.code == PartRequest.product_code)
            .outerjoin(Warehouse, Warehouse.code == PartRequest.to_warehouse_code)
            .where(PartRequest.ticket_id == t.id, PartRequest.status.in_(tuple(PART_LABELS))).order_by(PartRequest.id)):
        if r.status == 'DA_CAP' and kind != 'THO':
            continue  # cấp cho xưởng, không phải hàng thợ mang đi
        rows.append(dict(key=f'YC:{r.id}', group='Linh kiện cho phiếu', code=r.product_code, name=name, qty=r.qty,
                         ready=r.status == 'DA_CAP', label=PART_LABELS[r.status]))
    if t.assignee_id is None:
        return rows
    from .sales_orders import remaining_lines
    orders = session.scalars(select(SalesOrder).where(SalesOrder.service_ticket_id == t.id, SalesOrder.delivery_assignee_id == t.assignee_id,
                                                      SalesOrder.status.in_(('DA_CHOT', 'GIAO_MOT_PHAN'))).order_by(SalesOrder.id))
    for order in orders:
        left = remaining_lines(session, order)
        held = {}
        for line_no, qty in session.execute(select(StockReservation.order_line_id, StockReservation.qty).join(Warehouse, Warehouse.code == StockReservation.warehouse_code)
                .where(StockReservation.order_id == order.id, StockReservation.status == 'GIU', Warehouse.kind == 'THO')):
            held[line_no] = held.get(line_no, Decimal(0)) + qty
        for line in order.lines:
            qty = left[line.line_no]['remaining']
            if line.kind != 'HANG' or qty <= 0:
                continue
            ready = held.get(line.line_no, Decimal(0)) >= qty
            rows.append(dict(key=f'DB:{order.id}:{line.line_no}', group='Đơn giao kèm ' + order.number, code=line.product_code or '',
                             name=line.description, qty=qty, ready=ready, label='Kho đã giao' if ready else 'Chờ kho'))
    return rows


def signature(rows):
    return ', '.join(sorted(f"{r['code'] or r['name']}×{_qty(r['qty'])}" for r in rows))


def last_step(session, t):
    return session.scalar(select(ServiceStep).where(ServiceStep.ticket_id == t.id, ServiceStep.action.in_(ACTIONS))
                          .order_by(ServiceStep.id.desc()).limit(1))


def state(session, t, rows=None):
    """dict(items, required, step): step là lần xác nhận còn hiệu lực với danh sách hàng hiện tại."""
    from .service_charges import return_locked
    if t.status not in BEFORE_ARRIVAL or t.assignee_id is None or return_locked(t):
        return dict(items=[], required=False, step=None)
    rows = items(session, t) if rows is None else rows
    step = last_step(session, t) if rows else None
    if step is not None and step.note.rsplit('\n', 1)[-1] != SIGN + signature(rows):
        step = None  # hàng đã đổi sau lần xác nhận
    return dict(items=rows, required=bool(rows), step=step)


def check_arrival(session, t):
    """Thợ bấm Đã đến khách: phiếu có hàng kèm theo thì phải xác nhận trước."""
    s = state(session, t)
    if s['required'] and s['step'] is None:
        raise BusinessError('GOODS_UNCONFIRMED', 'Kiểm hàng mang theo trước: bấm Đã nhận đủ hàng hoặc Báo thiếu hàng rồi mới Đã đến khách.', status=409)


def confirm(session, user, ticket_id, version, short_keys=(), reason='', note=''):
    """Thợ được giao xác nhận đủ hàng (short_keys rỗng) hoặc báo thiếu các dòng short_keys."""
    note = (note or '').strip()
    if len(note) > 1000:
        raise BusinessError('NOTE_TOO_LONG', 'Ghi chú tối đa 1.000 ký tự.')
    with svc._transaction(session):
        t = svc._get(session, user, ticket_id, lock=True)
        svc._check_version(t, version)
        if 'technician' not in user.roles or t.assignee_id != user.id:
            raise BusinessError('FORBIDDEN', 'Chỉ thợ được giao mới xác nhận hàng mang theo.', status=403)
        if t.status not in BEFORE_ARRIVAL:
            raise BusinessError('BAD_STATUS', 'Chỉ xác nhận hàng trước khi đến khách.', status=409)
        s = state(session, t)
        if not s['required']:
            raise BusinessError('NO_GOODS', 'Phiếu không có hàng kèm theo.', status=409)
        by_key = {r['key']: r for r in s['items']}
        short = [by_key[k] for k in dict.fromkeys(short_keys) if k in by_key]
        if len(short) != len(set(short_keys)):
            raise BusinessError('BAD_ITEMS', 'Danh sách hàng vừa thay đổi, hãy tải lại.', status=409)
        if short:
            if reason not in SHORT_REASONS:
                raise BusinessError('REASON_REQUIRED', 'Chọn lý do thiếu hàng.')
            text = 'Báo thiếu: ' + ', '.join(f"{r['name']} × {_qty(r['qty'])}" for r in short) + ' · ' + reason + ('. ' + note if note else '')
        else:
            text = 'Đã nhận đủ hàng' + ('. ' + note if note else '')
        before = svc.snapshot(t)
        svc._record(session, user, t, 'goods_short' if short else 'goods_ok', before, t.status, text + '\n' + SIGN + signature(s['items']))
    return t
