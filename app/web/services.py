"""Màn hình D3: đọc danh mục để chọn, mọi thay đổi phiếu đi qua lõi D2."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from .. import attachments, service_appointments, service_charges, service_visits, service_warranty, technician_returns
from ..config import get_settings
from .. import customers as customer_svc
from .. import service_tickets as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, Product, TechnicianReturn, User, ServiceTicket
from ..schemas import ServiceAssign, ServiceKindSet, ServiceReason, ServiceTicketIn, ServiceTransition
from ..security import has_role
from . import checked_form, render, templates, web_user, nav

from . import next_steps, picked, steps

router = APIRouter()
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
STATUSES = {
    "MOI": "Mới", "DA_GIAO": "Đã giao", "DA_NHAN": "Đã nhận", "DA_HEN": "Đã hẹn",
    "DANG_LAM": "Đang làm", "CHO_LINH_KIEN": "Chờ linh kiện", "TAM_DUNG": "Tạm dừng", "HOAN_THANH": "Hoàn thành",
    "DA_DONG": "Đã đóng", "KHONG_LAM_DUOC": "Không làm được", "DA_HUY": "Đã hủy",
}
KINDS = {"SUA_TAI_CHO": "Sửa tại chỗ", "MANG_VE": "Mang về", "BAO_HANH": "Bảo hành", "MUC_DINH_KY": "Mực định kỳ"}
PRIORITIES = {"KHAN": "Khẩn", "GAP": "Gấp", "THUONG": "Thường"}
TRANSITIONS = {
    "DA_NHAN": "Nhận đơn", "DA_HEN": "Hẹn khách", "DANG_LAM": "Đã đến khách", "CHO_LINH_KIEN": "Chờ linh kiện",
    "TAM_DUNG": "Tạm dừng", "HOAN_THANH": "Hoàn thành", "DA_DONG": "Đóng hồ sơ", "KHONG_LAM_DUOC": "Không làm được", "DA_HUY": "Hủy",
}
STEPS = {"create": "Nhận đơn", "assign": "Giao thợ", "reassign": "Giao lại", "set_kind": "Chọn loại dịch vụ",
         "transition": "Chuyển trạng thái", "reschedule": "Dời hẹn", "cancel": "Hủy", "workshop":"Mang máy về xưởng", "repair_done":"Sửa xong, chờ trả máy",
         "transfer_request": "Xin chuyển việc", "transfer_reject": "Từ chối chuyển",
         "no_show": "Không gặp khách", "reopen": "Mở lại", "close": "Đóng hồ sơ"}


def local_time(value):
    return value.astimezone(TZ).strftime("%d/%m/%Y %H:%M") if value else "—"


templates.env.filters["service_time"] = local_time
templates.env.filters["service_short"] = lambda value: value.astimezone(TZ).strftime("%d/%m %H:%M") if value else "—"
templates.env.filters["appointment_label"] = service_appointments.label
templates.env.globals.update(service_statuses=STATUSES, service_kinds={None: 'Chưa phân loại', **KINDS}, service_kind_choices=KINDS,
                             service_priorities=PRIORITIES, step_labels=STEPS, hold_reasons=svc.HOLD_REASONS)


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
    if not error['loc']:
        return str(error.get('ctx', {}).get('error', 'Dữ liệu chưa hợp lệ, hãy kiểm tra lại.'))
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


LIST_GROUPS = {
    "moi": ("Chưa giao thợ", ("MOI",)),
    "lam": ("Đang xử lý", ("DA_GIAO", "DA_NHAN", "DA_HEN", "DANG_LAM")),
    "cho": ("Chờ linh kiện · tạm dừng", ("CHO_LINH_KIEN", "TAM_DUNG")),
    "xong": ("Hoàn thành", ("HOAN_THANH", "DA_DONG")),
    "huy": ("Không làm được · hủy", ("KHONG_LAM_DUOC", "DA_HUY")),
}
STATUS_GROUP = {st: key for key, (_, sts) in LIST_GROUPS.items() for st in sts}


def _list_rows(session, rows):
    """Tên khách, số điện thoại, địa điểm, máy cho từng dòng danh sách, mỗi loại một truy vấn."""
    from ..models import CustomerPhone, CustomerSite, Equipment
    codes = {t.customer_code for t in rows}
    customers = dict(session.execute(select(Customer.code, Customer.name).where(Customer.code.in_(codes))).all()) if codes else {}
    phones = {}
    if codes:
        for code, phone in session.execute(select(CustomerPhone.customer_code, CustomerPhone.phone)
                                           .where(CustomerPhone.customer_code.in_(codes))
                                           .order_by(CustomerPhone.is_contact, CustomerPhone.phone)):
            phones.setdefault(code, phone)
    site_ids = {t.site_id for t in rows if t.site_id}
    sites = dict(session.execute(select(CustomerSite.id, CustomerSite.name).where(CustomerSite.id.in_(site_ids))).all()) if site_ids else {}
    serials = {t.serial for t in rows if t.serial}
    machines = dict(session.execute(select(Equipment.serial, Product.name).join(Product, Product.code == Equipment.product_code)
                                    .where(Equipment.serial.in_(serials))).all()) if serials else {}
    return customers, phones, sites, machines


@router.get("/dich-vu")
def listing(request: Request, q: str = "", status: str = "", assignee_id: str = "", overdue: bool = False,
            phan: int = 0, nhom: str = "", loai: str = "", trang: str = "", so: str = "",
            user: User = Depends(web_user), session: Session = Depends(get_session)):
    from . import paging
    q = q[:200]
    own_only = _own_only(user)
    unassigned = assignee_id == "chua" and not own_only
    selected = int(assignee_id) if assignee_id.isdigit() and len(assignee_id) <= 18 else None
    kind = svc._ANY if not loai else (None if loai == "chua" else loai)
    if loai and loai != "chua" and loai not in KINDS:
        kind, loai = svc._ANY, ""
    group = nhom if nhom in LIST_GROUPS else ""
    status = status if status in STATUSES else ""
    filters = dict(assignee_id=selected, overdue=overdue, q=q, kind=kind, unassigned=unassigned)
    counts = svc.count_by_status(session, user, **filters)
    if status:
        statuses, total = (status,), counts.get(status, 0)
    elif group:
        statuses = LIST_GROUPS[group][1]
        total = sum(counts.get(st, 0) for st in statuses)
    else:
        statuses, total = None, sum(counts.values())
    query = dict(q=q, status=status, nhom=group, assignee_id=assignee_id if (selected or unassigned) else "",
                 loai=loai, overdue="1" if overdue else "")
    page = paging.build(trang, so, total, query)
    rows = svc.search(session, user, None, selected, overdue, q, page["size"], statuses=statuses, kind=kind,
                      unassigned=unassigned, offset=page["offset"], by_priority=group in ("moi", "lam")) if total else []
    customers, phones, sites, machines = _list_rows(session, rows)
    ctx = dict(rows=rows, q=q, selected_status=status, selected_group=group, selected_assignee=selected,
               unassigned=unassigned, selected_kind=loai, overdue=overdue, page=page,
               customers=customers, phones=phones, sites=sites, machines=machines, users=_users(session),
               is_overdue=_is_overdue, status_group=STATUS_GROUP, own_only=own_only)
    if phan:
        return render(request, "dich_vu_dong.jinja", **ctx)
    # Ô số và tab: ô số đếm toàn bộ phiếu (chỉ giới hạn theo quyền), tab đếm theo bộ lọc đang chọn.
    everything = svc.count_by_status(session, user)
    overdue_all = sum(svc.count_by_status(session, user, overdue=True).values())
    urgent_new = session.scalar(select(func.count()).select_from(svc._search_stmt(user, "MOI").where(ServiceTicket.priority == "KHAN").subquery()))
    def group_count(source, key):
        return sum(source.get(st, 0) for st in LIST_GROUPS[key][1])
    tiles = [
        dict(key="moi", label="Chưa giao thợ", count=group_count(everything, "moi"), sub=f"{urgent_new} phiếu Khẩn" if urgent_new else "Không có phiếu Khẩn", tone="warn"),
        dict(key="lam", label="Đang xử lý", count=group_count(everything, "lam"), sub=f"{everything.get('DANG_LAM', 0)} phiếu đang làm", tone="info"),
        dict(key="qua-han", label="Quá hạn", count=overdue_all, sub="Cần xử lý trước" if overdue_all else "Không có phiếu trễ", tone="bad"),
        dict(key="cho", label="Chờ linh kiện · tạm dừng", count=group_count(everything, "cho"), sub=f"{everything.get('CHO_LINH_KIEN', 0)} chờ linh kiện", tone=""),
    ]
    tabs = [dict(key="", label="Tất cả", count=sum(counts.values()))] + [
        dict(key=key, label=label, count=group_count(counts, key)) for key, (label, _) in LIST_GROUPS.items()]
    base = {k: v for k, v in query.items() if k not in ("nhom", "status")}
    from urllib.parse import urlencode
    def link(**changes):
        merged = {k: v for k, v in {**base, **changes}.items() if v}
        return "/dich-vu" + ("?" + urlencode(merged) if merged else "")
    return render(request, "dich_vu_ds.jinja", **ctx, tiles=tiles, tabs=tabs, link=link,
                  technicians=[] if own_only else _technicians(session),
                  heading="Việc của tôi" if own_only else "Dịch vụ",
                  can_create=has_role(user, *svc.CREATORS), can_receivables=nav.allowed(user, "/dich-vu/can-thu"))


def _detail(request, session, user, ticket_id, form=None, failed_action="", error=None, error_code="", status=200, photo_form=None, charge_shown=None, recovered_form=None, warranty_form=None, receipt_form=None, receipt_decision=None, failed_receipt_id=None, failed_receipt_action="", part_form=None, custody_shown=None, technician_view=False, completion_form=None, return_form=None, workshop_form=None):
    from .recovered_parts import detail_context as recovered_context
    from .custody import context as custody_context
    from .parts import detail_context as part_context
    from .charges import detail_context
    from .receipts import ticket_context
    t = svc.load(session, user, ticket_id)
    technician_view = technician_view or getattr(request.state,'technician_return',False)
    if technician_view:
        from .technician import owned
        owned(session,user,ticket_id)
    dispatcher = has_role(user, *svc.DISPATCHERS) and not technician_view
    own = "technician" in user.roles and t.assignee_id == user.id and t.status not in ('HOAN_THANH','DA_DONG')
    targets = sorted(svc.FLOW.get(t.status, ()), key=lambda s: list(STATUSES).index(s)) if dispatcher or own else []
    targets=[target for target in targets if target!='DA_DONG']
    if t.status=='TAM_DUNG':
        targets=[s for s in targets if not (s=='DA_HEN' and (not dispatcher or t.hold_reason!='SUA_XONG_CHO_TRA')) and not (s=='DANG_LAM' and t.hold_reason=='SUA_XONG_CHO_TRA')]
    if dispatcher and t.status in svc.CANCELLABLE:
        if "DA_HUY" not in targets: targets.append("DA_HUY")
    if t.status == 'DA_NHAN' and t.appointment_at and (dispatcher or own):
        targets = [x for x in targets if x != 'DA_HEN'] + ['DANG_LAM']
    labels = dict(TRANSITIONS)
    labels["DA_HUY"] = "Hủy đơn"
    if t.status in ("CHO_LINH_KIEN", "TAM_DUNG"):
        labels["DANG_LAM"] = "Tiếp tục làm"
    if t.status in ('HOAN_THANH','DA_DONG'):
        labels["DANG_LAM"] = "Mở lại"
    if t.status == "DANG_LAM":
        labels["DA_HEN"] = "Khách hẹn lại"
    if t.status == "DA_HEN":
        labels["DA_HEN"] = "Dời hẹn"
    can_no_show = (dispatcher or own) and (t.status == "DA_HEN" or (t.status == "DA_NHAN" and t.appointment_at is not None))
    visible_actions = {"transition:" + target for target in targets}
    visible_actions.update(("workshop","repair-done","schedule-return","kind"))
    if can_no_show:
        visible_actions.add("no-show")
    if dispatcher and t.status in ('MOI','DA_GIAO','DA_NHAN','DA_HEN'): visible_actions.add('reschedule')
    if dispatcher and t.status in svc.ASSIGNABLE:
        visible_actions.add("assign")
    if own and t.status in svc.OPEN and not t.transfer_request:
        visible_actions.add("transfer-request")
    if dispatcher and t.transfer_request:
        visible_actions.add("transfer-reject")
    retained_fields = [(key, str(value)) for key, value in (form or {}).items()
                       if key in {"version", "assignee_id", "to_status", "note", "reason", "appointment_at", "hold_reason"}]
    # D1 là danh mục chỉ đọc; phiếu và lịch sử luôn được tải qua lõi D2.
    from ..models import CustomerSite
    site = session.get(CustomerSite, t.site_id) if t.site_id else None
    from ..models import SalesOrder
    linked_orders=list(session.scalars(select(SalesOrder).where(SalesOrder.service_ticket_id==t.id)))
    if not has_role(user,'dispatcher','sales','warehouse','finance'):
        linked_orders=[o for o in linked_orders if o.delivery_assignee_id==user.id]
    returning = technician_returns.proposal(session,t) if t.status in svc.OPEN else dict(rows=[],warehouses=[],warehouse=None)
    return_rows = return_form if return_form is not None else returning['rows']
    ctx = dict(can_set_kind=t.status in svc.OPEN and (dispatcher or own),workshop_form=workshop_form or {},can_schedule_return=dispatcher and t.status=='TAM_DUNG' and t.hold_reason=='SUA_XONG_CHO_TRA',technician_cancel_allowed=own and not dispatcher and t.status in ('DANG_LAM','CHO_LINH_KIEN','TAM_DUNG'),appointment_choices=service_appointments.choices(svc._now()), appointment_return_choices=service_appointments.choices(svc._now(),True),
                  can_reschedule=dispatcher and t.status in ('MOI','DA_GIAO','DA_NHAN','DA_HEN'),
                  return_rows=return_rows, return_warehouses=returning['warehouses'],
                  return_names=dict(session.execute(select(Product.code,Product.name).where(Product.code.in_([r['product_code'] for r in return_rows]))).all()),
                  ticket_returns=list(session.scalars(select(TechnicianReturn).where(TechnicianReturn.ticket_id==t.id))),
                  receipt_error=receipt_form is not None, completion_form=completion_form or {}, warranty=service_warranty.info(session,t),
                  warranty_children=list(session.scalars(select(ServiceTicket).where(ServiceTicket.warranty_of_id==t.id).order_by(ServiceTicket.id))),
                  warranty_form=warranty_form or {}, can_relink=dispatcher and t.kind=='BAO_HANH' and t.status in svc.OPEN,
                  **recovered_context(session,user,t,recovered_form), visits=service_visits.list_for(session,t.id), visit_outcomes=service_visits.OUTCOMES, can_no_show=can_no_show, linked_orders=linked_orders, can_add_sale=has_role(user,'sales') and t.status in svc.OPEN, t=t, customer=session.get(Customer, t.customer_code), site=site,
                  files=attachments.list_for(session, user, "service_ticket", str(t.id)),
                  next_step=next_steps.service(session,user,t),can_upload=has_role(user, *svc.CREATORS) or own,
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
                  **part_context(session, user, t, part_form),
                  **custody_context(session,user,t,custody_shown),
                  **ticket_context(session,user,t,receipt_form,receipt_decision,failed_receipt_id,failed_receipt_action))
    if not technician_view:
        ctx['workflow']=steps.service(t)
        ctx['check_heading']=''
        ctx['primary_checks']=steps.service_checks(session,t,ctx['next_step']['primary'] if ctx['next_step'] else None)
        if t.status=='HOAN_THANH':ctx['primary_checks']=[]  # waiting to close never blocks reporting receipts
    ctx['auto_close_checks']=steps.service_checks(session,t,None) if t.status=='HOAN_THANH' else []
    if technician_view:
        from .technician import detail_context as technician_context
        ctx.update(technician_context(session,user,t,ctx))
    return render(request, 'tho_phieu.jinja' if technician_view else 'dich_vu_chi_tiet.jinja', **ctx)


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
    data = data or {"customer_code": "", "site_id": "", "serial": "", "kind": "", "priority": "THUONG", "symptom": "", "warranty_of_id": ""}
    customer = session.get(Customer, data['customer_code']) if data['customer_code'] else None
    sites, machines = _choices(session, data['customer_code'])
    if not error:
        if 'site_id' not in data and len(sites)==1: data['site_id']=str(sites[0].id)
        if 'serial' not in data and len(machines)==1: data['serial']=machines[0][0].serial
    can_create=has_role(request.state.user, *svc.CREATORS)
    return render(request, "dich_vu_moi.jinja", appointment_choices=service_appointments.choices(svc._now()), next_step=next_steps.block(next_steps.form("Nhận đơn","service-create","/dich-vu/moi")) if can_create else None,d=data, customer_card=picked.customer_cards(session,request.state.user,[customer]).get(customer.code) if customer else None, customer_name=customer.name if customer else "",
                  sites=sites, sites_main_address=customer.address if customer else '', machines=machines, warranty_selected=session.get(ServiceTicket,int(data['warranty_of_id'])) if str(data.get('warranty_of_id','')).isdigit() else None, error=error, error_code=error_code, status=status,
                  can_create=can_create,can_assign_new=has_role(request.state.user,*svc.DISPATCHERS),technicians=_technicians(session) if has_role(request.state.user,*svc.DISPATCHERS) else [])


@router.get("/dich-vu/tim-khach")
def pick_customer(request: Request, q: str = "", user: User = Depends(web_user), session: Session = Depends(get_session)):
    rows=[c for c in customer_svc.search(session,q[:200]) if c.status=="DANG_DUNG"]
    return render(request,"dich_vu_chon_khach.jinja",rows=rows,customer_cards=picked.customer_cards(session,user,rows))


@router.get("/dich-vu/chon-khach")
def pick_choices(request: Request, customer_code: str = "", user: User = Depends(web_user), session: Session = Depends(get_session)):
    sites, machines = _choices(session, customer_code)
    customer = session.get(Customer, customer_code[:50]) if customer_code else None
    return render(request, "dich_vu_chon_may.jinja", sites=sites, machines=machines, sites_main_address=customer.address if customer else '', d=dict(site_id=str(sites[0].id) if len(sites)==1 else '', serial=machines[0][0].serial if len(machines)==1 else ''))


@router.get("/dich-vu/moi")
def new(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    if 'tiep_theo' in request.query_params:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy trang này.', status=404)
    chosen = session.get(Customer, request.query_params.get('customer_code','')[:50]) if request.query_params.get('customer_code') else None
    if chosen and chosen.status == 'DANG_DUNG':
        # Quay về từ màn tạo/chọn khách: chọn sẵn khách, để _new_form tự chọn máy/địa điểm duy nhất.
        return _new_form(request, session, dict(customer_code=chosen.code, kind='', priority='THUONG', symptom='', warranty_of_id=''))
    return _new_form(request, session)


@router.post("/dich-vu/moi")
def create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    data = {key: str(form.get(key, "")) for key in ("customer_code", "site_id", "serial", "kind", "priority", "symptom", "warranty_of_id", "appointment_at", "appointment_slot", "appointment_choice", "assignee_id")}
    try:
        appointment, slot = service_appointments.parse(form,svc._now())
        body = ServiceTicketIn(**{**{k:v for k,v in data.items() if k not in ('appointment_at','appointment_slot','appointment_choice','assignee_id')},
                                 "appointment_at":appointment,"appointment_slot":slot, "site_id": data['site_id'] or None, "serial": data['serial'] or None, "warranty_of_id": data['warranty_of_id'] or None})
        assignee=str(form.get('assignee_id','')).strip()
        if assignee:
            if not assignee.isdigit() or len(assignee)>18 or int(assignee)<1: raise BusinessError('BAD_ASSIGNEE','Thợ được chọn chưa hợp lệ.')
            t=svc.create_and_assign(session,request.state.user,body,int(assignee))
        else: t=svc.create(session,request.state.user,body)
    except (BusinessError, ValidationError) as exc:
        error, code, status = _message(exc)
        return _new_form(request, session, data, error, code, status)
    return RedirectResponse(f"/dich-vu/{t.id}?da_luu=1", status_code=303)


@router.get('/dich-vu/tim-bao-hanh')
def warranty_suggestions(request: Request, customer_code: str = '', serial: str = '', q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    svc._require(user, svc.CREATORS, 'Chỉ người nhận đơn hoặc điều phối được tìm phiếu bảo hành.')
    rows = service_warranty.suggestions(session, customer_code, svc._serial(serial), q[:200])
    return render(request, 'bao_hanh_chon.jinja', rows=rows,
        completions={t.id: service_warranty.completion(session,t) for t in rows},
        today=service_warranty.today(), warranty_tz=TZ, warranty_days=get_settings().service_warranty_days,
        users=_users(session))


@router.get("/dich-vu/{ticket_id}")
def detail(request: Request, ticket_id: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _detail(request, session, user, ticket_id)


def _workshop_materials(form):
    codes,quantities=form.getlist('material_product'),form.getlist('material_qty')
    if len(codes)!=len(quantities):
        raise BusinessError('BAD_FORM','Dòng vật tư chưa đủ dữ liệu.')
    return [dict(product_code=str(c),qty=str(q)) for c,q in zip(codes,quantities)]


@router.post('/dich-vu/{ticket_id}/mang-ve-xuong')
def workshop_bring(request:Request,ticket_id:int,form=Depends(checked_form),session:Session=Depends(get_session)):
    from .. import service_workshop
    from ..schemas import CustodyIntake
    from .custody import _accessory_values
    from .technician import owned,after_post
    user=request.state.user;owned(session,user,ticket_id)
    shown=dict(condition_note=str(form.get('condition_note','')),accessories=[str(v) for v in form.getlist('accessories')],accessories_note=str(form.get('accessories_note','')))
    try:
        shown['materials']=_workshop_materials(form)
        body=CustodyIntake(version=form.get('machine_version'),accessories=_accessory_values(form),
            condition_note=shown['condition_note'],accessories_note=shown['accessories_note'])
        service_workshop.bring(session,user,ticket_id,_version(form),body,shown['materials'])
    except (BusinessError,ValidationError) as exc:
        message,code,status=_message(exc)
        return _detail(request,session,user,ticket_id,failed_action='workshop',error=message,error_code=code,status=status,technician_view=True,workshop_form=shown)
    return after_post(request,session,user,ticket_id,f'/tho/phieu/{ticket_id}?da_luu=1')


@router.post('/dich-vu/{ticket_id}/sua-xong-cho-tra')
def workshop_repaired(request:Request,ticket_id:int,form=Depends(checked_form),session:Session=Depends(get_session)):
    from .. import service_workshop
    from .technician import owned,after_post,report_fields
    user=request.state.user;owned(session,user,ticket_id)
    shown={key:str(form.get(key,'')) for key in ('customer_amount','collection_mode','free_reason')}
    try:
        shown['materials']=_workshop_materials(form)
        fault,fix,report=report_fields(form);shown.update(report)
        service_workshop.repair_done(session,user,ticket_id,_version(form),shown['customer_amount'],shown['collection_mode'],
            shown['free_reason'],shown['materials'],fault,fix)
    except (BusinessError,ValidationError) as exc:
        message,code,status=_message(exc)
        return _detail(request,session,user,ticket_id,failed_action='repair-done',error=message,error_code=code,status=status,completion_form=shown,technician_view=True)
    return after_post(request,session,user,ticket_id,f'/tho/phieu/{ticket_id}?da_luu=1')


@router.post('/dich-vu/{ticket_id}/hen-tra-may')
def workshop_schedule(request:Request,ticket_id:int,form=Depends(checked_form),session:Session=Depends(get_session)):
    from .. import service_workshop
    user=request.state.user;svc._require(user,svc.DISPATCHERS,'Chỉ điều phối được hẹn trả máy.')
    try:
        appointment,slot=service_appointments.parse(form,svc._now())
        ident=str(form.get('assignee_id',''))
        if not ident.isdigit():raise BusinessError('BAD_ASSIGNEE','Chọn thợ đi trả máy.')
        service_workshop.schedule_return(session,user,ticket_id,_version(form),appointment,slot,int(ident),str(form.get('note','')))
    except (BusinessError,ValidationError) as exc:
        message,code,status=_message(exc)
        return _detail(request,session,user,ticket_id,form=form,failed_action='schedule-return',error=message,error_code=code,status=status)
    return RedirectResponse(f'/dich-vu/{ticket_id}?da_luu=1',status_code=303)


@router.post('/dich-vu/{ticket_id}/kind')
def set_kind(request: Request, ticket_id: int, form=Depends(checked_form), session: Session=Depends(get_session)):
    user=request.state.user
    try:
        svc.set_kind(session,user,ticket_id,ServiceKindSet(kind=form.get('kind'),version=_version(form)))
    except (BusinessError,ValidationError) as exc:
        if isinstance(exc,BusinessError) and exc.status in (403,404):
            return render(request,'loi.jinja',error=exc,status=exc.status)
        error,code,status=_message(exc)
        return _detail(request,session,user,ticket_id,form,failed_action='kind',error=error,error_code=code,status=status)
    from .technician import after_post
    return after_post(request,session,user,ticket_id,f'/dich-vu/{ticket_id}?da_luu=1')


@router.post("/dich-vu/{ticket_id}/{action}")
def action(request: Request, ticket_id: int, action: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    if ('technician' in user.roles and (form.get('appointment_choice') or (form.get('to_status')=='DA_HUY' and not has_role(user,*svc.DISPATCHERS)))):
        from .technician import owned
        owned(session,user,ticket_id)
    # Người chỉ nhận giữ máy bị 403 khi sửa phiếu; thợ ngoài cuộc vẫn 404.
    svc._get(session, user, ticket_id, lock=True)
    failed_action = "transition:" + str(form.get("to_status", "")) if action == "transition" else action
    try:
        if form.get("appointment_choice") == "LAN_SAU":
            raise BusinessError("BAD_APPOINTMENT", "Khung hẹn chưa hợp lệ.")
        version = _version(form)
        if action == "assign":
            body = ServiceAssign(assignee_id=form.get('assignee_id'), version=version, note=form.get('note', ''))
            svc.assign(session, user, ticket_id, body.assignee_id, body.version, body.note)
        elif action == 'reschedule':
            appointment, slot = service_appointments.parse(form,svc._now())
            svc.reschedule(session,user,ticket_id,version,appointment,slot,str(form.get('note','')))
        elif action in ("transition", "no-show"):
            if action=='transition' and form.get('to_status')=='DA_HUY' and not has_role(user,*svc.DISPATCHERS):
                technician_returns.cancel_by_technician(session,user,ticket_id,version,str(form.get('note','')),return_lines(form) if form.getlist('return_product') else None)
                from .technician import after_post
                return after_post(request,session,user,ticket_id,f'/dich-vu/{ticket_id}?da_luu=1')
            slot = None
            appointment = str(form.get("appointment_at", "")).strip()
            if appointment:
                try:
                    value = datetime.fromisoformat(appointment)
                except ValueError:
                    raise BusinessError("BAD_APPOINTMENT", "Ngày giờ hẹn chưa hợp lệ.")
                # datetime-local không có múi giờ: luôn gửi giờ Việt Nam +07:00 tới lõi.
                appointment = value.replace(tzinfo=TZ).isoformat() if value.tzinfo is None else value.isoformat()
            if form.get('appointment_choice') or form.get('appointment_slot'):
                value, slot = service_appointments.parse(form,svc._now())
                appointment = value.isoformat()
            body = ServiceTransition(appointment_slot=slot, to_status="DA_HEN" if action == "no-show" else form.get('to_status'), version=version,
                                     note=form.get('note', ''), appointment_at=appointment or None, hold_reason=str(form.get("hold_reason", "")))
            if action == "no-show":
                svc.no_show(session, user, ticket_id, body.version, body.note, body.appointment_at, body.appointment_slot)
            else:
                svc.transition(session, user, ticket_id, body.to_status, body.version, body.note, body.appointment_at, body.hold_reason, body.appointment_slot)
                if body.to_status == 'DANG_LAM' and form.get('gps_lat'):
                    service_visits.record_arrival(session, user, ticket_id, form.get('gps_lat'), form.get('gps_lng'), form.get('gps_accuracy'))
        elif action in ("transfer-request", "transfer-reject"):
            body = ServiceReason(reason=form.get('reason', ''), version=version)
            fn = svc.request_transfer if action == "transfer-request" else svc.reject_transfer
            fn(session, user, ticket_id, body.reason, body.version)
        else:
            raise BusinessError("NOT_FOUND", "Không tìm thấy thao tác này.", status=404)
    except (BusinessError, ValidationError) as exc:
        error, code, status = _message(exc)
        return _detail(request, session, user, ticket_id, form, failed_action, error, code, status, return_form=return_lines(form, photos=False) if form.getlist("return_product") else None)
    from .technician import after_post
    return after_post(request,session,user,ticket_id,f"/dich-vu/{ticket_id}?da_luu=1")


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
    from .technician import after_post
    return after_post(request,session,user,ticket_id,f"/dich-vu/{ticket_id}?da_luu=1#anh")


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
    from .technician import after_post
    return after_post(request,session,user,ticket_id,f"/dich-vu/{ticket_id}?da_luu=1#anh")


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
                  work_note=completion.note if completion and t.status in ('HOAN_THANH','DA_DONG') else last.note if last else "",
                  completed_at=completion.at if completion else None, photos=photos)


def return_lines(form, photos=True):
    from ..config import get_settings
    from starlette.datastructures import UploadFile
    codes=form.getlist('return_product');quantities=form.getlist('return_qty');warehouses=form.getlist('return_warehouse')
    if not len(codes)==len(quantities)==len(warehouses): raise BusinessError('BAD_LINE','Dòng trả kho chưa đủ thông tin.')
    rows=[]
    for i,(code,qty,warehouse) in enumerate(zip(codes,quantities,warehouses)):
        row=dict(product_code=str(code),qty=str(qty),to_warehouse_code=str(warehouse),
            condition=str(form.get(f'return_condition_{i}','TOT')),damage_reason=str(form.get(f'return_damage_reason_{i}','')))
        upload=form.get(f'return_photo_{i}')
        if photos and isinstance(upload,UploadFile) and upload.filename:
            row['photo']=dict(filename=upload.filename,data=upload.file.read(get_settings().max_upload_mb*1024*1024+1))
        rows.append(row)
    return rows
