"""SX1: stock-backed production. All writes, stock documents and audit commit together."""
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from . import audit, doc_numbers, equipment, sales_warranty, stock_docs, stock_posting, technician_stock
from .errors import BusinessError
from .models import ProductionOrder as Order, ProductionOrderLine as Line, Product, StockDoc, StockDocLine, StockLedger, User, Warehouse
from .schemas import ProductionIn, ProductionComplete, ProductionReceive, StockDocIn
from .security import has_role

KINDS = {'DO_MUC': 'Đổ mực', 'TAN_TRANG': 'Tân trang', 'LAP_RAP': 'Lắp ráp / khác'}
STATUSES = {'MOI': 'Mới', 'DANG_LAM': 'Đang làm', 'HOAN_THANH': 'Chờ nhập kho', 'DA_NHAP': 'Đã nhập kho', 'DA_HUY': 'Đã hủy'}
READERS = ('technician', 'dispatcher', 'warehouse')
TZ = ZoneInfo('Asia/Ho_Chi_Minh')


def _now(): return datetime.now(UTC)


def _require(user, *roles):
    if not user.active or not has_role(user, *roles):
        raise BusinessError('FORBIDDEN', 'Bạn không có quyền thao tác phiếu sản xuất.', status=403)


def _tech_only(user): return 'technician' in user.roles and not has_role(user, 'dispatcher', 'warehouse')


def search(session, user, status=None):
    _require(user, *READERS)
    q = select(Order).options(selectinload(Order.lines))
    if _tech_only(user): q = q.where(Order.technician_id == user.id)
    if status: q = q.where(Order.status == status)
    return q.order_by(Order.id.desc())


def load(session, user, ident, *, lock=False, version=None):
    _require(user, *READERS)
    q = select(Order).where(Order.id == ident if isinstance(ident, int) else Order.number == ident)
    if lock: q = q.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(q)
    if row is None: raise BusinessError('NOT_FOUND', 'Không tìm thấy phiếu sản xuất.', status=404)
    if _tech_only(user) and row.technician_id != user.id:
        raise BusinessError('FORBIDDEN', 'Bạn chỉ được xem phiếu sản xuất của mình.', status=403)
    if version is not None and row.version != version:
        raise BusinessError('VERSION_CONFLICT', 'Phiếu đã thay đổi, hãy tải lại.', status=409)
    return row


def snapshot(row):
    def value(v): return v.astimezone(UTC).isoformat() if isinstance(v, datetime) else format(v.normalize(), 'f') if isinstance(v, Decimal) else v
    return {**{c.key: value(getattr(row, c.key)) for c in row.__table__.columns},
            'lines': [{c.key: value(getattr(l, c.key)) for c in l.__table__.columns} for l in row.lines]}


def public(user, row):
    data = snapshot(row)
    if not has_role(user, 'warehouse', 'finance'):
        for key in ('material_cost', 'unit_cost'): data.pop(key)
    return data


def _record(session, user, row, action, before=None):
    session.flush()
    audit.record(session, user, 'production.' + action, 'production_order', str(row.id), before, snapshot(row))


def _status(row, status):
    if row.status != status: raise BusinessError('BAD_STATUS', 'Trạng thái phiếu không cho phép thao tác này.', status=409)


def _warehouse(session, code, kinds):
    row = session.scalar(select(Warehouse).where(Warehouse.code == code, Warehouse.active.is_(True), Warehouse.kind.in_(kinds)).with_for_update(read=True))
    if row is None: raise BusinessError('BAD_WAREHOUSE', 'Chọn kho đang dùng đúng loại.')
    return row


def _technician(session, ident):
    row = session.get(User, ident) if ident else None
    if row is None or not row.active or 'technician' not in row.roles:
        raise BusinessError('BAD_TECHNICIAN', 'Chọn thợ đang hoạt động.')
    own = technician_stock.own_warehouse(session, row, lock=True)
    if own is None: raise BusinessError('NO_TECH_WAREHOUSE', 'Thợ chưa có kho riêng.', status=409)
    return own


def _source(session, code, technician_id):
    wh = _warehouse(session, code, ('THO', 'KHO'))
    if wh.kind == 'THO' and wh.holder_id != technician_id:
        raise BusinessError('BAD_WAREHOUSE', 'Kho vật tư phải là kho của thợ được giao hoặc kho nội bộ.')
    return wh


def _product(session, code):
    row = session.scalar(select(Product).where(Product.code == code, Product.active.is_(True)).with_for_update(read=True))
    if row is None: raise BusinessError('BAD_PRODUCT', 'Mã hàng không tồn tại hoặc đã ngừng dùng.')
    return row


