"""Đổi thợ: hàng đã cấp cho phiếu/đơn đi theo thợ mới bằng một phiếu CK kho thợ cũ → kho thợ mới (TUẤN 29/10/2026)."""
from collections import Counter
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from . import service_sla, stock_docs, stock_posting, stock_reservations
from .errors import BusinessError
from .models import PartRequest, TechnicianReturn, User, Warehouse
from .schemas import StockDocIn, StockDocLineIn

ZERO = Decimal(0)


def _new_warehouse(session, assignee_id):
    from .technician_stock import own_warehouse
    tech = session.get(User, assignee_id)
    wh = own_warehouse(session, tech, lock=True) if tech else None
    if wh is None:
        raise BusinessError('NO_TECH_WAREHOUSE', 'Thợ nhận chưa có kho thợ đang dùng để nhận hàng của phiếu.', status=409)
    return wh


def _pending_returns(session, warehouse_code, ticket_id=None):
    stmt = select(TechnicianReturn).where(TechnicianReturn.warehouse_code == warehouse_code, TechnicianReturn.status == 'CHO_NHAN')
    if ticket_id is not None:
        stmt = stmt.where(TechnicianReturn.ticket_id == ticket_id)
    out = Counter()
    for row in session.scalars(stmt.with_for_update()):
        for line in row.lines:
            out[line.product_code] += line.qty
    return out


def ticket_need(session, t, old_code):
    """Số hàng của phiếu đang nằm ở kho thợ: đã cấp − đã dùng − đã trả/đang trả; ít nhất bằng vật tư dự kiến lấy kho thợ cũ."""
    supplied = Counter()
    for code, qty in session.execute(select(PartRequest.product_code, PartRequest.qty).join(Warehouse, Warehouse.code == PartRequest.to_warehouse_code)
            .where(PartRequest.ticket_id == t.id, PartRequest.status == 'DA_CAP', Warehouse.kind == 'THO')):
        supplied[code] += qty
    for line in t.lines:
        if line.kind == 'VAT_TU' and line.stock_doc_id is not None and session.get(Warehouse, line.warehouse_code).kind == 'THO':
            supplied[line.product_code] -= line.qty
    for row in session.scalars(select(TechnicianReturn).where(TechnicianReturn.ticket_id == t.id, TechnicianReturn.status != 'DA_HUY')):
        for line in row.lines:
            supplied[line.product_code] -= line.qty if row.status == 'CHO_NHAN' else (line.received_qty or ZERO)
    planned = Counter()
    for line in t.lines:
        if line.kind == 'VAT_TU' and line.stock_doc_id is None and line.warehouse_code == old_code:
            planned[line.product_code] += line.qty
    need = {code: max(supplied[code], planned[code]) for code in set(supplied) | set(planned)}
    return {code: qty for code, qty in need.items() if qty > 0}


def move_ticket_stock(session, user, t, assignee_id):
    """Gọi trong transaction đổi thợ, trước khi đổi assignee. Trả (phiếu CK hoặc None, {mã: số thiếu})."""
    if t.assignee_id is None or t.assignee_id == assignee_id:
        return None, {}
    from .technician_stock import own_warehouse
    old = own_warehouse(session, session.get(User, t.assignee_id), lock=True)
    if old is None:
        return None, {}
    need = ticket_need(session, t, old.code)
    if not need:
        tech = session.get(User, assignee_id)
        new = own_warehouse(session, tech, lock=True) if tech else None
        if new is not None:
            _repoint_lines(t, old.code, new.code)
        return None, {}
    new = _new_warehouse(session, assignee_id)
    stock_posting._lock_balances(session, {(code, product) for product in need for code in (old.code, new.code)})
    pending = _pending_returns(session, old.code)
    moved, short = {}, {}
    for code, qty in sorted(need.items()):
        # Không lấy phần đang giữ cho đơn khác hoặc phần thợ cũ đã gửi yêu cầu trả kho.
        free = stock_reservations.available(session, old.code, code) - pending[code]
        take = min(qty, max(free, ZERO))
        if take > 0:
            moved[code] = take
        if take < qty:
            short[code] = qty - take
    doc = None
    if moved:
        doc = stock_docs.insert(session, user, StockDocIn(kind='CK', warehouse_code=old.code, to_warehouse_code=new.code,
            doc_date=_today(), reason=f'{t.number}: đổi thợ, hàng đi theo phiếu',
            lines=[StockDocLineIn(product_code=code, qty=qty) for code, qty in moved.items()]), source_kind='DOI_THO')
        stock_posting.post_in_tx(session, user, doc.id, None)
    _repoint_lines(t, old.code, new.code)
    return doc, short


def _today():
    return datetime.now(service_sla.TZ).date()


def _repoint_lines(t, old_code, new_code):
    # Dòng vật tư dự kiến lấy kho thợ cũ: đổi sang kho thợ mới (chưa ghi PX nên chưa trừ kho nào).
    for line in t.lines:
        if line.kind == 'VAT_TU' and line.stock_doc_id is None and line.warehouse_code == old_code:
            line.warehouse_code = new_code


def describe(session, doc, short):
    from .models import Product
    names = dict(session.execute(select(Product.code, Product.name).where(Product.code.in_(list(short)))).all()) if short else {}
    parts = []
    if doc is not None:
        parts.append(f'Hàng đi theo phiếu sang kho thợ mới ({doc.number}): ' + ', '.join(f'{l.product_code} × {_qty(l.qty)}' for l in doc.lines))
    if short:
        parts.append('Kho thợ cũ thiếu, chưa chuyển: ' + ', '.join(f'{names.get(c, c)} ({c}) × {_qty(q)}' for c, q in short.items()))
    return '. '.join(parts)


def _qty(value):
    return format(Decimal(value).normalize(), 'f')


def move_order_stock(session, user, order, assignee_id):
    """Đơn Thợ giao: hàng đang giữ ở kho thợ cũ chuyển sang kho thợ mới, giữ hàng đi theo."""
    from .technician_deliveries import held_by_tech
    rows = [r for r in held_by_tech(session, order) if r.qty > 0]
    if not rows:
        return None
    new = _new_warehouse(session, assignee_id)
    lines = {l.line_no: l for l in order.lines if l.kind == 'HANG'}
    groups = {}
    for r in rows:
        if r.warehouse_code == new.code:
            continue
        groups.setdefault(r.warehouse_code, []).append((lines[r.order_line_id], r.qty))
    docs = []
    for source, parts in sorted(groups.items()):
        totals = Counter()
        for line, qty in parts:
            totals[line.product_code] += qty
        doc = stock_docs.insert(session, user, StockDocIn(kind='CK', warehouse_code=source, to_warehouse_code=new.code,
            doc_date=_today(), reason=f'{order.number}: đổi thợ giao, hàng đi theo đơn',
            lines=[StockDocLineIn(product_code=code, qty=qty) for code, qty in sorted(totals.items())]), source_kind='TECHNICIAN_DELIVERY')
        docs.append((doc, parts))
    stock_posting._lock_balances(session, {(code, line.product_code) for doc, parts in docs for line, _ in parts for code in (doc.warehouse_code, new.code)})
    for doc, parts in docs:
        stock_posting.post_in_tx(session, user, doc.id, None, for_order_id=order.id)
        for line, qty in parts:
            stock_reservations.transfer_in_tx(session, user, order, line, qty, doc.warehouse_code, new.code, f'Chuyển cho thợ {doc.number}')
    return [doc for doc, _ in docs]
