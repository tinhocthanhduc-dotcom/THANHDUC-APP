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


MAX_NEW = 20


def new_machines(form):
    """TUẤN 13/11/2026: nhiều máy mới trong một lần lưu. Mỗi khung có nm_idx; tick "Không có serial" thì app cấp mã tạm."""
    idx=[str(v) for v in form.getlist('nm_idx')]
    serials,qs,codes=form.getlist('nm_serial'),form.getlist('nm_product_q'),form.getlist('nm_product_code')
    if not idx or len(idx)>MAX_NEW or not (len(serials)==len(qs)==len(codes)==len(idx)):
        raise BusinessError('BAD_PRODUCT',f'Máy mới chưa hợp lệ (tối đa {MAX_NEW} máy một lần).')
    temp={str(v) for v in form.getlist('nm_no_serial')}
    out=[]
    for i,serial,q,code in zip(idx,serials,qs,codes):
        serial=service_tickets.TEMP_SERIAL if i in temp else str(serial).strip()
        if not serial:
            raise BusinessError('BAD_FORM','Serial máy mới: nhập serial, hoặc tick "Không có serial" để app cấp mã tạm.')
        code,q=str(code).strip(),str(q).strip()
        if code:
            out.append(EquipmentIn(serial=serial,product_code=code))
        elif q:
            out.append(EquipmentIn(serial=serial,model_name=q[:160]))
        else:
            raise BusinessError('BAD_PRODUCT','Chưa có máy: gõ tên hoặc mã máy, có trong Hàng hóa thì bấm chọn một dòng.')
    return out


def shown_new(form):
    temp={str(v) for v in form.getlist('nm_no_serial')}
    return [dict(idx=str(i),serial=str(s),q=str(q),code=str(c),temp=str(i) in temp) for i,s,q,c in
            zip(form.getlist('nm_idx'),form.getlist('nm_serial'),form.getlist('nm_product_q'),form.getlist('nm_product_code'))]


def multiple(form):
    choices=[str(value) for value in form.getlist('machine_pick')]
    if 'NONE' in choices or choices.count('NEW')>1:
        raise BusinessError('BAD_SERIAL','Chọn danh sách máy hợp lệ, không chọn Không có máy cùng máy khác.')
    news=(new_machines(form) if form.getlist('nm_idx') else [new_machine(form)]) if 'NEW' in choices else []
    return ([value.removeprefix('SERIAL:') for value in choices if value!='NEW'],news)


def add_picks(form):
    """Form "+ Thêm máy của khách": máy có sẵn (add_pick=SERIAL:x) + khung máy mới."""
    choices=[str(value) for value in form.getlist('add_pick')]
    news=new_machines(form) if 'NEW' in choices else []
    return [value.removeprefix('SERIAL:') for value in choices if value.startswith('SERIAL:')],news


def family_rows(session, ticket):
    """Bảng "Máy cùng chỗ": phiếu gốc + phiếu tách, kèm tên máy."""
    rows=service_tickets.family(session,ticket)
    if len(rows)<2:return []
    names=dict(session.execute(select(Equipment.serial,equipment.name_column()).outerjoin(Product,Product.code==Equipment.product_code)
        .where(Equipment.serial.in_([r.serial for r in rows if r.serial]))).all())
    return [dict(t=r,name=names.get(r.serial,''),root=r.split_from_id is None) for r in rows]


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
    # TUẤN 13/11/2026: phiếu đã có máy vẫn thêm được máy khác của khách (mỗi máy một phiếu), thợ tại chỗ hoặc điều phối.
    can_add = permitted and not locked and bool(ticket.serial) and ticket.status in service_tickets.ADD_STATUSES and not ticket.counter_intake
    add_list = []
    if can_add:
        busy = dict(session.execute(select(ServiceTicket.serial, ServiceTicket.number).where(ServiceTicket.customer_code == ticket.customer_code,
            ServiceTicket.serial.is_not(None), ServiceTicket.status.in_(service_tickets.OPEN))).all())
        held_add = {}
        for eq, name in session.execute(select(Equipment, equipment.name_column()).outerjoin(Product, Product.code == Equipment.product_code).where(
                Equipment.active.is_(True), Equipment.customer_code == ticket.customer_code, Equipment.serial != ticket.serial,
                or_(Equipment.site_id.is_(None), Equipment.site_id == ticket.site_id) if ticket.site_id is not None else Equipment.site_id.is_(None))
                .order_by(Equipment.serial)):
            why = busy.get(eq.serial)
            if why is None and eq.custody_location != 'TAI_KHACH' and eq.custody_ticket_id is not None:
                held_add.setdefault('ids', []).append(eq.custody_ticket_id); why = eq.custody_ticket_id
            add_list.append(dict(eq=eq, name=name, why=why))
        if held_add:
            nums = dict(session.execute(select(ServiceTicket.id, ServiceTicket.number).where(ServiceTicket.id.in_(held_add['ids']))).all())
            for row in add_list:
                if isinstance(row['why'], int): row['why'] = nums.get(row['why'], 'phiếu khác')
    family = family_rows(session, ticket)
    family_total = 0
    if family:
        from .. import sales_receipts
        dues = {r['t'].id: r for r in sales_receipts.family_due(session, user, ticket)}
        for row in family:
            row['money'] = dues.get(row['t'].id)
        family_total = sum(r['due'] for r in dues.values())
    return dict(can_add_machine=can_add, add_machines=add_list, machine_family=family, family_due_total=family_total,
                can_set_machine=editable, can_create_machine=can_create(user), machine_form=data, ticket_machines=machines,
                machine_held={serial: numbers.get(ident, '') for serial, ident in held_ids.items()},
                machine_lock_reason='Máy đã được nhận về xưởng, không đổi máy được.' if locked else
                    'Phiếu đã có căn cứ bảo hành, không đổi máy được.' if warranty else '')
