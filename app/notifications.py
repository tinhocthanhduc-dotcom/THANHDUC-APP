"""Thông báo theo vị trí: chỉ SELECT từ hàng việc có sẵn, không bảng mới, không ghi."""
import re
from datetime import timedelta
from urllib.parse import urlencode

from sqlalchemy import or_, select

from . import my_work
from .models import AttendanceRequest, Customer, NotificationRead, SalaryAdvance, ServiceStep, ServiceTicket, User
from .security import has_role

# Nhóm việc "người khác vừa đẩy tới mình" mới bật thông báo; nhóm do chính mình làm tiếp (đang làm, đã nhận...) chỉ đếm.
INCOMING = ('orders_cancel_requested', 'service_assigned', 'service_unassigned', 'service_transferred', 'service_goods_short', 'device_reports', 'zalo_ticket_drafts', 'customer_message_replies', 'service_parts_ready', 'service_close',
    'machines_pending', 'machines_warehouse', 'technician_deliveries', 'delivery_handover', 'delivery_unclaimed', 'orders_waiting',
    'parts_pending', 'technician_returns', 'purchases_waiting', 'stock_low', 'refunds_pending', 'receipts_pending',
    'receipts_rejected', 'expenses_pending', 'pending_destruction', 'pending_supplier_returns', 'stock_counts_pending')
OWN_TICKET = ('service_assigned', 'service_parts_ready')
RECENT = timedelta(days=3)
ADVANCE = {'DA_DUYET': 'Tạm ứng đã được duyệt', 'TU_CHOI': 'Tạm ứng bị từ chối', 'DA_CHI': 'Tạm ứng đã chi'}
REQUEST = {'DA_DUYET': 'Đơn chấm công đã được duyệt', 'TU_CHOI': 'Đơn chấm công bị từ chối'}


def _item(key, ident, title, text, link, info=False):
    # info: chỉ báo kết quả (tạm ứng/chấm công đã duyệt...) nên xem rồi là ẩn; việc cần làm vẫn hiện tới khi xong.
    return dict(id=f'{key}:{ident}', title=title, text=text, link=link, info=info)


