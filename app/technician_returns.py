"""UX1c: đóng hẹn lần sau và yêu cầu trả kho; kho nhận mới ghi CK."""
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from sqlalchemy import select
from . import audit, part_requests, service_sla, service_tickets as svc, stock_docs, stock_posting
from .doc_numbers import next_number
from .errors import BusinessError
from .models import PartRequest, Product, SalesReceipt, StockBalance, TechnicianReturn, TechnicianReturnLine, User, Warehouse
from .stock_docs import _transaction
from .schemas import StockDocIn
from .security import has_role

STATUSES = {'CHO_NHAN':'Chờ nhận', 'DA_NHAN':'Đã nhận', 'DA_HUY':'Đã hủy'}


def quantity(value):
    try:
        qty = Decimal(str(value).replace(',', '.'))
        if not qty.is_finite() or qty < 0 or qty >= Decimal('100000000000') or qty.as_tuple().exponent < -3:
            raise ValueError
        return qty
    except (InvalidOperation, ValueError):
        raise BusinessError('BAD_QTY', 'Số trả phải không âm, tối đa 3 số lẻ.')


def proposal(session, ticket, lock=False):
    tech = session.get(User, ticket.assignee_id) if ticket.assignee_id else None
    from .technician_stock import own_warehouse
    own = own_warehouse(session, tech, lock=lock) if tech else None
    warehouses = list(session.scalars(select(Warehouse).where(Warehouse.kind=='KHO', Warehouse.active.is_(True)).order_by(Warehouse.code)))
    requests = list(session.scalars(select(PartRequest).where(PartRequest.ticket_id==ticket.id, PartRequest.status=='DA_CAP',
        PartRequest.to_warehouse_code==own.code if own else False).order_by(PartRequest.decided_at.desc(), PartRequest.id.desc())))
    default = requests[0].from_warehouse_code if requests else (warehouses[0].code if warehouses else '')
    stocks = dict(session.execute(select(StockBalance.product_code, StockBalance.qty).where(
        StockBalance.warehouse_code==own.code if own else False, StockBalance.qty>0)).all())
    allocated = defaultdict(Decimal)
    for row in requests: allocated[(row.product_code, row.from_warehouse_code)] += row.qty
    remaining = dict(stocks); rows = []
    for (product, dest), supplied in allocated.items():
        available = remaining.get(product, Decimal(0)); qty = min(supplied, available)
        rows.append(dict(product_code=product, to_warehouse_code=dest, qty=qty, supplied=supplied, held=stocks.get(product, 0)))
        remaining[product] = available-qty
    allocated_products = {code for code, _ in allocated}
    for code, qty in stocks.items():
        if code not in allocated_products:
            rows.append(dict(product_code=code, to_warehouse_code=default, qty=0, supplied=0, held=qty))
    return dict(warehouse=own, warehouses=warehouses, rows=rows)


def snapshot(row):
    return dict(id=row.id, number=row.number, ticket_id=row.ticket_id, technician_id=row.technician_id,
        warehouse_code=row.warehouse_code, to_warehouse_code=row.to_warehouse_code, status=row.status,
        stock_doc_id=row.stock_doc_id, note=row.note, void_reason=row.void_reason, version=row.version,
        lines=[dict(product_code=l.product_code, qty=str(l.qty), received_qty=str(l.received_qty) if l.received_qty is not None else None) for l in row.lines])


def record(session, user, row, action, before=None):
    session.flush()
    audit.record(session, user, 'technician_return.'+action, 'technician_return', str(row.id), before, snapshot(row), row.void_reason or row.note)


