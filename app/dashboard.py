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


# ---- Tổng quan bản 2 (TUẤN duyệt 28/10/2026): ai đang làm gì · việc cần làm tiếp · việc đang diễn ra.
# Chỉ SELECT gộp hoặc danh sách nhỏ của hôm nay; không ghi, không đổi số tiền.

WAITING=('DA_GIAO','DA_NHAN','DA_HEN')
TIMED_LIMIT=8


def _clock(at):
    return at.astimezone(TZ).strftime('%H:%M')


def _cut(text,n=60):
    text=' '.join((text or '').split())
    return text if len(text)<=n else text[:n-1].rstrip()+'…'


def _short(name):
    # "Đoàn Cảnh Phong" → "Phong"; "Nguyễn Văn A" → "Văn A" (tên một chữ cái khó đọc).
    words=(name or '').split()
    if not words:return '—'
    return ' '.join(words[-2:]) if len(words[-1])<=2 and len(words)>1 else words[-1]


def _late_text(minutes):
    if minutes>=1440:return f'Trễ {minutes//1440} ngày'
    if minutes>=60:return f'Trễ {minutes//60} giờ'
    return f'Trễ {max(minutes,1)} phút'


def _staff(session):
    users=_rows(session,select(User.id,User.full_name,User.roles).where(User.active.is_(True)).order_by(User.full_name))
    return [u for u in users if set(u['roles'] or [])-{'admin'}]


def _owner(users,role):
    # Người làm việc theo vai trò; không ai giữ vai trò thì giám đốc làm.
    hit=[u for u in users if role in u['roles'] and 'director' not in u['roles']] or [u for u in users if 'director' in u['roles']]
    return dict(who=', '.join(_short(u['full_name']) for u in hit) or '—',ids=[u['id'] for u in hit])


