"""Thông báo theo vị trí: chỉ SELECT từ hàng việc có sẵn, không bảng mới, không ghi."""
from datetime import timedelta

from sqlalchemy import or_, select

from . import my_work
from .models import AttendanceRequest, SalaryAdvance, User
from .security import has_role

# Nhóm việc "người khác vừa đẩy tới mình" mới bật thông báo; nhóm do chính mình làm tiếp (đang làm, đã nhận...) chỉ đếm.
INCOMING = ('service_assigned', 'service_unassigned', 'service_transferred', 'service_parts_ready', 'service_close',
    'machines_pending', 'machines_warehouse', 'technician_deliveries', 'delivery_handover', 'orders_waiting',
    'parts_pending', 'technician_returns', 'purchases_waiting', 'stock_low', 'refunds_pending', 'receipts_pending',
    'receipts_rejected', 'pending_destruction', 'pending_supplier_returns')
RECENT = timedelta(days=3)
ADVANCE = {'DA_DUYET': 'Tạm ứng đã được duyệt', 'TU_CHOI': 'Tạm ứng bị từ chối', 'DA_CHI': 'Tạm ứng đã chi'}
REQUEST = {'DA_DUYET': 'Đơn chấm công đã được duyệt', 'TU_CHOI': 'Đơn chấm công bị từ chối'}


def _item(key, ident, title, text, link):
    return dict(id=f'{key}:{ident}', title=title, text=text, link=link)


def items(session, user):
    out = []
    for group in my_work.collect(session, user):
        if group['key'] not in INCOMING:
            continue
        for row in group['rows']:
            text = ' · '.join(str(part) for part in (row.get('number'), row.get('customer')) if part)
            out.append(_item(group['key'], row['id'], group['title'], text, row['link']))
    names = None
    def who(user_id):
        nonlocal names
        if names is None:
            names = dict(session.execute(select(User.id, User.full_name)).all())
        return names.get(user_id, '')
    payroll_reader = not ('admin' in user.roles and 'director' not in user.roles)
    if payroll_reader and has_role(user, 'director'):
        for row in session.scalars(select(SalaryAdvance).where(SalaryAdvance.status == 'CHO_DUYET').order_by(SalaryAdvance.id).limit(my_work.LIMIT)):
            out.append(_item('advance_approve', row.id, 'Tạm ứng chờ duyệt', f'{row.number} · {who(row.user_id)}', f'/tam-ung#tu-{row.id}'))
    if payroll_reader and has_role(user, 'finance'):
        for row in session.scalars(select(SalaryAdvance).where(SalaryAdvance.status == 'DA_DUYET').order_by(SalaryAdvance.id).limit(my_work.LIMIT)):
            out.append(_item('advance_pay', row.id, 'Tạm ứng đã duyệt · chờ chi', f'{row.number} · {who(row.user_id)}', f'/tam-ung#tu-{row.id}'))
    if has_role(user, 'director'):
        for row in session.scalars(select(AttendanceRequest).where(AttendanceRequest.status == 'CHO_DUYET').order_by(AttendanceRequest.id).limit(my_work.LIMIT)):
            out.append(_item('attendance_approve', row.id, 'Chấm công chờ duyệt', f'{row.number} · {who(row.user_id)}', '/cham-cong/duyet'))
    # Kết quả gửi về người xin: tạm ứng / đơn chấm công vừa được quyết trong 3 ngày.
    since = my_work._now() - RECENT
    for row in session.scalars(select(SalaryAdvance).where(SalaryAdvance.user_id == user.id, SalaryAdvance.status.in_(tuple(ADVANCE)),
                                                           or_(SalaryAdvance.decided_at >= since, SalaryAdvance.paid_at >= since))):
        out.append(_item('advance_' + row.status.lower(), row.id, ADVANCE[row.status], row.number, '/tam-ung'))
    for row in session.scalars(select(AttendanceRequest).where(AttendanceRequest.user_id == user.id, AttendanceRequest.status.in_(tuple(REQUEST)),
                                                               AttendanceRequest.decided_at >= since)):
        out.append(_item('attendance_' + row.status.lower(), row.id, REQUEST[row.status], row.number, '/cham-cong'))
    return out
