"""Phiếu dịch vụ (D2): nhận đơn, giao thợ, chuyển trạng thái có hạn xử lý. Màn hình chỉ gọi các hàm ở đây.

Quyền kiểm tại lõi: điều phối giao và hủy; thợ chỉ thấy và chuyển phiếu được giao cho mình.
Mỗi thay đổi ghi một dòng service_steps (chỉ thêm) và một dòng audit_log trong cùng transaction.
"""
import re
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from . import audit, service_sla
from .config import get_settings
from .customers import like_pattern
from .doc_numbers import next_number
from .errors import BusinessError
from .models import Customer, ServiceStep, ServiceTicket, User
from .schemas import ServiceTicketIn, ServiceTicketOut
from .security import has_role
from .stock_docs import _transaction
from .text import fold

CREATORS = ("dispatcher", "sales", "reception")
DISPATCHERS = ("dispatcher",)
TECHNICIAN = "technician"

OPEN = ("MOI", "DA_GIAO", "DA_NHAN", "DA_HEN", "DANG_LAM", "CHO_LINH_KIEN")
CLOSED = ("HOAN_THANH", "KHONG_LAM_DUOC", "DA_HUY")
# Trạng thái đích hợp lệ từ mỗi trạng thái, ngoài giao thợ (assign) và hủy (DA_HUY, chỉ điều phối).
FLOW = {
    "DA_GIAO": {"DA_NHAN"},
    "DA_NHAN": {"DA_HEN", "KHONG_LAM_DUOC"},
    "DA_HEN": {"DA_HEN", "DANG_LAM", "KHONG_LAM_DUOC"},  # DA_HEN -> DA_HEN là dời lịch hẹn
    "DANG_LAM": {"HOAN_THANH", "CHO_LINH_KIEN", "KHONG_LAM_DUOC"},
    "CHO_LINH_KIEN": {"DANG_LAM", "KHONG_LAM_DUOC"},
}
NEEDS_REASON = ("KHONG_LAM_DUOC", "DA_HUY")
ASSIGNABLE = ("MOI", "DA_GIAO", "DA_NHAN", "DA_HEN", "DANG_LAM", "CHO_LINH_KIEN")


def _now() -> datetime:
    return datetime.now(UTC)


def snapshot(t: ServiceTicket) -> dict:
    return ServiceTicketOut.model_validate(t).model_dump(mode="json")


def _own_only(user: User) -> bool:
    """Thợ (không kiêm điều phối, kinh doanh, lễ tân) chỉ thấy phiếu của mình."""
    return TECHNICIAN in user.roles and not has_role(user, *CREATORS)


def _require(user: User, roles, message: str):
    if not has_role(user, *roles):
        raise BusinessError("FORBIDDEN", message, status=403)


def _get(session: Session, user: User, ticket_id: int, lock: bool = False) -> ServiceTicket:
    stmt = select(ServiceTicket).where(ServiceTicket.id == ticket_id)
    if lock:
        stmt = stmt.with_for_update()
    t = session.scalars(stmt).first()
    # Thợ không thấy phiếu của người khác: trả như không tồn tại.
    if t is None or (_own_only(user) and t.assignee_id != user.id):
        raise BusinessError("NOT_FOUND", "Không tìm thấy phiếu dịch vụ.", status=404)
    return t


def load(session: Session, user: User, ticket_id: int) -> ServiceTicket:
    t = _get(session, user, ticket_id)
    session.refresh(t, ["steps"])
    return t


def _check_version(t: ServiceTicket, version: int | None):
    if version is not None and t.version != version:
        raise BusinessError("VERSION_CONFLICT", "Người khác vừa cập nhật phiếu, hãy tải lại.", {"current": t.version}, status=409)


def _record(session, user, t, action, before, from_status, note="", reason=""):
    session.add(ServiceStep(ticket_id=t.id, action=action, from_status=from_status, to_status=t.status,
                            actor_id=user.id, assignee_id=t.assignee_id, note=note))
    session.flush()
    audit.record(session, user, "service_ticket." + action, "service_ticket", str(t.id), before, snapshot(t), reason)


def _serial(value: str | None) -> str | None:
    s = re.sub(r"\s+", "", value or "").upper()
    return s or None


