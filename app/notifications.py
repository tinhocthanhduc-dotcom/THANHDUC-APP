"""Thông báo theo vị trí: chỉ SELECT từ hàng việc có sẵn, không bảng mới, không ghi."""
from datetime import timedelta

from sqlalchemy import or_, select

from . import my_work
from .models import AttendanceRequest, NotificationRead, SalaryAdvance, User
from .security import has_role

# Nhóm việc "người khác vừa đẩy tới mình" mới bật thông báo; nhóm do chính mình làm tiếp (đang làm, đã nhận...) chỉ đếm.
INCOMING = ('orders_cancel_requested', 'service_assigned', 'service_unassigned', 'service_transferred', 'service_parts_ready', 'service_close',
    'machines_pending', 'machines_warehouse', 'technician_deliveries', 'delivery_handover', 'orders_waiting',
    'parts_pending', 'technician_returns', 'purchases_waiting', 'stock_low', 'refunds_pending', 'receipts_pending',
    'receipts_rejected', 'pending_destruction', 'pending_supplier_returns')
OWN_TICKET = ('service_assigned', 'service_parts_ready')
RECENT = timedelta(days=3)
ADVANCE = {'DA_DUYET': 'Tạm ứng đã được duyệt', 'TU_CHOI': 'Tạm ứng bị từ chối', 'DA_CHI': 'Tạm ứng đã chi'}
REQUEST = {'DA_DUYET': 'Đơn chấm công đã được duyệt', 'TU_CHOI': 'Đơn chấm công bị từ chối'}


def _item(key, ident, title, text, link, info=False):
    # info: chỉ báo kết quả (tạm ứng/chấm công đã duyệt...) nên xem rồi là ẩn; việc cần làm vẫn hiện tới khi xong.
    return dict(id=f'{key}:{ident}', title=title, text=text, link=link, info=info)


def items(session, user):
    out = []
    for group in my_work.collect(session, user):
        if group['key'] not in INCOMING:
            continue
        for row in group['rows']:
            text = ' · '.join(str(part) for part in (row.get('number'), row.get('customer')) if part)
            link = row['link']
            if group['key'] in OWN_TICKET and 'technician' in user.roles and 'director' not in user.roles:
                link = f"/tho/phieu/{row['id']}"  # thợ mở thẳng màn thợ của phiếu mình
            out.append(_item(group['key'], row['id'], group['title'], text, link))
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
        out.append(_item('advance_' + row.status.lower(), row.id, ADVANCE[row.status], row.number, '/tam-ung', info=True))
    for row in session.scalars(select(AttendanceRequest).where(AttendanceRequest.user_id == user.id, AttendanceRequest.status.in_(tuple(REQUEST)),
                                                               AttendanceRequest.decided_at >= since)):
        out.append(_item('attendance_' + row.status.lower(), row.id, REQUEST[row.status], row.number, '/cham-cong', info=True))
    return out


def with_reads(session, user, rows):
    """Gắn cờ đã xem; thông báo kết quả đã xem thì bỏ khỏi danh sách."""
    ids = [row['id'] for row in rows]
    read = set(session.scalars(select(NotificationRead.item_id).where(
        NotificationRead.user_id == user.id, NotificationRead.item_id.in_(ids)))) if ids else set()
    out = []
    for row in rows:
        if row['info'] and row['id'] in read:
            continue
        out.append({**row, 'read': row['id'] in read})
    return out


def mark_read(session, user, ids):
    from sqlalchemy import delete
    from sqlalchemy.dialects.postgresql import insert
    ids = [str(i)[:120] for i in ids if i][:200]
    # Dọn dấu cũ: việc đã xong không còn trong danh sách, giữ 30 ngày là đủ.
    session.execute(delete(NotificationRead).where(NotificationRead.user_id == user.id,
                                                   NotificationRead.read_at < my_work._now() - timedelta(days=30)))
    if ids:
        session.execute(insert(NotificationRead).values([dict(user_id=user.id, item_id=i) for i in ids])
                        .on_conflict_do_nothing(constraint='uq_notification_reads_user_item'))
    session.commit()
