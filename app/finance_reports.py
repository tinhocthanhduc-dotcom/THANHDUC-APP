"""T5: báo cáo tài chính chỉ SELECT, tổng hợp tại PostgreSQL."""
from calendar import monthrange
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Date, case, cast, func, literal, select, union_all

from . import sales_orders
from .errors import BusinessError
from .models import PendingDisposition, CashCategory, CashDoc, Customer, Fund, FundBalance, FundLedger, SalesOrder, SalesReceipt, ServiceTicket
from .security import has_role

TZ = ZoneInfo('Asia/Ho_Chi_Minh')


def _today():
    return datetime.now(TZ).date()


def check_reader(user):
    if not has_role(user, 'finance'):
        raise BusinessError('FORBIDDEN', 'Chỉ kế toán hoặc giám đốc được xem báo cáo tài chính.', status=403)


def date_range(start=None, end=None):
    today = _today()
    start = start or today.replace(day=1)
    end = end or today.replace(day=monthrange(today.year, today.month)[1])
    if start > end:
        raise BusinessError('BAD_DATE_RANGE', 'Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')
    return start, end


def _rows(session, statement):
    # Không flush cả khi caller đang giữ đối tượng chưa lưu.
    with session.no_autoflush:
        rows = [dict(r) for r in session.execute(statement).mappings()]
    for row in rows:
        for key in ('balance', 'documents', 'amount', 'income', 'expense', 'net', 'closing_balance',
                    'receivable', 'paid', 'pending', 'remaining', 'days'):
            if key in row:
                row[key] = int(row[key])
    return rows


def funds(session, user):
    check_reader(user)
    return _rows(session, select(Fund.code, Fund.name, Fund.active, func.coalesce(FundBalance.balance, 0).label('balance'))
                 .outerjoin(FundBalance, FundBalance.fund_code == Fund.code).order_by(Fund.code))


def fund_total(session, user):
    check_reader(user)
    return _rows(session, select(func.coalesce(func.sum(FundBalance.balance), 0).label('balance')))[0]['balance']


def _income():
    return case(((CashDoc.kind == 'PT') & CashDoc.is_opening.is_(False), FundLedger.amount), else_=0)


def _expense():
    return case(((CashDoc.kind == 'PC') & CashDoc.is_opening.is_(False), -FundLedger.amount), else_=0)


def categories(session, user, direction, start=None, end=None):
    check_reader(user)
    if direction not in ('THU', 'CHI'):
        raise BusinessError('BAD_DIRECTION', 'Chiều thu chi không hợp lệ.')
    start, end = date_range(start, end)
    amount = FundLedger.amount if direction == 'THU' else -FundLedger.amount
    return _rows(session, select(CashDoc.category_code.label('code'), CashCategory.name,
            func.count(func.distinct(FundLedger.doc_id)).label('documents'), func.sum(amount).label('amount'))
        .select_from(FundLedger).join(CashDoc, CashDoc.id == FundLedger.doc_id)
        .join(CashCategory, CashCategory.code == CashDoc.category_code)
        .where(FundLedger.entry_date.between(start, end), CashDoc.kind == ('PT' if direction == 'THU' else 'PC'),
               CashDoc.is_opening.is_(False))
        .group_by(CashDoc.category_code, CashCategory.name).order_by(CashDoc.category_code))


def category_totals(session, user, start=None, end=None):
    check_reader(user)
    start, end = date_range(start, end)
    return _rows(session, select(func.coalesce(func.sum(_income()), 0).label('income'),
            func.coalesce(func.sum(_expense()), 0).label('expense'),
            func.coalesce(func.sum(_income() - _expense()), 0).label('net'),
            func.count(func.distinct(FundLedger.doc_id)).filter(CashDoc.kind == 'PT').label('income_documents'),
            func.count(func.distinct(FundLedger.doc_id)).filter(CashDoc.kind == 'PC').label('expense_documents'))
        .select_from(FundLedger).join(CashDoc, CashDoc.id == FundLedger.doc_id)
        .where(FundLedger.entry_date.between(start, end), CashDoc.kind.in_(('PT', 'PC')), CashDoc.is_opening.is_(False)))[0]


