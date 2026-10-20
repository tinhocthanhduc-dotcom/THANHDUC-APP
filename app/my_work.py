"""U2: nhóm công việc bằng SELECT, không ghi, không thay quyền hoặc công nợ."""
from datetime import datetime, time, timedelta
from urllib.parse import quote, urlencode

from sqlalchemy import case, func, literal, or_, select

from . import equipment_custody, sales_receipts, sales_orders, service_sla, service_tickets, stock_min_levels
from .errors import BusinessError
from .models import Attachment, Equipment, EquipmentCustody, Customer, PartRequest, PurchaseOrder, Quote, SalesOrder, SalesReceipt, SalesRefund, ServiceTicket, StockReservation, Warehouse, Supplier, TechnicianReturn, TechnicianReturnLine
from .security import has_role

LIMIT = 20


def _now():
    return datetime.now(service_sla.TZ)


def _available(model):
    rows=sales_receipts.totals(model)
    reported=select(rows.c.paid+rows.c.pending).where(rows.c.parent_id==model.id).correlate(model).scalar_subquery()
    return func.coalesce(model.receivable,0)-func.coalesce(reported,0)


def _specs(user, today, now):
    director = has_role(user, 'director')
    specs = {}
    def add(key, title, stmt, kind, link):
        specs[key] = dict(key=key, title=title, statement=stmt, kind=kind, link=link)
    def docs(model, name, amount=None, overdue=None):
        return select(model.id, model.number, model.status, name.label('customer'),
            (amount if amount is not None else model.total).label('amount'),
            (literal(False) if overdue is None else overdue).label('overdue'))

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
        add('service_accepted', 'Đã nhận · hẹn / đến khách', own.where(t.status=='DA_NHAN'), 'service', '/dich-vu')
        add('service_appointment_overdue', 'Lịch hẹn từ ngày trước chưa xử lý',
            own.where(t.status=='DA_HEN', t.appointment_at<start), 'service', '/dich-vu')
        add('service_today', 'Hôm nay có hẹn', own.where(t.status=='DA_HEN', t.appointment_at>=start, t.appointment_at<end), 'service', '/dich-vu')
        ready = (t.status == 'CHO_LINH_KIEN') & select(PartRequest.id).where(PartRequest.ticket_id == t.id).exists() & ~select(PartRequest.id).where(PartRequest.ticket_id == t.id, PartRequest.status == 'CHO_CAP').exists()
        add('service_parts_ready', 'Đã có linh kiện', own.where(ready), 'service', '/dich-vu')
        add('service_paused', 'Tạm dừng · cần tiếp tục', own.where(t.status == 'TAM_DUNG'), 'service', '/dich-vu')
        add('service_working', 'Đang làm và chờ linh kiện', own.where(t.status.in_(('DANG_LAM','CHO_LINH_KIEN')), ~ready), 'service', '/dich-vu')
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

    if has_role(user,'technician'):
        eq=Equipment
        cycle=equipment_custody.cycle_starts()
        holding=select(t.id,(t.number+' · '+eq.serial).label('number'),literal('THO_GIU').label('status'),
            Customer.name.label('customer'),literal(0).label('amount'),literal(False).label('overdue')).join(
                eq,eq.custody_ticket_id==t.id).join(Customer,Customer.code==t.customer_code).join(cycle,cycle.c.serial==eq.serial).where(
                eq.custody_location=='THO_GIU')
        if not director:holding=holding.where(eq.custody_holder_id==user.id)
        add('machines_held','Máy tôi đang giữ',holding.order_by(cycle.c.started,eq.serial),'custody','/viec-cua-toi?nhom=machines_held')
    if has_role(user,'technician','warehouse'):
        incoming=select(t.id,(t.number+' · '+EquipmentCustody.serial).label('number'),EquipmentCustody.status,
            Customer.name.label('customer'),literal(0).label('amount'),literal(False).label('overdue')).join(
                EquipmentCustody,EquipmentCustody.ticket_id==t.id).join(Customer,Customer.code==t.customer_code).where(
                EquipmentCustody.status=='CHO_NHAN').order_by(EquipmentCustody.created_at,EquipmentCustody.id)
        if has_role(user,'technician'):
            tech=incoming.where(EquipmentCustody.location=='THO_GIU')
            if not director:tech=tech.where(EquipmentCustody.holder_id==user.id)
            add('machines_pending','Máy chờ tôi nhận',tech,'custody','/viec-cua-toi?nhom=machines_pending')
        if has_role(user,'warehouse'):
            add('machines_warehouse','Máy chờ nhận vào kho kỹ thuật',incoming.where(EquipmentCustody.location=='KHO_KY_THUAT'),
                'custody','/may-dang-giu')

    sales_deliveries=docs(SalesOrder,Customer.name,SalesOrder.total-sales_orders.returned_total_sql()-(
        func.coalesce(SalesOrder.receivable,0)-_available(SalesOrder))).join(Customer,Customer.code==SalesOrder.customer_code).where(
        SalesOrder.fulfillment=='THO_GIAO',SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN')))
    tech_holds=select(StockReservation.id).join(Warehouse).where(StockReservation.order_id==SalesOrder.id,StockReservation.status=='GIU',Warehouse.kind=='THO').exists()
    if has_role(user,'technician'):
        add('technician_deliveries','Đơn bán cần giao',sales_deliveries.where(SalesOrder.delivery_assignee_id==user.id).add_columns(
            SalesOrder.delivery_address.label('address'),case((tech_holds,'Giao hàng / Ghi không giao được'),else_='Chờ kho bàn giao').label('next_label')).order_by(SalesOrder.promised_date,SalesOrder.id),'delivery','/viec-cua-toi?nhom=technician_deliveries')
    if has_role(user,'warehouse'):
        add('delivery_handover','Đơn chờ bàn giao cho thợ',sales_deliveries.where(SalesOrder.delivery_assignee_id.is_not(None),select(StockReservation.id).join(Warehouse).where(StockReservation.order_id==SalesOrder.id,StockReservation.status=='GIU',Warehouse.kind=='KHO').exists()).order_by(SalesOrder.id),'delivery','/viec-cua-toi?nhom=delivery_handover')
    if has_role(user, 'sales'):
        q = docs(Quote, Customer.name).join(Customer, Customer.code==Quote.customer_code).where(Quote.status.in_(('NHAP','DA_GUI')))
        if not director:q=q.where(Quote.created_by==user.id)
        add('quotes_open', 'Báo giá chờ kết quả', q.order_by(Quote.created_at, Quote.id), 'quote', '/bao-gia')
        orders = docs(SalesOrder, Customer.name).join(Customer, Customer.code==SalesOrder.customer_code)
        if not director:orders=orders.where(SalesOrder.created_by==user.id)
        add('orders_draft', 'Đơn nháp chưa chốt', orders.where(SalesOrder.status=='NHAP').order_by(SalesOrder.created_at, SalesOrder.id), 'order', '/don-ban')
        add('orders_unreported', 'Đã giao · chưa báo thu đủ', orders.where(SalesOrder.status.in_(('GIAO_MOT_PHAN','DA_GIAO')), _available(SalesOrder)>0)
            .order_by(SalesOrder.confirmed_at, SalesOrder.id), 'order', '/don-ban')
        deposit_gap=func.least(SalesOrder.deposit_amount,SalesOrder.total-sales_orders.returned_total_sql())-(func.coalesce(SalesOrder.receivable,0)-_available(SalesOrder))
        deposits=docs(SalesOrder,Customer.name,deposit_gap).join(Customer,Customer.code==SalesOrder.customer_code).where(
            SalesOrder.payment_term=='DAT_COC',SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN','DA_GIAO')),deposit_gap>0)
        if not director:deposits=deposits.where(SalesOrder.created_by==user.id)
        add('orders_deposit','Chưa thu cọc',deposits.order_by(SalesOrder.confirmed_at,SalesOrder.id),'order','/don-ban')
        rejected = select(SalesReceipt.id, SalesReceipt.number, SalesReceipt.status, Customer.name.label('customer'),
            SalesReceipt.amount, literal(False).label('overdue'), SalesReceipt.order_id, SalesReceipt.ticket_id,
            SalesReceipt.reason).outerjoin(SalesOrder, SalesOrder.id==SalesReceipt.order_id).outerjoin(t, t.id==SalesReceipt.ticket_id).join(
                Customer, Customer.code==func.coalesce(SalesOrder.customer_code, t.customer_code)).where(SalesReceipt.status=='TU_CHOI',
                or_((SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN','DA_GIAO'))) & (_available(SalesOrder)>0),
                    (literal(has_role(user, 'dispatcher', 'reception', 'technician'))) &
                    (t.status.in_(('HOAN_THANH','DA_DONG'))) & (_available(t)>0)))
        if not director:rejected=rejected.where(SalesReceipt.reported_by==user.id)
        add('receipts_rejected', 'Báo thu bị từ chối · cần làm lại', rejected.order_by(SalesReceipt.reported_at, SalesReceipt.id), 'receipt', '/bao-thu')
    if has_role(user, 'sales', 'warehouse'):
        waiting = docs(SalesOrder, Customer.name).join(Customer, Customer.code==SalesOrder.customer_code).where(SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN')))
        if not director and not has_role(user, 'warehouse'):waiting=waiting.where(SalesOrder.created_by==user.id)
        add('orders_waiting', 'Đơn đã chốt · chờ giao hàng', waiting.order_by(SalesOrder.confirmed_at, SalesOrder.id), 'warehouse_order' if has_role(user,'warehouse') else 'order', '/don-ban')
        pickups=docs(SalesOrder,Customer.name,overdue=literal(True)).join(Customer,Customer.code==SalesOrder.customer_code).where(
            SalesOrder.fulfillment=='KHACH_LAY_SAU',SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN')),SalesOrder.promised_date<today)
        if not director and not has_role(user,'warehouse'):pickups=pickups.where(SalesOrder.created_by==user.id)
        add('orders_pickup_late','Khách hẹn lấy quá ngày',pickups.order_by(SalesOrder.promised_date,SalesOrder.id),'order','/don-ban')
    if has_role(user, 'warehouse'):
        requests = select(PartRequest.id, PartRequest.number, PartRequest.status, Customer.name.label('customer'),
            literal(0).label('amount'), literal(False).label('overdue')).join(t, t.id == PartRequest.ticket_id).join(
                Customer, Customer.code == t.customer_code).where(PartRequest.status == 'CHO_CAP').order_by(PartRequest.created_at, PartRequest.id)
        add('parts_pending', 'Yêu cầu linh kiện chờ cấp', requests, 'part', '/yeu-cau-linh-kien')
        returning = select(TechnicianReturn.id, TechnicianReturn.number, TechnicianReturn.status, func.coalesce(Customer.name,'Trả kho không gắn phiếu').label('customer'),
            literal(0).label('amount'), literal(False).label('overdue')).outerjoin(t,t.id==TechnicianReturn.ticket_id).outerjoin(
                Customer,Customer.code==t.customer_code).where(TechnicianReturn.status=='CHO_NHAN').order_by(TechnicianReturn.created_at,TechnicianReturn.id)
        damaged=select(TechnicianReturnLine.return_id).where(
            TechnicianReturnLine.return_id==TechnicianReturn.id,
            TechnicianReturnLine.condition=='HU_HONG').exists()
        returning=returning.add_columns(damaged.label('damaged'))
        add('technician_returns','Thợ trả hàng',returning,'technician_return','/viec-cua-toi?nhom=technician_returns')
        orders = docs(PurchaseOrder, Supplier.name).join(Supplier, Supplier.code==PurchaseOrder.supplier_code).where(
            PurchaseOrder.status.in_(('DA_DAT','NHAN_MOT_PHAN'))).order_by(PurchaseOrder.expected_date.asc().nulls_last(), PurchaseOrder.id)
        add('purchases_waiting', 'Đơn mua chờ nhận hàng', orders, 'purchase', '/don-mua')
        add('stock_low', 'Hàng dưới mức tối thiểu', stock_min_levels.low_stock_query(), 'stock', '/ton-kho?canh-bao=1')
    if has_role(user, 'finance'):
        refunds=select(SalesRefund.id,SalesRefund.number,SalesRefund.status,Customer.name.label('customer'),SalesRefund.amount,literal(False).label('overdue')).join(
            SalesOrder,SalesOrder.id==SalesRefund.order_id).join(Customer,Customer.code==SalesOrder.customer_code).where(SalesRefund.status=='CHO_DUYET').order_by(SalesRefund.id)
        add('refunds_pending','Hoàn tiền chờ duyệt',refunds,'refund','/bao-thu?tab=hoan-tien')
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
            prefix = {'service':'/dich-vu', 'quote':'/bao-gia', 'order':'/don-ban', 'warehouse_order':'/kho/giao', 'purchase':'/don-mua', 'receipt':'/bao-thu', 'part':'/yeu-cau-linh-kien', 'custody':'/giu-may', 'delivery':'/giao-don', 'refund':'/hoan-tien', 'technician_return':'/tho-tra-hang'}[kind]
            row['link'] = f"{prefix}/{row['id']}"
            if kind=='purchase':row['link']=f"/don-mua/{row['id']}/nhan#nhan-hang"
            if kind=='refund':row['link']=f"/bao-thu?tab=hoan-tien#refund-{row['id']}"
            if spec['key']=='orders_deposit':row['link']=f"/don-ban/{row['id']}?coc=1#thu-tien"
            if spec['key']=='receipts_rejected':
                row['link'] = f"/don-ban/{row['order_id']}#thu-tien" if row['order_id'] else f"/dich-vu/{row['ticket_id']}#thu-tien"
            if kind == 'part':row['link'] = f"/yeu-cau-linh-kien#yc-{row['id']}"
            row['amount'] = int(row['amount'] or 0)
        row['kind'] = 'order' if kind=='warehouse_order' else kind
        rows.append(row)
    if spec['kind']=='technician_return' and rows:
        damaged=list(session.scalars(select(TechnicianReturnLine).where(TechnicianReturnLine.return_id.in_([r['id'] for r in rows]),TechnicianReturnLine.condition=='HU_HONG')))
        keys=[f'{line.return_id}:{line.line_no}' for line in damaged]
        photos=list(session.scalars(select(Attachment).where(Attachment.entity=='technician_return_line',Attachment.entity_id.in_(keys),Attachment.hidden.is_(False)).order_by(Attachment.id)))
        for row in rows:
            matching=[line for line in damaged if line.return_id==row['id']]
            row['damage_reason']='; '.join(line.damage_reason for line in matching)
            row['damage_photos']=[photo.id for photo in photos if photo.entity_id in [f"{row['id']}:{line.line_no}" for line in matching]]
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
