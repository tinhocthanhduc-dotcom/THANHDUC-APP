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
