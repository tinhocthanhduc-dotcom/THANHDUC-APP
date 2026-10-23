"""Phiếu dịch vụ (D2): nhận đơn, giao thợ, chuyển trạng thái có hạn xử lý. Màn hình chỉ gọi các hàm ở đây.

Quyền kiểm tại lõi: điều phối giao và hủy; thợ chỉ thấy và chuyển phiếu được giao cho mình.
Mỗi thay đổi ghi một dòng service_steps (chỉ thêm) và một dòng audit_log trong cùng transaction.
"""
import re
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import audit, service_sla, service_visits
from .config import get_settings
from .customers import like_pattern
from .doc_numbers import next_number
from .errors import BusinessError
from .models import Customer, CustomerSite, Equipment, SalesReceipt, ServiceStep, ServiceTicket, User
from .schemas import ServiceTicketIn, ServiceTicketOut
from .security import has_role
from .stock_docs import _transaction
from .text import fold

CREATORS = ("dispatcher", "sales", "reception")
DISPATCHERS = ("dispatcher",)
TECHNICIAN = "technician"

OPEN = ("MOI", "DA_GIAO", "DA_NHAN", "DA_HEN", "DANG_LAM", "CHO_LINH_KIEN", "TAM_DUNG")
CLOSED = ("HOAN_THANH", "DA_DONG", "KHONG_LAM_DUOC", "DA_HUY")
# Trạng thái đích ngoài giao thợ; điều phối còn được hủy các trạng thái CANCELLABLE.
FLOW = {
    "DA_GIAO": {"DA_NHAN"},
    "DA_NHAN": {"DA_HEN", "KHONG_LAM_DUOC"},
    "DA_HEN": {"DA_HEN", "DANG_LAM", "KHONG_LAM_DUOC"},  # DA_HEN -> DA_HEN là dời lịch hẹn
    "DANG_LAM": {"DA_HEN", "HOAN_THANH", "CHO_LINH_KIEN", "TAM_DUNG", "KHONG_LAM_DUOC", "DA_HUY"},
    "CHO_LINH_KIEN": {"DANG_LAM", "KHONG_LAM_DUOC", "DA_HUY"},
    "TAM_DUNG": {"DANG_LAM", "DA_HEN", "KHONG_LAM_DUOC", "DA_HUY"},
    "HOAN_THANH": {"DANG_LAM", "DA_DONG"},  # chỉ điều phối: mở lại nghiệm thu (có lý do) hoặc đóng hồ sơ
    "DA_DONG": {"DANG_LAM"},
}
# Phiếu đã hoàn thành chỉ điều phối được mở lại, hủy hoặc đóng; hủy lúc đó trả vật tư về kho.
CANCELLABLE = OPEN + ("HOAN_THANH", "DA_DONG")
HOLD_REASONS = {"CHO_KHACH_DUYET": "Chờ khách duyệt", "CHO_KY_THUAT_KHAC": "Chờ kỹ thuật khác",
                "CHO_DOI_TAC": "Chờ đối tác", "HEN_NGAY_KHAC": "Hẹn ngày khác", "KHAC": "Khác", "SUA_XONG_CHO_TRA": "Sửa xong, chờ trả máy"}
NEEDS_REASON = ("KHONG_LAM_DUOC", "DA_HUY")
ASSIGNABLE = ("MOI", "DA_GIAO", "DA_NHAN", "DA_HEN", "DANG_LAM", "CHO_LINH_KIEN", "TAM_DUNG")


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
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    t = session.scalars(stmt).first()
    # Người nhận giữ máy chỉ có quyền giữ máy; không có quyền sửa phiếu của thợ khác.
    if t is not None and lock and _own_only(user) and t.assignee_id != user.id:
        from .models import EquipmentCustody
        if session.scalar(select(EquipmentCustody.id).where(EquipmentCustody.ticket_id==t.id,
            or_(EquipmentCustody.holder_id==user.id,EquipmentCustody.created_by==user.id)).limit(1)):
            raise BusinessError('FORBIDDEN','Bạn chỉ được thao tác giữ máy, không được sửa phiếu này.',status=403)
    # Thợ không thấy phiếu của người khác: trả như không tồn tại.
    if t is None or (_own_only(user) and t.assignee_id != user.id):
        raise BusinessError("NOT_FOUND", "Không tìm thấy phiếu dịch vụ.", status=404)
    return t