def create(session, user, body: ProductionIn):
    _require(user, *READERS)
    with stock_docs._transaction(session):
        values = body.model_dump(exclude={'lines'})
        if _tech_only(user):
            if body.technician_id not in (None, user.id): raise BusinessError('FORBIDDEN', 'Thợ chỉ được tự lập cho mình.', status=403)
            own = _technician(session, user.id)
            if body.from_warehouse_code not in (None, own.code): raise BusinessError('FORBIDDEN', 'Thợ chỉ dùng kho của mình.', status=403)
            values.update(technician_id=user.id, from_warehouse_code=own.code)
        elif body.technician_id:
            own = _technician(session, body.technician_id)
            if not body.from_warehouse_code: values['from_warehouse_code'] = own.code
        _warehouse(session, body.to_warehouse_code, ('KHO',))
        if values['from_warehouse_code']: _source(session, values['from_warehouse_code'], values['technician_id'])
        for code in sorted({body.product_code} | {l.product_code for l in body.lines}): _product(session, code)
        now = _now()
        row = Order(**values, number=doc_numbers.next_number(session, 'SX', now.astimezone(TZ).date()), created_by=user.id, created_at=now,
                    lines=[Line(line_no=i, **l.model_dump()) for i, l in enumerate(body.lines, 1)])
        session.add(row); _record(session, user, row, 'create')
    return row


def assign(session, user, ident, technician_id, version):
    _require(user, 'dispatcher', 'warehouse')
    with stock_docs._transaction(session):
        row = load(session, user, ident, lock=True, version=version); _status(row, 'MOI'); before = snapshot(row)
        own = _technician(session, technician_id)
        if row.from_warehouse_code:
            _source(session, row.from_warehouse_code, technician_id)
        else: row.from_warehouse_code = own.code
        row.technician_id = technician_id; _record(session, user, row, 'assign', before)
    return row


def start(session, user, ident, version):
    with stock_docs._transaction(session):
        row = load(session, user, ident, lock=True, version=version); _status(row, 'MOI')
        if not has_role(user, 'dispatcher') and row.technician_id != user.id: _require(user, 'director')
        _technician(session, row.technician_id)
        _source(session, row.from_warehouse_code, row.technician_id)
        before = snapshot(row); row.status = 'DANG_LAM'; row.started_at = _now()
        _record(session, user, row, 'start', before)
    return row


def complete(session, user, ident, body: ProductionComplete):
    with stock_docs._transaction(session):
        row = load(session, user, ident, lock=True, version=body.version); _status(row, 'DANG_LAM')
        if row.technician_id != user.id or 'technician' not in user.roles: _require(user, 'director')
        if body.qty_good + body.qty_scrap > row.qty_plan: raise BusinessError('BAD_QTY', 'Tốt và hỏng không được vượt số định làm.')
        if body.qty_good == 0 and len(body.void_reason.strip()) < 3: raise BusinessError('REASON_REQUIRED', 'Hỏng hết phải ghi lý do ít nhất 3 ký tự.')
        before = snapshot(row); _source(session, row.from_warehouse_code, row.technician_id)
        existing = {l.line_no: l for l in row.lines}; seen = set()
        for line in row.lines: line.qty_used = 0
        for item in body.lines:
            line = existing.get(item.line_no) if item.line_no is not None else None
            if item.line_no is not None and line is None: raise BusinessError('BAD_MATERIAL', 'Không tìm thấy dòng vật tư.')
            if line is not None:
                if line.line_no in seen or (item.product_code and item.product_code != line.product_code): raise BusinessError('BAD_MATERIAL', 'Dòng vật tư trùng hoặc sai mã hàng.')
                seen.add(line.line_no)
            else:
                if not item.product_code: raise BusinessError('BAD_MATERIAL', 'Dòng mới phải có mã vật tư.')
                _product(session, item.product_code)
                line = Line(line_no=max(existing, default=0)+1, product_code=item.product_code, qty_plan=0)
                row.lines.append(line); existing[line.line_no] = line
            line.qty_used = item.qty_used
        used = [dict(product_code=l.product_code, qty=l.qty_used) for l in row.lines if l.qty_used > 0]
        if not used: raise BusinessError('MATERIAL_REQUIRED', 'Nhập ít nhất một vật tư thực dùng để lập phiếu xuất.')
        now = _now()
        doc = stock_docs.insert(session, user, StockDocIn(kind='PX', warehouse_code=row.from_warehouse_code,
            doc_date=now.astimezone(TZ).date(), reason='Sản xuất ' + row.number, lines=used), source_kind='SAN_XUAT')
        stock_posting.post_in_tx(session, user, doc.id, doc.version)
        row.px_doc_id = doc.id
        row.material_cost = -int(session.scalar(select(func.coalesce(func.sum(StockLedger.amount), 0)).where(StockLedger.doc_id == doc.id)))
        row.qty_good = body.qty_good; row.qty_scrap = body.qty_scrap; row.finished_at = now
        if body.qty_good:
            from .service_charges import _round
            row.unit_cost = _round(Decimal(row.material_cost) / body.qty_good); row.status = 'HOAN_THANH'
        else:
            row.status = 'DA_HUY'; row.void_reason = body.void_reason.strip()
        _record(session, user, row, 'complete', before)
    return row


