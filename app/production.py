"""SX1: stock-backed production. All writes, stock documents and audit commit together."""
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from . import audit, doc_numbers, equipment, sales_warranty, stock_docs, stock_posting, technician_stock
from .errors import BusinessError
from .models import ProductionOrder as Order, ProductionOrderLine as Line, ProductionOutput as Output, Product, StockDoc, StockDocLine, StockLedger, User, Warehouse
from .schemas import ProductionIn, ProductionComplete, ProductionMaterials, ProductionOutputDone, ProductionOutputReceive, ProductionReceive, StockDocIn
from .security import has_role

KINDS = {'DO_MUC': 'Đổ mực', 'TAN_TRANG': 'Tân trang', 'LAP_RAP': 'Lắp ráp / khác'}
STATUSES = {'MOI': 'Mới', 'DANG_LAM': 'Đang làm', 'HOAN_THANH': 'Chờ nhập kho', 'DA_NHAP': 'Đã nhập kho', 'DA_HUY': 'Đã hủy'}
READERS = ('technician', 'dispatcher', 'warehouse')
TZ = ZoneInfo('Asia/Ho_Chi_Minh')


def _now(): return datetime.now(UTC)


def _round(x): return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def no_material(row):
    """SX2: phiếu đã hoàn thành mà không có phiếu xuất vật tư (giá vốn 0)."""
    return row.status in ('HOAN_THANH', 'DA_NHAP') and row.px_doc_id is None


def title_extra():
    """SQL ' +N' khi phiếu có nhiều thành phẩm, ghép sau tên mã đầu tiên ở Việc của tôi, /tho, Tổng quan."""
    from sqlalchemy import String, case, cast, literal
    n = select(func.count()).where(Output.order_id == Order.id).correlate(Order).scalar_subquery()
    return case((n > 1, literal(' +') + cast(n - 1, String)), else_=literal(''))


def split_cost(material_cost, outputs, qty_of, price_of):
    """Chia tổng vật tư cho các thành phẩm theo tỷ lệ giá bán × số cái (mã chưa có giá bán: chia đều theo cái).
    Trả về {line_no: (tiền phân bổ, giá vốn 1 cái)}; tổng tiền phân bổ đúng bằng material_cost."""
    rows = [(o, qty_of(o)) for o in outputs if qty_of(o) > 0]
    if not rows: return {}
    weights = [Decimal(price_of(o.product_code) or 0) * q for o, q in rows]
    if not any(weights): weights = [Decimal(q) for _, q in rows]
    total = sum(weights); out = {}; left = material_cost
    for i, ((o, q), w) in enumerate(zip(rows, weights)):
        amount = left if i == len(rows) - 1 else _round(Decimal(material_cost) * w / total)
        amount = max(0, min(amount, left)); left -= amount
        out[o.line_no] = (amount, _round(Decimal(amount) / q))
    return out


def _require(user, *roles):
    if not user.active or not has_role(user, *roles):
        raise BusinessError('FORBIDDEN', 'Bạn không có quyền thao tác phiếu sản xuất.', status=403)


def _tech_only(user): return 'technician' in user.roles and not has_role(user, 'dispatcher', 'warehouse')


def search(session, user, status=None):
    _require(user, *READERS)
    q = select(Order).options(selectinload(Order.lines), selectinload(Order.outputs))
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
            'lines': [{c.key: value(getattr(l, c.key)) for c in l.__table__.columns} for l in row.lines],
            'outputs': [{c.key: value(getattr(o, c.key)) for c in o.__table__.columns} for o in row.outputs]}


def public(user, row):
    data = snapshot(row)
    if not has_role(user, 'warehouse', 'finance'):
        for key in ('material_cost', 'unit_cost'): data.pop(key)
        for o in data['outputs']: o.pop('unit_cost')
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
        values.pop('outputs'); values.pop('product_code'); values.pop('qty_plan')
        for code in sorted({o.product_code for o in body.outputs} | {l.product_code for l in body.lines}): _product(session, code)
        now = _now()
        row = Order(**values, product_code=body.outputs[0].product_code, qty_plan=sum(o.qty_plan for o in body.outputs),
                    number=doc_numbers.next_number(session, 'SX', now.astimezone(TZ).date()), created_by=user.id, created_at=now,
                    lines=[Line(line_no=i, **l.model_dump()) for i, l in enumerate(body.lines, 1)],
                    outputs=[Output(line_no=i, **o.model_dump()) for i, o in enumerate(body.outputs, 1)])
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