def load(session: Session, user: User, ticket_id: int) -> ServiceTicket:
    t = _get(session, user, ticket_id)
    session.refresh(t, ["steps", "lines"])
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


def _check_site_and_serial(session: Session, customer_code: str, site_id: int | None, serial: str | None):
    if site_id is not None:
        site = session.get(CustomerSite, site_id)
        if site is None or not site.active or site.customer_code != customer_code:
            raise BusinessError("BAD_SITE", "Địa điểm không thuộc khách này hoặc đã ngừng dùng.", {"site_id": site_id})
    if serial is not None:
        eq = session.get(Equipment, serial)
        if eq is None or not eq.active:
            raise BusinessError("BAD_SERIAL", "Máy chưa có trong danh mục thiết bị, hãy thêm ở trang Thiết bị trước.", {"serial": serial})
        if eq.customer_code is not None and eq.customer_code != customer_code:
            raise BusinessError("BAD_SERIAL", "Máy này đang ghi cho khách khác.", {"serial": serial, "customer_code": eq.customer_code})


def create(session: Session, user: User, body: ServiceTicketIn) -> ServiceTicket:
    _require(user, CREATORS, "Chỉ điều phối, kinh doanh, lễ tân hoặc giám đốc được nhận đơn.")
    with _transaction(session):
        return create_in_tx(session,user,body)


def create_in_tx(session: Session, user: User, body: ServiceTicketIn) -> ServiceTicket:
    _require(user, CREATORS, "Chỉ điều phối, kinh doanh, lễ tân hoặc giám đốc được nhận đơn.")
    customer = session.scalars(select(Customer).where(Customer.code == body.customer_code).with_for_update(read=True)).first()
    if customer is None or customer.status != "DANG_DUNG":
        details = {"merged_into": customer.merged_into} if customer is not None and customer.merged_into else {}
        raise BusinessError("BAD_CUSTOMER", "Khách hàng không tồn tại hoặc đã ngừng/gộp.", details)
    serial = _serial(body.serial)
    _check_site_and_serial(session, customer.code, body.site_id, serial)
    now = _now()
    if body.appointment_at is not None or body.appointment_slot is not None:
        from .service_appointments import validate
        validate(body.appointment_at, body.appointment_slot, now)
    t = ServiceTicket(
        number=next_number(session, "DV", now.astimezone(service_sla.TZ).date()),
        customer_code=customer.code, site_id=body.site_id, serial=serial,
        kind=body.kind, priority=body.priority, symptom=body.symptom, status="MOI",
        appointment_at=body.appointment_at, appointment_slot=body.appointment_slot,
        due_at=service_sla.due_at("MOI", body.priority, now, None), created_by=user.id,
    )
    from . import service_warranty
    service_warranty.apply(session, t, body.warranty_of_id)
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
        return assign_in_tx(session,user,ticket_id,assignee_id,version,note)


def assign_in_tx(session: Session, user: User, ticket_id: int, assignee_id: int, version: int | None, note: str = "", *, _technician_transfer=False) -> ServiceTicket:
    """Giao thợ, hoặc chuyển sang thợ khác (cũng là cách duyệt yêu cầu chuyển việc). Phiếu về DA_GIAO."""
    if not _technician_transfer:
        _require(user, DISPATCHERS, "Chỉ điều phối hoặc giám đốc được giao thợ.")
    t = _get(session, user, ticket_id, lock=True)
    if _technician_transfer and ("technician" not in user.roles or t.assignee_id!=user.id or t.status not in ("DA_GIAO","DA_NHAN","DA_HEN","DANG_LAM","CHO_LINH_KIEN","TAM_DUNG")):
        raise BusinessError("FORBIDDEN","Không được chuyển phiếu này.",status=403)
    _check_version(t, version)
    if t.status not in ASSIGNABLE:
        raise BusinessError("BAD_STATUS", "Phiếu đã đóng, không giao thợ được.", {"status": t.status}, status=409)
    tech = _technician(session, assignee_id)
    if t.assignee_id == tech.id and t.status == "DA_GIAO":
        raise BusinessError("SAME_ASSIGNEE", "Phiếu đang giao cho thợ này rồi.", status=409)
    before, from_status = snapshot(t), t.status
    if from_status == "DANG_LAM" and not _technician_transfer:
        service_visits.close_visit(session, t, "CHUYEN_THO", _now(), note.strip())
    action = "assign" if t.assignee_id is None else "reassign"
    t.assignee_id, t.status, t.transfer_request = tech.id, "DA_GIAO", ""
    t.hold_reason = ""
    t.due_at = service_sla.due_at("DA_GIAO", t.priority, _now(), None)
    _record(session, user, t, action, before, from_status, note)
    return t


