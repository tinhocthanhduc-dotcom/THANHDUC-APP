"""KT4: máy khách không vào kho công ty. Khóa phiếu → máy → dòng bàn giao.

Chỉ chốt một lần dòng CHO_NHAN; nội dung và mọi dòng đã chốt bất biến.
"""
from datetime import UTC, datetime

from sqlalchemy import func, or_, select

from . import audit, service_sla, service_tickets
from .errors import BusinessError
from .models import Attachment, Equipment, EquipmentCustody, ServiceTicket, User
from .schemas import CustodyOut
from .security import has_role
from .stock_docs import _transaction

LOCATIONS = {'TAI_KHACH': 'Tại khách', 'THO_GIU': 'Thợ giữ', 'KHO_KY_THUAT': 'Kho kỹ thuật', 'DOI_TAC': 'Đối tác'}
ACCESSORIES = {'DAY_NGUON': 'Dây nguồn', 'KHAY_GIAY': 'Khay giấy', 'HOP_MUC': 'Hộp mực', 'DAY_USB': 'Dây USB', 'KHAC': 'Khác'}
STATUSES = {'CHO_NHAN': 'Chờ nhận', 'DA_NHAN': 'Đã nhận', 'DA_HUY': 'Đã hủy'}


def _now():
    return datetime.now(UTC)


def _forbidden():
    raise BusinessError('FORBIDDEN', 'Bạn không có quyền thao tác giữ máy này.', status=403)


def _version(row, version):
    if row.version != version:
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa cập nhật giữ máy, hãy tải lại.', {'current': row.version}, status=409)


def _ticket(session, ident, lock=False):
    stmt = select(ServiceTicket).where(ServiceTicket.id == ident)
    if lock:stmt = stmt.with_for_update().execution_options(populate_existing=True)
    ticket = session.scalar(stmt)
    if ticket is None:raise BusinessError('NOT_FOUND', 'Không tìm thấy phiếu dịch vụ.', status=404)
    if not ticket.serial:raise BusinessError('SERIAL_REQUIRED', 'Phiếu phải có serial máy trước khi nhận giữ.')
    return ticket


def _machine(session, serial, lock=False):
    stmt = select(Equipment).where(Equipment.serial == serial)
    if lock:stmt = stmt.with_for_update().execution_options(populate_existing=True)
    machine = session.scalar(stmt)
    if machine is None:raise BusinessError('NOT_FOUND', 'Không tìm thấy máy khách.', status=404)
    return machine


def latest(session, serial):
    return session.scalar(select(EquipmentCustody).where(EquipmentCustody.serial == serial,
        EquipmentCustody.status == 'DA_NHAN').order_by(EquipmentCustody.id.desc()).limit(1))


def pending(session, serial):
    return session.scalar(select(EquipmentCustody).where(EquipmentCustody.serial == serial, EquipmentCustody.status == 'CHO_NHAN'))


def _no_pending(session, serial):
    if pending(session, serial):raise BusinessError('HANDOVER_PENDING', 'Máy đang có một bàn giao chờ nhận.', status=409)


def _is_holder(user, machine):
    return (machine.custody_location == 'THO_GIU' and machine.custody_holder_id == user.id and 'technician' in user.roles) or (
        machine.custody_location == 'KHO_KY_THUAT' and has_role(user, 'warehouse')) or (
        machine.custody_location == 'DOI_TAC' and has_role(user, 'dispatcher'))


def can_confirm(user, row):
    return (row.location == 'THO_GIU' and row.holder_id == user.id and 'technician' in user.roles) or (
        row.location == 'KHO_KY_THUAT' and has_role(user, 'warehouse', 'dispatcher')) or (
        row.location == 'DOI_TAC' and has_role(user, 'dispatcher'))


def can_view(session, user, ticket, machine=None):
    if has_role(user, 'dispatcher', 'warehouse'):return True
    if 'technician' not in user.roles:return False
    if ticket.assignee_id == user.id:return True
    machine = machine or _machine(session, ticket.serial)
    if machine.custody_ticket_id == ticket.id and machine.custody_holder_id == user.id:return True
    return session.scalar(select(EquipmentCustody.id).where(EquipmentCustody.ticket_id == ticket.id,
        or_(EquipmentCustody.holder_id == user.id, EquipmentCustody.created_by == user.id),
        EquipmentCustody.status.in_(('CHO_NHAN','DA_NHAN'))).limit(1)) is not None


