"""U2: nhóm công việc bằng SELECT, không ghi, không thay quyền hoặc công nợ."""
from datetime import datetime, time, timedelta
from urllib.parse import quote, urlencode

from sqlalchemy import Select, String, case, func, literal, or_, select, union_all

from . import equipment_custody, sales_receipts, sales_orders, service_sla, service_tickets, stock_min_levels, supplier_debts, service_workshop
from .config import get_settings
from .errors import BusinessError
from .models import AuditLog, PendingDisposition, PendingItem, StockBalance, StockMinLevel, Product, Attachment, Equipment, EquipmentCustody, Customer, PartRequest, PurchaseOrder, PurchaseOrderLine, PurchaseReceipt, Quote, SalesDeliveryAttempt, SalesOrder, SalesOrderLine, SalesReceipt, SalesRefund, ServiceTicket, ServiceRecoveredPart, StockReservation, Warehouse, Supplier, SupplierOpeningDebt, TechnicianReturn, TechnicianReturnLine, ServiceStep, User
from sqlalchemy import String
from .models import ProductionOrder
from .security import has_role

LIMIT = 20
# Báo giá đã đồng ý quá số ngày này mà chưa có đơn thì tô đỏ.
ACCEPTED_ALERT_DAYS = 2


def _now():
    return datetime.now(service_sla.TZ)


def _available(model):
    rows=sales_receipts.totals(model)
    reported=select(rows.c.paid+rows.c.pending).where(rows.c.parent_id==model.id).correlate(model).scalar_subquery()
    return func.coalesce(model.receivable,0)-func.coalesce(reported,0)


def _supplier_debt_dates(deductions):
    # Opening debt always comes first, even when its date is newer than a receipt.
    charges = union_all(
        select(SupplierOpeningDebt.supplier_code, literal(0).label('source_order'),
            SupplierOpeningDebt.as_of.label('debt_date'), literal(0).label('debt_id'),
            SupplierOpeningDebt.amount.label('amount')),
        select(PurchaseOrder.supplier_code, literal(1), PurchaseReceipt.receipt_date,
            PurchaseReceipt.id, PurchaseReceipt.total).join(PurchaseReceipt,
                PurchaseReceipt.order_id == PurchaseOrder.id).where(PurchaseReceipt.status == 'DA_GHI'),
    ).subquery()
    running = select(charges, func.sum(charges.c.amount).over(
        partition_by=charges.c.supplier_code,
        order_by=(charges.c.source_order, charges.c.debt_date, charges.c.debt_id),
        rows=(None, 0)).label('cumulative')).subquery()
    unpaid = select(running.c.supplier_code, running.c.debt_date, running.c.debt_id,
        func.row_number().over(partition_by=running.c.supplier_code,
            order_by=(running.c.source_order, running.c.debt_date, running.c.debt_id)).label('position'))
    unpaid = unpaid.outerjoin(deductions, deductions.c.supplier_code == running.c.supplier_code).where(
        running.c.cumulative > func.coalesce(deductions.c.amount, 0)).subquery()
    return select(unpaid.c.supplier_code, unpaid.c.debt_date.label('oldest_debt_date'),
        unpaid.c.debt_id.label('oldest_debt_id')).where(
        unpaid.c.position == 1).subquery()


