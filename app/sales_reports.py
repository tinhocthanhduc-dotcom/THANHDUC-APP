"""B5: báo cáo chỉ đọc, tổng hợp tại PostgreSQL và phân quyền trong từng truy vấn."""
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Date, Numeric, case, cast, exists, func, literal, select, true
from sqlalchemy.orm import Session

from . import sales_receipts, sales_orders
from .errors import BusinessError
from .models import Customer, Quote, SalesOrder, SalesReceipt, User, SalesDelivery
from .security import has_role

TZ = ZoneInfo('Asia/Ho_Chi_Minh')
BUCKETS = {'0_7':'0–7 ngày', '8_30':'8–30 ngày', '31_60':'31–60 ngày', 'over_60':'Trên 60 ngày'}


def _today():
    return datetime.now(TZ).date()


def check_reader(user):
    if not has_role(user, 'sales', 'finance'):
        raise BusinessError('FORBIDDEN', 'Chỉ kinh doanh, kế toán hoặc giám đốc được xem báo cáo bán hàng.', status=403)


def _scope(user, column):
    check_reader(user)
    return true() if has_role(user, 'finance') else column == user.id


def date_range(start: date | None=None, end: date | None=None):
    today = _today()
    start = start or today.replace(day=1)
    end = end or today.replace(day=monthrange(today.year,today.month)[1])
    if start > end:
        raise BusinessError('BAD_DATE_RANGE', 'Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')
    return start,end


def _window(column, start, end):
    conditions = [column >= datetime.combine(start,time.min,TZ)]
    if end < date.max:
        conditions.append(column < datetime.combine(end+timedelta(days=1),time.min,TZ))
    return conditions


def _rows(session, statement):
    # Kể cả caller đang có đối tượng dirty, báo cáo cũng không tự flush ghi dữ liệu.
    with session.no_autoflush:
        return [dict(row) for row in session.execute(statement).mappings()]


def _money_rows(rows):
    for row in rows:
        for key in ('orders','subtotal','vat_total','total','receivable','paid','pending','remaining'):
            if key in row:
                row[key] = int(row[key])
    return rows


def _revenue(user,start,end):
    return (SalesDelivery.status == 'DA_GIAO', _scope(user,SalesOrder.created_by),
            *_window(SalesDelivery.delivered_at,start,end))


def _money():
    return (func.count(func.distinct(SalesOrder.id)).label('orders'),
            func.coalesce(func.sum(SalesDelivery.subtotal),0).label('subtotal'),
            func.coalesce(func.sum(SalesDelivery.vat_total),0).label('vat_total'),
            func.coalesce(func.sum(SalesDelivery.total),0).label('total'))


def daily(session: Session,user,start: date | None=None,end: date | None=None):
    start,end = date_range(start,end)
    day = cast(func.timezone('Asia/Ho_Chi_Minh',SalesDelivery.delivered_at),Date).label('day')
    rows = _rows(session,select(day,*_money()).select_from(SalesDelivery).join(SalesOrder,SalesOrder.id==SalesDelivery.order_id).where(*_revenue(user,start,end)).group_by(day).order_by(day))
    return _money_rows(rows)


def totals(session: Session,user,start: date | None=None,end: date | None=None):
    start,end = date_range(start,end)
    return _money_rows(_rows(session,select(*_money()).select_from(SalesDelivery).join(SalesOrder,SalesOrder.id==SalesDelivery.order_id).where(*_revenue(user,start,end))))[0]


def by_employee(session: Session,user,start: date | None=None,end: date | None=None):
    start,end = date_range(start,end)
    stmt = (select(SalesOrder.created_by.label('user_id'),User.full_name.label('name'),*_money())
            .select_from(SalesDelivery).join(SalesOrder,SalesOrder.id==SalesDelivery.order_id).join(User,User.id==SalesOrder.created_by).where(*_revenue(user,start,end))
            .group_by(SalesOrder.created_by,User.full_name).order_by(func.sum(SalesDelivery.total).desc(),SalesOrder.created_by))
    return _money_rows(_rows(session,stmt))


