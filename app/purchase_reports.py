"""M5: báo cáo mua hàng, tuổi nợ và lãi gộp; chỉ SELECT, không tự flush."""
from calendar import monthrange

from sqlalchemy import Date, Numeric, String, case, cast, func, literal, select, union, union_all

from . import finance_reports, supplier_debts
from .errors import BusinessError
from .models import (Customer, ServiceExpense, Product, PurchaseOrder, PurchaseOrderLine, PurchaseReceipt, PurchaseReceiptLine,
                     PurchaseReturn, PurchaseReturnLine, SalesDelivery, SalesDeliveryLine, SalesOrder, SalesOrderLine, SalesReturn, SalesReturnLine, ServiceLine,
                     ServiceTicket, StockDoc, StockLedger, Supplier, SupplierOpeningDebt)
from .sales_reports import _window
from .security import has_role

_today = finance_reports._today
TZ = finance_reports.TZ
BUCKETS = {'days_0_30': '0–30 ngày', 'days_31_60': '31–60 ngày',
           'days_61_90': '61–90 ngày', 'days_over_90': 'Trên 90 ngày'}
MONEY = ('received_amount','received_vat','received_total','returned_total','net_total',
         'net_amount','avg_cost','remaining','advance',*BUCKETS,'revenue','cost','profit','documents')


def date_range(start=None,end=None):
    today=_today()
    return finance_reports.date_range(start or today.replace(day=1),end or today.replace(day=monthrange(today.year,today.month)[1]))


def check_reader(user, financial=False):
    if not has_role(user, 'finance') and (financial or not has_role(user, 'warehouse')):
        raise BusinessError('FORBIDDEN', 'Chỉ kế toán hoặc giám đốc xem tuổi nợ và lãi gộp; thủ kho chỉ xem mua hàng.', status=403)


def _rows(session, statement):
    with session.no_autoflush:
        rows = [dict(row) for row in session.execute(statement).mappings()]
    for row in rows:
        for key in MONEY:
            if key in row and row[key] is not None:
                row[key] = int(row[key])
    return rows


def _sum(source, keys):
    return select(*[func.coalesce(func.sum(source.c[k]), 0).label(k) for k in keys]).select_from(source)


def _supplier_purchases(start, end):
    receipts = select(PurchaseReceipt.order_id, PurchaseReceipt.amount, PurchaseReceipt.vat_amount,
        PurchaseReceipt.total).where(PurchaseReceipt.status == 'DA_GHI', PurchaseReceipt.receipt_date.between(start, end)).subquery()
    incoming = select(PurchaseOrder.supplier_code, receipts.c.amount.label('received_amount'),
        receipts.c.vat_amount.label('received_vat'), receipts.c.total.label('received_total'), literal(0).label('returned_total'))
    incoming = incoming.join(receipts, receipts.c.order_id == PurchaseOrder.id)
    outgoing = select(PurchaseReturn.supplier_code, literal(0), literal(0), literal(0), PurchaseReturn.total).where(
        PurchaseReturn.status == 'DA_GHI', PurchaseReturn.return_date.between(start, end))
    events = union_all(incoming, outgoing).subquery()
    return (select(events.c.supplier_code, Supplier.name,
        *[func.sum(events.c[k]).label(k) for k in ('received_amount','received_vat','received_total','returned_total')],
        func.sum(events.c.received_total-events.c.returned_total).label('net_total'))
        .join(Supplier, Supplier.code == events.c.supplier_code).group_by(events.c.supplier_code, Supplier.name))


def purchases_by_supplier(session, user, start=None, end=None):
    check_reader(user)
    start, end = date_range(start, end)
    return _rows(session, _supplier_purchases(start, end).order_by('supplier_code'))


def purchase_totals(session, user, start=None, end=None):
    check_reader(user)
    start, end = date_range(start, end)
    return _rows(session, _sum(_supplier_purchases(start,end).subquery(),
        ('received_amount','received_vat','received_total','returned_total','net_total')))[0]


