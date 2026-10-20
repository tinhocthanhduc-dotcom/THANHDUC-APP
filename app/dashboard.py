"""R1: tổng quan chỉ đọc, dùng cùng số liệu và quyền của các báo cáo nguồn."""
from datetime import datetime, timedelta

from sqlalchemy import Date, and_, func, select

from . import finance_reports, purchase_reports, sales_reports, service_tickets
from .errors import BusinessError
from .models import SalesReceipt, ServiceTicket, StockBalance, Warehouse
from .security import has_role

TZ = finance_reports.TZ


def _now():
    return datetime.now(TZ)


def check_reader(user):
    if not has_role(user,'finance'):
        raise BusinessError('FORBIDDEN','Chỉ giám đốc hoặc kế toán được xem Tổng quan.',status=403)


def _rows(session,stmt):
    with session.no_autoflush:
        return [dict(r) for r in session.execute(stmt).mappings()]


def _service_revenue():
    return (ServiceTicket.status.in_(('HOAN_THANH','DA_DONG')),
            ServiceTicket.priced_at.is_not(None),ServiceTicket.billing=='THU_TIEN')


def _inventory(session):
    values=(select(Warehouse.code.label('warehouse_code'),Warehouse.name,
        func.round(func.coalesce(func.sum(StockBalance.qty*StockBalance.avg_cost),0)).label('value'))
        .outerjoin(StockBalance,StockBalance.warehouse_code==Warehouse.code).group_by(Warehouse.code,Warehouse.name)).subquery()
    rows=_rows(session,select(values).order_by(values.c.warehouse_code))
    for row in rows:row['value']=int(row['value'])
    total=_rows(session,select(func.coalesce(func.sum(values.c.value),0).label('value')))[0]['value']
    return rows,int(total)


def summary(session,user,today=None) -> dict:
    # Bảo vệ cả lúc kiểm quyền phải tải lại thuộc tính user đã hết hạn trong Session.
    with session.no_autoflush:
        return _summary(session,user,today)


def _summary(session,user,today):
    check_reader(user)
    now=_now()
    today=today or now.astimezone(TZ).date()
    month_start=today.replace(day=1)
    last_end=month_start-timedelta(days=1)
    last_start=last_end.replace(day=1)
    chart_start=today-timedelta(days=29)
    periods={'today':(today,today),'month':(month_start,today),'last_month':(last_start,last_end)}
    sales={key:sales_reports.totals(session,user,start,end)['subtotal'] for key,(start,end) in periods.items()}
    service=_rows(session,select(*[func.coalesce(func.sum(ServiceTicket.subtotal).filter(
        and_(*sales_reports._window(ServiceTicket.priced_at,start,end))),0).label(key)
        for key,(start,end) in periods.items()]).where(*_service_revenue(),
            *sales_reports._window(ServiceTicket.priced_at,last_start,today)))[0]
    sales_days={r['day']:r['subtotal'] for r in sales_reports.daily(session,user,chart_start,today)}
    day=func.timezone('Asia/Ho_Chi_Minh',ServiceTicket.priced_at).cast(Date)
    service_days={r['day']:int(r['amount']) for r in _rows(session,select(day.label('day'),func.sum(ServiceTicket.subtotal).label('amount'))
        .where(*_service_revenue(),*sales_reports._window(ServiceTicket.priced_at,chart_start,today)).group_by(day))}
    # Chỉ ghép 30 tổng ngày từ SQL; không nạp chứng từ để cộng trong Python.
    daily=[]
    for n in range(30):
        d=chart_start+timedelta(days=n)
        daily.append(dict(day=d,amount=sales_days.get(d,0)+service_days.get(d,0)))
    opened=ServiceTicket.status.in_(service_tickets.OPEN)
    counts=_rows(session,select(func.count().filter(opened).label('open'),
        func.count().filter(opened,ServiceTicket.due_at<now).label('overdue')))[0]
    pending=_rows(session,select(func.count().label('count'),func.coalesce(func.sum(SalesReceipt.amount),0).label('amount'))
        .where(SalesReceipt.status=='CHO_XAC_NHAN'))[0]
    pending={k:int(v) for k,v in pending.items()}
    result=dict(today=today,month_start=month_start,last_month_start=last_start,last_month_end=last_end,chart_start=chart_start,
        revenue_today=sales['today']+int(service['today']),revenue_month=sales['month']+int(service['month']),
        revenue_last_month=sales['last_month']+int(service['last_month']),revenue_daily=daily,
        funds=finance_reports.funds(session,user),fund_total=finance_reports.fund_total(session,user),
        customer_debt=finance_reports.debt_totals(session,user)['remaining'],
        supplier_debt=purchase_reports.aging_totals(session,user,today)['remaining'],services=counts,pending_receipts=pending)
    if has_role(user,'director'):
        result['damage_cost_month']=finance_reports.damage_cost(session,user,month_start,today)
        result['gross_margin_month']=purchase_reports.gross_margin(session,user,month_start,today)['totals']['profit']
        result['inventory'],result['inventory_total']=_inventory(session)
        from . import pending_items
        groups=pending_items.totals(session,user,now=now)
        result['pending_items']=dict(value=sum(g['value'] for g in groups.values()),overdue=sum(g['overdue'] for g in groups.values()))
    return result
