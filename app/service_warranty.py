"""KT6: kiểm hạn tại lúc tạo/đổi liên kết, không sửa giá hay lịch sử phiếu gốc."""
from datetime import timedelta
from calendar import monthrange

from sqlalchemy import and_, case, or_, select

from . import audit, service_sla
from .config import get_settings
from .customers import like_pattern
from .errors import BusinessError
from .models import Customer, Equipment, ServiceStep, ServiceTicket
from .security import has_role
from .stock_docs import _transaction
from . import search as tim


def today():
    from .service_tickets import _now
    return _now().astimezone(service_sla.TZ).date()


def completion(session, ticket, before=None):
    stmt = select(ServiceStep).where(ServiceStep.ticket_id == ticket.id,
        ServiceStep.to_status == 'HOAN_THANH', ServiceStep.from_status.is_distinct_from('HOAN_THANH'))
    if before is not None: stmt = stmt.where(ServiceStep.at <= before)
    step = session.scalar(stmt.order_by(ServiceStep.at.desc(), ServiceStep.id.desc()).limit(1))
    stamp = step.at if step else ticket.priced_at
    if step and getattr(ticket, 'done_at', None) is not None and before is None:
        stamp = ticket.done_at  # giám đốc đã sửa ngày hoàn thành
    return stamp, step.assignee_id if step else ticket.assignee_id


def add_months(day, months):
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    return day.replace(year=year, month=month+1, day=min(day.day, monthrange(year, month+1)[1]))


def warranty_end(session, ticket):
    stamp, _ = completion(session,ticket)
    if stamp is None or ticket.warranty_months==0:return None
    day=stamp.astimezone(service_sla.TZ).date()
    return add_months(day,ticket.warranty_months) if ticket.warranty_months is not None else day+timedelta(days=get_settings().service_warranty_days)


def terms(session,ticket):
    if ticket.warranty_months==0 or ticket.kind=='MUC_DINH_KY':return 'Không bảo hành'
    # TUẤN 28/10: phiếu chưa xong chưa có số tháng thì không hiện mặc định 90 ngày; thợ chọn khi hoàn thành.
    if ticket.warranty_months is None and ticket.status not in ('HOAN_THANH','DA_DONG'):return 'Bảo hành: thợ chọn khi hoàn thành'
    length=str(ticket.warranty_months)+' tháng' if ticket.warranty_months is not None else str(get_settings().service_warranty_days)+' ngày'
    end=warranty_end(session,ticket) if ticket.status in ('HOAN_THANH','DA_DONG') else None
    return 'Bảo hành: '+length+(', đến '+end.strftime('%d/%m/%Y') if end else ', tính từ ngày hoàn thành')+(' · '+ticket.warranty_scope if ticket.warranty_scope else '')


def void_of(session, serial):
    """Thiết bị đã bị từ chối bảo hành (TUẤN 19/11/2026), không có thì None."""
    machine = session.get(Equipment, serial) if serial else None
    return machine if machine is not None and machine.warranty_void_on is not None else None


def voided_before(session, machine, parent):
    """Phiếu sửa xong trước (hoặc trong) ngày từ chối thì hết quyền bảo hành; sửa lại sau đó thì bảo hành phiếu mới bình thường."""
    if machine is None or machine.warranty_void_on is None or parent is None:
        return False
    stamp, _ = completion(session, parent)
    return stamp is None or stamp.astimezone(service_sla.TZ).date() <= machine.warranty_void_on


def void_text(machine):
    if machine is None or machine.warranty_void_on is None:
        return ''
    return 'Máy đã bị từ chối bảo hành ngày ' + machine.warranty_void_on.strftime('%d/%m/%Y') + (
        ': ' + machine.warranty_void_reason if machine.warranty_void_reason else '')


def mark_void(session, user, ticket, reason):
    """Đánh dấu máy của phiếu hết quyền bảo hành. Caller giữ transaction."""
    if not ticket.serial:
        raise BusinessError('SERIAL_REQUIRED', 'Phiếu chưa có serial máy nên không đánh dấu máy hết bảo hành được.')
    machine = session.get(Equipment, ticket.serial, with_for_update=True)
    if machine is None:
        raise BusinessError('BAD_SERIAL', 'Không tìm thấy thiết bị của phiếu.')
    from . import equipment
    before = equipment.snapshot(machine)
    machine.warranty_void_on, machine.warranty_void_reason, machine.warranty_void_ticket_id = today(), reason[:2000], ticket.id
    session.flush()
    audit.record(session, user, 'equipment.warranty_void', 'equipment', machine.serial, before, equipment.snapshot(machine), reason)


def clear_void(session, user, serial, version):
    from . import equipment
    equipment.check_editor(user)
    with _transaction(session):
        machine = session.get(Equipment, equipment.normalize_serial(serial), with_for_update=True)
        if machine is None:
            raise BusinessError('NOT_FOUND', 'Không tìm thấy thiết bị.', status=404)
        if version is not None and machine.version != version:
            raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa thiết bị, hãy tải lại.', status=409)
        if machine.warranty_void_on is None:
            return machine
        before = equipment.snapshot(machine)
        machine.warranty_void_on, machine.warranty_void_reason, machine.warranty_void_ticket_id = None, '', None
        session.flush()
        audit.record(session, user, 'equipment.warranty_void_clear', 'equipment', machine.serial, before, equipment.snapshot(machine), '')
    return machine


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
        end = warranty_end(session,parent)
        if voided_before(session, void_of(session, ticket.serial or parent.serial), parent): return 'HET_HAN', end, ''
        if end is not None and end >= today(): return 'PHIEU_TRUOC', end, 'Bảo hành ' + parent.number
        return 'HET_HAN', end, ''
    machine = session.get(Equipment, ticket.serial) if ticket.serial else None
    end = machine.warranty_until if machine else None
    if machine is not None and machine.warranty_void_on is not None: return 'HET_HAN', end, ''
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
            and_(*tim.like_all(Customer.search_text, q))))
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
        end = warranty_end(session,parent)
    else:
        machine = session.get(Equipment, ticket.serial) if ticket.serial else None
        end = machine.warranty_until if machine else None
    machine = void_of(session, ticket.serial or (parent.serial if parent else None))
    return dict(parent=parent, completed_at=stamp, technician_id=technician_id, until=end,
                days=(end - today()).days if end else None, void=void_text(machine), void_ticket_id=machine.warranty_void_ticket_id if machine else None)