def transition(session: Session, user: User, ticket_id: int, to_status: str, version: int | None,
               note: str = "", appointment_at: datetime | None = None, hold_reason: str = "", appointment_slot: str | None = None) -> ServiceTicket:
    if to_status == "DA_HUY" and not has_role(user, *DISPATCHERS):
        from .technician_returns import cancel_by_technician
        return cancel_by_technician(session, user, ticket_id, version, note)
    return _transition(session, user, ticket_id, to_status, version, note, appointment_at, hold_reason, appointment_slot=appointment_slot)


def no_show(session: Session, user: User, ticket_id: int, version: int | None,
            note: str = '', appointment_at: datetime | None = None, appointment_slot: str | None = None) -> ServiceTicket:
    """Ghi nhận lần đến không gặp khách bằng đường dời hẹn hiện có."""
    return _transition(session, user, ticket_id, 'DA_HEN', version, note, appointment_at, no_show=True, appointment_slot=appointment_slot)


def _transition(session: Session, user: User, ticket_id: int, to_status: str, version: int | None,
                note: str = '', appointment_at: datetime | None = None, hold_reason: str = '',
                no_show: bool = False, appointment_slot: str | None = None) -> ServiceTicket:
    note = (note or "").strip()
    with _transaction(session):
        t = _get(session, user, ticket_id, lock=True)
        _check_version(t, version)
        return transition_in_tx(session, user, t, to_status, note, appointment_at, hold_reason, no_show, appointment_slot)


