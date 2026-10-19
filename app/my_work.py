"""U2: nhóm công việc bằng SELECT, không ghi, không thay quyền hoặc công nợ."""
from datetime import datetime, time, timedelta
from urllib.parse import quote, urlencode

from sqlalchemy import case, func, literal, or_, select

from . import sales_receipts, service_sla, service_tickets, stock_min_levels
from .errors import BusinessError
from .models import Customer, PurchaseOrder, Quote, SalesOrder, SalesReceipt, ServiceTicket, Supplier
from .security import has_role

LIMIT = 20


def _now():
    return datetime.now(service_sla.TZ)


def _available(model):
    column = SalesReceipt.ticket_id if model is ServiceTicket else SalesReceipt.order_id
    paid, pending = sales_receipts._sums()
    reported = select(paid+pending).where(column == model.id).correlate(model).scalar_subquery()
    return func.coalesce(model.receivable, 0)-reported


def _specs(user, today, now):
    director = has_role(user, 'director')
    specs = {}
    def add(key, title, stmt, kind, link):
        specs[key] = dict(key=key, title=title, statement=stmt, kind=kind, link=link)
    def docs(model, name, amount=None):
        return select(model.id, model.number, model.status, name.label('customer'),
            (amount if amount is not None else model.total).label('amount'),
            literal(False).label('overdue'))

    t = ServiceTicket
    overdue = (t.status.in_(service_tickets.OPEN)) & t.due_at.is_not(None) & (t.due_at < now)
    service = select(t.id, t.number, t.status, Customer.name.label('customer'),
        func.coalesce(t.total, 0).label('amount'), overdue.label('overdue')).join(Customer, Customer.code == t.customer_code)
    service = service.order_by(overdue.desc(), case((t.priority=='KHAN', 0), (t.priority=='GAP', 1), else_=2),
                               t.due_at.asc().nulls_last(), t.created_at, t.id)
    start = datetime.combine(today, time.min, service_sla.TZ)
    end = start+timedelta(days=1)
    if has_role(user, 'technician'):
        own = service if director else service.where(t.assignee_id == user.id)
        add('service_assigned', 'Mới giao · chờ nhận', own.where(t.status=='DA_GIAO'), 'service', '/dich-vu')
        # Không để phiếu biến mất sau Nhận việc: lõi yêu cầu hẹn trước khi làm.
        add('service_accepted', 'Đã nhận · cần hẹn khách', own.where(t.status=='DA_NHAN'), 'service', '/dich-vu')
        add('service_appointment_overdue', 'Lịch hẹn từ ngày trước chưa xử lý',
            own.where(t.status=='DA_HEN', t.appointment_at<start), 'service', '/dich-vu')
        add('service_today', 'Hôm nay có hẹn', own.where(t.status=='DA_HEN', t.appointment_at>=start, t.appointment_at<end), 'service', '/dich-vu')
        add('service_working', 'Đang làm và chờ linh kiện', own.where(t.status.in_(('DANG_LAM','CHO_LINH_KIEN'))), 'service', '/dich-vu')
    if has_role(user, 'technician', 'dispatcher'):
        pending = service.where(t.status.in_(('HOAN_THANH','DA_DONG')), t.billing=='THU_TIEN', t.priced_at.is_not(None), _available(t)>0)
        if not director and not has_role(user, 'dispatcher'):
            pending = pending.where(t.assignee_id==user.id)
        add('service_unreported', 'Đã xong · chưa báo thu đủ', pending, 'service', '/dich-vu')
    if has_role(user, 'dispatcher', 'reception'):
        add('service_unassigned', 'Phiếu mới chưa giao thợ', service.where(t.status=='MOI', t.assignee_id.is_(None)), 'service', '/dich-vu')
    if has_role(user, 'dispatcher'):
        add('service_close', 'Dịch vụ chờ đóng phiếu', service.where(t.status=='HOAN_THANH',
            or_(t.billing=='MIEN_PHI', (t.priced_at.is_not(None)) & (_available(t)<=0))), 'service', '/dich-vu')

    if has_role(user, 'sales'):
        q = docs(Quote, Customer.name).join(Customer, Customer.code==Quote.customer_code).where(Quote.status.in_(('NHAP','DA_GUI')))
        if not director:q=q.where(Quote.created_by==user.id)
        add('quotes_open', 'Báo giá chờ kết quả', q.order_by(Quote.created_at, Quote.id), 'quote', '/bao-gia')
        orders = docs(SalesOrder, Customer.name).join(Customer, Customer.code==SalesOrder.customer_code)
        if not director:orders=orders.where(SalesOrder.created_by==user.id)
        add('orders_draft', 'Đơn nháp chưa chốt', orders.where(SalesOrder.status=='NHAP').order_by(SalesOrder.created_at, SalesOrder.id), 'order', '/don-ban')
        add('orders_unreported', 'Đã giao · chưa báo thu đủ', orders.where(SalesOrder.status=='DA_GIAO', _available(SalesOrder)>0)
            .order_by(SalesOrder.confirmed_at, SalesOrder.id), 'order', '/don-ban')
        rejected = select(SalesReceipt.id, SalesReceipt.number, SalesReceipt.status, Customer.name.label('customer'),
            SalesReceipt.amount, literal(False).label('overdue'), SalesReceipt.order_id, SalesReceipt.ticket_id,
            SalesReceipt.reason).outerjoin(SalesOrder, SalesOrder.id==SalesReceipt.order_id).outerjoin(t, t.id==SalesReceipt.ticket_id).join(
                Customer, Customer.code==func.coalesce(SalesOrder.customer_code, t.customer_code)).where(SalesReceipt.status=='TU_CHOI',
                or_((SalesOrder.status.in_(('DA_CHOT','DA_GIAO'))) & (_available(SalesOrder)>0),
                    (literal(has_role(user, 'dispatcher', 'reception', 'technician'))) &
                    (t.status.in_(('HOAN_THANH','DA_DONG'))) & (_available(t)>0)))
        if not director:rejected=rejected.where(SalesReceipt.reported_by==user.id)
        add('receipts_rejected', 'Báo thu bị từ chối · cần làm lại', rejected.order_by(SalesReceipt.reported_at, SalesReceipt.id), 'receipt', '/bao-thu')
    if has_role(user, 'sales', 'warehouse'):
        waiting = docs(SalesOrder, Customer.name).join(Customer, Customer.code==SalesOrder.customer_code).where(SalesOrder.status=='DA_CHOT')
        if not director and not has_role(user, 'warehouse'):waiting=waiting.where(SalesOrder.created_by==user.id)
        add('orders_waiting', 'Đơn đã chốt · chờ giao hàng', waiting.order_by(SalesOrder.confirmed_at, SalesOrder.id), 'order', '/don-ban')
    if has_role(user, 'warehouse'):
        orders = docs(PurchaseOrder, Supplier.name).join(Supplier, Supplier.code==PurchaseOrder.supplier_code).where(
            PurchaseOrder.status.in_(('DA_DAT','NHAN_MOT_PHAN'))).order_by(PurchaseOrder.expected_date.asc().nulls_last(), PurchaseOrder.id)
        add('purchases_waiting', 'Đơn mua chờ nhận hàng', orders, 'purchase', '/don-mua')
        add('stock_low', 'Hàng dưới mức tối thiểu', stock_min_levels.low_stock_query(), 'stock', '/ton-kho?canh-bao=1')
    if has_role(user, 'finance'):
        pending = select(SalesReceipt.id, SalesReceipt.number, SalesReceipt.status, Customer.name.label('customer'),
            SalesReceipt.amount, literal(False).label('overdue')).outerjoin(SalesOrder, SalesOrder.id==SalesReceipt.order_id).outerjoin(
                t, t.id==SalesReceipt.ticket_id).join(Customer, Customer.code==func.coalesce(SalesOrder.customer_code, t.customer_code)).where(
                    SalesReceipt.status=='CHO_XAC_NHAN').order_by(SalesReceipt.reported_at, SalesReceipt.id)
        add('receipts_pending', 'Báo thu chờ xác nhận', pending, 'receipt', '/bao-thu')
    return specs