def _match_output(row, item, seen):
    """Tìm dòng thành phẩm theo line_no hoặc mã hàng; mỗi dòng chỉ một lần."""
    outs = {o.line_no: o for o in row.outputs}
    out = outs.get(item.line_no) if item.line_no is not None else None
    if item.line_no is None and item.product_code:
        out = next((o for o in row.outputs if o.product_code == item.product_code), None)
    if out is None or (item.product_code and item.product_code != out.product_code) or out.line_no in seen:
        raise BusinessError('BAD_OUTPUT', 'Dòng thành phẩm không đúng hoặc bị trùng.')
    seen.add(out.line_no)
    return out


def _used_lines(session, row, items):
    """Ghi số thật dùng vào dòng vật tư (dòng mới nếu cần); trả về dòng PX."""
    existing = {l.line_no: l for l in row.lines}; seen = set()
    for line in row.lines: line.qty_used = 0
    for item in items:
        line = existing.get(item.line_no) if item.line_no is not None else None
        if item.line_no is not None and line is None: raise BusinessError('BAD_MATERIAL', 'Không tìm thấy dòng vật tư.')
        if item.line_no is None and item.product_code:
            matches = [l for l in existing.values() if l.product_code == item.product_code]
            if len(matches) > 1: raise BusinessError('BAD_MATERIAL', 'Mã vật tư có nhiều dòng, hãy gửi số dòng.')
            if matches: line = matches[0]
        if line is not None:
            if line.line_no in seen or (item.product_code and item.product_code != line.product_code): raise BusinessError('BAD_MATERIAL', 'Dòng vật tư trùng hoặc sai mã hàng.')
            seen.add(line.line_no)
        else:
            if not item.product_code: raise BusinessError('BAD_MATERIAL', 'Dòng mới phải có mã vật tư.')
            if item.qty_used == 0: continue
            _product(session, item.product_code)
            line = Line(line_no=max(existing, default=0)+1, product_code=item.product_code, qty_plan=0)
            row.lines.append(line); existing[line.line_no] = line
            seen.add(line.line_no)
        line.qty_used = item.qty_used
    return [dict(product_code=l.product_code, qty=l.qty_used) for l in row.lines if l.qty_used > 0]


def _issue(session, user, row, used, now):
    """Một phiếu PX từ kho vật tư; material_cost = tổng amount sổ kho."""
    doc = stock_docs.insert(session, user, StockDocIn(kind='PX', warehouse_code=row.from_warehouse_code,
        doc_date=now.astimezone(TZ).date(), reason='Sản xuất ' + row.number, lines=used), source_kind='SAN_XUAT')
    stock_posting.post_in_tx(session, user, doc.id, doc.version)
    row.px_doc_id = doc.id
    row.material_cost = -int(session.scalar(select(func.coalesce(func.sum(StockLedger.amount), 0)).where(StockLedger.doc_id == doc.id)))


def _price_outputs(session, row, qty_of):
    """Giá vốn từng thành phẩm theo split_cost; unit_cost phiếu = bình quân mọi cái."""
    prices = {o.product_code: session.get(Product, o.product_code).sale_price for o in row.outputs}
    split = split_cost(row.material_cost, row.outputs, qty_of, prices.get)
    for o in row.outputs: o.unit_cost = split.get(o.line_no, (0, 0))[1]
    total = sum(qty_of(o) for o in row.outputs)
    row.unit_cost = _round(Decimal(row.material_cost) / total) if total else 0