def create(session: Session, user: User, body: ServiceTicketIn) -> ServiceTicket:
    _require(user, CREATORS, "Chỉ điều phối, kinh doanh, lễ tân hoặc giám đốc được nhận đơn.")
    with _transaction(session):
        customer = session.scalars(select(Customer).where(Customer.code == body.customer_code).with_for_update(read=True)).first()
        if customer is None or customer.status != "DANG_DUNG":
            details = {"merged_into": customer.merged_into} if customer is not None and customer.merged_into else {}
            raise BusinessError("BAD_CUSTOMER", "Khách hàng không tồn tại hoặc đã ngừng/gộp.", details)
        now = _now()
        t = ServiceTicket(
            number=next_number(session, "DV", now.astimezone(service_sla.TZ).date()),
            customer_code=customer.code, site_id=body.site_id, serial=_serial(body.serial),
            kind=body.kind, priority=body.priority, symptom=body.symptom, status="MOI",
            due_at=service_sla.due_at("MOI", body.priority, now, None), created_by=user.id,
        )
        session.add(t)
        session.flush()
        _record(session, user, t, "create", None, None)
    return t


def _technician(session: Session, user_id: int) -> User:
    u = session.get(User, user_id)
    if u is None or not u.active or TECHNICIAN not in u.roles:
        raise BusinessError("BAD_ASSIGNEE", "Người được giao phải là kỹ thuật viên đang làm việc.", {"assignee_id": user_id})
    return u


def assign(session: Session, user: User, ticket_id: int, assignee_id: int, version: int | None, note: str = "") -> ServiceTicket:
    """Giao thợ, hoặc chuyển sang thợ khác (cũng là cách duyệt yêu cầu chuyển việc). Phiếu về DA_GIAO."""
    _require(user, DISPATCHERS, "Chỉ điều phối hoặc giám đốc được giao thợ.")
    with _transaction(session):
        t = _get(session, user, ticket_id, lock=True)
        _check_version(t, version)
        if t.status not in ASSIGNABLE:
            raise BusinessError("BAD_STATUS", "Phiếu đã đóng, không giao thợ được.", {"status": t.status}, status=409)
        tech = _technician(session, assignee_id)
        if t.assignee_id == tech.id and t.status == "DA_GIAO":
            raise BusinessError("SAME_ASSIGNEE", "Phiếu đang giao cho thợ này rồi.", status=409)
        before, from_status = snapshot(t), t.status
        action = "assign" if t.assignee_id is None else "reassign"
        t.assignee_id, t.status, t.transfer_request, t.appointment_at = tech.id, "DA_GIAO", "", None
        t.due_at = service_sla.due_at("DA_GIAO", t.priority, _now(), None)
        _record(session, user, t, action, before, from_status, note)
    return t


def transition(session: Session, user: User, ticket_id: int, to_status: str, version: int | None,
               note: str = "", appointment_at: datetime | None = None) -> ServiceTicket:
    note = (note or "").strip()
    with _transaction(session):
        t = _get(session, user, ticket_id, lock=True)
        _check_version(t, version)
        is_dispatcher = has_role(user, *DISPATCHERS)
        if to_status == "DA_HUY":
            if not is_dispatcher:
                raise BusinessError("FORBIDDEN", "Chỉ điều phối hoặc giám đốc được hủy phiếu.", status=403)
            if t.status not in OPEN:
                raise BusinessError("BAD_STATUS", "Phiếu đã đóng, không hủy được.", {"status": t.status}, status=409)
        else:
            if not is_dispatcher and t.assignee_id != user.id:
                raise BusinessError("FORBIDDEN", "Chỉ thợ được giao hoặc điều phối được cập nhật phiếu này.", status=403)
            if to_status not in FLOW.get(t.status, ()):
                raise BusinessError("BAD_TRANSITION", "Không chuyển được sang trạng thái này.",
                                    {"from": t.status, "to": to_status, "allowed": sorted(FLOW.get(t.status, ()))}, status=409)
        if to_status in NEEDS_REASON and len(note) < 3:
            raise BusinessError("REASON_REQUIRED", "Phải ghi lý do.")
        if to_status == "DA_HEN":
            if appointment_at is None:
                raise BusinessError("APPOINTMENT_REQUIRED", "Phải chọn giờ hẹn khách.")
            t.appointment_at = appointment_at
        elif appointment_at is not None:
            raise BusinessError("BAD_APPOINTMENT", "Chỉ gửi giờ hẹn khi chốt lịch hẹn.")
        before, from_status = snapshot(t), t.status
        t.status = to_status
        if to_status in NEEDS_REASON:
            t.status_reason = note
        if to_status in CLOSED:
            t.transfer_request = ""
        t.due_at = service_sla.due_at(to_status, t.priority, _now(), t.appointment_at)
        action = "cancel" if to_status == "DA_HUY" else "reschedule" if from_status == to_status else "transition"
        _record(session, user, t, action, before, from_status, note, note if to_status in NEEDS_REASON else "")
    return t