def _product_purchases(start, end):
    incoming = (select(PurchaseOrderLine.product_code, PurchaseReceiptLine.qty.label('received_qty'),
        literal(0).label('returned_qty'), PurchaseReceiptLine.amount.label('received_amount'), literal(0).label('returned_amount'))
        .select_from(PurchaseReceiptLine).join(PurchaseReceipt).join(PurchaseOrderLine, PurchaseOrderLine.id == PurchaseReceiptLine.order_line_id)
        .where(PurchaseReceipt.status == 'DA_GHI', PurchaseReceipt.receipt_date.between(start, end)))
    outgoing = (select(PurchaseOrderLine.product_code, literal(0), PurchaseReturnLine.qty, literal(0), PurchaseReturnLine.amount)
        .select_from(PurchaseReturnLine).join(PurchaseReturn).join(PurchaseReceiptLine, PurchaseReceiptLine.id == PurchaseReturnLine.receipt_line_id)
        .join(PurchaseOrderLine, PurchaseOrderLine.id == PurchaseReceiptLine.order_line_id)
        .where(PurchaseReturn.status == 'DA_GHI', PurchaseReturn.return_date.between(start, end)))
    events = union_all(incoming, outgoing).subquery()
    received_qty = func.sum(events.c.received_qty)
    return (select(events.c.product_code, Product.name, received_qty.label('received_qty'),
        func.sum(events.c.returned_qty).label('returned_qty'),
        func.sum(events.c.received_amount-events.c.returned_amount).label('net_amount'),
        func.round(func.sum(events.c.received_amount)/func.nullif(received_qty,0)).label('avg_cost'))
        .join(Product, Product.code == events.c.product_code).group_by(events.c.product_code, Product.name))


def purchases_by_product(session, user, start=None, end=None):
    check_reader(user)
    start, end = date_range(start, end)
    return _rows(session, _product_purchases(start,end).order_by('product_code'))


def product_totals(session, user, start=None, end=None):
    check_reader(user)
    start, end = date_range(start, end)
    return _rows(session, _sum(_product_purchases(start,end).subquery(), ('received_qty','returned_qty','net_amount')))[0]


def _aging(as_of, overdue_days=None):
    # Cùng truy vấn nguồn với supplier_debts.balance, không nạp từng NCC vào ORM.
    balances = supplier_debts._balances().subquery()
    charges = union_all(
        select(PurchaseOrder.supplier_code, PurchaseReceipt.receipt_date.label('day'), literal('PN').label('kind'),
            PurchaseReceipt.id, PurchaseReceipt.total.label('amount')).join(PurchaseReceipt, PurchaseReceipt.order_id == PurchaseOrder.id)
            .where(PurchaseReceipt.status == 'DA_GHI'),
        select(SupplierOpeningDebt.supplier_code, SupplierOpeningDebt.as_of, literal('DK'), literal(0), SupplierOpeningDebt.amount)).subquery()
    newer = func.coalesce(func.sum(charges.c.amount).over(partition_by=charges.c.supplier_code,
        order_by=(charges.c.day.desc(), charges.c.kind.desc(), charges.c.id.desc()), rows=(None,-1)), 0)
    allocation = select(charges.c.supplier_code, charges.c.day,
        func.greatest(0,func.least(charges.c.amount, balances.c.remaining-newer)).label('amount'))
    allocation = allocation.join(balances,balances.c.supplier_code == charges.c.supplier_code).subquery()
    days = func.greatest(0,literal(as_of,type_=Date)-allocation.c.day)
    bucket = case((days<=30,'days_0_30'),(days<=60,'days_31_60'),(days<=90,'days_61_90'),else_='days_over_90')
    extra = [] if overdue_days is None else [
        func.sum(case((days>overdue_days,allocation.c.amount),else_=0)).label('overdue'),
        func.min(allocation.c.day).filter((days>overdue_days)&(allocation.c.amount>0)).label('oldest')]
    allocated = select(allocation.c.supplier_code,
        *[func.sum(case((bucket==key,allocation.c.amount),else_=0)).label(key) for key in BUCKETS], *extra)
    allocated = allocated.group_by(allocation.c.supplier_code).subquery()
    return (select(balances.c.supplier_code, balances.c.name,
        *[func.coalesce(allocated.c[k],0).label(k) for k in BUCKETS],
        func.greatest(0,-balances.c.remaining).label('advance'), balances.c.remaining,
        *([] if overdue_days is None else [func.coalesce(allocated.c.overdue,0).label('overdue'),allocated.c.oldest]))
        .outerjoin(allocated,allocated.c.supplier_code==balances.c.supplier_code).where(balances.c.remaining!=0))


