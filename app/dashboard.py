"""R1: tổng quan chỉ đọc, dùng cùng số liệu và quyền của các báo cáo nguồn."""
from datetime import datetime, timedelta

from sqlalchemy import Date, and_, func, select

from . import finance_reports, purchase_reports, sales_reports, service_tickets
from .errors import BusinessError
from .models import SalesReceipt, ServiceTicket, StockBalance, User, Warehouse
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


# ---- Tổng quan điều hành (TUẤN duyệt 26/10/2026): bảng thợ, kinh doanh, xưởng trong ngày. Chỉ SELECT gộp, không nạp từng chứng từ.

def _day_window(column,today):
    from .sales_reports import _window
    return _window(column,today,today)


def _day_counts(session,today,now):
    from .models import EquipmentCustody, Quote, SalesOrder, ServiceStep
    t=ServiceTicket
    final=select(func.max(ServiceStep.id)).where(ServiceStep.to_status.in_(('HOAN_THANH','KHONG_LAM_DUOC')),ServiceStep.action=='transition').group_by(ServiceStep.ticket_id)
    done=(select(func.count().filter(ServiceStep.to_status=='HOAN_THANH').label('done'),
        func.count().filter(ServiceStep.to_status=='KHONG_LAM_DUOC').label('failed'))
        .select_from(ServiceStep).join(t,t.id==ServiceStep.ticket_id)
        .where(ServiceStep.id.in_(final),*_day_window(ServiceStep.at,today),
            ((ServiceStep.to_status=='HOAN_THANH')&t.status.in_(('HOAN_THANH','DA_DONG')))|((ServiceStep.to_status=='KHONG_LAM_DUOC')&(t.status=='KHONG_LAM_DUOC'))))
    row=dict(_rows(session,done)[0])
    opened=t.status.in_(service_tickets.OPEN)
    row.update(_rows(session,select(func.count().filter(*_day_window(t.created_at,today)).label('received'),
        func.count().filter(opened).label('open'),func.count().filter(opened,t.due_at<now).label('overdue'),
        func.count().filter(t.status=='MOI').label('unassigned')))[0])
    o=SalesOrder
    row.update(_rows(session,select(func.count().label('orders'),func.coalesce(func.sum(o.subtotal),0).label('orders_amount'),
        func.count().filter(o.status=='DA_GIAO').label('orders_delivered'),
        func.count().filter(o.status.in_(('DA_CHOT','GIAO_MOT_PHAN'))).label('orders_waiting'))
        .where(o.status!='DA_HUY',o.confirmed_at.is_not(None),*_day_window(o.confirmed_at,today)))[0])
    r=SalesReceipt
    row.update(_rows(session,select(func.coalesce(func.sum(r.amount),0).label('collected'))
        .where(r.status=='DA_XAC_NHAN',r.received_at==today))[0])
    c=EquipmentCustody
    row.update(_rows(session,select(func.count().label('brought_in')).where(c.status=='DA_NHAN',c.location=='KHO_KY_THUAT',*_day_window(c.confirmed_at,today)))[0])
    row.update(_rows(session,select(func.count().label('quotes_sent')).where(Quote.status.in_(('DA_GUI','CHAP_NHAN')),*_day_window(Quote.created_at,today)))[0])
    return {k:int(v) for k,v in row.items()}