def request_transfer(session: Session, user: User, ticket_id: int, reason: str, version: int | None) -> ServiceTicket:
    """Thợ xin chuyển việc cho người khác; điều phối duyệt bằng assign hoặc từ chối bằng reject_transfer."""
    reason = (reason or "").strip()
    with _transaction(session):
        t = _get(session, user, ticket_id, lock=True)
        _check_version(t, version)
        if t.assignee_id != user.id:
            raise BusinessError("FORBIDDEN", "Chỉ thợ đang được giao mới xin chuyển việc.", status=403)
        if t.status not in OPEN:
            raise BusinessError("BAD_STATUS", "Phiếu đã đóng.", {"status": t.status}, status=409)
        if len(reason) < 3:
            raise BusinessError("REASON_REQUIRED", "Phải ghi lý do xin chuyển việc.")
        if t.transfer_request:
            raise BusinessError("TRANSFER_PENDING", "Đã có yêu cầu chuyển việc đang chờ duyệt.", status=409)
        before = snapshot(t)
        t.transfer_request = reason
        _record(session, user, t, "transfer_request", before, t.status, reason)
    return t


def reject_transfer(session: Session, user: User, ticket_id: int, reason: str, version: int | None) -> ServiceTicket:
    _require(user, DISPATCHERS, "Chỉ điều phối hoặc giám đốc được duyệt chuyển việc.")
    with _transaction(session):
        t = _get(session, user, ticket_id, lock=True)
        _check_version(t, version)
        if not t.transfer_request:
            raise BusinessError("NO_TRANSFER", "Phiếu không có yêu cầu chuyển việc.", status=409)
        before = snapshot(t)
        t.transfer_request = ""
        _record(session, user, t, "transfer_reject", before, t.status, (reason or "").strip())
    return t


def search(session: Session, user: User, status: str | None = None, assignee_id: int | None = None,
           overdue: bool = False, q: str | None = None, limit: int | None = None) -> list[ServiceTicket]:
    stmt = select(ServiceTicket)
    if _own_only(user):
        stmt = stmt.where(ServiceTicket.assignee_id == user.id)
    if status is not None:
        stmt = stmt.where(ServiceTicket.status == status)
    if assignee_id is not None:
        stmt = stmt.where(ServiceTicket.assignee_id == assignee_id)
    if overdue:
        stmt = stmt.where(ServiceTicket.status.in_(OPEN), ServiceTicket.due_at < _now())
    needle = fold(q)
    if needle:
        stmt = stmt.join(Customer, Customer.code == ServiceTicket.customer_code).where(or_(
            ServiceTicket.number.ilike(f"%{needle}%"),
            ServiceTicket.serial.ilike(f"%{needle}%"),
            Customer.search_text.like(like_pattern(needle)),
        ))
    # Quá hạn lâu nhất lên đầu, phiếu không có hạn xếp sau.
    order = (ServiceTicket.due_at.asc().nulls_last(), ServiceTicket.id.desc()) if overdue else (ServiceTicket.id.desc(),)
    return list(session.scalars(stmt.order_by(*order).limit(limit or get_settings().search_limit * 5)))


def open_counts(session: Session) -> dict[int, int]:
    """Số phiếu đang mở của từng thợ, cho màn hình giao thợ."""
    rows = session.execute(
        select(ServiceTicket.assignee_id, func.count()).where(ServiceTicket.status.in_(OPEN), ServiceTicket.assignee_id.is_not(None))
        .group_by(ServiceTicket.assignee_id)
    )
    return {uid: n for uid, n in rows}
