"""Thợ hủy đơn và yêu cầu trả kho; kho nhận mới ghi CK."""
from collections import Counter, defaultdict
from contextlib import contextmanager
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
    used=Counter()
    for line in getattr(ticket,'lines',()):
        if line.kind=='VAT_TU' and line.stock_doc_id is not None and own and line.warehouse_code==own.code:
            used[line.product_code]+=line.qty
    for key in allocated:
        code,_=key;consumed=min(used[code],allocated[key]);allocated[key]-=consumed;used[code]-=consumed
    planned=Counter()
    for line in getattr(ticket,'lines',()):
        if line.kind=='VAT_TU' and line.stock_doc_id is None and own and line.warehouse_code==own.code:planned[line.product_code]+=line.qty
    for code,qty in planned.items():
        supplied=sum(amount for (product,_),amount in allocated.items() if product==code)
        if qty>supplied:allocated[(code,default)]+=qty-supplied
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
        stock_doc_id=row.stock_doc_id, defect_stock_doc_id=row.defect_stock_doc_id, note=row.note, void_reason=row.void_reason, version=row.version,
        lines=[dict(line_no=l.line_no, condition=l.condition, damage_reason=l.damage_reason, received_condition=l.received_condition, received_damage_reason=l.received_damage_reason, product_code=l.product_code, qty=str(l.qty), received_qty=str(l.received_qty) if l.received_qty is not None else None) for l in row.lines])


def record(session, user, row, action, before=None):
    session.flush()
    audit.record(session, user, 'technician_return.'+action, 'technician_return', str(row.id), before, snapshot(row), row.void_reason or row.note)


def cancel_by_technician(session, user, ticket_id, version, reason, lines=None):
    with photo_transaction(session):
        ticket = part_requests._ticket(session, ticket_id)
        if 'technician' not in user.roles or ticket.assignee_id != user.id:
            raise BusinessError('FORBIDDEN', 'Chỉ thợ được giao được tự hủy đơn.', status=403)
        svc._check_version(ticket, version)
        svc.transition_in_tx(session, user, ticket, 'DA_HUY', reason, return_lines=lines)
    return ticket


def prepare_cancel(session, ticket, lines):
    """Caller đã kiểm quyền/lý do và khóa phiếu; chưa thay dữ liệu nguồn."""
    if session.scalar(select(SalesReceipt.id).where(SalesReceipt.ticket_id==ticket.id).limit(1)):
        raise BusinessError('HAS_RECEIPTS', 'Phiếu đã báo thu, không được tự hủy đơn.', status=409)
    data = proposal(session, ticket, lock=True)
    grouped = prepare(session, data['warehouse'], data['rows'] if lines is None else lines)
    return data['warehouse'], grouped


def finish_cancel(session, user, ticket, reason, cancellation):
    part_requests.cancel_pending_for_ticket(session, user, ticket, 'Hủy đơn: '+reason)
    warehouse, grouped = cancellation
    create_requests(session, user, ticket.assignee_id, warehouse, ticket.id, grouped)


def load(session, user, ident, lock=False):
    # Cùng thứ tự khóa với đóng phiếu: phiếu -> yêu cầu -> kho.
    source = session.execute(select(TechnicianReturn.ticket_id).where(TechnicianReturn.id==ident)).first()
    if source is None: raise BusinessError('NOT_FOUND','Không tìm thấy yêu cầu trả kho.',status=404)
    if lock and source.ticket_id is not None: part_requests._ticket(session, source.ticket_id)
    stmt = select(TechnicianReturn).where(TechnicianReturn.id==ident)
    if lock: stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(stmt)
    if not has_role(user,'warehouse','dispatcher') and row.technician_id!=user.id:
        raise BusinessError('FORBIDDEN','Yêu cầu không thuộc bạn.',status=403)
    return row


def receive(session, user, ident, version, quantities, note='', conditions=None, damage_reasons=None):
    part_requests.check_supplier(user)
    conditions = conditions or {}; damage_reasons = damage_reasons or {}
    with _transaction(session):
        row = load(session,user,ident,lock=True)
        check_pending(row, version)
        counts = Counter(line.product_code for line in row.lines)
        allowed = {line.line_no for line in row.lines} | {line.product_code for line in row.lines if counts[line.product_code]==1}
        if set(quantities)-allowed or set(conditions)-{l.line_no for l in row.lines} or set(damage_reasons)-{l.line_no for l in row.lines}:
            raise BusinessError('BAD_LINE','Phải nhập số thực nhận theo từng dòng hàng.')
        before = snapshot(row); entries = {'TOT':defaultdict(Decimal), 'HU_HONG':defaultdict(Decimal)}
        decisions = []
        for line in row.lines:
            qty = quantity(quantities.get(line.line_no, quantities.get(line.product_code, line.qty)))
            if qty > line.qty: raise BusinessError('BAD_QTY','Số thực nhận không được vượt số yêu cầu.')
            condition = conditions.get(line.line_no, line.condition)
            if (qty != line.qty or condition != line.condition) and not note.strip():
                raise BusinessError('REASON_REQUIRED','Phải ghi chú khi nhận thiếu hoặc đổi tình trạng.')
            reason = damage_reasons.get(line.line_no, line.damage_reason or note)
            reason = check_condition(condition, reason)
            entries[condition][line.product_code] += qty
            decisions.append((line,qty,condition,reason))
        defect = None
        if any(entries['HU_HONG'].values()):
            defect = session.scalar(select(Warehouse).where(Warehouse.kind=='LOI',Warehouse.supplier_hold.is_(False),Warehouse.recovered_only.is_(False),Warehouse.active.is_(True)).order_by(Warehouse.code).with_for_update(read=True))
            if defect is None: raise BusinessError('NO_DEFECT_WAREHOUSE','Chưa có Kho Chờ Xử lý đang dùng.',status=409)
        for condition in ('TOT','HU_HONG'):
            lines = [dict(product_code=code,qty=qty) for code,qty in sorted(entries[condition].items()) if qty]
            if not lines: continue
            destination = defect.code if condition=='HU_HONG' else row.to_warehouse_code
            doc = stock_docs.insert(session,user,StockDocIn(kind='CK',warehouse_code=row.warehouse_code,to_warehouse_code=destination,
                doc_date=svc._now().astimezone(service_sla.TZ).date(),reason=f'Trả {row.number}' + (' · Thợ báo hư' if condition=='HU_HONG' else ''),lines=lines),
                _technician_return=condition=='HU_HONG', source_kind='TECHNICIAN_RETURN')
            stock_posting.post_in_tx(session,user,doc.id,doc.version,pending_context={
                l.line_no:dict(source='THO_BAO_HU',group_code='HANG_LOI',source_ref=row.number,reason='; '.join(reason for original,qty,decided,reason in decisions if original.product_code==l.product_code and decided=='HU_HONG' and qty))
                for l in doc.lines} if condition=='HU_HONG' else None)
            if condition=='HU_HONG': row.defect_stock_doc_id=doc.id
            else: row.stock_doc_id=doc.id
        for line,qty,condition,reason in decisions:
            line.received_qty=qty;line.received_condition=condition;line.received_damage_reason=reason
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