def _group(session, spec, page=1):
    stmt = spec['statement']
    count = int(session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())))
    rows = []
    for record in session.execute(stmt.offset((page-1)*LIMIT).limit(LIMIT)).mappings():
        row = dict(record)
        kind = spec['kind']
        if kind=='stock':
            row.update(number=row['product_code'], customer=row['name'], amount=None, status='SAP_HET', overdue=row['qty']<=0,
                link='/the-kho/'+quote(row['warehouse_code'],safe='')+'/'+quote(row['product_code'],safe=''))
        else:
            prefix = {'service':'/dich-vu', 'quote':'/bao-gia', 'order':'/don-ban', 'purchase':'/don-mua', 'receipt':'/bao-thu'}[kind]
            row['link'] = f"{prefix}/{row['id']}"
            if spec['key']=='receipts_rejected':
                row['link'] = f"/don-ban/{row['order_id']}#thu-tien" if row['order_id'] else f"/dich-vu/{row['ticket_id']}#thu-tien"
            row['amount'] = int(row['amount'] or 0)
        row['kind'] = kind
        rows.append(row)
    return dict(key=spec['key'], title=spec['title'], count=count, rows=rows,
        link='/viec-cua-toi?'+urlencode({'nhom':spec['key']}), list_link=spec['link'])


def collect(session, user, today=None):
    with session.no_autoflush:
        now = _now()
        return [_group(session, spec) for spec in _specs(user, today or now.date(), now).values()]


def filtered(session, user, key, page=1, today=None):
    with session.no_autoflush:
        now = _now()
        spec = _specs(user, today or now.date(), now).get(key)
        if spec is None:
            raise BusinessError('FORBIDDEN', 'Bạn không có quyền vào nhóm việc này.', status=403)
        return _group(session, spec, page)