def close_ticket(session, user, ticket_id, version, reason, lines=None):
    with _transaction(session):
        ticket = part_requests._ticket(session, ticket_id)
        part_requests.check_requester(user, ticket)
        svc._check_version(ticket, version)
        if ticket.status not in ('DANG_LAM','CHO_LINH_KIEN','TAM_DUNG'):
            raise BusinessError('BAD_STATUS', 'Chỉ đóng hẹn lần sau khi đang làm, chờ linh kiện hoặc tạm dừng.', status=409)
        if session.scalar(select(SalesReceipt.id).where(SalesReceipt.ticket_id==ticket.id).limit(1)):
            raise BusinessError('HAS_RECEIPTS', 'Phiếu đã báo thu, không đóng hẹn lần sau.', status=409)
        data = proposal(session, ticket, lock=True)
        entries = data['rows'] if lines is None else lines
        own = data['warehouse']; grouped = defaultdict(lambda: defaultdict(Decimal)); totals = defaultdict(Decimal)
        for entry in entries:
            qty = quantity(entry['qty'])
            if not qty: continue
            if own is None: raise BusinessError('NO_TECH_WAREHOUSE', 'Thợ chưa có kho riêng đang dùng.', status=409)
            code = str(entry['product_code']); dest = str(entry.get('to_warehouse_code') or '')
            warehouse = session.scalar(select(Warehouse).where(Warehouse.code==dest, Warehouse.kind=='KHO', Warehouse.active.is_(True)).with_for_update(read=True))
            if warehouse is None: raise BusinessError('BAD_WAREHOUSE', 'Chọn kho công ty đang dùng để nhận hàng trả.')
            if session.get(Product, code) is None: raise BusinessError('BAD_PRODUCT', 'Không tìm thấy hàng trả.')
            grouped[dest][code] += qty; totals[code] += qty
        # Chốt số đang giữ theo từng mã; chưa trừ/giữ tồn cho yêu cầu trả.
        for code in sorted(totals):
            stock = session.scalar(select(StockBalance).where(StockBalance.warehouse_code==own.code,
                StockBalance.product_code==code).with_for_update())
            available = stock.qty if stock else 0
            if totals[code] > available:
                raise BusinessError('BAD_RETURN_QTY', 'Số trả vượt số đang giữ.', {'product':code,'available':str(available)}, status=409)
        svc.transition_in_tx(session, user, ticket, 'KHACH_HEN_SAU', reason)
        # Yêu cầu chưa cấp không còn được thực hiện sau khi kết thúc phiếu.
        pending = session.scalars(select(PartRequest).where(PartRequest.ticket_id==ticket.id,PartRequest.status=='CHO_CAP').with_for_update())
        for request in pending:
            before = part_requests.snapshot(request)
            request.status='DA_HUY';request.decision_note='Khách hẹn lần sau: '+reason.strip()
            request.decided_by=user.id;request.decided_at=datetime.now(UTC)
            part_requests._audit(session,user,request,'cancel',before)
        for dest, items in sorted(grouped.items()):
            row = TechnicianReturn(number=next_number(session,'TK',svc._now().astimezone(service_sla.TZ).date()),
                ticket_id=ticket.id, technician_id=ticket.assignee_id, warehouse_code=own.code,
                to_warehouse_code=dest, created_by=user.id, note='', void_reason='')
            row.lines = [TechnicianReturnLine(line_no=i, product_code=code, qty=qty) for i,(code,qty) in enumerate(sorted(items.items()),1)]
            session.add(row); record(session,user,row,'create')
    return ticket


def load(session, user, ident, lock=False):
    # Cùng thứ tự khóa với đóng phiếu: phiếu -> yêu cầu -> kho.
    ticket_id = session.scalar(select(TechnicianReturn.ticket_id).where(TechnicianReturn.id==ident))
    if ticket_id is None: raise BusinessError('NOT_FOUND','Không tìm thấy yêu cầu trả kho.',status=404)
    if lock: part_requests._ticket(session, ticket_id)
    stmt = select(TechnicianReturn).where(TechnicianReturn.id==ident)
    if lock: stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(stmt)
    if not has_role(user,'warehouse','dispatcher') and row.technician_id!=user.id:
        raise BusinessError('FORBIDDEN','Yêu cầu không thuộc bạn.',status=403)
    return row


def receive(session, user, ident, version, quantities, note=''):
    part_requests.check_supplier(user)
    with _transaction(session):
        row = load(session,user,ident,lock=True)
        check_pending(row, version)
        values = {line.product_code: quantity(quantities.get(line.product_code, line.qty)) for line in row.lines}
        if set(quantities)-set(values): raise BusinessError('BAD_LINE','Hàng thực nhận không thuộc yêu cầu.')
        if any(values[line.product_code] > line.qty for line in row.lines):
            raise BusinessError('BAD_QTY','Số thực nhận không được vượt số yêu cầu.')
        if any(values[line.product_code] != line.qty for line in row.lines) and not note.strip():
            raise BusinessError('REASON_REQUIRED','Phải ghi chú chênh lệch số thực nhận.')
        before = snapshot(row)
        entries = [dict(product_code=code, qty=qty) for code,qty in sorted(values.items()) if qty]
        if entries:
            doc = stock_docs.insert(session,user,StockDocIn(kind='CK',
                warehouse_code=row.warehouse_code,to_warehouse_code=row.to_warehouse_code,
                doc_date=svc._now().astimezone(service_sla.TZ).date(),reason=f'Trả {row.number}',lines=entries))
            stock_posting.post_in_tx(session,user,doc.id,doc.version)
            row.stock_doc_id = doc.id
        for line in row.lines: line.received_qty = values[line.product_code]
        row.status='DA_NHAN';row.note=note.strip();row.received_by=user.id;row.received_at=datetime.now(UTC)
        record(session,user,row,'receive',before)
    return row


def check_pending(row, version):
    if version != row.version: raise BusinessError('VERSION_CONFLICT','Yêu cầu vừa được cập nhật, hãy tải lại.',status=409)
    if row.status!='CHO_NHAN': raise BusinessError('BAD_STATUS','Yêu cầu không còn chờ nhận.',status=409)


def cancel(session,user,ident,version,reason):
    with _transaction(session):
        row=load(session,user,ident,lock=True);check_pending(row,version)
        if not reason.strip(): raise BusinessError('REASON_REQUIRED','Phải ghi lý do hủy.')
        before=snapshot(row);row.status='DA_HUY';row.void_reason=reason.strip()
        record(session,user,row,'cancel',before)
    return row
