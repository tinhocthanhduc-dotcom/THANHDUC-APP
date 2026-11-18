"""Đợt 6 tối ưu thao tác (TUẤN duyệt mẫu 18/11/2026): việc kho bấm xong ngay tại dòng Việc của tôi.

Chỉ chọn dòng nào bấm được (không cần nhập gì thêm) và gọi đúng hàm nghiệp vụ của trang gốc.
Kiểm tra tồn, giữ hàng, quyền, phiên bản, công nợ vẫn nằm ở hàm nghiệp vụ; ở đây không ghi tiền hay tồn.
"""
from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select

from .errors import BusinessError
from .models import (EquipmentCustody, ProductionOrder, ProductionOutput, Product, PurchaseOrder, PurchaseOrderLine,
                     SalesOrder, SalesOrderLine, StockBalance, StockReservation, TechnicianReturn, Warehouse)
from .security import has_role

# nhóm việc → loại thao tác ở /viec-cua-toi/kho/{loai}/{id}
KEYS = {'orders_waiting': 'giao', 'purchases_waiting': 'nhan-mua', 'machines_warehouse': 'nhan-may',
        'technician_returns': 'nhan-tra', 'production_receive': 'nhan-sx'}
KINDS = set(KEYS.values())
DONE = {'giao': 'Đã giao đủ', 'nhan-mua': 'Đã nhận đủ', 'nhan-may': 'Đã nhận vào kho', 'nhan-tra': 'Đã nhận đủ', 'nhan-sx': 'Đã nhận thành phẩm'}
FULFILL = {'TAI_QUAY': 'Tại quầy', 'KHACH_LAY_SAU': 'Khách lấy sau', 'THO_GIAO': 'Thợ giao', 'VAN_CHUYEN': 'Gửi vận chuyển'}


def _act(row, kind, ident, version, ok):
    row['act'] = dict(kind=kind, id=ident, version=version, ok=ok, done=DONE[kind], url=f'/viec-cua-toi/kho/{kind}/{ident}', inline_open=True)


def _serial(session, codes):
    codes = {c for c in codes if c}
    return set(session.scalars(select(Product.code).where(Product.code.in_(codes), Product.track_serial.is_(True)))) if codes else set()


def _orders(session, rows):
    from . import sales_orders
    orders = {o.id: o for o in session.scalars(select(SalesOrder).where(SalesOrder.id.in_([r['id'] for r in rows])))}
    for row in rows:
        o = orders.get(row['id'])
        if o is None:
            continue
        left = sales_orders.remaining_lines(session, o)
        lines = [l for l in o.lines if left[l.line_no]['remaining'] > 0]
        goods = [l for l in lines if l.kind == 'HANG']
        whs = sorted({l.warehouse_code for l in goods if l.warehouse_code})
        row['reason'] = ' · '.join(x for x in (FULFILL.get(o.fulfillment, ''), f'{len(lines)} dòng' if lines else '') if x)
        if o.delivery_assignee_id is not None or o.fulfillment == 'THO_GIAO':
            row['why_not'] = 'đơn thợ giao'; continue
        if o.fulfillment == 'VAN_CHUYEN':
            row['why_not'] = 'ghi vận chuyển'; continue
        if not lines:
            row['why_not'] = 'không còn dòng giao'; continue
        if any(not l.warehouse_code for l in goods):
            row['why_not'] = 'chọn kho xuất'; continue
        if _serial(session, [l.product_code for l in goods]):
            row['why_not'] = 'cần nhập serial'; continue
        kinds = dict(session.execute(select(Warehouse.code, Warehouse.kind).where(Warehouse.code.in_(whs))).all())
        if any(kinds.get(w) != 'KHO' for w in whs):
            row['why_not'] = 'kho đặc biệt'; continue
        need = defaultdict(int)
        for l in goods:
            need[(l.warehouse_code, l.product_code)] += left[l.line_no]['remaining']
        have = {(w, p): q for w, p, q in session.execute(select(StockBalance.warehouse_code, StockBalance.product_code, StockBalance.qty).where(
            StockBalance.warehouse_code.in_(whs), StockBalance.product_code.in_({p for _, p in need})))} if need else {}
        # Hàng đơn khác đang giữ không tính là còn (giữ hàng vẫn do hàm giao kiểm).
        held = {(w, p): q for w, p, q in session.execute(select(StockReservation.warehouse_code, StockReservation.product_code, func.sum(StockReservation.qty)).where(
            StockReservation.status == 'GIU', StockReservation.order_id != o.id, StockReservation.warehouse_code.in_(whs),
            StockReservation.product_code.in_({p for _, p in need})).group_by(StockReservation.warehouse_code, StockReservation.product_code))} if need else {}
        short = sorted({k[0] for k, q in need.items() if have.get(k, 0) - held.get(k, 0) < q})
        if short:
            row['why_not'] = 'thiếu hàng ở ' + ', '.join(short); continue
        if whs:
            row['reason'] += ' · ' + ', '.join(whs) + ' đủ hàng'
        _act(row, 'giao', o.id, o.version, 'Giao đủ')