def receive(session, user, ident, body: ProductionReceive):
    _require(user, 'warehouse')
    with stock_docs._transaction(session):
        row = load(session, user, ident, lock=True, version=body.version); _status(row, 'HOAN_THANH')
        if not has_role(user, 'director') and user.warehouse_codes and row.to_warehouse_code not in user.warehouse_codes:
            raise BusinessError('FORBIDDEN', 'Bạn không phụ trách kho nhận thành phẩm.', status=403)
        _warehouse(session, row.to_warehouse_code, ('KHO',))
        if not 1 <= body.qty_received <= row.qty_good: raise BusinessError('BAD_QTY', 'Số nhận phải từ 1 đến số thành phẩm tốt.')
        if body.qty_received < row.qty_good and len(body.note.strip()) < 3: raise BusinessError("REASON_REQUIRED", "Nhận thiếu phải ghi chú ít nhất 3 ký tự.")
        product = _product(session, row.product_code); before = snapshot(row)
        values = sales_warranty.checked_serials(body.serials, body.qty_received, product.warranty_months > 0)
        # Same sorted advisory/equipment locks as sales. Equipment is enrolled only when sold (BH1).
        for serial in sorted(values):
            eq = sales_warranty._lock(session, serial)
            if eq and (eq.customer_code is not None or eq.product_code != product.code or eq.custody_location != 'TAI_KHACH' or eq.custody_ticket_id is not None):
                raise BusinessError('SERIAL_IN_USE', f'Serial {serial} đang được sử dụng.', status=409)
            duplicate = session.scalar(select(StockDocLine.doc_id).join(StockDoc).where(StockDocLine.serial == serial, StockDoc.kind == 'PN', StockDoc.status == 'DA_GHI').limit(1))
            if duplicate: raise BusinessError('SERIAL_IN_USE', f'Serial {serial} đã được nhập kho.', status=409)
        from .service_charges import _round
        cost = _round(Decimal(row.material_cost) / body.qty_received)
        lines = [dict(product_code=row.product_code, qty=1, unit_cost=cost, serial=s) for s in values] if values else [dict(product_code=row.product_code, qty=body.qty_received, unit_cost=cost)]
        now = _now()
        doc = stock_docs.insert(session, user, StockDocIn(kind='PN', warehouse_code=row.to_warehouse_code, doc_date=now.astimezone(TZ).date(),
            reason='Nhập thành phẩm ' + row.number, lines=lines), source_kind='SAN_XUAT')
        stock_posting.post_in_tx(session, user, doc.id, doc.version)
        row.qty_scrap += row.qty_good - body.qty_received; row.qty_good = body.qty_received
        if body.note.strip(): row.note = (row.note + "\nNhận kho: " + body.note.strip()).strip()
        row.unit_cost = cost; row.pn_doc_id = doc.id; row.status = 'DA_NHAP'; row.received_by = user.id; row.received_at = now
        _record(session, user, row, 'receive', before)
    return row


def void(session, user, ident, reason, version):
    with stock_docs._transaction(session):
        row = load(session, user, ident, lock=True, version=version)
        if not has_role(user, 'dispatcher') and user.id not in (row.created_by, row.technician_id): _require(user, 'director')
        if row.status not in ('MOI', 'DANG_LAM') or row.px_doc_id: raise BusinessError('BAD_STATUS', 'Đã xuất vật tư thì không hủy phiếu sản xuất.', status=409)
        reason = reason.strip()
        if not 3 <= len(reason) <= 2000: raise BusinessError('REASON_REQUIRED', 'Ghi lý do hủy từ 3 đến 2.000 ký tự.')
        before = snapshot(row); row.status = 'DA_HUY'; row.void_reason = reason
        _record(session, user, row, 'void', before)
    return row