def load(session, user, ticket_id):
    t = _ticket(session,ticket_id)
    if not can_view(session,user,t):_forbidden()
    return t


def check_photo(session, user, ticket_id, write=False, tag=None):
    t = load(session,user,ticket_id)
    if not write:return
    eq = _machine(session,t.serial)
    if tag == 'NHAN_MAY' and (has_role(user,'dispatcher') or ('technician' in user.roles and t.assignee_id == user.id)):
        return
    if tag == 'TRA_MAY' and eq.custody_ticket_id == t.id and eq.custody_location == 'THO_GIU' and eq.custody_holder_id == user.id:
        return
    _forbidden()


def _photo(session, ticket_id, tag, since=None):
    stmt = select(Attachment.id).where(Attachment.entity.in_(('service_ticket','equipment_custody')),
        Attachment.entity_id == str(ticket_id), Attachment.tag == tag, Attachment.hidden.is_(False), Attachment.content_type.startswith('image/'))
    if since is not None:stmt = stmt.where(Attachment.created_at >= since)
    if session.scalar(stmt.limit(1)) is None:
        raise BusinessError('PHOTO_REQUIRED', 'Cần chụp ảnh nhận máy trước khi nhận máy về.' if tag == 'NHAN_MAY' else 'Cần chụp ảnh trả máy trước khi trả khách.')


def _record(session,user,row,action,before=None):
    session.flush()
    audit.record(session,user,'custody.'+action,'equipment_custody',str(row.id),before,CustodyOut.model_validate(row).model_dump(mode='json'))


def _accept_cache(session,user,eq,row):
    from .equipment import snapshot
    before = snapshot(eq)
    eq.custody_location, eq.custody_holder_id, eq.custody_ticket_id = row.location,row.holder_id,row.ticket_id
    session.flush()
    audit.record(session,user,'equipment.custody','equipment',eq.serial,before,snapshot(eq))


def _accessories(values,note):
    if values is None:raise BusinessError('ACCESSORIES_REQUIRED','Phải chọn phụ kiện hoặc xác nhận không kèm phụ kiện.')
    if len(values) != len(set(values)) or set(values)-ACCESSORIES.keys():
        raise BusinessError('BAD_ACCESSORIES','Danh sách phụ kiện chưa hợp lệ.')
    if 'KHAC' in values and not note.strip():raise BusinessError('REASON_REQUIRED','Phụ kiện khác phải có ghi chú.')
    return list(values)


def receive(session,user,ticket_id,body):
    with _transaction(session):
        return receive_in_tx(session,user,ticket_id,body)


def receive_in_tx(session,user,ticket_id,body):
    """Cùng luật KT4; caller quản lý transaction, không commit tại đây."""
    t = _ticket(session,ticket_id,lock=True)
    if not has_role(user,'dispatcher') and not ('technician' in user.roles and t.assignee_id == user.id):_forbidden()
    if t.status not in service_tickets.OPEN:raise BusinessError('BAD_STATUS','Phiếu đã đóng, không nhận máy về.',status=409)
    eq = _machine(session,t.serial,lock=True)
    _version(eq,body.version)
    if eq.custody_location != 'TAI_KHACH':
        raise BusinessError('MACHINE_HELD','Máy đang được giữ; phải trả máy theo phiếu đang giữ trước.',{'ticket_id':eq.custody_ticket_id},status=409)
    _no_pending(session,eq.serial)
    tech = service_tickets._technician(session,t.assignee_id)
    if not body.condition_note.strip():raise BusinessError('CONDITION_REQUIRED','Phải ghi tình trạng máy khi nhận.')
    values = _accessories(body.accessories,body.accessories_note)
    old = latest(session,eq.serial)
    _photo(session,t.id,'NHAN_MAY',old.confirmed_at if old else None)
    row = EquipmentCustody(serial=eq.serial,ticket_id=t.id,location='THO_GIU',holder_id=tech.id,
        accessories=values,accessories_note=body.accessories_note,condition_note=body.condition_note,
        status='DA_NHAN',created_by=user.id,confirmed_by=user.id,confirmed_at=_now())
    session.add(row);_record(session,user,row,'receive');_accept_cache(session,user,eq,row)
    return row

def handover(session,user,ticket_id,body):
    with _transaction(session):
        return handover_in_tx(session,user,ticket_id,body)