def complete(session, user, ident, body: ProductionComplete):
    with stock_docs._transaction(session):
        row = load(session, user, ident, lock=True, version=body.version); _status(row, 'DANG_LAM')
        if row.technician_id != user.id or 'technician' not in user.roles: _require(user, 'director')
        # Số tốt / hỏng theo từng thành phẩm; phiếu một mã nhận qty_good/qty_scrap như SX1.
        done = body.outputs
        if not done:
            if body.qty_good is None or len(row.outputs) > 1: raise BusinessError('BAD_OUTPUT', 'Ghi số tốt / hỏng cho từng thành phẩm.')
            done = [ProductionOutputDone(line_no=row.outputs[0].line_no, qty_good=body.qty_good, qty_scrap=body.qty_scrap or 0)]
        seen = set(); counts = {}
        for item in done:
            out = _match_output(row, item, seen)
            if item.qty_good + item.qty_scrap > out.qty_plan: raise BusinessError('BAD_QTY', 'Tốt và hỏng không được vượt số định làm.')
            counts[out.line_no] = (item.qty_good, item.qty_scrap)
        good = sum(g for g, _ in counts.values()); scrap = sum(x for _, x in counts.values())
        if good == 0 and len(body.void_reason.strip()) < 3: raise BusinessError('REASON_REQUIRED', 'Hỏng hết phải ghi lý do ít nhất 3 ký tự.')
        before = snapshot(row); _source(session, row.from_warehouse_code, row.technician_id)
        used = _used_lines(session, row, body.lines)
        now = _now()
        if used:
            _issue(session, user, row, used, now)
        else:
            # SX2: hoàn thành không vật tư, phải qua bước xác nhận kèm lý do; kho thợ không đổi, giá vốn 0.
            if not body.confirm_no_material: raise BusinessError('MATERIAL_REQUIRED', 'Chưa ghi vật tư nào. Ghi vật tư thật dùng, hoặc xác nhận hoàn thành không vật tư kèm lý do.')
            if len(body.no_material_reason.strip()) < 3: raise BusinessError('REASON_REQUIRED', 'Hoàn thành không vật tư phải ghi lý do ít nhất 3 ký tự.')
            row.no_material_reason = body.no_material_reason.strip(); row.material_cost = 0
        for o in row.outputs: o.qty_good, o.qty_scrap = counts.get(o.line_no, (0, 0))
        row.qty_good = good; row.qty_scrap = scrap; row.finished_at = now
        _price_outputs(session, row, lambda o: o.qty_good)
        if good:
            row.status = 'HOAN_THANH'
        else:
            row.status = 'DA_HUY'; row.void_reason = body.void_reason.strip()
        _record(session, user, row, 'complete', before)
    return row


def add_materials(session, user, ident, body: ProductionMaterials):
    """SX2: phiếu Chờ nhập kho chưa có phiếu xuất → ghi vật tư, lập PX, tính lại giá vốn. Sau khi nhận kho thì chốt."""
    _require(user, *READERS)
    with stock_docs._transaction(session):
        row = load(session, user, ident, lock=True, version=body.version); _status(row, 'HOAN_THANH')
        if row.px_doc_id: raise BusinessError('BAD_STATUS', 'Phiếu đã có phiếu xuất vật tư.', status=409)
        if _tech_only(user) and row.technician_id != user.id: raise BusinessError('FORBIDDEN', 'Chỉ thợ của phiếu bổ sung được.', status=403)
        before = snapshot(row); _source(session, row.from_warehouse_code, row.technician_id)
        used = _used_lines(session, row, body.lines)
        if not used: raise BusinessError('MATERIAL_REQUIRED', 'Ghi ít nhất một vật tư thật dùng.')
        _issue(session, user, row, used, _now())
        _price_outputs(session, row, lambda o: o.qty_good)
        _record(session, user, row, 'materials', before)
    return row