def _technicians(session,today,now):
    from .models import AttendancePunch, Customer, EquipmentCustody, ServiceStep, ServiceVisit
    from .config import get_settings
    t=ServiceTicket
    users=_rows(session,select(User.id,User.full_name).where(User.active.is_(True),User.roles.any('technician')).order_by(User.full_name))
    rows={u['id']:dict(user_id=u['id'],name=u['full_name'],first_in=None,late_minutes=0,assigned=0,done=0,failed=0,visits=0,visits_done=0,
        visits_return=0,visits_no_show=0,brought_in=0,collected=0,pending=0,items=[],open=0,overdue=0) for u in users}
    def put(key,stmt):
        for r in _rows(session,stmt):
            row=rows.get(r.pop('uid'))
            if row is not None:row[key]=int(r[key])
    s=ServiceStep
    put('assigned',select(s.assignee_id.label('uid'),func.count(func.distinct(s.ticket_id)).label('assigned'))
        .where(s.action.in_(('assign','reassign')),*_day_window(s.at,today)).group_by(s.assignee_id))
    final=select(func.max(s.id)).where(s.to_status.in_(('HOAN_THANH','KHONG_LAM_DUOC')),s.action=='transition').group_by(s.ticket_id)
    ending=(select(s.assignee_id.label('uid'),func.count().filter(s.to_status=='HOAN_THANH').label('done'),
        func.count().filter(s.to_status=='KHONG_LAM_DUOC').label('failed')).join(t,t.id==s.ticket_id)
        .where(s.id.in_(final),*_day_window(s.at,today),
            ((s.to_status=='HOAN_THANH')&t.status.in_(('HOAN_THANH','DA_DONG')))|((s.to_status=='KHONG_LAM_DUOC')&(t.status=='KHONG_LAM_DUOC'))).group_by(s.assignee_id))
    for r in _rows(session,ending):
        row=rows.get(r['uid'])
        if row is not None:row['done'],row['failed']=int(r['done']),int(r['failed'])
    v=ServiceVisit
    for r in _rows(session,select(v.technician_id.label('uid'),func.count().label('visits'),
            func.count().filter(v.outcome=='XONG').label('visits_done'),func.count().filter(v.outcome=='HEN_LAI').label('visits_return'),
            func.count().filter(v.outcome=='KHONG_GAP_KHACH').label('visits_no_show')).where(*_day_window(v.started_at,today)).group_by(v.technician_id)):
        row=rows.get(r.pop('uid'))
        if row is not None:row.update({k:int(x) for k,x in r.items()})
    c=EquipmentCustody
    put('brought_in',select(c.created_by.label('uid'),func.count().label('brought_in'))
        .where(c.status=='DA_NHAN',c.location=='KHO_KY_THUAT',*_day_window(c.confirmed_at,today)).group_by(c.created_by))
    r=SalesReceipt
    for row_ in _rows(session,select(r.reported_by.label('uid'),func.coalesce(func.sum(r.amount).filter(r.status=='DA_XAC_NHAN'),0).label('collected'),
            func.coalesce(func.sum(r.amount).filter(r.status=='CHO_XAC_NHAN'),0).label('pending'))
            .where(r.status.in_(('DA_XAC_NHAN','CHO_XAC_NHAN')),r.received_at==today).group_by(r.reported_by)):
        row=rows.get(row_['uid'])
        if row is not None:row['collected'],row['pending']=int(row_['collected']),int(row_['pending'])
    p=AttendancePunch
    cfg=get_settings();start=datetime.combine(today,cfg.work_start,TZ)+timedelta(minutes=cfg.late_grace_minutes)
    for r in _rows(session,select(p.user_id.label('uid'),func.min(p.at).label('first_in')).where(p.kind=='IN',*_day_window(p.at,today)).group_by(p.user_id)):
        row=rows.get(r['uid'])
        if row is not None:
            first=r['first_in'].astimezone(TZ);row['first_in']=first
            row['late_minutes']=int((first-start).total_seconds()//60)+cfg.late_grace_minutes if first>start else 0
    # Phiếu đang mở của thợ (mọi ngày) và phiếu không làm được hôm nay: một SELECT, đủ số, trạng thái, lý do.
    failed_today=select(s.ticket_id).where(s.id.in_(final),s.to_status=='KHONG_LAM_DUOC',*_day_window(s.at,today))
    items=_rows(session,select(t.id,t.number,t.assignee_id,t.status,t.hold_reason,t.status_reason,t.due_at,t.appointment_at,Customer.name.label('customer'))
        .join(Customer,Customer.code==t.customer_code)
        .where(t.assignee_id.is_not(None),t.status.in_(service_tickets.OPEN)|((t.status=='KHONG_LAM_DUOC')&t.id.in_(failed_today)))
        .order_by(t.due_at.nulls_last(),t.id))
    for it in items:
        row=rows.get(it['assignee_id'])
        if row is None:continue
        it['overdue']=bool(it['due_at'] and it['status'] in service_tickets.OPEN and it['due_at']<now)
        it['reason']=service_tickets.HOLD_REASONS.get(it['hold_reason'],'') if it['status']=='TAM_DUNG' else (it['status_reason'] or '')
        row['items'].append(it)
        if it['status'] in service_tickets.OPEN:
            row['open']+=1;row['overdue']+=it['overdue']
    return list(rows.values())


def _sales(session,today):
    from .models import Quote, SalesOrder
    o=SalesOrder;r=SalesReceipt
    orders=(select(o.created_by.label('uid'),func.count().label('orders'),func.coalesce(func.sum(o.subtotal),0).label('amount'),
        func.count().filter(o.status=='DA_GIAO').label('delivered'),func.count().filter(o.status.in_(('DA_CHOT','GIAO_MOT_PHAN'))).label('waiting'))
        .where(o.status!='DA_HUY',o.confirmed_at.is_not(None),*_day_window(o.confirmed_at,today)).group_by(o.created_by)).subquery()
    quotes=(select(Quote.created_by.label('uid'),func.count().label('quotes')).where(Quote.status.in_(('DA_GUI','CHAP_NHAN')),*_day_window(Quote.created_at,today))
        .group_by(Quote.created_by)).subquery()
    receipts=(select(r.reported_by.label('uid'),func.coalesce(func.sum(r.amount),0).label('collected'))
        .where(r.status=='DA_XAC_NHAN',r.received_at==today,r.order_id.is_not(None)).group_by(r.reported_by)).subquery()
    from sqlalchemy import union
    ids=union(select(orders.c.uid),select(quotes.c.uid),select(receipts.c.uid)).subquery()
    rows=_rows(session,select(User.id.label('user_id'),User.full_name.label('name'),func.coalesce(orders.c.orders,0).label('orders'),func.coalesce(orders.c.amount,0).label('amount'),
        func.coalesce(orders.c.delivered,0).label('delivered'),func.coalesce(orders.c.waiting,0).label('waiting'),func.coalesce(quotes.c.quotes,0).label('quotes'),
        func.coalesce(receipts.c.collected,0).label('collected')).join(ids,ids.c.uid==User.id)
        .outerjoin(orders,orders.c.uid==User.id).outerjoin(quotes,quotes.c.uid==User.id).outerjoin(receipts,receipts.c.uid==User.id).order_by(User.full_name))
    return [{k:(int(v) if k not in ('name',) else v) for k,v in r.items()} for r in rows]


def day_board(session,user,today=None,now=None) -> dict:
    """Bảng ngày cho giám đốc: số đếm chung (mọi người đọc Tổng quan), bảng thợ / kinh doanh / xưởng chỉ giám đốc."""
    from . import service_workshop
    with session.no_autoflush:
        check_reader(user)
        now=now or _now();today=today or now.astimezone(TZ).date()
        result=dict(counts=_day_counts(session,today,now))
        if has_role(user,'director'):
            result['technicians']=_technicians(session,today,now)
            result['sales']=_sales(session,today)
            result['workshop']=_rows(session,service_workshop.queue_query(now))
        return result