def handover_in_tx(session,user,ticket_id,body):
    """Cùng luật KT4; caller quản lý transaction, không commit tại đây."""
    t = _ticket(session,ticket_id,lock=True);eq = _machine(session,t.serial,lock=True)
    if not has_role(user,'dispatcher') and not _is_holder(user,eq):_forbidden()
    _version(eq,body.version)
    if eq.custody_ticket_id != t.id or eq.custody_location == 'TAI_KHACH':
        raise BusinessError('BAD_STATUS','Máy không đang giữ theo phiếu này.',status=409)
    _no_pending(session,eq.serial)
    if body.location not in ('THO_GIU','KHO_KY_THUAT','DOI_TAC'):
        raise BusinessError('BAD_LOCATION','Giao trả khách phải dùng thao tác Đã trả khách.')
    if body.location == 'THO_GIU':
        if body.holder_id is None:raise BusinessError('BAD_HOLDER','Phải chọn thợ nhận máy.')
        service_tickets._technician(session,body.holder_id)
        if eq.custody_location == 'THO_GIU' and eq.custody_holder_id == body.holder_id:
            raise BusinessError('SAME_HOLDER','Máy đang do thợ này giữ.',status=409)
    elif body.holder_id is not None:raise BusinessError('BAD_HOLDER','Chỉ chọn người giữ khi bàn giao cho thợ.')
    if body.location == 'DOI_TAC' and not body.partner_name.strip():raise BusinessError('PARTNER_REQUIRED','Phải ghi tên đối tác.')
    if body.location != 'DOI_TAC' and (body.partner_name or body.expected_back):raise BusinessError('BAD_LOCATION','Chỉ ghi đối tác và ngày về khi gửi đối tác.')
    old = latest(session,eq.serial)
    note = old.accessories_note if body.accessories_note is None else body.accessories_note
    values = _accessories(old.accessories if body.accessories is None else body.accessories,note)
    row = EquipmentCustody(serial=eq.serial,ticket_id=t.id,location=body.location,holder_id=body.holder_id,
        partner_name=body.partner_name,expected_back=body.expected_back,accessories=values,accessories_note=note,
        condition_note=old.condition_note if body.condition_note is None else body.condition_note,status='CHO_NHAN',created_by=user.id)
    session.add(row);_record(session,user,row,'handover')
    return row

def _locked(session,ident,version):
    ref = session.get(EquipmentCustody,ident)
    if ref is None:raise BusinessError('NOT_FOUND','Không tìm thấy bàn giao máy.',status=404)
    t = _ticket(session,ref.ticket_id,lock=True);eq = _machine(session,ref.serial,lock=True)
    row = session.scalar(select(EquipmentCustody).where(EquipmentCustody.id==ident).with_for_update().execution_options(populate_existing=True))
    _version(row,version)
    if row.status != 'CHO_NHAN':raise BusinessError('BAD_STATUS','Bàn giao đã được chốt.',status=409)
    return eq,row


def confirm(session,user,ident,version):
    with _transaction(session):
        eq,row = _locked(session,ident,version)
        if not can_confirm(user,row):_forbidden()
        before = CustodyOut.model_validate(row).model_dump(mode='json')
        row.status,row.confirmed_by,row.confirmed_at = 'DA_NHAN',user.id,_now()
        _record(session,user,row,'confirm',before);_accept_cache(session,user,eq,row)
    return row


def cancel(session,user,ident,version):
    with _transaction(session):
        _,row = _locked(session,ident,version)
        if row.created_by != user.id:_forbidden()
        before = CustodyOut.model_validate(row).model_dump(mode='json')
        row.status,row.confirmed_by,row.confirmed_at = 'DA_HUY',user.id,_now()
        _record(session,user,row,'cancel',before)
    return row


def return_customer(session,user,ticket_id,body):
    with _transaction(session):
        return return_customer_in_tx(session,user,ticket_id,body)