def receive(session, user, ident, body: ProductionReceive):
    _require(user, 'warehouse')
    with stock_docs._transaction(session):
        row = load(session, user, ident, lock=True, version=body.version); _status(row, 'HOAN_THANH')
        if not has_role(user, 'director') and user.warehouse_codes and row.to_warehouse_code not in user.warehouse_codes:
            raise BusinessError('FORBIDDEN', 'Bạn không phụ trách kho nhận thành phẩm.', status=403)
        _warehouse(session, row.to_warehouse_code, ('KHO',))
        items = body.outputs
        if not items:
            if body.qty_received is None or len(row.outputs) > 1: raise BusinessError('BAD_OUTPUT', 'Ghi số thật nhận cho từng thành phẩm.')
            items = [ProductionOutputReceive(line_no=row.outputs[0].line_no, qty_received=body.qty_received, serials=body.serials)]
        seen = set(); received = {}; serials = {}
        for item in items:
            out = _match_output(row, item, seen)
            if not 0 <= item.qty_received <= out.qty_good: raise BusinessError('BAD_QTY', 'Số nhận phải từ 0 đến số thành phẩm tốt của từng mã.')
            received[out.line_no] = item.qty_received; serials[out.line_no] = item.serials
        for o in row.outputs: received.setdefault(o.line_no, 0); serials.setdefault(o.line_no, [])
        if sum(received.values()) < 1: raise BusinessError('BAD_QTY', 'Phải nhận ít nhất 1 thành phẩm; hỏng hết thì ghi ở bước Hoàn thành.')
        if any(received[o.line_no] < o.qty_good for o in row.outputs) and len(body.note.strip()) < 3:
            raise BusinessError("REASON_REQUIRED", "Nhận thiếu phải ghi chú ít nhất 3 ký tự.")
        before = snapshot(row)
        products = {o.line_no: _product(session, o.product_code) for o in row.outputs}
        values = {o.line_no: sales_warranty.checked_serials(serials[o.line_no], received[o.line_no], products[o.line_no].warranty_months > 0 and received[o.line_no] > 0)
                  for o in row.outputs}
        owner = {s: products[n] for n, ss in values.items() for s in ss}
        if len(owner) != sum(len(ss) for ss in values.values()): raise BusinessError('SERIAL_DUPLICATE', 'Serial bị trùng.')
        # Same sorted advisory/equipment locks as sales. Equipment is enrolled only when sold (BH1).
        for serial in sorted(owner):
            product = owner[serial]
            eq = sales_warranty._lock(session, serial)
            if eq and (eq.customer_code is not None or eq.product_code != product.code or eq.custody_location != 'TAI_KHACH' or eq.custody_ticket_id is not None):
                raise BusinessError('SERIAL_IN_USE', f'Serial {serial} đang được sử dụng.', status=409)
            duplicate = session.scalar(select(StockDocLine.doc_id).join(StockDoc).where(StockDocLine.serial == serial, StockDoc.kind == 'PN', StockDoc.status == 'DA_GHI').limit(1))
            if duplicate: raise BusinessError('SERIAL_IN_USE', f'Serial {serial} đã được nhập kho.', status=409)
        _price_outputs(session, row, lambda o: received[o.line_no])
        lines = []
        for o in row.outputs:
            qty = received[o.line_no]
            if not qty: continue
            lines += [dict(product_code=o.product_code, qty=1, unit_cost=o.unit_cost, serial=s) for s in values[o.line_no]] if values[o.line_no] \
                else [dict(product_code=o.product_code, qty=qty, unit_cost=o.unit_cost)]
        now = _now()
        doc = stock_docs.insert(session, user, StockDocIn(kind='PN', warehouse_code=row.to_warehouse_code, doc_date=now.astimezone(TZ).date(),
            reason='Nhập thành phẩm ' + row.number, lines=lines), source_kind='SAN_XUAT')
        stock_posting.post_in_tx(session, user, doc.id, doc.version)
        for o in row.outputs:
            o.qty_scrap += o.qty_good - received[o.line_no]; o.qty_good = received[o.line_no]
        row.qty_scrap += row.qty_good - sum(received.values()); row.qty_good = sum(received.values())
        if body.note.strip(): row.note = (row.note + "\nNhận kho: " + body.note.strip()).strip()
        row.pn_doc_id = doc.id; row.status = 'DA_NHAP'; row.received_by = user.id; row.received_at = now
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
