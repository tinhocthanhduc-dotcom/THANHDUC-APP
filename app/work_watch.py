"""Dấu vân tay hàng việc: chỉ SELECT, để trang tự biết có việc mới mà không phải tải lại tay."""
import hashlib

from sqlalchemy import select

from . import my_work, service_tickets
from .models import SalesOrder, ServiceTicket

DELIVERING = ('DA_CHOT', 'GIAO_MOT_PHAN')


def _digest(items):
    return hashlib.sha1(repr(sorted(items)).encode()).hexdigest()[:16]


def technician(session, user):
    """Màn Hôm nay: phiếu đang mở và đơn thợ giao của chính thợ; đổi trạng thái/hẹn cũng tính."""
    t = ServiceTicket
    rows = [('service', *r) for r in session.execute(select(t.id, t.status, t.version).where(
        t.assignee_id == user.id, t.status.in_(service_tickets.OPEN))).all()]
    o = SalesOrder
    rows += [('delivery', *r) for r in session.execute(select(o.id, o.status, o.version).where(
        o.delivery_assignee_id == user.id, o.fulfillment == 'THO_GIAO', o.status.in_(DELIVERING))).all()]
    return _digest(rows)


def my_queue(session, user, groups=None):
    """Việc của tôi: nhóm nào thêm/bớt chứng từ thì dấu đổi (không tính đổi số tiền)."""
    groups = my_work.collect(session, user) if groups is None else groups
    return _digest([(g['key'], g['count'], tuple(r['id'] for r in g['rows'])) for g in groups])


def dispatch(session):
    """Điều phối: phiếu đang mở và đơn thợ giao, theo trạng thái và người nhận."""
    t = ServiceTicket
    rows = [('service', *r) for r in session.execute(select(t.id, t.status, t.assignee_id).where(
        t.status.in_(service_tickets.OPEN))).all()]
    o = SalesOrder
    rows += [('delivery', *r) for r in session.execute(select(o.id, o.status, o.delivery_assignee_id).where(
        o.fulfillment == 'THO_GIAO', o.status.in_(DELIVERING))).all()]
    return _digest(rows)
