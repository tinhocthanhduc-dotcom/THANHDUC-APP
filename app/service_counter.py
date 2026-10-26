"""Khách mang máy tới cửa hàng (TUẤN duyệt 26/10/2026): nhận máy và trả máy tại quầy, không qua thợ.

Nhận: tạo phiếu Mang về, ảnh NHAN_MAY, máy vào thẳng Kho kỹ thuật, phiếu chờ sửa ở xưởng; không ghi lượt đến khách.
Báo giá, Khách đồng ý, Sửa xong vẫn do điều phối như sửa ở xưởng (service_workshop).
Trả: khách tới lấy, người ở quầy chụp ảnh TRA_MAY, máy về khách, phiếu hoàn thành, tiền thu thành Báo thu chờ kế toán.
Quầy = điều phối, giám đốc, kinh doanh; kinh doanh chỉ thao tác phiếu nhận tại quầy (dòng service_steps counter_intake).
"""
from datetime import date, datetime

from sqlalchemy import select

from . import attachments, equipment_custody as custody, sales_receipts, service_charges, service_sla, service_tickets as tickets, technician_work
from .errors import BusinessError
from .models import EquipmentCustody
from .schemas import CustodyIntake, SalesReceiptReport, ServiceTicketIn
from .security import has_role
from .stock_docs import _transaction

ROLES = ('dispatcher', 'sales')
WAITING = 'CHO_SUA_XUONG'
READY = 'SUA_XONG_CHO_TRA'


def walk_in(session, ticket_id):
    from .models import ServiceTicket
    return bool(session.scalar(select(ServiceTicket.counter_intake).where(ServiceTicket.id == ticket_id)))


def can_counter(session, user, t):
    """Điều phối làm mọi phiếu ở xưởng; kinh doanh chỉ phiếu khách mang tới quầy."""
    if has_role(user, 'dispatcher'):
        return True
    return has_role(user, *ROLES) and bool(t.counter_intake)


def _require(user):
    if not has_role(user, *ROLES):
        raise BusinessError('FORBIDDEN', 'Chỉ điều phối, kinh doanh hoặc giám đốc được nhận máy tại quầy.', status=403)


def load(session, user, ticket_id):
    _require(user)
    t = tickets._get(session, user, ticket_id)
    if not can_counter(session, user, t):
        raise BusinessError('NOT_FOUND', 'Không tìm thấy phiếu nhận tại quầy.', status=404)
    session.refresh(t, ['steps', 'lines'])
    return t


def intake(session, user, body: ServiceTicketIn, accessories, accessories_note, condition_note, photo, expected_on: date | None = None):
    """Một transaction: phiếu + ảnh nhận máy + dòng giữ máy Kho kỹ thuật. Khóa máy và kiểm Tại khách thay cho phiên bản máy."""
    _require(user)
    if body.appointment_at is not None or body.appointment_slot is not None:
        raise BusinessError('BAD_APPOINTMENT', 'Khách mang máy tới không hẹn thợ.')
    if expected_on is not None and (isinstance(expected_on, datetime) or expected_on < tickets._now().astimezone(service_sla.TZ).date()):
        raise BusinessError('BAD_APPOINTMENT', 'Ngày hẹn khách quay lại phải từ hôm nay trở đi.')
    if not photo or not photo[1]:
        raise BusinessError('PHOTO_REQUIRED', 'Cần chụp 1 ảnh máy lúc nhận.')
    accessories_note, condition_note = (accessories_note or '').strip()[:2000], (condition_note or '').strip()[:2000]
    if not condition_note:
        raise BusinessError('CONDITION_REQUIRED', 'Phải ghi tình trạng máy khi nhận.')
    values = custody._accessories(accessories, accessories_note)
    with _transaction(session):
        t = tickets.create_in_tx(session, user, body.model_copy(update={'kind': 'MANG_VE'}), _counter=True)
        eq = custody._machine(session, t.serial, lock=True)
        if eq.custody_location != 'TAI_KHACH':
            raise BusinessError('MACHINE_HELD', 'Máy đang được giữ theo phiếu khác; trả máy theo phiếu đó trước.', {'ticket_id': eq.custody_ticket_id}, status=409)
        custody._no_pending(session, eq.serial)
        before, from_status = tickets.snapshot(t), t.status
        t.status, t.hold_reason, t.counter_intake = 'TAM_DUNG', WAITING, True
        t.promised_return_on = expected_on
        t.due_at = service_sla.due_at(t.status, t.priority, tickets._now(), None, None)
        t.version += 1
        tickets._record(session, user, t, 'counter_intake', before, from_status, 'Khách mang máy tới, nhận tại quầy')
        attachments.add(session, user, 'service_ticket', str(t.id), 'NHAN_MAY', photo[0], photo[1], _in_tx=True)
        row = EquipmentCustody(serial=eq.serial, ticket_id=t.id, location='KHO_KY_THUAT', holder_id=None,
            accessories=values, accessories_note=accessories_note, condition_note=condition_note,
            status='DA_NHAN', created_by=user.id, confirmed_by=user.id, confirmed_at=custody._now())
        session.add(row)
        custody._record(session, user, row, 'receive')
        custody._accept_cache(session, user, eq, row)
    return t