def _specs(user, today, now):
    director = has_role(user, 'director')
    # Kho phụ trách chỉ lọc hàng đợi của thủ kho; quyền thao tác không đổi.
    kho = list(user.warehouse_codes or []) if has_role(user, 'warehouse') and not director else []
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
        func.coalesce(t.total, 0).label('amount'), overdue.label('overdue'),
        # Chỉ để màn Việc của tôi ghi "Trễ x giờ" / "Hẹn hh:mm"; không đổi điều kiện lọc.
        t.due_at.label('due_at'), t.appointment_at.label('appointment_at')).join(Customer, Customer.code == t.customer_code)
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
        add('service_paused', 'Tạm dừng · cần tiếp tục', own.where(t.status == 'TAM_DUNG',t.hold_reason.not_in(('SUA_XONG_CHO_TRA','CHO_SUA_XUONG'))), 'service', '/dich-vu')
        add('service_working', 'Đang làm và chờ linh kiện', own.where(t.status.in_(('DANG_LAM','CHO_LINH_KIEN')), ~ready), 'service', '/dich-vu')
    if has_role(user, 'technician', 'dispatcher'):
        pending = service.where(t.status.in_(('HOAN_THANH','DA_DONG')), t.billing=='THU_TIEN', t.priced_at.is_not(None), _available(t)>0)
        if not director and not has_role(user, 'dispatcher'):
            pending = pending.where(t.assignee_id==user.id)
        add('service_unreported', 'Đã xong · chưa báo thu đủ', pending, 'service', '/dich-vu')
    if has_role(user, 'dispatcher', 'reception'):
        add('service_unassigned', 'Phiếu mới chưa giao thợ', service.where(t.status=='MOI', t.assignee_id.is_(None)), 'service', '/dich-vu')
    if has_role(user, 'dispatcher'):
        add('service_workshop','Máy chờ sửa ở xưởng',service_workshop.queue_query(now),'service','/dich-vu')
        add('service_return_ready','Máy sửa xong chờ trả',service.where(t.status=='TAM_DUNG',t.hold_reason=='SUA_XONG_CHO_TRA'), 'service','/dich-vu')
        recent=select(ServiceStep.ticket_id).join(User,User.id==ServiceStep.actor_id).where(ServiceStep.action=='reassign',ServiceStep.at>=now-timedelta(days=7),User.roles.contains(['technician']),ServiceStep.note.startswith('Thợ '))
        add('service_transferred','Phiếu vừa chuyển thợ',service.where(t.id.in_(recent)), 'service','/dich-vu')
        recovered_count=select(func.count()).where(ServiceRecoveredPart.ticket_id==t.id,
            ServiceRecoveredPart.status=='CHO_NOP').correlate(t).scalar_subquery()
        recovered_holders=select(func.string_agg(func.distinct(User.full_name),', ')).join(
            ServiceRecoveredPart,ServiceRecoveredPart.holder_id==User.id).where(
            ServiceRecoveredPart.ticket_id==t.id,ServiceRecoveredPart.status=='CHO_NOP').correlate(t).scalar_subquery()
        add('service_close', 'Dịch vụ chờ nộp linh kiện thu hồi', service.where(t.status=='HOAN_THANH',recovered_count>0)
            .add_columns(recovered_count.label('recovered_pending'),recovered_holders.label('recovered_holders')), 'service', '/linh-kien-thu-hoi')

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
    last_fail=select(SalesDeliveryAttempt.reason).where(SalesDeliveryAttempt.order_id==SalesOrder.id).order_by(
        SalesDeliveryAttempt.id.desc()).limit(1).correlate(SalesOrder).scalar_subquery()
    tech_holds=select(StockReservation.id).join(Warehouse).where(StockReservation.order_id==SalesOrder.id,StockReservation.status=='GIU',Warehouse.kind=='THO').exists()
    if has_role(user,'technician'):
        add('technician_deliveries','Đơn bán cần giao',sales_deliveries.where(SalesOrder.delivery_assignee_id==user.id).add_columns(
            SalesOrder.delivery_address.label('address'),case((last_fail=='DE_NGHI_HUY','Đã đề nghị hủy · chờ bán hàng'),(tech_holds,'Giao hàng / Ghi không giao được'),else_='Chờ kho bàn giao').label('next_label')).order_by(SalesOrder.promised_date,SalesOrder.id),'delivery','/viec-cua-toi?nhom=technician_deliveries')
    if has_role(user,'warehouse'):
        add('delivery_handover','Đơn chờ bàn giao cho thợ',sales_deliveries.where(SalesOrder.delivery_assignee_id.is_not(None),select(StockReservation.id).join(Warehouse).where(StockReservation.order_id==SalesOrder.id,StockReservation.status=='GIU',Warehouse.kind=='KHO',*([StockReservation.warehouse_code.in_(kho)] if kho else [])).exists()).order_by(SalesOrder.id),'delivery','/viec-cua-toi?nhom=delivery_handover')
    if has_role(user, 'sales'):
        q = docs(Quote, Customer.name).join(Customer, Customer.code==Quote.customer_code).where(Quote.status.in_(('NHAP','DA_GUI')))
        if not director:q=q.where(Quote.created_by==user.id)
        # Khách đã đồng ý nhưng chưa có đơn còn hiệu lực: báo giá không được rời hàng đợi.
        accepted_at=func.coalesce(select(func.max(AuditLog.at)).where(AuditLog.entity=='quote',AuditLog.action=='quote.accept',
            AuditLog.entity_id==func.cast(Quote.id,String)).correlate(Quote).scalar_subquery(),Quote.created_at)
        has_order=select(SalesOrder.id).where(SalesOrder.quote_id==Quote.id,SalesOrder.status!='DA_HUY').exists()
        accepted=docs(Quote,Customer.name,overdue=accepted_at<now-timedelta(days=ACCEPTED_ALERT_DAYS)).join(
            Customer,Customer.code==Quote.customer_code).where(Quote.status=='CHAP_NHAN',~has_order)
        if not director:accepted=accepted.where(Quote.created_by==user.id)
        add('quotes_accepted','Báo giá đã đồng ý · chưa tạo đơn',accepted.order_by(accepted_at,Quote.id),'quote','/bao-gia')
        add('quotes_open', 'Báo giá chờ kết quả', q.order_by(Quote.created_at, Quote.id), 'quote', '/bao-gia')
        orders = docs(SalesOrder, Customer.name).join(Customer, Customer.code==SalesOrder.customer_code)
        if not director:orders=orders.where(SalesOrder.created_by==user.id)
        add('orders_cancel_requested', 'Thợ đề nghị hủy đơn', orders.where(SalesOrder.status=='DA_CHOT',last_fail=='DE_NGHI_HUY')
            .order_by(SalesOrder.id), 'order', '/don-ban')
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
        line_kho=lambda *codes:select(SalesOrderLine.order_id).where(SalesOrderLine.order_id==SalesOrder.id,SalesOrderLine.warehouse_code.is_not(None),*codes).exists()
        # Đơn chỉ có dòng không lấy hàng kho vẫn hiện cho mọi thủ kho.
        in_kho=or_(line_kho(SalesOrderLine.warehouse_code.in_(kho)),~line_kho())
        waiting = docs(SalesOrder, Customer.name).join(Customer, Customer.code==SalesOrder.customer_code).where(SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN')))
        if not director and not has_role(user, 'warehouse'):waiting=waiting.where(SalesOrder.created_by==user.id)
        if kho:waiting=waiting.where(or_(SalesOrder.created_by==user.id,in_kho))
        add('orders_waiting', 'Đơn đã chốt · chờ giao hàng', waiting.order_by(SalesOrder.confirmed_at, SalesOrder.id), 'warehouse_order' if has_role(user,'warehouse') else 'order', '/don-ban')
        pickups=docs(SalesOrder,Customer.name,overdue=literal(True)).join(Customer,Customer.code==SalesOrder.customer_code).where(
            SalesOrder.fulfillment=='KHACH_LAY_SAU',SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN')),SalesOrder.promised_date<today)
        if not director and not has_role(user,'warehouse'):pickups=pickups.where(SalesOrder.created_by==user.id)
        if kho:pickups=pickups.where(or_(SalesOrder.created_by==user.id,in_kho))
        add('orders_pickup_late','Khách hẹn lấy quá ngày',pickups.order_by(SalesOrder.promised_date,SalesOrder.id),'order','/don-ban')
    if has_role(user, 'warehouse'):
        requests = select(PartRequest.id, PartRequest.number, PartRequest.status, func.coalesce(Customer.name, 'Thợ xin mang theo').label('customer'),
            literal(0).label('amount'), literal(False).label('overdue')).outerjoin(t, t.id == PartRequest.ticket_id).outerjoin(
                Customer, Customer.code == t.customer_code).where(PartRequest.status == 'CHO_CAP').order_by(PartRequest.created_at, PartRequest.id)
        if kho:
            # Chờ cấp chưa gắn kho: hiện cho kho đang còn hàng; không kho nào còn thì hiện cho mọi thủ kho.
            stocked=lambda codes:select(StockBalance.product_code).join(Warehouse,Warehouse.code==StockBalance.warehouse_code).where(
                StockBalance.product_code==PartRequest.product_code,StockBalance.qty>0,Warehouse.kind=='KHO',*codes).exists()
            requests=requests.where(or_(stocked([StockBalance.warehouse_code.in_(kho)]),~stocked([])))
        add('parts_pending', 'Yêu cầu linh kiện chờ cấp', requests, 'part', '/yeu-cau-linh-kien')
        returning = select(TechnicianReturn.id, TechnicianReturn.number, TechnicianReturn.status, func.coalesce(Customer.name,'Trả kho không gắn phiếu').label('customer'),
            literal(0).label('amount'), literal(False).label('overdue')).outerjoin(t,t.id==TechnicianReturn.ticket_id).outerjoin(
                Customer,Customer.code==t.customer_code).where(TechnicianReturn.status=='CHO_NHAN').order_by(TechnicianReturn.created_at,TechnicianReturn.id)
        damaged=select(TechnicianReturnLine.return_id).where(
            TechnicianReturnLine.return_id==TechnicianReturn.id,
            TechnicianReturnLine.condition=='HU_HONG').exists()
        returning=returning.add_columns(damaged.label('damaged'))
        if kho:returning=returning.where(TechnicianReturn.to_warehouse_code.in_(kho))
        add('technician_returns','Thợ trả hàng',returning,'technician_return','/viec-cua-toi?nhom=technician_returns')
        orders = docs(PurchaseOrder, Supplier.name).join(Supplier, Supplier.code==PurchaseOrder.supplier_code).where(
            PurchaseOrder.status.in_(('DA_DAT','NHAN_MOT_PHAN'))).order_by(PurchaseOrder.expected_date.asc().nulls_last(), PurchaseOrder.id)
        low=stock_min_levels.low_stock_query()
        if kho:
            orders=orders.where(select(PurchaseOrderLine.order_id).where(PurchaseOrderLine.order_id==PurchaseOrder.id,PurchaseOrderLine.warehouse_code.in_(kho)).exists())
            low=low.where(StockMinLevel.warehouse_code.in_(kho))
        add('purchases_waiting', 'Đơn mua chờ nhận hàng', orders, 'purchase', '/don-mua')
        add('stock_low', 'Hàng dưới mức tối thiểu', low, 'stock', '/ton-kho?canh-bao=1')
    if has_role(user, 'finance'):
        deductions = supplier_debts._deductions().subquery()
        balances = supplier_debts._balances(deductions).subquery()
        unpaid_dates = _supplier_debt_dates(deductions)
        oldest = unpaid_dates.c.oldest_debt_date
        debt_overdue = func.coalesce(oldest < today - timedelta(days=get_settings().supplier_debt_alert_days), False)
        debt_order = (debt_overdue.desc(), oldest.asc().nulls_last(), balances.c.supplier_code)
        debts = select(func.row_number().over(order_by=debt_order).label('id'),
            balances.c.supplier_code.label('number'), literal('CON_NO').label('status'),
            balances.c.name.label('customer'), balances.c.remaining.label('amount'),
            debt_overdue.label('overdue'), oldest).select_from(balances).outerjoin(unpaid_dates,
                unpaid_dates.c.supplier_code == balances.c.supplier_code).where(
                balances.c.remaining > 0).order_by(*debt_order)
        add('supplier_debts_due', 'Nợ NCC cần trả', debts, 'supplier_debt', '/cong-no-ncc')
        specs['supplier_debts_due']['page_size'] = 25
        refunds=select(SalesRefund.id,SalesRefund.number,SalesRefund.status,Customer.name.label('customer'),SalesRefund.amount,literal(False).label('overdue')).join(
            SalesOrder,SalesOrder.id==SalesRefund.order_id).join(Customer,Customer.code==SalesOrder.customer_code).where(SalesRefund.status=='CHO_DUYET').order_by(SalesRefund.id)
        add('refunds_pending','Hoàn tiền chờ duyệt',refunds,'refund','/bao-thu?tab=hoan-tien')
        pending = select(SalesReceipt.id, SalesReceipt.number, SalesReceipt.status, Customer.name.label('customer'),
            SalesReceipt.amount, literal(False).label('overdue')).outerjoin(SalesOrder, SalesOrder.id==SalesReceipt.order_id).outerjoin(
                t, t.id==SalesReceipt.ticket_id).join(Customer, Customer.code==func.coalesce(SalesOrder.customer_code, t.customer_code)).where(
                    SalesReceipt.status=='CHO_XAC_NHAN').order_by(SalesReceipt.reported_at, SalesReceipt.id)
        add('receipts_pending', 'Báo thu chờ xác nhận', pending, 'receipt', '/bao-thu')
    if director:
        requests=select(PendingDisposition.id,PendingDisposition.number,PendingDisposition.status,Product.name.label('customer'),
            (PendingDisposition.qty*func.coalesce(StockBalance.avg_cost,0)).label('amount'),literal(False).label('overdue')).join(
            PendingItem,PendingItem.id==PendingDisposition.item_id).join(Product,Product.code==PendingItem.product_code).outerjoin(
            StockBalance,(StockBalance.warehouse_code==PendingItem.warehouse_code)&(StockBalance.product_code==PendingItem.product_code)).where(
            PendingDisposition.kind=='HUY',PendingDisposition.status=='CHO_DUYET').order_by(PendingDisposition.requested_at,PendingDisposition.id)
        add('pending_destruction','Hủy hàng chờ duyệt',requests,'pending_disposition','/cho-xu-ly/duyet-huy')
    if has_role(user,'finance'):
        pending_returns=select(PendingDisposition.id,PendingDisposition.number,PendingDisposition.status,Product.name.label('customer'),PendingDisposition.total.label('amount'),literal(False).label('overdue')).join(PendingItem,PendingItem.id==PendingDisposition.item_id).join(Product,Product.code==PendingItem.product_code).where(PendingDisposition.kind=='TRA_NCC',PendingDisposition.status=='CHO_DUYET').order_by(PendingDisposition.requested_at,PendingDisposition.id)
        add('pending_supplier_returns','Trả hàng lỗi NCC chờ duyệt',pending_returns,'pending_supplier_return','/cho-xu-ly/duyet-tra-ncc')
    sx = ProductionOrder
    production_docs = select(sx.id, sx.number, sx.status,
        (literal('Làm hàng: ') + sx.qty_plan.cast(String) + literal(' ') + Product.name).label('customer'),
        literal(0).label('amount'), literal(False).label('overdue')).join(Product, Product.code==sx.product_code)
    if 'technician' in user.roles:
        add('production_work','Hàng sản xuất',production_docs.where(sx.technician_id==user.id,sx.status.in_(('MOI','DANG_LAM'))).order_by(sx.id), 'production','/san-xuat')
    if has_role(user,'warehouse'):
        waiting = production_docs.with_only_columns(sx.id,sx.number,sx.status,
            (literal('Nhận thành phẩm ') + sx.number).label('customer'),literal(0).label('amount'),literal(False).label('overdue')).where(sx.status=='HOAN_THANH')
        if user.warehouse_codes and not director: waiting=waiting.where(sx.to_warehouse_code.in_(user.warehouse_codes))
        add('production_receive','Nhận thành phẩm',waiting.order_by(sx.id),'production','/san-xuat?status=HOAN_THANH')
    return specs


def _group(session, spec, page=1):
    stmt = spec['statement']
    page_size = spec.get('page_size', LIMIT)
    # TD2: đếm tổng bằng count() over() ngay trong câu lấy dòng, mỗi nhóm một truy vấn thay vì hai.
    windowed = isinstance(stmt, Select)
    query = stmt.add_columns(func.count().over().label('_total')) if windowed else stmt
    records = [dict(r) for r in session.execute(query.offset((page-1)*page_size).limit(page_size)).mappings()]
    if windowed and (records or page == 1):
        count = int(records[0]['_total']) if records else 0
    else:
        count = int(session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())))
    rows = []
    for row in records:
        row.pop('_total', None)
        kind = spec['kind']
        if kind=='stock':
            row.update(id=row['warehouse_code']+'/'+row['product_code'], number=row['product_code'], customer=row['name'], amount=None, status='SAP_HET', overdue=row['qty']<=0,
                # Đợt 4: mở Sắp hết lọc đúng mã để thấy Đang về, Nên đặt rồi bấm Lập đơn mua.
                link='/ton-kho?'+urlencode({'canh-bao':'1','kho':row['warehouse_code'],'q':row['product_code']}))
        else:
            prefix = {'production':'/san-xuat', 'supplier_debt':'/cong-no-ncc', 'pending_supplier_return':'/cho-xu-ly/duyet-tra-ncc#xl-', 'pending_disposition':'/cho-xu-ly/duyet-huy#xl-', 'service':'/dich-vu', 'quote':'/bao-gia', 'order':'/don-ban', 'warehouse_order':'/kho/giao', 'purchase':'/don-mua', 'receipt':'/bao-thu', 'part':'/yeu-cau-linh-kien', 'custody':'/giu-may', 'delivery':'/giao-don', 'refund':'/hoan-tien', 'technician_return':'/tho-tra-hang'}[kind]
            row['link'] = f"{prefix}/{row['id']}"
            if kind=='production': row['link']='/san-xuat/'+row['number']
            if kind=='supplier_debt':row['link']='/cong-no-ncc/'+quote(row['number'],safe='')+'#tra-tien'
            if kind=='pending_supplier_return':row['link']=f"/cho-xu-ly/duyet-tra-ncc#xl-{row['id']}"
            if kind=='pending_disposition':row['link']=f"/cho-xu-ly/duyet-huy#xl-{row['id']}"
            if kind=='purchase':row['link']=f"/don-mua/{row['id']}/nhan#nhan-hang"
            if kind=='refund':row['link']=f"/bao-thu?tab=hoan-tien#refund-{row['id']}"
            if spec['key']=='quotes_accepted':row['link']=f"/don-ban/tu-bao-gia/{row['id']}"
            if spec['key']=='orders_deposit':row['link']=f"/don-ban/{row['id']}?coc=1#thu-tien"
            if spec['key']=='orders_cancel_requested':row['link']=f"/don-ban/{row['id']}#de-nghi-huy"
            if spec['key']=='service_close':row['link']=f"/linh-kien-thu-hoi?ticket_id={row['id']}"
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
    return dict(key=spec['key'], title=spec['title'], count=count, rows=rows, page_size=page_size,
        link='/viec-cua-toi?'+urlencode({'nhom':spec['key']}), list_link=spec['link'])


def collect(session, user, today=None, keys=None):
    """keys: chỉ tính các nhóm này (chuông thông báo chỉ cần nhóm việc người khác đẩy tới)."""
    with session.no_autoflush:
        now = _now()
        return [_group(session, spec) for spec in _specs(user, today or now.date(), now).values() if keys is None or spec['key'] in keys]


def filtered(session, user, key, page=1, today=None):
    with session.no_autoflush:
        now = _now()
        spec = _specs(user, today or now.date(), now).get(key)
        if spec is None:
            raise BusinessError('FORBIDDEN', 'Bạn không có quyền vào nhóm việc này.', status=403)
        return _group(session, spec, page)
