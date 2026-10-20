"""KT6: kiểm hạn tại lúc tạo/đổi liên kết, không sửa giá hay lịch sử phiếu gốc."""
from datetime import timedelta

from sqlalchemy import case, or_, select

from . import audit, service_sla
from .config import get_settings
from .customers import like_pattern
from .errors import BusinessError
from .models import Customer, Equipment, ServiceStep, ServiceTicket
from .security import has_role
from .stock_docs import _transaction
from .text import fold


def today():
    from .service_tickets import _now
    return _now().astimezone(service_sla.TZ).date()


def completion(session, ticket, before=None):
    stmt = select(ServiceStep).where(ServiceStep.ticket_id == ticket.id,
        ServiceStep.to_status == 'HOAN_THANH', ServiceStep.from_status.is_distinct_from('HOAN_THANH'))
    if before is not None: stmt = stmt.where(ServiceStep.at <= before)
    step = session.scalar(stmt.order_by(ServiceStep.at.desc(), ServiceStep.id.desc()).limit(1))
    stamp = step.at if step else ticket.priced_at
    return stamp, step.assignee_id if step else ticket.assignee_id


def assess(session, ticket, warranty_of_id):
    if ticket.kind != 'BAO_HANH':
        if warranty_of_id is not None:
            raise BusinessError('WARRANTY_MISMATCH', 'Chỉ phiếu Bảo hành được chọn phiếu trước.')
        return None, None, ''
    if warranty_of_id is not None:
        parent = session.scalar(select(ServiceTicket).where(ServiceTicket.id == warranty_of_id).with_for_update(read=True))
        if parent is None or parent.id == ticket.id or parent.customer_code != ticket.customer_code or (
                parent.serial and ticket.serial and parent.serial != ticket.serial):
            raise BusinessError('WARRANTY_MISMATCH', 'Phiếu trước phải cùng khách và cùng serial nếu cả hai có serial.')
        if parent.status not in ('HOAN_THANH', 'DA_DONG'):
            raise BusinessError('WARRANTY_NOT_COMPLETED', 'Phiếu trước chưa hoàn thành.', status=409)
        stamp, _ = completion(session, parent)
        end = stamp.astimezone(service_sla.TZ).date() + timedelta(days=get_settings().service_warranty_days) if stamp else None
        if end is not None and end >= today(): return 'PHIEU_TRUOC', end, 'Bảo hành ' + parent.number
        return 'HET_HAN', end, ''
    machine = session.get(Equipment, ticket.serial) if ticket.serial else None
    end = machine.warranty_until if machine else None
    if end is not None and end >= today(): return 'MAY_CON_HAN', end, 'Bảo hành máy đến ' + end.strftime('%d/%m/%Y')
    return 'HET_HAN', end, ''


def apply(session, ticket, parent_id):
    basis, _, reason = assess(session, ticket, parent_id)
    ticket.warranty_of_id, ticket.warranty_basis = parent_id, basis
    if basis in ('PHIEU_TRUOC', 'MAY_CON_HAN'):
        ticket.billing, ticket.free_reason = 'MIEN_PHI', reason
    elif ticket.kind == 'BAO_HANH':
        ticket.billing, ticket.free_reason = None, ''


def relink(session, user, ticket_id, body):
    from . import service_tickets as svc
    if not has_role(user, 'dispatcher'):
        raise BusinessError('FORBIDDEN', 'Chỉ điều phối hoặc giám đốc được đổi phiếu bảo hành.', status=403)
    with _transaction(session):
        # Khóa theo ID để hai yêu cầu đổi liên kết ngược nhau không khóa chéo.
        ids = sorted({ticket_id, body.warranty_of_id} - {None})
        list(session.scalars(select(ServiceTicket.id).where(ServiceTicket.id.in_(ids)).order_by(ServiceTicket.id).with_for_update()))
        ticket = svc._get(session, user, ticket_id, lock=True)
        svc._check_version(ticket, body.version)
        if ticket.kind != 'BAO_HANH': raise BusinessError('WARRANTY_MISMATCH', 'Đây không phải phiếu Bảo hành.')
        if ticket.status not in svc.OPEN: raise BusinessError('PRICE_LOCKED', 'Chỉ đổi liên kết khi phiếu bảo hành còn mở.', status=409)
        before = svc.snapshot(ticket)
        apply(session, ticket, body.warranty_of_id)
        ticket.version += 1
        session.flush()
        audit.record(session, user, 'service_ticket.warranty', 'service_ticket', str(ticket.id), before, svc.snapshot(ticket), body.note)
    return ticket


def suggestions(session, customer_code, serial=None, q=''):
    stmt = select(ServiceTicket).join(Customer, Customer.code == ServiceTicket.customer_code).where(
        ServiceTicket.status.in_(('HOAN_THANH', 'DA_DONG')), ServiceTicket.customer_code == customer_code)
    if q.strip():
        needle = like_pattern(q.strip())
        stmt = stmt.where(or_(ServiceTicket.number.ilike(needle), ServiceTicket.serial.ilike(needle),
            Customer.search_text.like(like_pattern(fold(q)))))
    # Ngày hoàn thành quyết định thứ tự, không dựa vào ngày nhận đơn.
    ended = select(ServiceStep.at).where(ServiceStep.ticket_id == ServiceTicket.id,
        ServiceStep.to_status == 'HOAN_THANH', ServiceStep.from_status.is_distinct_from('HOAN_THANH')).order_by(
        ServiceStep.at.desc(), ServiceStep.id.desc()).limit(1).scalar_subquery()
    order = [ended.desc().nulls_last(), ServiceTicket.priced_at.desc().nulls_last(), ServiceTicket.id.desc()]
    if serial: order.insert(0, case((ServiceTicket.serial == serial, 0), else_=1))
    return list(session.scalars(stmt.order_by(*order).limit(20)))


def info(session, ticket):
    parent = session.get(ServiceTicket, ticket.warranty_of_id) if ticket.warranty_of_id else None
    stamp, technician_id = completion(session, parent) if parent else (None, None)
    if parent:
        end = stamp.astimezone(service_sla.TZ).date() + timedelta(days=get_settings().service_warranty_days) if stamp else None
    else:
        machine = session.get(Equipment, ticket.serial) if ticket.serial else None
        end = machine.warranty_until if machine else None
    return dict(parent=parent, completed_at=stamp, technician_id=technician_id, until=end,
                days=(end - today()).days if end else None)