def _next_rows(session,users,today,now,approvals):
    from .models import Customer, PartRequest, ProductionOrder, Quote, SalesOrder, ServiceVisit, TechnicianReturn
    from .my_work import _available
    t=ServiceTicket
    start=datetime.combine(today,datetime.min.time(),TZ);end=start+timedelta(days=1)
    names={u['id']:_short(u['full_name']) for u in users}
    dispatch=_owner(users,'dispatcher')
    visiting=t.id.in_(select(ServiceVisit.ticket_id).where(ServiceVisit.ended_at.is_(None)))
    appt_today=(t.appointment_at>=start)&(t.appointment_at<end)&t.status.in_(WAITING)
    late=t.status.in_(service_tickets.OPEN)&(t.due_at<now)
    tickets=_rows(session,select(t.id,t.number,t.status,t.assignee_id,t.symptom,t.due_at,t.appointment_at,Customer.name.label('customer'),visiting.label('visiting'))
        .join(Customer,Customer.code==t.customer_code).where((t.status=='MOI')|late|appt_today).order_by(t.id))
    timed=[]
    for r in tickets:
        appt=r['appointment_at'];uid=r['assignee_id']
        row=dict(ticket_id=r['id'],title=f"{r['number']} · {r['customer']}",detail=_cut(r['symptom']),who=names.get(uid,'—'),ids=[uid] if uid else [],none=False,link=None,n=1)
        if r['status']=='MOI':
            row.update(state='late',when='Chưa giao',who='Chưa có',none=True,ids=dispatch['ids'],sort=(0,0,r['id']))
        elif r['due_at'] and r['due_at']<now:
            row.update(state='late',when=_late_text(int((now-r['due_at']).total_seconds()//60)),sort=(1,r['due_at'].timestamp(),r['id']))
        elif appt and appt<now and not r['visiting']:
            row.update(state='late',when='Trễ hẹn',detail=f'hẹn {_clock(appt)} · chưa đến khách',sort=(1,appt.timestamp(),r['id']))
        elif appt and appt>=now and r['status'] in WAITING:
            row.update(state='time',when=_clock(appt),sort=(2,appt.timestamp(),r['id']))
        else:continue
        timed.append(row)
    timed.sort(key=lambda x:x['sort'])
    late_rows=[r for r in timed if r['state']=='late'];upcoming=[r for r in timed if r['state']=='time']
    day=[]
    def add(cat,title,detail,owner,link,n):
        if n:day.append(dict(state='day',when=cat,title=title,detail=detail,who=owner['who'],ids=owner['ids'],none=False,link=link,n=n,ticket_id=None))
    kho=_owner(users,'warehouse')
    r=_rows(session,select(func.count().label('n'),func.string_agg(PartRequest.number,', ').label('nums')).where(PartRequest.status=='CHO_CAP'))[0]
    add('Kho',f"Cấp {r['n']} yêu cầu linh kiện",', '.join((r['nums'] or '').split(', ')[:3]),kho,'/yeu-cau-linh-kien',r['n'])
    n=session.scalar(select(func.count()).select_from(TechnicianReturn).where(TechnicianReturn.status=='CHO_NHAN'))
    add('Kho',f'Nhận {n} phiếu thợ trả hàng','thợ mang hàng về kho',kho,'/viec-cua-toi?nhom=technician_returns',n)
    o=SalesOrder
    r=_rows(session,select(func.count().filter(o.fulfillment!='THO_GIAO').label('store'),func.count().filter(o.fulfillment=='THO_GIAO',o.delivery_assignee_id.is_(None)).label('free'))
        .where(o.status.in_(('DA_CHOT','GIAO_MOT_PHAN'))))[0]
    add('Kho',f"Giao {r['store']} đơn đã chốt",'khách lấy hoặc kho gửi',kho,'/don-ban?status=DA_CHOT',r['store'])
    for g in _rows(session,select(o.delivery_assignee_id.label('uid'),func.count().label('n')).where(o.fulfillment=='THO_GIAO',o.status.in_(('DA_CHOT','GIAO_MOT_PHAN')),o.delivery_assignee_id.is_not(None)).group_by(o.delivery_assignee_id)):
        add('Giao hàng',f"Giao {g['n']} đơn bán",'thợ giao tận nơi',dict(who=names.get(g['uid'],'—'),ids=[g['uid']]),'/dieu-phoi',g['n'])
    add('Giao hàng',f"{r['free']} đơn thợ giao chưa chọn thợ",'cần chọn người giao',dispatch,'/dieu-phoi',r['free'])
    n=session.scalar(select(func.count()).select_from(ProductionOrder).where(ProductionOrder.status=='HOAN_THANH'))
    add('Kho',f'Nhận thành phẩm {n} lệnh sản xuất','thợ làm xong, chờ nhập kho',kho,'/san-xuat?status=HOAN_THANH',n)
    has_order=select(o.id).where(o.quote_id==Quote.id,o.status!='DA_HUY').exists()
    for g in _rows(session,select(Quote.created_by.label('uid'),func.count().label('n'),func.min(Quote.id).label('id'),func.min(Quote.number).label('number'),
            func.min(Customer.name).label('customer')).join(Customer,Customer.code==Quote.customer_code).where(Quote.status=='CHAP_NHAN',~has_order).group_by(Quote.created_by)):
        one=g['n']==1
        add('Bán hàng',f"Tạo đơn từ {g['number']}" if one else f"Tạo đơn từ {g['n']} báo giá",f"{g['customer']} đã đồng ý báo giá" if one else 'khách đã đồng ý, chưa có đơn',
            dict(who=names.get(g['uid'],'—'),ids=[g['uid']]),f"/don-ban/tu-bao-gia/{g['id']}" if one else '/bao-gia',g['n'])
    for g in _rows(session,select(o.created_by.label('uid'),func.count().label('n'),func.min(o.id).label('id'),func.min(o.number).label('number'))
            .where(o.status=='NHAP').group_by(o.created_by)):
        one=g['n']==1
        add('Bán hàng',f"Chốt đơn nháp {g['number']}" if one else f"Chốt {g['n']} đơn nháp",'đơn chưa chốt',dict(who=names.get(g['uid'],'—'),ids=[g['uid']]),
            f"/don-ban/{g['id']}" if one else '/don-ban?status=NHAP',g['n'])
    n=session.scalar(select(func.count()).select_from(t).where(t.status=='TAM_DUNG',t.hold_reason=='SUA_XONG_CHO_TRA'))
    add('Xưởng',f'Hẹn trả {n} máy sửa xong','máy ở xưởng đã sửa xong',dispatch,'/dieu-phoi?view=return_ready',n)
    r=_rows(session,select(func.count().label('n'),func.coalesce(func.sum(SalesReceipt.amount),0).label('amount')).where(SalesReceipt.status=='CHO_XAC_NHAN'))[0]
    add('Tiền',f"Xác nhận {r['n']} báo thu",f"{int(r['amount']):,}".replace(',','.')+' đồng',_owner(users,'finance'),'/bao-thu',r['n'])
    attendance,advance=approvals
    parts=([f'{attendance} chấm công'] if attendance else [])+([f'{advance} tạm ứng'] if advance else [])
    boss=[u for u in users if 'director' in u['roles']]
    add('Duyệt','Duyệt '+' · '.join(parts),'chờ giám đốc',dict(who=', '.join(_short(u['full_name']) for u in boss) or '—',ids=[u['id'] for u in boss]),
        '/cham-cong/duyet' if attendance else '/tam-ung',attendance+advance)
    return dict(late=late_rows,upcoming=upcoming,day=day,late_count=len(late_rows),unassigned=sum(1 for r in late_rows if r['none']),
        total=len(timed)+sum(r['n'] for r in day))


def _lanes(session,now):
    from .models import PartRequest, ProductionOrder, PurchaseOrder, Quote, SalesOrder, SalesRefund, TechnicianReturn
    from .my_work import _available
    t=ServiceTicket;o=SalesOrder;sx=ProductionOrder
    paused=(t.status=='TAM_DUNG')
    sv=[('x','chưa giao thợ',t.status=='MOI','/dieu-phoi?view=new'),('x','sửa xong chờ trả',paused&(t.hold_reason=='SUA_XONG_CHO_TRA'),'/dieu-phoi?view=return_ready'),
        ('a','đang làm',t.status=='DANG_LAM','/dieu-phoi?view=open'),('a','đang sửa ở xưởng',paused&(t.hold_reason=='CHO_SUA_XUONG'),'/dieu-phoi?view=workshop'),
        ('w','chờ thợ nhận',t.status=='DA_GIAO','/dieu-phoi?view=open'),('w','thợ đã nhận · chưa hẹn',t.status=='DA_NHAN','/dieu-phoi?view=open'),
        ('w','đã hẹn',t.status=='DA_HEN','/dieu-phoi?view=open'),('w','chờ linh kiện',t.status=='CHO_LINH_KIEN','/dieu-phoi?view=parts'),
        ('w','tạm dừng',paused&t.hold_reason.not_in(('SUA_XONG_CHO_TRA','CHO_SUA_XUONG')),'/dieu-phoi?view=paused')]
    has_order=select(o.id).where(o.quote_id==Quote.id,o.status!='DA_HUY').exists()
    waiting=o.status.in_(('DA_CHOT','GIAO_MOT_PHAN'))
    def counts(model,stages):
        row=_rows(session,select(*[func.count().filter(cond).label(f's{n}') for n,(_,_,cond,_) in enumerate(stages)]).select_from(model))[0]
        return [dict(group=g,label=label,n=int(row[f's{n}']),link=link) for n,(g,label,_,link) in enumerate(stages)]
    sales=counts(Quote,[('x','khách đồng ý · chưa tạo đơn',(Quote.status=='CHAP_NHAN')&~has_order,'/bao-gia?status=CHAP_NHAN'),('w','báo giá chờ khách',Quote.status=='DA_GUI','/bao-gia?status=DA_GUI')])
    sales+=counts(o,[('x','đơn nháp',o.status=='NHAP','/don-ban?status=NHAP'),('a','thợ đang giao',waiting&(o.fulfillment=='THO_GIAO'),'/dieu-phoi'),
        ('a','đã chốt · chờ giao',waiting&(o.fulfillment!='THO_GIAO'),'/don-ban?status=DA_CHOT'),
        ('w','đã giao · chưa báo thu đủ',o.status.in_(('GIAO_MOT_PHAN','DA_GIAO'))&(_available(o)>0),'/don-ban')])
    stock=counts(PartRequest,[('x','linh kiện chờ cấp',PartRequest.status=='CHO_CAP','/yeu-cau-linh-kien')])
    stock+=counts(TechnicianReturn,[('x','thợ trả hàng chờ nhận',TechnicianReturn.status=='CHO_NHAN','/viec-cua-toi?nhom=technician_returns')])
    stock+=counts(PurchaseOrder,[('w','đơn mua chờ hàng về',PurchaseOrder.status.in_(('DA_DAT','NHAN_MOT_PHAN')),'/don-mua')])
    production=counts(sx,[('x','làm xong · chờ kho nhận',sx.status=='HOAN_THANH','/san-xuat?status=HOAN_THANH'),('a','đang làm',sx.status=='DANG_LAM','/san-xuat'),('w','lệnh mới chưa làm',sx.status=='MOI','/san-xuat')])
    money=counts(SalesReceipt,[('x','báo thu chờ xác nhận',SalesReceipt.status=='CHO_XAC_NHAN','/bao-thu')])
    money+=counts(SalesRefund,[('x','hoàn tiền chờ duyệt',SalesRefund.status=='CHO_DUYET','/bao-thu?tab=hoan-tien')])
    lanes=[]
    for key,label,link,stages in (('dv','Dịch vụ','/dieu-phoi?view=open',counts(t,sv)),('bh','Bán hàng','/don-ban',sales),('kho','Kho','/yeu-cau-linh-kien',stock),
            ('sx','Sản xuất','/san-xuat',production),('tien','Tiền','/bao-thu',money)):
        sums={g:sum(s['n'] for s in stages if s['group']==g) for g in 'awx'}
        lanes.append(dict(key=key,label=label,link=link,total=sum(sums.values()),doing=sums['a'],waiting=sums['w'],todo=sums['x'],
            groups=[(title,[s for s in stages if s['group']==g and s['n']]) for g,title in (('a','Đang làm'),('w','Đang chờ'),('x','Cần xử lý'))]))
    return lanes


def _people(session,users,techs,nexts,today,now):
    from .config import get_settings
    from .models import AttendancePunch, Customer, Product, ProductionOrder, Quote, SalesDelivery, SalesOrder, ServiceVisit
    from sqlalchemy import literal, union_all
    cfg=get_settings()
    w0=datetime.combine(today,cfg.work_start,TZ);w1=datetime.combine(today,cfg.work_end,TZ);span=(w1-w0).total_seconds()
    def pct(at):return round(min(max((at-w0).total_seconds()/span,0),1)*100,1)
    def seg(a,b,cls,title):
        left=pct(a);return dict(left=left,width=max(min(pct(b)-left,100-left),1.2),cls=cls,title=title,dot=False)
    p=AttendancePunch
    punches={}
    for r in _rows(session,select(p.user_id,p.kind,p.at).where(*_day_window(p.at,today)).order_by(p.at,p.id)):
        punches.setdefault(r['user_id'],[]).append(r)
    v=ServiceVisit;t=ServiceTicket
    visits={}
    for r in _rows(session,select(v.technician_id,v.started_at,v.ended_at,v.outcome,t.number,t.symptom,Customer.name.label('customer'))
            .join(t,t.id==v.ticket_id).join(Customer,Customer.code==t.customer_code)
            .where(and_(*_day_window(v.started_at,today))|v.ended_at.is_(None)).order_by(v.started_at,v.id)):
        visits.setdefault(r['technician_id'],[]).append(r)
    sx=ProductionOrder
    production={}
    from .production import title_extra
    for r in _rows(session,select(sx.technician_id,sx.number,sx.status,sx.qty_plan,sx.qty_good,sx.started_at,sx.finished_at,(Product.name+title_extra()).label('product'))
            .join(Product,Product.code==sx.product_code).where(sx.technician_id.is_not(None),
                (sx.status=='DANG_LAM')|and_(*_day_window(sx.finished_at,today))).order_by(sx.started_at.nulls_last(),sx.id)):
        production.setdefault(r['technician_id'],[]).append(r)
    o=SalesOrder;d=SalesDelivery
    events=union_all(
        select(o.created_by.label('uid'),o.confirmed_at.label('at'),(literal('chốt ')+o.number).label('what')).where(o.status!='DA_HUY',o.confirmed_at.is_not(None),*_day_window(o.confirmed_at,today)),
        select(Quote.created_by,Quote.created_at,literal('gửi ')+Quote.number).where(Quote.status.in_(('DA_GUI','CHAP_NHAN')),*_day_window(Quote.created_at,today)),
        select(SalesReceipt.decided_by,SalesReceipt.decided_at,literal('xác nhận thu ')+SalesReceipt.number).where(SalesReceipt.status=='DA_XAC_NHAN',SalesReceipt.decided_at.is_not(None),*_day_window(SalesReceipt.decided_at,today)),
        select(d.delivered_by,d.delivered_at,literal('giao ')+d.number).where(d.status=='DA_GIAO',*_day_window(d.delivered_at,today))).subquery()
    dots={}
    for r in _rows(session,select(events).order_by(events.c.at)):
        dots.setdefault(r['uid'],[]).append(r)
    rows_all=nexts['late']+nexts['upcoming']+nexts['day']
    hours=[dict(left=pct(datetime.combine(today,cfg.work_start,TZ).replace(hour=h,minute=0)),label=f'{h}h') for h in range(cfg.work_start.hour+1,cfg.work_end.hour+1) if h%2==0]
    def order(u):
        return (2 if 'director' in u['roles'] else 0 if 'technician' in u['roles'] else 1,u['full_name'])
    people=[]
    for u in sorted(users,key=order):
        uid=u['id'];tech=techs.get(uid);own=[r for r in rows_all if uid in r['ids']]
        mine=punches.get(uid,[]);ins=[x for x in mine if x['kind']=='IN']
        on_shift=bool(mine) and mine[-1]['kind']!='OUT'
        shift=('vào ca '+_clock(ins[0]['at'])) if ins else 'chưa chấm công'
        if mine and mine[-1]['kind']=='OUT':shift+=' · ra '+_clock(mine[-1]['at'])
        vs=visits.get(uid,[]);current=[x for x in vs if x['ended_at'] is None]
        making=[x for x in production.get(uid,[]) if x['status']=='DANG_LAM']
        working=[i for i in (tech or {}).get('items',[]) if i['status']=='DANG_LAM']
        now_title=now_detail='';pill,tone='',''
        if current:
            x=current[0];pill,tone=f"Ở khách từ {_clock(x['started_at'])}",'bl'
            now_title=f"{x['number']} · {x['customer']}";now_detail=_cut(x['symptom'],40)+f" · đã {int((now-x['started_at']).total_seconds()//60)} phút"
        elif making:
            x=making[0];pill,tone='Đang sản xuất','pur'
            now_title=f"{x['number']} · {x['product']}";now_detail=f"xong {x['qty_good']}/{x['qty_plan']}"+(f" · bắt đầu {_clock(x['started_at'])}" if x['started_at'] else '')
        elif working:
            x=working[0];pill,tone='Đang làm','bl';now_title=f"{x['number']} · {x['customer']}";now_detail='chưa bấm Đã đến khách'
        elif on_shift:
            pill,tone=('Trong ca · chưa có việc','gr') if 'technician' in u['roles'] and 'director' not in u['roles'] else ('Ở văn phòng','gr')
        elif mine:pill,tone='Đã ra ca','off'
        else:pill,tone='Chưa vào ca','off'
        evs=dots.get(uid,[])
        if not now_title and evs:
            last=evs[-1];now_title=f"Vừa {last['what']} lúc {_clock(last['at'])}";now_detail=f'{len(evs)} chứng từ hôm nay'
        segs=[]
        for x in vs:
            if x['ended_at'] is None:segs.append(seg(max(x['started_at'],w0),now,'cur',f"{_clock(x['started_at'])} · {x['number']} đang làm"))
            else:segs.append(seg(x['started_at'],x['ended_at'],'done' if x['outcome']=='XONG' else 'part',f"{_clock(x['started_at'])}–{_clock(x['ended_at'])} · {x['number']}"))
        for x in production.get(uid,[]):
            if x['started_at']:segs.append(seg(max(x['started_at'],w0),x['finished_at'] or now,'sx',f"{x['number']} sản xuất"))
        for i in (tech or {}).get('items',[]):
            a=i.get('appointment_at')
            if a and i['status'] in WAITING and w0.date()==a.astimezone(TZ).date():
                if a>=now:segs.append(seg(a,a+timedelta(minutes=45),'plan',f"{_clock(a)} · {i['number']} hẹn"))
                elif not current or current[0]['number']!=i['number']:segs.append(seg(a,a+timedelta(minutes=30),'miss',f"{_clock(a)} · {i['number']} lỡ hẹn"))
        for x in evs:segs.append(dict(left=pct(x['at']),width=0,cls='dot',title=f"{_clock(x['at'])} · {x['what']}",dot=True))
        upcoming=[r for r in own if r['state']=='time'];rest=[r for r in own if r['state']!='time']
        queue=upcoming+[r for r in rest if r['state']=='day']
        warn=next((r for r in rest if r['state']=='late' and not r['none']),None)
        nxt=queue[0] if queue else None
        sub=''
        if nxt:sub=nxt['detail'] if len(queue)==1 else (f"rồi {queue[1]['when']} · {queue[1]['title']}" if queue[1]['state']=='time' else f'và {len(queue)-1} việc khác')
        if tech:done,left=tech['done'],tech['open']
        else:done,left=len(evs),sum(r['n'] for r in own)
        people.append(dict(user_id=uid,name=u['full_name'],roles=u['roles'],shift=shift,pill=pill,tone=tone,now_title=now_title,now_detail=now_detail,
            warn=(f"{warn['when']} · {warn['title']}" if warn else ''),segs=segs,
            next_title=(f"{nxt['when']} · {nxt['title']}" if nxt and nxt['state']=='time' else nxt['title'] if nxt else ''),next_detail=sub,
            done=done,left=left,busy=bool(current or making or working),visits=vs,items=(tech or {}).get('items',[]),own=own,events=evs))
    return dict(people=people,hours=hours,lunch=dict(left=pct(datetime.combine(today,cfg.lunch_start,TZ)),
        width=round(pct(datetime.combine(today,cfg.lunch_end,TZ))-pct(datetime.combine(today,cfg.lunch_start,TZ)),1)),
        now=pct(now) if w0<=now<=w1 else None,start=cfg.work_start.strftime('%H:%M'),end=cfg.work_end.strftime('%H:%M'))


def live_board(session,user,today=None,now=None,approvals=(0,0),technicians=None) -> dict:
    """Tổng quan bản 2: việc cần làm tiếp và việc đang diễn ra cho mọi người đọc; bảng người chỉ giám đốc.
    Kế toán chỉ thấy các việc của mình trong danh sách cần làm tiếp."""
    with session.no_autoflush:
        check_reader(user)
        now=now or _now();today=today or now.astimezone(TZ).date()
        users=_staff(session)
        nexts=_next_rows(session,users,today,now,approvals)
        director=has_role(user,'director')
        if not director:
            for key in ('late','upcoming','day'):nexts[key]=[r for r in nexts[key] if user.id in r['ids']]
            nexts['late_count']=len(nexts['late']);nexts['unassigned']=sum(1 for r in nexts['late'] if r['none'])
            nexts['total']=len(nexts['late'])+len(nexts['upcoming'])+sum(r['n'] for r in nexts['day'])
        result=dict(next=nexts,lanes=_lanes(session,now),now=now)
        if director:
            techs={r['user_id']:r for r in (technicians if technicians is not None else _technicians(session,today,now))}
            result['board']=_people(session,users,techs,nexts,today,now)
        return result


# ---- Đơn trong ngày (TUẤN 30/10/2026): mọi đơn bán và phiếu dịch vụ phát sinh hoặc có việc hôm nay, kèm trạng thái.
ORDER_DONE=('DA_GIAO','DA_HUY')
SERVICE_DONE=('HOAN_THANH','DA_DONG','KHONG_LAM_DUOC','DA_HUY')


def _tone(status,hold='',late=False):
    if status in ('DA_HUY','KHONG_LAM_DUOC') or late:return 'bad'
    if status in ORDER_DONE+SERVICE_DONE:return 'ok'
    if status in ('MOI','NHAP'):return 'new'
    if status in ('TAM_DUNG','CHO_LINH_KIEN') or status in WAITING:return 'wait'
    return 'doing'


def day_orders(session,user,today=None,now=None) -> list[dict]:
    """Đơn bán: tạo, chốt, giao hoặc hẹn giao hôm nay. Phiếu dịch vụ: nhận, hẹn hoặc có bước xử lý hôm nay.
    Mới nhất lên đầu; danh sách trong ngày nhỏ nên lọc và phân trang trong bộ nhớ."""
    from sqlalchemy import exists, or_
    from .models import Customer, SalesOrder, ServiceStep
    with session.no_autoflush:
        check_reader(user)
        now=now or _now();today=today or now.astimezone(TZ).date()
        o=SalesOrder;t=ServiceTicket;seller=User.__table__.alias('seller');tech=User.__table__.alias('tech')
        orders=_rows(session,select(o.id,o.number,o.status,o.fulfillment,o.total,o.promised_date,o.created_at,o.confirmed_at,o.delivered_at,
            Customer.name.label('customer'),seller.c.full_name.label('who'))
            .join(Customer,Customer.code==o.customer_code).join(seller,seller.c.id==o.created_by)
            .where(or_(and_(*_day_window(o.created_at,today)),and_(*_day_window(o.confirmed_at,today)),and_(*_day_window(o.delivered_at,today)),
                and_(o.promised_date==today,o.status.in_(('DA_CHOT','GIAO_MOT_PHAN'))))))
        stepped=exists().where(ServiceStep.ticket_id==t.id,*_day_window(ServiceStep.at,today))
        tickets=_rows(session,select(t.id,t.number,t.status,t.hold_reason,t.kind,t.symptom,t.total,t.appointment_at,t.due_at,t.created_at,
            Customer.name.label('customer'),tech.c.full_name.label('who'))
            .join(Customer,Customer.code==t.customer_code).outerjoin(tech,tech.c.id==t.assignee_id)
            .where(or_(and_(*_day_window(t.created_at,today)),and_(*_day_window(t.appointment_at,today)),stepped)))
    rows=[]
    for r in orders:
        note=[]
        if r['promised_date']==today and r['status'] not in ORDER_DONE:note.append('hẹn giao hôm nay')
        if r['delivered_at'] and r['delivered_at'].astimezone(TZ).date()==today:note.append('giao lúc '+_clock(r['delivered_at']))
        rows.append(dict(type='ban',id=r['id'],number=r['number'],customer=r['customer'],status=r['status'],hold_reason='',fulfillment=r['fulfillment'],
            detail=' · '.join(note),amount=int(r['total'] or 0),who=_short(r['who']),created_at=r['created_at'],
            done=r['status'] in ORDER_DONE,tone=_tone(r['status'])))
    for r in tickets:
        late=bool(r['due_at'] and r['due_at']<now and r['status'] not in SERVICE_DONE)
        note=[_cut(r['symptom'],48)] if r['symptom'] else []
        if r['appointment_at'] and r['appointment_at'].astimezone(TZ).date()==today:note.insert(0,'hẹn '+_clock(r['appointment_at']))
        rows.append(dict(type='dv',id=r['id'],number=r['number'],customer=r['customer'],status=r['status'],hold_reason=r['hold_reason'] or '',kind=r['kind'],
            detail=' · '.join(note),amount=int(r['total']) if r['total'] is not None else None,who=_short(r['who']) if r['who'] else 'Chưa giao thợ',
            none=not r['who'],created_at=r['created_at'],done=r['status'] in SERVICE_DONE,late=late,tone=_tone(r['status'],late=late)))
    for r in rows:
        at=r['created_at'].astimezone(TZ)
        r['when']=at.strftime('%H:%M') if at.date()==today else at.strftime('%d/%m')
    rows.sort(key=lambda r:(r['created_at'],r['id']),reverse=True)
    return rows