def supplier_aging(session, user, as_of=None):
    check_reader(user,financial=True)
    query = _aging(as_of or _today()).subquery()
    return _rows(session, select(query).order_by(query.c.remaining.desc(),query.c.supplier_code))


def aging_totals(session, user, as_of=None):
    check_reader(user,financial=True)
    return _rows(session, _sum(_aging(as_of or _today()).subquery(), (*BUCKETS,'advance','remaining')))[0]


def _margin_sources(start, end):
    sales = select(literal('GH').label('kind'), SalesDelivery.id, SalesDelivery.number, SalesOrder.customer_code,
        cast(func.timezone('Asia/Ho_Chi_Minh', SalesDelivery.delivered_at),Date).label('day'),
        SalesDelivery.subtotal.label('revenue'), literal('THU_TIEN').label('billing')).join(
        SalesOrder,SalesOrder.id==SalesDelivery.order_id).where(
        SalesDelivery.status=='DA_GIAO', *_window(SalesDelivery.delivered_at,start,end))
    service_amounts = select(ServiceLine.ticket_id,func.sum(ServiceLine.amount).label('amount')).group_by(ServiceLine.ticket_id).subquery()
    services = select(literal('DV'),ServiceTicket.id,ServiceTicket.number,ServiceTicket.customer_code,
        cast(func.timezone('Asia/Ho_Chi_Minh',ServiceTicket.priced_at),Date),
        case((ServiceTicket.billing=='MIEN_PHI',0),else_=func.coalesce(service_amounts.c.amount,0)),ServiceTicket.billing)
    services = services.outerjoin(service_amounts,service_amounts.c.ticket_id==ServiceTicket.id).where(
        ServiceTicket.status.in_(('HOAN_THANH','DA_DONG')),ServiceTicket.priced_at.is_not(None),*_window(ServiceTicket.priced_at,start,end))
    returns=select(literal('TH'),SalesReturn.id,SalesReturn.number,SalesOrder.customer_code,
        cast(func.timezone('Asia/Ho_Chi_Minh',SalesReturn.returned_at),Date),-SalesReturn.subtotal,literal('THU_TIEN')).join(
        SalesOrder,SalesOrder.id==SalesReturn.order_id).where(SalesReturn.status=='DA_NHAN',*_window(SalesReturn.returned_at,start,end))
    documents = union_all(sales,services,returns).cte('margin_documents')
    # Một PX có thể gắn nhiều dòng, kể cả nhiều dòng cùng mã. Khử trùng trước khi nối sổ.
    links = union(select(literal('GH').label('kind'),SalesDeliveryLine.delivery_id.label('id'),SalesDeliveryLine.stock_doc_id.label('doc_id'))
            .where(SalesDeliveryLine.stock_doc_id.is_not(None)),
        select(literal('DV'),ServiceLine.ticket_id,ServiceLine.stock_doc_id).where(ServiceLine.stock_doc_id.is_not(None)),
        select(literal('TH'),SalesReturnLine.return_id,SalesReturnLine.stock_doc_id).where(SalesReturnLine.stock_doc_id.is_not(None))).subquery()
    cost = (select(documents.c.kind,documents.c.id,StockLedger.product_code,(-func.sum(StockLedger.amount)).label('cost'))
        .select_from(documents).join(links,(links.c.kind==documents.c.kind)&(links.c.id==documents.c.id))
        .join(StockDoc,StockDoc.id==links.c.doc_id).join(StockLedger,StockLedger.doc_id==StockDoc.id)
        .where(StockDoc.kind.in_(('PX','PN')),StockDoc.status=='DA_GHI',StockLedger.is_reversal.is_(False))
        .group_by(documents.c.kind,documents.c.id,StockLedger.product_code))
    # TUẤN 30/10/2026: chi phí phát sinh thợ tự trả (đã duyệt) tính vào giá vốn phiếu dịch vụ; không gắn mã hàng.
    expenses = (select(documents.c.kind,documents.c.id,cast(literal(None),String).label('product_code'),func.sum(ServiceExpense.amount))
        .select_from(documents).join(ServiceExpense,(documents.c.kind=='DV')&(ServiceExpense.ticket_id==documents.c.id))
        .where(ServiceExpense.status.in_(('DA_DUYET','DA_HOAN'))).group_by(documents.c.kind,documents.c.id))
    cost = union_all(cost,expenses).cte('margin_costs')
    return documents,cost