def items(session, user, tho=False):
    out = []
    for group in my_work.collect(session, user, keys=INCOMING):
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
    # Điều phối sửa thông tin phiếu của thợ (mô tả lỗi, ưu tiên, địa điểm) trong 3 ngày, phiếu còn mở.
    if 'technician' in user.roles:
        from .service_tickets import OPEN
        for step_id, ticket_id, number in session.execute(select(ServiceStep.id, ServiceStep.ticket_id, ServiceTicket.number)
                .join(ServiceTicket, ServiceTicket.id == ServiceStep.ticket_id)
                .where(ServiceStep.action == 'update_info', ServiceStep.at >= since, ServiceStep.actor_id != user.id,
                       ServiceTicket.assignee_id == user.id, ServiceTicket.status.in_(OPEN))
                .order_by(ServiceStep.id.desc()).limit(my_work.LIMIT)):
            out.append(_item('ticket_info', step_id, 'Điều phối sửa thông tin phiếu', number, f'/tho/phieu/{ticket_id}', info=True))
    # Thợ tự nhận (TUẤN 04/11/2026): thợ thấy phiếu đang chờ nhận; điều phối biết ai vừa tự nhận.
    from . import service_self_claim
    now = my_work._now()
    if 'technician' in user.roles and service_self_claim.minutes() > 0 and service_self_claim.can_claim_more(session, user.id):
        for ticket_id, number, name in session.execute(select(ServiceTicket.id, ServiceTicket.number, Customer.name)
                .join(Customer, Customer.code == ServiceTicket.customer_code)
                .where(service_self_claim.claimable(now)).order_by(ServiceTicket.id).limit(my_work.LIMIT)):
            out.append(_item('self_claim_open', ticket_id, 'Phiếu mới chờ thợ nhận', f'{number} · {name}', '/tho#cho-nhan'))
    if has_role(user, 'dispatcher'):
        for step_id, ticket_id, number, assignee in session.execute(select(ServiceStep.id, ServiceStep.ticket_id, ServiceTicket.number, ServiceStep.assignee_id)
                .join(ServiceTicket, ServiceTicket.id == ServiceStep.ticket_id)
                .where(ServiceStep.action == 'assign', ServiceStep.note == service_self_claim.NOTE, ServiceStep.actor_id == ServiceStep.assignee_id,
                       ServiceStep.at >= now - timedelta(days=1))
                .order_by(ServiceStep.id.desc()).limit(my_work.LIMIT)):
            out.append(_item('self_claimed', step_id, f'{who(assignee)} đã tự nhận phiếu', number, f'/dich-vu/{ticket_id}', info=True))
    # Thợ thêm máy tại chỗ (TUẤN 13/11/2026): điều phối biết để kiểm tra máy mới, model, serial.
    if has_role(user, 'dispatcher'):
        for step_id, ticket_id, number, actor, note in session.execute(select(ServiceStep.id, ServiceStep.ticket_id, ServiceTicket.number, ServiceStep.actor_id, ServiceStep.note)
                .join(ServiceTicket, ServiceTicket.id == ServiceStep.ticket_id)
                .where(ServiceStep.action == 'add_machines', ServiceStep.actor_id != user.id, ServiceStep.at >= now - timedelta(days=1))
                .order_by(ServiceStep.id.desc()).limit(my_work.LIMIT)):
            count = len((note or '').split(':', 1)[-1].split(','))
            out.append(_item('machines_added', step_id, f'{who(actor)} thêm {count} máy tại chỗ', f'{number} · {note}', f'/dich-vu/{ticket_id}', info=True))
    # Điều phối AI (TUẤN 07/11/2026): điều phối biết phiếu nào nhân viên AI vừa giao, mở phiếu để đổi thợ / hoàn tác.
    if has_role(user, 'dispatcher'):
        from . import nhan_vien_ao
        for log_id, ticket_id, text in nhan_vien_ao.recent_assigned(session, now - timedelta(days=1)):
            out.append(_item('virtual_assigned', log_id, 'Điều phối AI đã giao', text, f'/dich-vu/{ticket_id}', info=True))
    # Nhắc sắp trễ hẹn (TUẤN 05/11/2026): thợ chưa bấm Đi / Đã đến khách. Đổi hẹn thì mốc mới, nhắc lại.
    from . import service_suggest
    if 'technician' in user.roles:
        for item in service_suggest.at_risk(session, now, level='tech', assignee_id=user.id):
            row = item['ticket']
            out.append(_item('appointment_soon', item['mark'], 'Sắp tới giờ hẹn',
                             f"{row.number} · {service_suggest.risk_label(item)}", f'/tho/phieu/{row.id}'))
    if has_role(user, 'dispatcher'):
        for item in service_suggest.at_risk(session, now, level='dispatch'):
            row = item['ticket']
            text = f"{row.number} · {who(row.assignee_id)} chưa đi · {service_suggest.risk_label(item).lower()}"
            out.append(_item('appointment_risk', item['mark'], 'Có thể trễ hẹn', text, '/dieu-phoi?view=late'))
        for step_id, ticket_id, number, actor, note in session.execute(select(ServiceStep.id, ServiceStep.ticket_id, ServiceTicket.number, ServiceStep.actor_id, ServiceStep.note)
                .join(ServiceTicket, ServiceTicket.id == ServiceStep.ticket_id)
                .where(ServiceStep.action == service_suggest.LATE, ServiceStep.at >= now - timedelta(hours=6),
                       ServiceTicket.status.in_(service_suggest.WAITING))
                .order_by(ServiceStep.id.desc()).limit(my_work.LIMIT)):
            out.append(_item('tech_late', step_id, f'{who(actor)} báo trễ', f'{number} · {note.lower()}', f'/dich-vu/{ticket_id}'))
    # Trợ lý AI: nhắc việc gửi tới mình (giữ 1 ngày), đề xuất nhập hàng giám đốc đã duyệt (cho thủ kho).
    from . import ai_agent
    for row in ai_agent.reminders_for(session, user, my_work._now() - timedelta(days=1)):
        if row.kind == 'NHAC_VIEC':
            link = '/viec-cua-toi'
            if row.ticket_id:
                pure = 'technician' in user.roles and 'director' not in user.roles
                link = f'/tho/phieu/{row.ticket_id}' if pure else f'/dich-vu/{row.ticket_id}'
            out.append(_item('ai_remind', row.id, 'Nhắc từ Trợ lý AI', row.title, link, info=True))
        else:
            out.append(_item('ai_restock', row.id, 'Giám đốc duyệt nhập thêm hàng', row.title,
                             '/ton-kho?' + urlencode({'canh-bao': '1', 'q': row.payload.get('product_code', '')}), info=True))
    if 'technician' in user.roles and (tho or set(user.roles) == {'technician'}):
        _tho_links(session, user, out)
    return out


_TICKET = re.compile(r'^/(?:dich-vu|giu-may)/(\d+)(?:[/?#]|$)')
_STAY = re.compile(r'^/(?:giao-don|tho-tra-hang)/[^/?#]+')


def _tho_links(session, user, rows):
    """Đang ở màn thợ: bấm thông báo vẫn ở khung thợ (TUẤN 04/11/2026).
    Phiếu/máy của chính mình -> /tho/phieu; giao đơn, trả kho -> giữ return_to=tho. Việc văn phòng giữ nguyên."""
    ids = {int(m.group(1)) for row in rows if (m := _TICKET.match(row['link']))}
    mine = set(session.scalars(select(ServiceTicket.id).where(ServiceTicket.id.in_(ids), ServiceTicket.assignee_id == user.id))) if ids else set()
    for row in rows:
        link = row['link']
        m = _TICKET.match(link)
        if m and int(m.group(1)) in mine:
            row['link'] = f'/tho/phieu/{m.group(1)}'
        elif _STAY.match(link) and 'return_to=' not in link:
            path, _, anchor = link.partition('#')
            row['link'] = path + ('&' if '?' in path else '?') + 'return_to=tho' + ('#' + anchor if anchor else '')


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