def transition_in_tx(session: Session, user: User, t: ServiceTicket, to_status: str,
                     note: str = "", appointment_at: datetime | None = None, hold_reason: str = "",
                     no_show: bool = False, appointment_slot: str | None = None, *, auto_close: bool = True,
                     return_lines=None, _action=None) -> ServiceTicket:
    """Caller đã khóa phiếu, kiểm phiên bản và quản lý transaction."""
    note = (note or "").strip()
    is_dispatcher = has_role(user, *DISPATCHERS)
    ready=t.status=='TAM_DUNG' and t.hold_reason=='SUA_XONG_CHO_TRA'
    if t.status=='TAM_DUNG' and to_status=='DA_HEN':
        if not is_dispatcher:raise BusinessError('FORBIDDEN','Chỉ điều phối được hẹn trả máy.',status=403)
        if not ready:raise BusinessError('BAD_TRANSITION','Chỉ hẹn trả khi máy đã sửa xong chờ trả.',status=409)
    if ready and to_status=='DANG_LAM':
        raise BusinessError('BAD_TRANSITION','Điều phối cần hẹn trả máy trước khi thợ đi trả.',status=409)
    if to_status == 'KHACH_HEN_SAU':
        raise BusinessError('BAD_TRANSITION', 'Trạng thái này không còn được sử dụng.', status=409)
    technician_cancel = to_status == 'DA_HUY' and not is_dispatcher
    if technician_cancel and ('technician' not in user.roles or t.assignee_id != user.id or
                              t.status not in ('DANG_LAM','CHO_LINH_KIEN','TAM_DUNG')):
        raise BusinessError('FORBIDDEN', 'Chỉ thợ được giao được hủy khi đang làm, chờ linh kiện hoặc tạm dừng.', status=403)
    if to_status in ('HOAN_THANH','KHONG_LAM_DUOC','DA_HUY','DA_DONG') and (
            is_dispatcher or (t.assignee_id == user.id and t.status not in ('HOAN_THANH','DA_DONG'))):
        from .equipment_custody import check_returned
        check_returned(session,t)
    if to_status == "DA_HUY":
        if t.status not in CANCELLABLE:
            raise BusinessError("BAD_STATUS", "Phiếu đã đóng, không hủy được.", {"status": t.status}, status=409)
    else:
        if not is_dispatcher and (t.assignee_id != user.id or t.status in ('HOAN_THANH','DA_DONG')):
            raise BusinessError("FORBIDDEN", "Chỉ thợ được giao hoặc điều phối được cập nhật phiếu này.", status=403)
        direct_arrival = t.status == "DA_NHAN" and to_status == "DANG_LAM" and t.appointment_at is not None
        if not direct_arrival and to_status not in FLOW.get(t.status, ()):
            raise BusinessError("BAD_TRANSITION", "Không chuyển được sang trạng thái này.",
                                {"from": t.status, "to": to_status, "allowed": sorted(FLOW.get(t.status, ()))}, status=409)
    if to_status == 'DA_DONG':
        from .service_recovered_parts import check_received
        check_received(session, t)
    returning = t.status == 'DANG_LAM' and to_status == 'DA_HEN'
    if no_show and not (t.status == 'DA_HEN' or (t.status == 'DA_NHAN' and t.appointment_at is not None)):
        raise BusinessError('BAD_STATUS', 'Chỉ ghi không gặp khách trên phiếu đang hẹn.', status=409)
    if returning or no_show:
        if not note:
            raise BusinessError('REASON_REQUIRED', 'Phải ghi lý do / người liên hệ và đã gọi khách chưa.')
        if appointment_at is None:
            raise BusinessError('APPOINTMENT_REQUIRED', 'Phải chọn giờ hẹn mới.')
        from .service_appointments import validate
        validate(appointment_at, appointment_slot, _now())
    if to_status == "TAM_DUNG":
        if hold_reason=='SUA_XONG_CHO_TRA' and (t.repaired_by is None or t.priced_at is None):
            raise BusinessError('WORKSHOP_REQUIRED','Dùng thao tác Sửa xong, chờ trả máy để chốt kết quả và tiền.')
        if hold_reason not in HOLD_REASONS:
            raise BusinessError("HOLD_REASON_REQUIRED", "Phải chọn lý do tạm dừng.")
        if hold_reason == "KHAC" and not note:
            raise BusinessError("REASON_REQUIRED", "Tạm dừng vì lý do khác phải ghi chú.")
    elif hold_reason:
        raise BusinessError("BAD_HOLD_REASON", "Chỉ gửi lý do tạm dừng khi tạm dừng phiếu.")
    if to_status == "HOAN_THANH":
        from .attachments import has_visible  # attachments cũng dùng module này

        if not has_visible(session, "service_ticket", t.id, "SAU"):
            raise BusinessError("PHOTO_REQUIRED", "Phải chụp ảnh sau khi sửa trước khi hoàn thành.")
    reopening = t.status in ('HOAN_THANH','DA_DONG') and to_status in ("DANG_LAM", "DA_HUY")
    if reopening and session.scalar(select(SalesReceipt.id).where(
        SalesReceipt.ticket_id==t.id,SalesReceipt.status.in_(('CHO_XAC_NHAN','DA_XAC_NHAN'))).limit(1)) is not None:
        raise BusinessError('HAS_RECEIPTS','Tài chính phải từ chối hoặc hủy báo thu trước.',status=409)
    if (to_status in NEEDS_REASON or reopening) and len(note) < 3:
        raise BusinessError("REASON_REQUIRED", "Phải ghi lý do.")
    if to_status == "DA_HEN":
        if appointment_at is None:
            raise BusinessError("APPOINTMENT_REQUIRED", "Phải chọn giờ hẹn khách.")
    elif appointment_at is not None or appointment_slot is not None:
        raise BusinessError("BAD_APPOINTMENT", "Chỉ gửi giờ hẹn khi chốt lịch hẹn.")
    if technician_cancel:
        from .technician_returns import prepare_cancel, finish_cancel
        cancellation = prepare_cancel(session, t, return_lines)
    before, from_status = snapshot(t), t.status
    from . import service_charges  # service_charges cũng dùng module này

    if to_status == "HOAN_THANH":
        service_charges.complete(session, user, t)
    elif reopening:
        service_charges.release(session, user, t, note)
    stamp = _now()
    if from_status == 'DANG_LAM':
        outcome = {'HOAN_THANH': 'XONG', 'DA_HEN': 'HEN_LAI', 'DA_HUY': 'HUY'}.get(to_status, to_status)
        service_visits.close_visit(session, t, outcome, stamp, note, appointment_at if returning else None)
    if to_status == 'DANG_LAM':
        if t.repaired_by is not None and t.priced_at is not None:
            from . import equipment_custody
            eq=equipment_custody._machine(session,t.serial,lock=True)
            if eq.custody_ticket_id!=t.id or eq.custody_location!='THO_GIU' or eq.custody_holder_id!=t.assignee_id or equipment_custody.pending(session,t.serial):
                raise BusinessError('MACHINE_NOT_RECEIVED','Thợ đi trả cần xác nhận đã nhận máy trước khi đến khách.',status=409)
        service_visits.open_visit(session, t, stamp)
    if no_show:
        service_visits.open_visit(session, t, stamp)
        service_visits.close_visit(session, t, 'KHONG_GAP_KHACH', stamp, note, appointment_at)
    if to_status == 'DA_HEN':
        from .service_appointments import validate
        validate(appointment_at, appointment_slot, _now())
        t.appointment_at = appointment_at
        t.appointment_slot = appointment_slot
    t.status = to_status
    if technician_cancel:
        t.receivable = 0
        t.appointment_at = t.appointment_slot = None
    if to_status == "TAM_DUNG":
        t.hold_reason = hold_reason
    elif to_status == "DANG_LAM":
        t.hold_reason = ""
    if to_status in NEEDS_REASON:
        t.status_reason = note
    if to_status in CLOSED:
        t.transfer_request = ""
    t.due_at = service_sla.due_at(to_status, t.priority, _now(), t.appointment_at, t.appointment_slot)
    action = ("cancel" if to_status == "DA_HUY" else "reschedule" if from_status == to_status
              else "reopen" if reopening else "close" if to_status == "DA_DONG" else "transition")
    if no_show:
        # Một lượt mới vẫn đổi phiên bản khi giờ hẹn tình cờ bằng giờ đã lưu.
        t.version += 1
        action = "no_show"
    _record(session, user, t, _action or action, before, from_status, note, note if to_status in NEEDS_REASON or reopening else "")
    if technician_cancel:
        finish_cancel(session, user, t, note, cancellation)
    elif to_status in CLOSED and from_status not in CLOSED:
        from .part_requests import cancel_pending_for_ticket
        cancel_pending_for_ticket(session, user, t, 'Tự hủy: phiếu ' + t.number + ' ' + {'HOAN_THANH': 'đã hoàn thành', 'DA_DONG': 'đã đóng', 'KHONG_LAM_DUOC': 'không làm được', 'DA_HUY': 'đã hủy'}[to_status])
    if to_status=='HOAN_THANH' and auto_close:
        auto_close_in_tx(session,user,t)
    return t