def return_at_counter(session, user, ticket_id, version, machine: CustodyIntake, mode, partial='', method='TIEN_MAT', note='', photo=None):
    """Khách tới lấy: máy Kho kỹ thuật → khách, phiếu Hoàn thành, tiền thu thành Báo thu chờ xác nhận."""
    _require(user)
    with _transaction(session):
        t = tickets._get(session, user, ticket_id, lock=True)
        if not can_counter(session, user, t):
            raise BusinessError('FORBIDDEN', 'Kinh doanh chỉ trả máy cho phiếu nhận tại quầy.', status=403)
        tickets._check_version(t, version)
        if t.status != 'TAM_DUNG' or t.hold_reason != READY or not service_charges.return_locked(t):
            raise BusinessError('BAD_STATUS', 'Máy chưa sửa xong chờ trả, chưa trả khách được.', status=409)
        eq = custody._machine(session, t.serial, lock=True)
        if eq.custody_ticket_id != t.id or eq.custody_location != 'KHO_KY_THUAT':
            raise BusinessError('MACHINE_NOT_AT_SHOP', 'Máy phải đang ở Kho kỹ thuật theo phiếu này mới trả tại quầy.', status=409)
        custody._no_pending(session, eq.serial)
        actual = t.receivable or 0
        if mode not in ('', 'FULL', 'PARTIAL'):
            raise BusinessError('BAD_COLLECTION', 'Chọn Thu đủ, Thu một phần hoặc Chưa thu.')
        received = 0
        if actual and mode == 'FULL':
            received = actual
        elif actual and mode == 'PARTIAL':
            received = technician_work.money(partial)
            if not 0 < received < actual:
                raise BusinessError('BAD_PARTIAL_AMOUNT', 'Thu một phần phải lớn hơn 0 và nhỏ hơn số phải thu.')
        if photo and photo[1]:
            attachments.add(session, user, 'service_ticket', str(t.id), 'TRA_MAY', photo[0], photo[1], _in_tx=True)
        custody.return_machine_in_tx(session, user, t, eq, machine)
        tickets.transition_in_tx(session, user, t, 'HOAN_THANH', note, auto_close=False, _action='counter_return')
        if received:
            sales_receipts.report_for_ticket_in_tx(session, user, t, SalesReceiptReport(version=t.version, amount=received, method=method, note=note))
        tickets.auto_close_in_tx(session, user, t)
    return t


def rows(session, user):
    """Phiếu nhận tại quầy cho danh sách quầy; điều phối thấy thêm phiếu xưởng sửa xong chờ trả có máy ở Kho kỹ thuật."""
    _require(user)
    from sqlalchemy import or_
    from .models import Customer, Equipment, ServiceTicket
    from . import equipment
    from .models import Product
    t = ServiceTicket
    marked = t.counter_intake
    scope = marked
    if has_role(user, 'dispatcher'):
        scope = or_(marked, (t.status == 'TAM_DUNG') & (t.hold_reason == READY) & (Equipment.custody_location == 'KHO_KY_THUAT'))
    stmt = (select(t, Customer.name, equipment.name_column().label('machine'), Equipment.custody_location, marked.label('walk_in'))
            .join(Customer, Customer.code == t.customer_code).join(Equipment, Equipment.serial == t.serial)
            .outerjoin(Product, Product.code == Equipment.product_code).where(scope).order_by(t.id.desc()))
    return [dict(ticket=x, customer=c, machine=m, location=loc, walk_in=w) for x, c, m, loc, w in session.execute(stmt)]