def daily(session, user, start=None, end=None):
    check_reader(user)
    start, end = date_range(start, end)
    # Lịch đầy đủ và cộng dồn bằng SQL, cả ngày không có phát sinh.
    offsets = func.generate_series(0, (end - start).days).table_valued('offset').render_derived()
    days = select((literal(start, type_=Date) + offsets.c.offset).label('day')).cte('report_days')
    flow = (select(FundLedger.entry_date.label('day'), func.sum(_income()).label('income'),
                   func.sum(_expense()).label('expense'), func.sum(FundLedger.amount).label('change'))
        .join(CashDoc, CashDoc.id == FundLedger.doc_id).where(FundLedger.entry_date.between(start, end))
        .group_by(FundLedger.entry_date).subquery())
    opening = select(func.coalesce(func.sum(FundLedger.amount), 0)).where(FundLedger.entry_date < start).scalar_subquery()
    income, expense = func.coalesce(flow.c.income, 0), func.coalesce(flow.c.expense, 0)
    closing = opening + func.sum(func.coalesce(flow.c.change, 0)).over(order_by=days.c.day)
    return _rows(session, select(days.c.day, income.label('income'), expense.label('expense'),
            (income - expense).label('net'), closing.label('closing_balance'))
        .outerjoin(flow, flow.c.day == days.c.day).order_by(days.c.day))


def _debts():
    def source(model, receipt_key, status, stamp, kind):
        from . import sales_receipts
        receipts=sales_receipts.totals(model)
        paid, pending = func.coalesce(receipts.c.paid, 0), func.coalesce(receipts.c.pending, 0)
        day = cast(func.timezone('Asia/Ho_Chi_Minh', stamp), Date)
        return (select(literal(kind).label('kind'), model.id, model.number, model.customer_code, day.label('day'),
                model.receivable, paid.label('paid'), pending.label('pending'), (model.receivable - paid).label('remaining'))
            .outerjoin(receipts, receipts.c.parent_id == model.id)
            .where(model.status.in_(status), model.receivable > paid))
    return union_all(source(SalesOrder, SalesReceipt.order_id, ('DA_CHOT', 'GIAO_MOT_PHAN', 'DA_GIAO'), sales_orders.first_delivery(), 'DB'),
                     source(ServiceTicket, SalesReceipt.ticket_id, ('HOAN_THANH', 'DA_DONG'), ServiceTicket.priced_at, 'DV')).subquery()


def customers(session, user):
    check_reader(user)
    debts = _debts()
    return _rows(session, select(debts.c.customer_code, Customer.name,
            func.count().label('documents'), func.sum(debts.c.receivable).label('receivable'),
            func.sum(debts.c.paid).label('paid'), func.sum(debts.c.pending).label('pending'), func.sum(debts.c.remaining).label('remaining'))
        .join(Customer, Customer.code == debts.c.customer_code).group_by(debts.c.customer_code, Customer.name)
        .order_by(func.sum(debts.c.remaining).desc(), debts.c.customer_code))


def debt_totals(session, user):
    check_reader(user)
    debts = _debts()
    return _rows(session, select(*[func.coalesce(func.sum(debts.c[k]), 0).label(k)
                                  for k in ('receivable', 'paid', 'pending', 'remaining')]))[0]


def customer_docs(session, user, customer_code):
    check_reader(user)
    debts = _debts()
    days = func.greatest(0, literal(_today(), type_=Date) - debts.c.day)
    return _rows(session, select(debts, days.label('days')).where(debts.c.customer_code == customer_code)
                 .order_by(debts.c.day, debts.c.kind, debts.c.id))


def pending_return_difference(session,user,start=None,end=None):
    check_reader(user);start,end=date_range(start,end)
    day=cast(func.timezone('Asia/Ho_Chi_Minh',PendingDisposition.decided_at),Date)
    with session.no_autoflush:
        return int(session.scalar(select(func.coalesce(func.sum(PendingDisposition.amount-PendingDisposition.cost_value),0)).where(PendingDisposition.kind=='TRA_NCC',PendingDisposition.status=='DA_GHI',day.between(start,end))))


def damage_cost(session,user,start=None,end=None):
    check_reader(user);start,end=date_range(start,end)
    day=cast(func.timezone('Asia/Ho_Chi_Minh',PendingDisposition.decided_at),Date)
    with session.no_autoflush:
        return int(session.scalar(select(func.coalesce(func.sum(PendingDisposition.cost_value),0)).where(
            PendingDisposition.kind=='HUY',PendingDisposition.status=='DA_GHI',day>=start,day<=end)))
