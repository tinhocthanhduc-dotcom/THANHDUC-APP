"""Màn hình D3: đọc danh mục để chọn, mọi thay đổi phiếu đi qua lõi D2."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from .. import attachments, service_charges
from ..config import get_settings
from .. import customers as customer_svc
from .. import service_tickets as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, User
from ..schemas import ServiceAssign, ServiceReason, ServiceTicketIn, ServiceTransition
from ..security import has_role
from . import checked_form, render, templates, web_user, nav

router = APIRouter()
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
STATUSES = {
    "MOI": "Mới", "DA_GIAO": "Đã giao", "DA_NHAN": "Đã nhận", "DA_HEN": "Đã hẹn",
    "DANG_LAM": "Đang làm", "CHO_LINH_KIEN": "Chờ linh kiện", "HOAN_THANH": "Hoàn thành",
    "DA_DONG": "Đã đóng", "KHONG_LAM_DUOC": "Không làm được", "DA_HUY": "Đã hủy",
}
KINDS = {"SUA_TAI_CHO": "Sửa tại chỗ", "MANG_VE": "Mang về", "BAO_HANH": "Bảo hành", "MUC_DINH_KY": "Mực định kỳ"}
PRIORITIES = {"KHAN": "Khẩn", "GAP": "Gấp", "THUONG": "Thường"}
TRANSITIONS = {
    "DA_NHAN": "Nhận đơn", "DA_HEN": "Hẹn khách", "DANG_LAM": "Bắt đầu", "CHO_LINH_KIEN": "Chờ linh kiện",
    "HOAN_THANH": "Hoàn thành", "DA_DONG": "Đóng hồ sơ", "KHONG_LAM_DUOC": "Không làm được", "DA_HUY": "Hủy",
}
STEPS = {"create": "Nhận đơn", "assign": "Giao thợ", "reassign": "Giao lại",
         "transition": "Chuyển trạng thái", "reschedule": "Dời hẹn", "cancel": "Hủy",
         "transfer_request": "Xin chuyển việc", "transfer_reject": "Từ chối chuyển",
         "reopen": "Mở lại", "close": "Đóng hồ sơ"}


def local_time(value):
    return value.astimezone(TZ).strftime("%d/%m/%Y %H:%M") if value else "—"


templates.env.filters["service_time"] = local_time
templates.env.globals.update(service_statuses=STATUSES, service_kinds=KINDS,
                             service_priorities=PRIORITIES, step_labels=STEPS)


def _own_only(user):
    return "technician" in user.roles and not has_role(user, *svc.CREATORS)


def _technicians(session):
    return list(session.scalars(select(User).where(User.active.is_(True), User.roles.any("technician"))
                               .order_by(User.full_name, User.id)))


def _users(session):
    return dict(session.execute(select(User.id, User.full_name)).all())


def _is_overdue(ticket):
    return ticket.status in svc.OPEN and ticket.due_at is not None and ticket.due_at < datetime.now(UTC)


def _version(form):
    try:
        value = int(form.get("version", ""))
        if value < 1:
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise BusinessError("VERSION_REQUIRED", "Thiếu phiên bản phiếu, hãy tải lại trang.")


def _validation_message(exc):
    fields = {"customer_code": "Khách hàng", "kind": "Loại dịch vụ", "priority": "Mức ưu tiên",
              "symptom": "Mô tả lỗi", "assignee_id": "Thợ được giao", "site_id": "Địa điểm",
              "serial": "Serial", "note": "Ghi chú / lý do", "reason": "Lý do", "appointment_at": "Ngày giờ hẹn"}
    error = exc.errors()[0]
    label = fields.get(error["loc"][0], "Dữ liệu")
    return f"{label} chưa hợp lệ, hãy kiểm tra lại."


def _message(exc):
    if isinstance(exc, ValidationError):
        return _validation_message(exc), "BAD_FORM", 400
    message = "Cần chụp ảnh sau sửa trước khi hoàn thành" if exc.code == "PHOTO_REQUIRED" else exc.message
    if exc.code == "NEGATIVE_STOCK":
        d = exc.details
        message = f"Không đủ tồn cho {d.get('product', '')} tại kho {d.get('warehouse', '')}: tồn hiện có {d.get('available', '')}, số cần {d.get('requested', '')}."
    return message, exc.code, exc.status


@router.get("/dich-vu")
def listing(request: Request, q: str = "", status: str = "", assignee_id: str = "", overdue: bool = False,
            phan: int = 0, user: User = Depends(web_user), session: Session = Depends(get_session)):
    selected = int(assignee_id) if assignee_id.isdigit() else None
    rows = svc.search(session, user, status or None, selected, overdue, q[:200])
    customers = dict(session.execute(select(Customer.code, Customer.name)
                                    .where(Customer.code.in_({t.customer_code for t in rows}))).all())
    return render(request, "dich_vu_dong.jinja" if phan else "dich_vu_ds.jinja", rows=rows,
                  q=q, selected_status=status, selected_assignee=selected, overdue=overdue,
                  technicians=[] if _own_only(user) else _technicians(session), own_only=_own_only(user),
                  heading="Việc của tôi" if _own_only(user) else "Dịch vụ",
                  can_create=has_role(user, *svc.CREATORS), can_receivables=nav.allowed(user, "/dich-vu/can-thu"), customers=customers, users=_users(session), is_overdue=_is_overdue)


def _detail(request, session, user, ticket_id, form=None, failed_action="", error=None, error_code="", status=200, photo_form=None, charge_shown=None, receipt_form=None, receipt_decision=None, failed_receipt_id=None, failed_receipt_action=""):
    from .charges import detail_context
    from .receipts import ticket_context
    t = svc.load(session, user, ticket_id)
    dispatcher = has_role(user, *svc.DISPATCHERS)
    own = "technician" in user.roles and t.assignee_id == user.id and t.status != "HOAN_THANH"
    targets = sorted(svc.FLOW.get(t.status, ()), key=lambda s: list(STATUSES).index(s)) if dispatcher or own else []
    if dispatcher and t.status in svc.CANCELLABLE:
        targets.append("DA_HUY")
    labels = dict(TRANSITIONS)
    if t.status == "CHO_LINH_KIEN":
        labels["DANG_LAM"] = "Làm tiếp"
    if t.status == "HOAN_THANH":
        labels["DANG_LAM"] = "Mở lại"
    if t.status == "DA_HEN":
        labels["DA_HEN"] = "Dời hẹn"
    visible_actions = {"transition:" + target for target in targets}
    if dispatcher and t.status in svc.ASSIGNABLE:
        visible_actions.add("assign")
    if own and t.status in svc.OPEN and not t.transfer_request:
        visible_actions.add("transfer-request")
    if dispatcher and t.transfer_request:
        visible_actions.add("transfer-reject")
    retained_fields = [(key, str(value)) for key, value in (form or {}).items()
                       if key in {"version", "assignee_id", "to_status", "note", "reason", "appointment_at"}]
    # D1 là danh mục chỉ đọc; phiếu và lịch sử luôn được tải qua lõi D2.
    from ..models import CustomerSite
    site = session.get(CustomerSite, t.site_id) if t.site_id else None
    return render(request, "dich_vu_chi_tiet.jinja", t=t, customer=session.get(Customer, t.customer_code), site=site,
                  files=attachments.list_for(session, user, "service_ticket", str(t.id)),
                  can_upload=has_role(user, *svc.CREATORS) or own,
                  can_hide=dispatcher, photo_form=photo_form or {},
                  users=_users(session), technicians=_technicians(session) if dispatcher else [],
                  counts=svc.open_counts(session) if dispatcher else {},
                  can_assign=dispatcher and t.status in svc.ASSIGNABLE,
                  can_request_transfer=own and t.status in svc.OPEN and not t.transfer_request,
                  can_reject_transfer=dispatcher and bool(t.transfer_request), transitions=targets,
                  transition_labels=labels, is_overdue=_is_overdue,
                  appointment_value=t.appointment_at.astimezone(TZ).strftime("%Y-%m-%dT%H:%M") if t.appointment_at else "",
                  form=form or {}, failed_action=failed_action, error=error, error_code=error_code,
                  retained_fields=retained_fields if error and failed_action not in visible_actions else [],
                  saved=request.query_params.get("da_luu") == "1" and not error, status=status,
                  **detail_context(session, user, t, charge_shown),
                  **ticket_context(session,user,t,receipt_form,receipt_decision,failed_receipt_id,failed_receipt_action))


# Đường dẫn /moi và các ô tìm/chọn được đăng ký trước /{ticket_id} ở cuối module.


def _choices(session, customer_code):
    from ..models import CustomerSite, Equipment, Product
    sites = list(session.scalars(select(CustomerSite).where(CustomerSite.customer_code == customer_code,
                                 CustomerSite.active.is_(True)).order_by(CustomerSite.id))) if customer_code else []
    machines = list(session.execute(select(Equipment, Product.name).join(Product)
                                    .where(Equipment.customer_code == customer_code, Equipment.active.is_(True))
                                    .order_by(Equipment.serial))) if customer_code else []
    return sites, machines


def _new_form(request, session, data=None, error=None, error_code="", status=200):
    data = data or {"customer_code": "", "site_id": "", "serial": "", "kind": "SUA_TAI_CHO", "priority": "THUONG", "symptom": ""}
    customer = session.get(Customer, data['customer_code']) if data['customer_code'] else None
    sites, machines = _choices(session, data['customer_code'])
    return render(request, "dich_vu_moi.jinja", d=data, customer_name=customer.name if customer else "",
                  sites=sites, machines=machines, error=error, error_code=error_code, status=status,
                  can_create=has_role(request.state.user, *svc.CREATORS))


@router.get("/dich-vu/tim-khach")
def pick_customer(request: Request, q: str = "", user: User = Depends(web_user), session: Session = Depends(get_session)):
    return render(request, "dich_vu_chon_khach.jinja", rows=[c for c in customer_svc.search(session, q[:200]) if c.status == "DANG_DUNG"])


@router.get("/dich-vu/chon-khach")
def pick_choices(request: Request, customer_code: str = "", user: User = Depends(web_user), session: Session = Depends(get_session)):
    sites, machines = _choices(session, customer_code)
    return render(request, "dich_vu_chon_may.jinja", sites=sites, machines=machines, d={})


@router.get("/dich-vu/moi")
def new(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _new_form(request, session)


@router.post("/dich-vu/moi")
def create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    data = {key: str(form.get(key, "")) for key in ("customer_code", "site_id", "serial", "kind", "priority", "symptom")}
    try:
        body = ServiceTicketIn(**{**data, "site_id": data['site_id'] or None, "serial": data['serial'] or None})
        t = svc.create(session, request.state.user, body)
    except (BusinessError, ValidationError) as exc:
        error, code, status = _message(exc)
        return _new_form(request, session, data, error, code, status)
    return RedirectResponse(f"/dich-vu/{t.id}?da_luu=1", status_code=303)


@router.get("/dich-vu/{ticket_id}")
def detail(request: Request, ticket_id: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _detail(request, session, user, ticket_id)


@router.post("/dich-vu/{ticket_id}/{action}")
def action(request: Request, ticket_id: int, action: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    # load ở đầu giữ nguyên quy tắc 404 cho thợ mở phiếu của người khác.
    svc.load(session, user, ticket_id)
    failed_action = "transition:" + str(form.get("to_status", "")) if action == "transition" else action
    try:
        version = _version(form)
        if action == "assign":
            body = ServiceAssign(assignee_id=form.get('assignee_id'), version=version, note=form.get('note', ''))
            svc.assign(session, user, ticket_id, body.assignee_id, body.version, body.note)
        elif action == "transition":
            appointment = str(form.get("appointment_at", "")).strip()
            if appointment:
                try:
                    value = datetime.fromisoformat(appointment)
                except ValueError:
                    raise BusinessError("BAD_APPOINTMENT", "Ngày giờ hẹn chưa hợp lệ.")
                # datetime-local không có múi giờ: luôn gửi giờ Việt Nam +07:00 tới lõi.
                appointment = value.replace(tzinfo=TZ).isoformat() if value.tzinfo is None else value.isoformat()
            body = ServiceTransition(to_status=form.get('to_status'), version=version,
                                     note=form.get('note', ''), appointment_at=appointment or None)
            svc.transition(session, user, ticket_id, body.to_status, body.version, body.note, body.appointment_at)
        elif action in ("transfer-request", "transfer-reject"):
            body = ServiceReason(reason=form.get('reason', ''), version=version)
            fn = svc.request_transfer if action == "transfer-request" else svc.reject_transfer
            fn(session, user, ticket_id, body.reason, body.version)
        else:
            raise BusinessError("NOT_FOUND", "Không tìm thấy thao tác này.", status=404)
    except (BusinessError, ValidationError) as exc:
        error, code, status = _message(exc)
        return _detail(request, session, user, ticket_id, form, failed_action, error, code, status)
    return RedirectResponse(f"/dich-vu/{ticket_id}?da_luu=1", status_code=303)


@router.post("/dich-vu/{ticket_id}/anh/tai")
async def photo_upload(request: Request, ticket_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    svc.load(session, user, ticket_id)
    tag = str(form.get("tag", ""))
    try:
        if tag not in ("TRUOC", "SAU", "KHAC"):
            raise BusinessError("BAD_TAG", "Hãy chọn ảnh trước, ảnh sau sửa hoặc tệp khác.")
        upload = form.get("file")
        if not isinstance(upload, UploadFile) or not upload.filename:
            raise BusinessError("EMPTY_FILE", "Hãy chọn ảnh hoặc tệp cần tải.")
        limit = get_settings().max_upload_mb * 1024 * 1024
        data = await upload.read(limit + 1)
        if len(data) > limit:
            raise BusinessError("FILE_TOO_LARGE", "Tệp quá lớn.", {"max_mb": get_settings().max_upload_mb})
        if tag in ("TRUOC", "SAU") and data.startswith(b"%PDF-"):
            raise BusinessError("BAD_FILE_TYPE", "Ảnh trước và ảnh sau sửa phải là ảnh JPEG, PNG hoặc WEBP.")
        attachments.add(session, user, "service_ticket", str(ticket_id), tag, upload.filename, data)
    except BusinessError as exc:
        return _detail(request, session, user, ticket_id, error=exc.message, error_code=exc.code,
                       status=exc.status, photo_form={"tag": tag})
    return RedirectResponse(f"/dich-vu/{ticket_id}?da_luu=1#anh", status_code=303)


@router.post("/dich-vu/{ticket_id}/anh/{attachment_id}/an")
def photo_hide(request: Request, ticket_id: int, attachment_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    svc.load(session, user, ticket_id)
    # D5 giới hạn quyền ẩn tại màn hình này hơn API D4 (API còn cho người tải tự ẩn).
    if not has_role(user, "dispatcher"):
        raise BusinessError("FORBIDDEN", "Chỉ điều phối hoặc giám đốc được ẩn ảnh.", status=403)
    files = attachments.list_for(session, user, "service_ticket", str(ticket_id))
    if attachment_id not in {a.id for a in files}:
        raise BusinessError("NOT_FOUND", "Không tìm thấy tệp của phiếu này.", status=404)
    try:
        attachments.hide(session, user, attachment_id, str(form.get("reason", "")), _version(form))
    except BusinessError as exc:
        return _detail(request, session, user, ticket_id, error=exc.message, error_code=exc.code, status=exc.status,
                       photo_form={"attachment_id": attachment_id, "reason": str(form.get("reason", "")),
                                   "version": form.get("version", "")})
    return RedirectResponse(f"/dich-vu/{ticket_id}?da_luu=1#anh", status_code=303)


@router.get("/dich-vu/{ticket_id}/bien-ban")
def acceptance_report(request: Request, ticket_id: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    from ..models import CustomerSite, Equipment, Product
    t = svc.load(session, user, ticket_id)
    machine = session.get(Equipment, t.serial) if t.serial else None
    product = session.get(Product, machine.product_code) if machine else None
    completion = next((step for step in reversed(t.steps) if step.to_status == "HOAN_THANH"), None)
    last = t.steps[-1] if t.steps else None
    photos = [a for a in attachments.list_for(session, user, "service_ticket", str(t.id))
              if a.tag == "SAU" and a.content_type.startswith("image/")]
    return render(request, "dich_vu_bien_ban.jinja", t=t, customer=session.get(Customer, t.customer_code),
                  site=session.get(CustomerSite, t.site_id) if t.site_id else None, product=product,
                  technician=session.get(User, t.assignee_id) if t.assignee_id else None,
                  work_note=last.note if last else "", completed_at=completion.at if completion else None, photos=photos)