def _margin_columns(revenue, cost):
    profit=revenue-cost
    return (revenue.label('revenue'),cost.label('cost'),profit.label('profit'),
        func.round(cast(profit,Numeric)*100/func.nullif(revenue,0),1).label('rate'))


def gross_margin(session, user, start=None, end=None):
    check_reader(user,financial=True)
    start,end=date_range(start,end)
    documents,costs=_margin_sources(start,end)
    doc_cost=select(costs.c.kind,costs.c.id,func.sum(costs.c.cost).label('cost')).group_by(costs.c.kind,costs.c.id).subquery()
    query=(select(documents.c.kind,documents.c.id,documents.c.number,documents.c.customer_code,documents.c.day,
        *_margin_columns(documents.c.revenue,func.coalesce(doc_cost.c.cost,0)))
        .outerjoin(doc_cost,(doc_cost.c.kind==documents.c.kind)&(doc_cost.c.id==documents.c.id))).subquery()
    rows=_rows(session,select(query,Customer.name.label('customer_name')).join(Customer,Customer.code==query.c.customer_code)
        .order_by(query.c.day,query.c.kind,query.c.id))
    total=_rows(session,select(func.count().label('documents'),
        *_margin_columns(func.coalesce(func.sum(query.c.revenue),0),func.coalesce(func.sum(query.c.cost),0))).select_from(query))[0]
    sales=(select(SalesOrderLine.product_code,SalesDeliveryLine.amount.label('revenue'),literal(0).label('cost'))
        .select_from(SalesDeliveryLine).join(SalesOrderLine,(SalesOrderLine.order_id==SalesDeliveryLine.order_id)&
            (SalesOrderLine.line_no==SalesDeliveryLine.order_line_no))
        .join(documents,(documents.c.kind=='GH')&(documents.c.id==SalesDeliveryLine.delivery_id)).where(SalesOrderLine.kind=='HANG'))
    services=(select(ServiceLine.product_code,case((documents.c.billing=='MIEN_PHI',0),else_=ServiceLine.amount),literal(0))
        .select_from(ServiceLine).join(documents,(documents.c.kind=='DV')&(documents.c.id==ServiceLine.ticket_id)).where(ServiceLine.kind=='VAT_TU'))
    returned_products=select(SalesOrderLine.product_code,-SalesReturnLine.amount,literal(0)).select_from(SalesReturnLine).join(
        SalesOrderLine,(SalesOrderLine.order_id==SalesReturnLine.order_id)&(SalesOrderLine.line_no==SalesReturnLine.order_line_no)).join(
        documents,(documents.c.kind=='TH')&(documents.c.id==SalesReturnLine.return_id)).where(SalesOrderLine.kind=='HANG')
    product_events=union_all(sales,services,returned_products,select(costs.c.product_code,literal(0),costs.c.cost)).subquery()
    products=select(product_events.c.product_code,Product.name,
        *_margin_columns(func.sum(product_events.c.revenue),func.sum(product_events.c.cost)))
    products=products.join(Product,Product.code==product_events.c.product_code).group_by(product_events.c.product_code,Product.name).subquery()
    product_rows=_rows(session,select(products).order_by(products.c.product_code))
    product_total=_rows(session,select(*_margin_columns(func.coalesce(func.sum(products.c.revenue),0),
        func.coalesce(func.sum(products.c.cost),0))).select_from(products))[0]
    return dict(documents=rows,totals=total,products=product_rows,product_totals=product_total)