def by_customer(session: Session,user,start: date | None=None,end: date | None=None):
    start,end = date_range(start,end)
    stmt = (select(SalesOrder.customer_code.label('customer_code'),Customer.name.label('name'),*_money())
            .select_from(SalesDelivery).join(SalesOrder,SalesOrder.id==SalesDelivery.order_id).join(Customer,Customer.code==SalesOrder.customer_code).where(*_revenue(user,start,end))
            .group_by(SalesOrder.customer_code,Customer.name).order_by(func.sum(SalesDelivery.total).desc(),SalesOrder.customer_code).limit(20))
    return _money_rows(_rows(session,stmt))


def _debts(user,as_of):
    # Cùng định nghĩa đã thu/chờ của B4, tổng hợp GROUP BY trước khi nối đơn để không nhân dòng.
    paid,pending = sales_receipts._sums()
    receipts = select(SalesReceipt.order_id,paid.label('paid'),pending.label('pending')).group_by(SalesReceipt.order_id).subquery()
    paid = func.coalesce(receipts.c.paid,0)
    pending = func.coalesce(receipts.c.pending,0)
    days = func.greatest(0,literal(as_of,type_=Date)-cast(func.timezone('Asia/Ho_Chi_Minh',sales_orders.first_delivery()),Date))
    bucket = case((days<=7,'0_7'),(days<=30,'8_30'),(days<=60,'31_60'),else_='over_60')
    return (select(SalesOrder.id.label('id'),SalesOrder.number.label('number'),SalesOrder.customer_code.label('customer_code'),
                   SalesOrder.created_by.label('created_by'),sales_orders.first_delivery().label('confirmed_at'),days.label('days'),
                   SalesOrder.receivable.label('receivable'),paid.label('paid'),pending.label('pending'),
                   (SalesOrder.receivable-paid).label('remaining'),bucket.label('bucket'))
            .outerjoin(receipts,receipts.c.order_id==SalesOrder.id)
            .where(SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN','DA_GIAO')),SalesOrder.receivable>paid,_scope(user,SalesOrder.created_by)).subquery())


def aging(session: Session,user,as_of: date | None=None):
    debts = _debts(user,as_of or _today())
    rows = _money_rows(_rows(session,select(debts.c.bucket,func.count().label('orders'),
        func.sum(debts.c.remaining).label('remaining'),func.sum(debts.c.pending).label('pending')).group_by(debts.c.bucket)))
    grouped = {row['bucket']:row for row in rows}
    # Chỉ thêm nhóm rỗng để hiển thị đủ bốn ô; không cộng số liệu trong Python.
    return [dict(label=label,**grouped.get(key,dict(bucket=key,orders=0,remaining=0,pending=0))) for key,label in BUCKETS.items()]


def aging_orders(session: Session,user,bucket: str,as_of: date | None=None):
    check_reader(user)
    if bucket not in BUCKETS:
        raise BusinessError('BAD_BUCKET','Nhóm tuổi nợ không hợp lệ.')
    debts = _debts(user,as_of or _today())
    stmt = (select(debts,Customer.name.label('customer_name'),User.full_name.label('employee_name'))
            .join(Customer,Customer.code==debts.c.customer_code).join(User,User.id==debts.c.created_by)
            .where(debts.c.bucket==bucket).order_by(debts.c.confirmed_at,debts.c.id))
    return _money_rows(_rows(session,stmt))


def quote_conversion(session: Session,user,start: date | None=None,end: date | None=None):
    start,end = date_range(start,end)
    active_order = exists(select(SalesOrder.id).where(SalesOrder.quote_id==Quote.id,SalesOrder.status!='DA_HUY',
                                                      _scope(user,SalesOrder.created_by)))
    count = func.count(Quote.id)
    converted = func.count(Quote.id).filter(active_order)
    rate = func.coalesce(func.round(cast(converted,Numeric)*100/func.nullif(count,0),2),Decimal('0.00'))
    stmt = select(count.label('quotes'),converted.label('converted'),rate.label('rate')).where(
        *_window(Quote.created_at,start,end),Quote.replaced_by_id.is_(None),_scope(user,Quote.created_by))
    return _rows(session,stmt)[0]
