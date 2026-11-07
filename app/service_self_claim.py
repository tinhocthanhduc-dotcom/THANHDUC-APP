"""Thợ tự nhận phiếu dịch vụ chưa giao (TUẤN 04/11/2026).

Phiếu dịch vụ mới (chưa giao thợ) hiện ở màn Hôm nay của tất cả thợ trong TD_SELF_CLAIM_MINUTES phút (mặc định 30).
Ai bấm Nhận trước người đó được: khóa dòng phiếu rồi mới giao, người sau thấy tên người đã nhận.
Hết giờ chưa ai nhận: phiếu rời màn thợ, quay về hàng Cần giao thợ của điều phối (chuông báo như phiếu chưa giao).
Không mở cho phiếu Khẩn, phiếu bảo hành, phiếu nhận tại quầy, phiếu đã chọn thợ lúc tạo.
Tự nhận ghi bước giao như điều phối giao (người giao = chính thợ), nên hoa hồng, KPI tính như cũ.
"""
from datetime import timedelta

from sqlalchemy import exists, func, select

from . import service_sla
from .config import get_settings
from .errors import BusinessError
from .models import ServiceStep, ServiceTicket, User

NOTE = 'Thợ tự nhận'


def minutes() -> int:
    return get_settings().self_claim_minutes


def eligible(t: ServiceTicket) -> bool:
    return (minutes() > 0 and t.status == 'MOI' and t.assignee_id is None and not t.counter_intake
            and t.priority != 'KHAN' and t.kind != 'BAO_HANH' and t.warranty_of_id is None)


def open_in_tx(t: ServiceTicket, now) -> bool:
    """Gọi trong transaction tạo phiếu. Trả True nếu phiếu được mở cho thợ tự nhận."""
    if not eligible(t):
        return False
    t.self_claim_until = now + timedelta(minutes=minutes())
    return True


def claimable(now):
    """Điều kiện phiếu đang chờ thợ tự nhận (dùng trong where)."""
    t = ServiceTicket
    return (t.status == 'MOI') & t.assignee_id.is_(None) & t.self_claim_until.is_not(None) & (t.self_claim_until > now)


def expired(now):
    """Phiếu đã mở cho thợ tự nhận nhưng hết giờ, chưa ai nhận: điều phối cần giao."""
    t = ServiceTicket
    return (t.status == 'MOI') & t.assignee_id.is_(None) & t.self_claim_until.is_not(None) & (t.self_claim_until <= now)


def minutes_left(t: ServiceTicket, now) -> int:
    if t.self_claim_until is None:
        return 0
    return max(0, -(-int((t.self_claim_until - now).total_seconds()) // 60))


def max_open() -> int:
    return get_settings().self_claim_max_open


def open_jobs(session, user_id: int) -> int:
    """Việc đang mở của thợ: phiếu dịch vụ + đơn Thợ giao (TUẤN 07/11/2026).
    Đơn đi cùng một phiếu đang mở của chính thợ là cùng chuyến, không tính thêm."""
    from .models import SalesOrder
    from .service_tickets import OPEN
    tickets = session.scalar(select(func.count()).select_from(ServiceTicket).where(
        ServiceTicket.assignee_id == user_id, ServiceTicket.status.in_(OPEN))) or 0
    same_trip = exists().where(ServiceTicket.id == SalesOrder.service_ticket_id, ServiceTicket.assignee_id == user_id,
                               ServiceTicket.status.in_(OPEN))
    orders = session.scalar(select(func.count()).select_from(SalesOrder).where(
        SalesOrder.delivery_assignee_id == user_id, SalesOrder.fulfillment == 'THO_GIAO',
        SalesOrder.status.in_(('DA_CHOT', 'GIAO_MOT_PHAN')), ~same_trip)) or 0
    return tickets + orders


def can_claim_more(session, user_id: int) -> bool:
    return open_jobs(session, user_id) < max_open()


def claim(session, user: User, ticket_id: int) -> ServiceTicket:
    from . import service_tickets as svc
    if svc.TECHNICIAN not in user.roles or not user.active:
        raise BusinessError('FORBIDDEN', 'Chỉ kỹ thuật viên được tự nhận phiếu.', status=403)
    with svc._transaction(session):
        # Khóa dòng phiếu: hai thợ bấm cùng lúc thì người sau chờ, rồi thấy phiếu đã có người nhận.
        t = session.scalar(select(ServiceTicket).where(ServiceTicket.id == ticket_id)
                           .with_for_update().execution_options(populate_existing=True))
        now = svc._now()
        if t is None or t.self_claim_until is None:
            raise BusinessError('NOT_FOUND', 'Không tìm thấy phiếu chờ nhận.', status=404)
        if t.assignee_id is not None:
            name = session.scalar(select(User.full_name).where(User.id == t.assignee_id)) or 'Thợ khác'
            raise BusinessError('ALREADY_CLAIMED', f'{name} đã nhận phiếu {t.number} trước bạn.', status=409)
        if t.status != 'MOI':
            raise BusinessError('BAD_STATUS', f'Phiếu {t.number} không còn chờ nhận.', status=409)
        if t.self_claim_until <= now:
            raise BusinessError('CLAIM_EXPIRED', f'Phiếu {t.number} đã hết giờ tự nhận, điều phối sẽ giao thợ.', status=409)
        limit = max_open()
        if open_jobs(session, user.id) >= limit:
            raise BusinessError('TOO_MANY_JOBS', f'Bạn đang có {limit} việc mở trở lên. Xong bớt một việc rồi mới nhận thêm được.', status=409)
        before, from_status = svc.snapshot(t), t.status
        t.assignee_id, t.status, t.transfer_request, t.hold_reason = user.id, 'DA_GIAO', '', ''
        t.due_at = service_sla.due_at('DA_GIAO', t.priority, now, None)
        svc._record(session, user, t, 'assign', before, from_status, NOTE)
        # Bấm Nhận là đã nhận việc: đi tiếp sang Đã nhận như thợ bấm Nhận đơn.
        svc.transition_in_tx(session, user, t, 'DA_NHAN', NOTE)
        # Đơn Thợ giao kèm phiếu đi theo người nhận phiếu (TUẤN 07/11/2026).
        from . import delivery_claim
        delivery_claim.follow_ticket(session, user, t)
    return t


def is_self_claim(step: ServiceStep) -> bool:
    return step.action == 'assign' and step.note == NOTE and step.actor_id == step.assignee_id


def month_stats(session, start, end) -> list[dict]:
    """Số phiếu tự nhận / điều phối giao theo thợ trong khoảng [start, end): lần giao đầu của mỗi phiếu."""
    s = ServiceStep
    rows = session.execute(select(s.assignee_id, s.actor_id, s.note).where(
        s.action == 'assign', s.at >= start, s.at < end, s.assignee_id.is_not(None))).all()
    names = dict(session.execute(select(User.id, User.full_name)).all())
    out = {}
    for assignee, actor, note in rows:
        row = out.setdefault(assignee, dict(name=names.get(assignee, ''), self=0, given=0))
        row['self' if note == NOTE and actor == assignee else 'given'] += 1
    for row in out.values():
        total = row['self'] + row['given']
        row['pct'] = round(row['self'] * 100 / total) if total else 0
    return sorted(out.values(), key=lambda r: (-r['self'], r['name']))