def _purchases(session, rows):
    orders = {o.id: o for o in session.scalars(select(PurchaseOrder).where(PurchaseOrder.id.in_([r['id'] for r in rows])))}
    for row in rows:
        o = orders.get(row['id'])
        if o is None:
            continue
        lines = [l for l in o.lines if l.qty > l.received_qty]
        whs = sorted({l.warehouse_code for l in lines if l.warehouse_code})
        row['reason'] = f'{len(lines)} dòng' + (' · về ' + ', '.join(whs) if whs else '')
        if not lines:
            row['why_not'] = 'không còn dòng nhận'; continue
        if any(not l.warehouse_code for l in lines):
            row['why_not'] = 'chọn kho nhận'; continue
        _act(row, 'nhan-mua', o.id, o.version, 'Nhận đủ')


def _machines(session, rows):
    found = {c.ticket_id: c for c in session.scalars(select(EquipmentCustody).where(
        EquipmentCustody.ticket_id.in_([r['id'] for r in rows]), EquipmentCustody.status == 'CHO_NHAN',
        EquipmentCustody.location == 'KHO_KY_THUAT').order_by(EquipmentCustody.id))}
    for row in rows:
        c = found.get(row['id'])
        if c is not None:
            _act(row, 'nhan-may', c.id, c.version, 'Nhận vào kho')


def _returns(session, rows):
    versions = dict(session.execute(select(TechnicianReturn.id, TechnicianReturn.version).where(TechnicianReturn.id.in_([r['id'] for r in rows]))).all())
    for row in rows:
        if row.get('damaged'):
            row['why_not'] = 'có hàng hư'; continue
        if row['id'] in versions:
            _act(row, 'nhan-tra', row['id'], versions[row['id']], 'Nhận đủ')


def _production(session, rows):
    orders = {o.id: o for o in session.scalars(select(ProductionOrder).where(ProductionOrder.id.in_([r['id'] for r in rows])))}
    outs = defaultdict(list)
    for out in session.scalars(select(ProductionOutput).where(ProductionOutput.order_id.in_(list(orders)))):
        outs[out.order_id].append(out)
    serial = _serial(session, [o.product_code for v in outs.values() for o in v])
    for row in rows:
        o = orders.get(row['id'])
        if o is None:
            continue
        mine = outs[o.id]
        row['reason'] = ' · '.join(x for x in (f'{sum(x.qty_good for x in mine)} sản phẩm tốt', f'về {o.to_warehouse_code}' if o.to_warehouse_code else '') if x)
        if any(x.product_code in serial for x in mine):
            row['why_not'] = 'cần nhập serial'; continue
        if sum(x.qty_good for x in mine) < 1:
            row['why_not'] = 'chưa có thành phẩm tốt'; continue
        _act(row, 'nhan-sx', o.id, o.version, 'Nhận thành phẩm')


FILL = {'orders_waiting': _orders, 'purchases_waiting': _purchases, 'machines_warehouse': _machines,
        'technician_returns': _returns, 'production_receive': _production}


def fill(session, key, rows):
    if rows and key in FILL:
        FILL[key](session, rows)


def run(session, user, kind, ident, version):
    """Gọi đúng hàm nghiệp vụ trang gốc với số đầy đủ còn lại."""
    if kind not in KINDS:
        raise BusinessError('NOT_FOUND', 'Không có thao tác này.', status=404)
    if not has_role(user, 'warehouse'):
        raise BusinessError('FORBIDDEN', 'Chỉ thủ kho được thao tác kho tại dòng.', status=403)
    try:
        version = int(str(version))
    except ValueError:
        raise BusinessError('BAD_VERSION', 'Thiếu phiên bản, hãy tải lại trang.')
    if kind == 'giao':
        from . import sales_orders
        order = session.get(SalesOrder, ident)
        if order is None:
            raise BusinessError('NOT_FOUND', 'Không tìm thấy đơn bán.', status=404)
        if order.fulfillment not in ('TAI_QUAY', 'KHACH_LAY_SAU') or _serial(session, [l.product_code for l in order.lines if l.kind == 'HANG']):
            raise BusinessError('OPEN_PAGE', 'Đơn này cần mở trang giao hàng để nhập thêm.', status=409)
        return sales_orders.deliver(session, user, ident, version)
    if kind == 'nhan-mua':
        from . import purchase_receipts, service_sla
        order = session.get(PurchaseOrder, ident)
        if order is None:
            raise BusinessError('NOT_FOUND', 'Không tìm thấy đơn mua.', status=404)
        lines = [dict(order_line_id=l.id, qty=l.qty - l.received_qty) for l in order.lines if l.qty > l.received_qty]
        return purchase_receipts.receive(session, user, ident, version, datetime.now(service_sla.TZ).date(), lines)
    if kind == 'nhan-may':
        from . import equipment_custody
        return equipment_custody.confirm(session, user, ident, version)
    if kind == 'nhan-tra':
        from . import technician_returns
        return technician_returns.receive(session, user, ident, version, {})
    from . import production
    from .schemas import ProductionReceive
    order = session.get(ProductionOrder, ident)
    if order is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy phiếu sản xuất.', status=404)
    outs = list(session.scalars(select(ProductionOutput).where(ProductionOutput.order_id == ident).order_by(ProductionOutput.line_no)))
    if _serial(session, [o.product_code for o in outs]):
        raise BusinessError('OPEN_PAGE', 'Thành phẩm có serial, hãy mở phiếu để nhập serial.', status=409)
    return production.receive(session, user, ident, ProductionReceive(version=version, outputs=[
        dict(line_no=o.line_no, qty_received=o.qty_good) for o in outs]))