def check_condition(condition, reason):
    if condition not in ('TOT','HU_HONG'): raise BusinessError('BAD_CONDITION','Tình trạng hàng chưa hợp lệ.')
    reason = str(reason or '').strip()
    if condition=='HU_HONG' and len(reason)<3:
        raise BusinessError('DAMAGE_REASON_REQUIRED','Hàng hư hỏng phải ghi lý do ít nhất 3 ký tự.')
    return reason if condition=='HU_HONG' else ''


@contextmanager
def photo_transaction(session):
    photos=[]
    try:
        with _transaction(session):
            session.info['return_photos']=photos
            yield
    except Exception:
        from .attachments import _root
        for path in photos: (_root()/path).unlink(missing_ok=True)
        raise
    finally: session.info.pop('return_photos',None)


def prepare(session, own, entries):
    grouped = defaultdict(dict);totals=defaultdict(Decimal)
    for entry in entries:
        qty=quantity(entry['qty'])
        if not qty: continue
        if own is None: raise BusinessError('NO_TECH_WAREHOUSE','Thợ chưa có kho riêng đang dùng.',status=409)
        code=str(entry['product_code']);dest=str(entry.get('to_warehouse_code') or '')
        condition=entry.get('condition','TOT');reason=check_condition(condition,entry.get('damage_reason',''))
        if not session.scalar(select(Warehouse).where(Warehouse.code==dest,Warehouse.kind=='KHO',Warehouse.active.is_(True)).with_for_update(read=True)):
            raise BusinessError('BAD_WAREHOUSE','Chọn kho công ty đang dùng để nhận hàng trả.')
        if session.get(Product,code) is None: raise BusinessError('BAD_PRODUCT','Không tìm thấy hàng trả.')
        item=grouped[dest].setdefault((code,condition),dict(qty=Decimal(0),reason=[],photos=[]))
        item['qty']+=qty;totals[code]+=qty
        if reason and reason not in item['reason']:item['reason'].append(reason)
        if entry.get('photo'):item['photos'].append(entry['photo'])
    for code in sorted(totals):
        stock=session.scalar(select(StockBalance).where(StockBalance.warehouse_code==own.code,StockBalance.product_code==code).with_for_update())
        available=stock.qty if stock else 0
        if totals[code]>available:
            raise BusinessError('BAD_RETURN_QTY','Số trả vượt số đang giữ.',{'product':code,'available':str(available)},status=409)
    return grouped


def create_requests(session,user,technician_id,own,ticket_id,grouped):
    from . import attachments
    rows=[]
    for dest,items in sorted(grouped.items()):
        row=TechnicianReturn(number=next_number(session,'TK',svc._now().astimezone(service_sla.TZ).date()),ticket_id=ticket_id,
            technician_id=technician_id,warehouse_code=own.code,to_warehouse_code=dest,created_by=user.id,note='',void_reason='')
        row.lines=[TechnicianReturnLine(line_no=i,product_code=code,condition=condition,damage_reason='; '.join(item['reason']),qty=item['qty'])
            for i,((code,condition),item) in enumerate(sorted(items.items()),1)]
        session.add(row);record(session,user,row,'create');rows.append(row)
        for line,item in zip(row.lines,[item for _,item in sorted(items.items())]):
            for photo in item['photos']:
                attachment=attachments.add(session,user,'technician_return_line',f'{row.id}:{line.line_no}','KHAC',photo['filename'],photo['data'],_in_tx=True)
                session.info['return_photos'].append(attachment.path)
    return rows


def create(session,user,entries):
    if 'technician' not in user.roles: raise BusinessError('FORBIDDEN','Chỉ thợ được tạo yêu cầu từ hàng mình giữ.',status=403)
    from .technician_stock import own_warehouse
    with photo_transaction(session):
        own=own_warehouse(session,user,lock=True)
        grouped=prepare(session,own,entries)
        if not grouped: raise BusinessError('EMPTY_RETURN','Phải chọn ít nhất một hàng để trả kho hoặc báo hư.')
        rows=create_requests(session,user,user.id,own,None,grouped)
    return rows