def return_customer_in_tx(session,user,ticket_id,body):
    """Cùng luật KT4; caller quản lý transaction, không commit tại đây."""
    t = _ticket(session,ticket_id,lock=True);eq = _machine(session,t.serial,lock=True)
    if eq.custody_location != 'THO_GIU' or eq.custody_holder_id != user.id or 'technician' not in user.roles:_forbidden()
    _version(eq,body.version)
    if eq.custody_ticket_id != t.id:raise BusinessError('BAD_STATUS','Máy không đang giữ theo phiếu này.',status=409)
    from .service_workshop import check_handover_record
    check_handover_record(session,t)
    _no_pending(session,eq.serial)
    # Lần nhận trực tiếp từ khách mở chu kỳ sau dòng TAI_KHACH gần nhất.
    last_return_id = session.scalar(select(func.max(EquipmentCustody.id)).where(EquipmentCustody.serial==eq.serial,
        EquipmentCustody.location=='TAI_KHACH',EquipmentCustody.status=='DA_NHAN')) or 0
    start = session.scalar(select(EquipmentCustody).where(EquipmentCustody.serial==eq.serial,EquipmentCustody.ticket_id==t.id,
        EquipmentCustody.status=='DA_NHAN',EquipmentCustody.id>last_return_id).order_by(EquipmentCustody.id).limit(1))
    values = _accessories(body.accessories,body.accessories_note)
    missing = sorted(set(start.accessories)-set(values))
    if missing and not body.accessories_note.strip():raise BusinessError('MISSING_ACCESSORIES','Thiếu phụ kiện so với lúc nhận; phải ghi chú.',{'missing':missing})
    _photo(session,t.id,'TRA_MAY',start.confirmed_at)
    row = EquipmentCustody(serial=eq.serial,ticket_id=t.id,location='TAI_KHACH',accessories=values,
        accessories_note=body.accessories_note,condition_note=body.condition_note or latest(session,eq.serial).condition_note,
        status='DA_NHAN',created_by=user.id,confirmed_by=user.id,confirmed_at=_now())
    session.add(row);_record(session,user,row,'return');_accept_cache(session,user,eq,row)
    return row

def check_returned(session,ticket):
    # Cùng thứ tự khóa với nhận/bàn giao, không thể đóng lọt khi đang nhận máy.
    if ticket.serial:
        eq = _machine(session,ticket.serial,lock=True)
        if eq.custody_location != 'TAI_KHACH' or pending(session,eq.serial):
            raise BusinessError('MACHINE_NOT_RETURNED','Phải giao trả máy cho khách trước khi kết thúc phiếu.',{'serial':eq.serial},status=409)


def history(session,serial):
    return list(session.scalars(select(EquipmentCustody).where(EquipmentCustody.serial==serial).order_by(EquipmentCustody.id)))


def cycle_starts():
    """Ngày nhận từ khách mở chu kỳ hiện tại, không đặt lại khi đổi người giữ."""
    returned = select(EquipmentCustody.serial,func.max(EquipmentCustody.id).label('id')).where(
        EquipmentCustody.status=='DA_NHAN',EquipmentCustody.location=='TAI_KHACH').group_by(EquipmentCustody.serial).subquery()
    return select(EquipmentCustody.serial,func.min(EquipmentCustody.confirmed_at).label('started')).outerjoin(
        returned,returned.c.serial==EquipmentCustody.serial).where(EquipmentCustody.status=='DA_NHAN',
        EquipmentCustody.id>func.coalesce(returned.c.id,0)).group_by(EquipmentCustody.serial).subquery()


def held(session,user,location='',today=None):
    if not has_role(user,'warehouse','dispatcher'):_forbidden()
    return held_rows(session,location,today)


def held_rows(session,location='',today=None,holder_id=None):
    """Shared read query; callers enforce their view's permission before calling."""
    now = _now();today = today or now.astimezone(service_sla.TZ).date()
    last = select(EquipmentCustody.serial,func.max(EquipmentCustody.id).label('id')).where(
        EquipmentCustody.status=='DA_NHAN').group_by(EquipmentCustody.serial).subquery()
    cycle = cycle_starts()
    rows = select(Equipment,EquipmentCustody,ServiceTicket,cycle.c.started).join(cycle,cycle.c.serial==Equipment.serial).join(last,last.c.serial==Equipment.serial).join(
        EquipmentCustody,EquipmentCustody.id==last.c.id).join(ServiceTicket,ServiceTicket.id==Equipment.custody_ticket_id).where(Equipment.custody_location!='TAI_KHACH')
    if location:rows=rows.where(Equipment.custody_location==location)
    if holder_id is not None:rows=rows.where(Equipment.custody_holder_id==holder_id)
    output=[]
    for eq,row,t,started in session.execute(rows.order_by(cycle.c.started,Equipment.serial)):
        days = max(0,(today-started.astimezone(service_sla.TZ).date()).days)
        output.append(dict(machine=eq,row=row,ticket=t,days=days,started=started,overdue=days>7 or bool(row.expected_back and today>row.expected_back)))
    return output
