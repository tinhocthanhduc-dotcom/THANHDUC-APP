"""Shared machine selection for intake, dispatch and the technician workshop step."""
from sqlalchemy import or_, select

from .. import customer_sites, equipment, service_tickets
from ..errors import BusinessError
from ..models import Equipment, EquipmentCustody, Product, ServiceTicket
from ..schemas import EquipmentIn, ServiceMachineSet
from ..security import has_role

FORM_FIELDS = ('machine_pick', 'serial', 'create_machine', 'new_serial', 'new_product_code', 'new_product_q', 'version')


def can_create(user):
    return has_role(user, *customer_sites.WRITERS)


def new_machine(form):
    # Bấm chọn hàng trong danh mục thì có mã hàng; không chọn thì tên đã gõ là tên máy ngoài danh mục.
    code = str(form.get('new_product_code', '')).strip()
    if code:
        return EquipmentIn(serial=str(form.get('new_serial', '')), product_code=code)
    name = str(form.get('new_product_q', '')).strip()
    if not name:
        raise BusinessError('BAD_PRODUCT', 'Chưa có máy: gõ tên hoặc mã máy, có trong Hàng hóa thì bấm chọn một dòng.')
    return EquipmentIn(serial=str(form.get('new_serial', '')), model_name=name[:160])


def intake(form):
    return new_machine(form) if form.get('create_machine') == '1' else None


def body(form, version):
    choice = str(form.get('machine_pick', ''))
    if choice == 'NEW':
        return ServiceMachineSet(version=version, new_machine=new_machine(form))
    serial = choice.removeprefix('SERIAL:') if choice.startswith('SERIAL:') else (None if choice == 'NONE' else form.get('serial'))
    return ServiceMachineSet(version=version, serial=serial)


def multiple(form):
    choices=[str(value) for value in form.getlist('machine_pick')]
    if 'NONE' in choices or choices.count('NEW')>1:
        raise BusinessError('BAD_SERIAL','Chọn danh sách máy hợp lệ, không chọn Không có máy cùng máy khác.')
    return ([value.removeprefix('SERIAL:') for value in choices if value!='NEW'],new_machine(form) if 'NEW' in choices else None)


def context(session, user, ticket, shown=None):
    own = 'technician' in user.roles and ticket.assignee_id == user.id
    permitted = has_role(user, *service_tickets.DISPATCHERS) or own
    locked = session.scalar(select(EquipmentCustody.id).where(EquipmentCustody.ticket_id == ticket.id).limit(1)) is not None
    warranty = ticket.warranty_of_id is not None or ticket.warranty_basis is not None
    editable = permitted and ticket.status in service_tickets.OPEN and not locked and not warranty
    data = dict(shown) if shown is not None else dict(machine_pick='SERIAL:' + ticket.serial if ticket.serial else 'NONE', version=ticket.version)
    machines = list(session.execute(select(Equipment, equipment.name_column()).outerjoin(Product, Product.code == Equipment.product_code).where(
        Equipment.active.is_(True), or_(Equipment.customer_code == ticket.customer_code, Equipment.serial == ticket.serial))
        .order_by(Equipment.serial))) if editable else []
    # Máy đang được giữ theo phiếu khác: hiện mờ, không chọn được (server cũng chặn MACHINE_HELD).
    held_ids = {eq.serial: eq.custody_ticket_id for eq, _ in machines
                if eq.custody_location != 'TAI_KHACH' and eq.custody_ticket_id is not None and eq.custody_ticket_id != ticket.id}
    numbers = dict(session.execute(select(ServiceTicket.id, ServiceTicket.number).where(ServiceTicket.id.in_(set(held_ids.values())))).all()) if held_ids else {}
    return dict(can_set_machine=editable, can_create_machine=can_create(user), machine_form=data, ticket_machines=machines,
                machine_held={serial: numbers.get(ident, '') for serial, ident in held_ids.items()},
                machine_lock_reason='Máy đã được nhận về xưởng, không đổi máy được.' if locked else
                    'Phiếu đã có căn cứ bảo hành, không đổi máy được.' if warranty else '')
