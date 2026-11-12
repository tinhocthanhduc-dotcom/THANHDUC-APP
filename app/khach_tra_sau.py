"""Khách trả sau (TUẤN duyệt mẫu 12/11/2026): thợ báo thu phiếu của mình đã đóng lúc chọn "Chưa thu".

Chỉ là lối vào: danh sách mở khi thợ bấm (không hiện lại trên Hôm nay, giữ luật 27/10), báo thu đi qua
sales_receipts.report_for_ticket như mọi báo thu dịch vụ (quyền thợ được giao, chặn vượt số còn thu, chờ kế toán xác nhận).
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select

from . import customers as customer_svc
from .errors import BusinessError
from .models import Customer, ServiceStep, ServiceTicket
from .my_work import _available
from .security import has_role

DAYS = 90


def check(user):
    if not has_role(user, 'technician'):
        raise BusinessError('FORBIDDEN', 'Chỉ thợ dùng mục Khách trả sau; văn phòng thu qua Công nợ khách hàng.', status=403)


def _closed_at():
    return (select(func.max(ServiceStep.at)).where(ServiceStep.ticket_id == ServiceTicket.id, ServiceStep.to_status == 'DA_DONG')
            .correlate(ServiceTicket).scalar_subquery())


def tickets(session, user, q='', ticket_id=None, now=None):
    """Phiếu thợ đang được giao, đã đóng trong DAYS ngày, thu tiền, còn số chưa báo (> 0). Mới đóng trước."""
    check(user)
    t = ServiceTicket
    due = _available(t)
    closed = _closed_at()
    since = (now or datetime.now(UTC)) - timedelta(days=DAYS)
    stmt = (select(t.id, t.number, t.kind, t.receivable, t.customer_code, Customer.name, due.label('due'), closed.label('closed_at'))
            .join(Customer, Customer.code == t.customer_code)
            .where(t.assignee_id == user.id, t.status == 'DA_DONG', t.billing == 'THU_TIEN', func.coalesce(t.receivable, 0) > 0,
                   due > 0, closed >= since)
            .order_by(closed.desc(), t.id.desc()))
    if ticket_id is not None:
        stmt = stmt.where(t.id == ticket_id)
    q = (q or '').strip()[:100]
    if q:
        stmt = stmt.where(or_(t.number.ilike('%' + q.replace('%', '').replace('_', '') + '%'), *customer_svc.matches(q)))
    return [dict(id=r.id, number=r.number, kind=r.kind, receivable=int(r.receivable or 0), customer=r.name,
                 due=int(r.due), closed_at=r.closed_at) for r in session.execute(stmt.limit(100))]


def one(session, user, ticket_id):
    rows = tickets(session, user, ticket_id=ticket_id)
    if not rows:
        raise BusinessError('NOT_ELIGIBLE', 'Phiếu này không còn trong mục Khách trả sau (đã báo đủ, quá 90 ngày hoặc không phải phiếu của bạn).', status=409)
    return rows[0]


# ---- Thu hộ theo khách (TUẤN duyệt mẫu 12/11/2026) ----
# Thợ thu tiền khách đang nợ dù đơn/phiếu không phải của mình: một số tổng, trừ chứng từ cũ nhất trước
# qua sales_receipts.report_for_customer(thu_ho=True). Chờ kế toán xác nhận như mọi báo thu; hoa hồng không đổi người hưởng.

def customers(session, user, q='', code=None, limit=50):
    """Khách còn chưa trả (còn nợ − chờ xác nhận > 0). Khách thợ từng làm phiếu hiện trước, rồi nợ cũ trước."""
    from .finance_reports import _debts
    check(user)
    d = _debts()
    unpaid = func.greatest(d.c.remaining - d.c.pending, 0)
    mine = (select(ServiceTicket.id).where(ServiceTicket.customer_code == Customer.code, ServiceTicket.assignee_id == user.id)
            .correlate(Customer).exists())
    since = func.min(d.c.day).filter(unpaid > 0)
    stmt = (select(Customer.code, Customer.name, func.count().filter(unpaid > 0).label('docs'), func.sum(unpaid).label('due'),
                   since.label('since'), mine.label('mine'))
            .join(Customer, Customer.code == d.c.customer_code)
            .group_by(Customer.code, Customer.name).having(func.sum(unpaid) > 0)
            .order_by(mine.desc(), since, Customer.code))
    if code is not None:
        stmt = stmt.where(Customer.code == code)
    q = (q or '').strip()[:100]
    if q:
        stmt = stmt.where(*customer_svc.matches(q))
    return [dict(code=r.code, name=r.name, docs=int(r.docs), due=int(r.due), since=r.since, mine=bool(r.mine))
            for r in session.execute(stmt.limit(limit))]


def customer(session, user, code):
    rows = customers(session, user, code=code)
    if not rows:
        raise BusinessError('NOTHING_DUE', 'Khách này không còn khoản nào chưa trả (có thể người khác vừa báo thu).', status=409)
    return rows[0]


def customer_name(session, receipt):
    from .models import CustomerOpeningDebt, SalesOrder
    parent = (session.get(ServiceTicket, receipt.ticket_id) if receipt.ticket_id else session.get(SalesOrder, receipt.order_id)
              if receipt.order_id else session.get(CustomerOpeningDebt, receipt.opening_id))
    c = session.get(Customer, parent.customer_code) if parent else None
    return c.name if c else ''
