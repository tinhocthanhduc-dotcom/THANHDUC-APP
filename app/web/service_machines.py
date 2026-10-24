"""Shared machine selection for intake, dispatch and the technician workshop step."""
from sqlalchemy import or_, select

from .. import customer_sites, service_tickets
from ..errors import BusinessError
from ..models import Equipment, EquipmentCustody, Product
from ..schemas import EquipmentIn, ServiceMachineSet
from ..security import has_role

FORM_FIELDS = ('machine_pick', 'serial', 'create_machine', 'new_serial', 'new_product_code', 'new_product_q', 'version')


def can_create(user):
    return has_role(user, *customer_sites.WRITERS)


def new_machine(form):
    code = str(form.get('new_product_code', '')).strip()
    if not code:
        raise BusinessError('BAD_PRODUCT', 'Chưa chọn mã hàng cho máy mới, hãy bấm chọn một kết quả tìm.')
    return EquipmentIn(serial=str(form.get('new_serial', '')), product_code=code)


def intake(form):
    return new_machine(form) if form.get('create_machine') == '1' else None


def body(form, version):
    choice = str(form.get('machine_pick', ''))
    if choice == 'NEW':
        return ServiceMachineSet(version=version, new_machine=new_machine(form))
    serial = choice.removeprefix('SERIAL:') if choice.startswith('SERIAL:') else (None if choice == 'NONE' else form.get('serial'))
    return ServiceMachineSet(version=version, serial=serial)


def context(session, user, ticket, shown=None):
    own = 'technician' in user.roles and ticket.assignee_id == user.id
    permitted = has_role(user, *service_tickets.DISPATCHERS) or own
    locked = session.scalar(select(EquipmentCustody.id).where(EquipmentCustody.ticket_id == ticket.id).limit(1)) is not None
    warranty = ticket.warranty_of_id is not None or ticket.warranty_basis is not None
    editable = permitted and ticket.status in service_tickets.OPEN and not locked and not warranty
    data = dict(shown) if shown is not None else dict(machine_pick='SERIAL:' + ticket.serial if ticket.serial else 'NONE', version=ticket.version)
    machines = list(session.execute(select(Equipment, Product.name).join(Product).where(
        Equipment.active.is_(True), or_(Equipment.customer_code == ticket.customer_code, Equipment.serial == ticket.serial))
        .order_by(Equipment.serial))) if editable else []
    return dict(can_set_machine=editable, can_create_machine=can_create(user), machine_form=data, ticket_machines=machines,
                machine_lock_reason='Máy đã được nhận về xưởng, không đổi máy được.' if locked else
                    'Phiếu đã có căn cứ bảo hành, không đổi máy được.' if warranty else '')