def auto_close_in_tx(session: Session, user: User, t: ServiceTicket) -> bool:
    """Caller holds the ticket lock; never commit or require dispatcher rights."""
    if t.status!='HOAN_THANH':return False
    from .equipment_custody import check_returned
    from .service_recovered_parts import check_received
    try:
        check_received(session,t)
        check_returned(session,t)
    except BusinessError:
        return False
    before=snapshot(t)
    t.status='DA_DONG'
    t.transfer_request=''
    t.due_at=service_sla.due_at('DA_DONG',t.priority,_now(),t.appointment_at,t.appointment_slot)
    _record(session,user,t,'close',before,'HOAN_THANH','Tự đóng: đủ điều kiện')
    return True


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


def reschedule(session, user, ticket_id, version, appointment_at, appointment_slot=None, note=''):
    _require(user, DISPATCHERS, 'Chỉ điều phối được đổi hẹn trước khi làm.')
    with _transaction(session):
        t = _get(session,user,ticket_id,lock=True);_check_version(t,version)
        if t.status not in ('MOI','DA_GIAO','DA_NHAN','DA_HEN'):
            raise BusinessError('BAD_STATUS','Chỉ đổi hẹn trước khi bắt đầu làm.',status=409)
        from .service_appointments import validate
        validate(appointment_at,appointment_slot,_now())
        before=snapshot(t);t.appointment_at=appointment_at;t.appointment_slot=appointment_slot
        t.due_at=service_sla.due_at(t.status,t.priority,_now(),t.appointment_at,t.appointment_slot)
        t.version += 1
        _record(session,user,t,'reschedule',before,t.status,note)
    return t


def create_and_assign(session,user,body,assignee_id):
    with _transaction(session):
        ticket=create_in_tx(session,user,body)
        return assign_in_tx(session,user,ticket.id,assignee_id,ticket.version)
