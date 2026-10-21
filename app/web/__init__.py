"""Giao diện web cho nhân viên: đăng nhập, khách hàng, hàng hóa. Mọi quy tắc nằm ở tầng nghiệp vụ, trang web chỉ gọi vào."""
import hashlib
import re
import hmac
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlencode

import jwt
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import customers as customer_svc
from .. import customer_sites as site_svc
from .. import products as product_svc
from .. import inventory_catalogs as catalog_svc
from .. import form_keys
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import Supplier, User, Warehouse
from ..routers.customers import WRITERS as CUSTOMER_WRITERS
from ..schemas import (Confirmation, CustomerCreate, CustomerIn, CustomerPatch, ProductIn, ProductPatch,
                       SupplierIn, SupplierPatch, WarehouseIn, WarehousePatch)
from ..security import has_role, issue_token, verify_password
from . import nav

HERE = Path(__file__).parent
COOKIE = "td_session"
router = APIRouter(include_in_schema=False)
static = StaticFiles(directory=HERE / "static")
templates = Jinja2Templates(directory=HERE / "templates")

KIND_LABELS = {"CA_NHAN": "Cá nhân", "CONG_TY": "Công ty"}
STATUS_LABELS = {"DANG_DUNG": "Đang dùng", "NGUNG": "Ngừng", "DA_GOP": "Đã gộp"}
WARNING_LABELS = {"SIMILAR_NAME": "Có khách khác cùng tên", "PHONE_SHARED_CONFIRMED": "Dùng chung số điện thoại với"}


def money(value: int | None) -> str:
    return f"{int(value or 0):,}".replace(",", ".")


def quantity(value):
    text = format(Decimal(str(value or 0)), 'f')
    return (text.rstrip('0').rstrip('.') if '.' in text else text).replace('.', ',')


templates.env.filters['qty'] = quantity
templates.env.filters["money"] = money
templates.env.filters["urlquote"] = lambda value: quote(str(value), safe="")
templates.env.globals["web_navigation"] = nav.menu
templates.env.globals["tab_icons"] = nav.TAB_ICONS
templates.env.globals["form_key"] = form_keys.form_key
templates.env.globals.update(KIND_LABELS=KIND_LABELS, STATUS_LABELS=STATUS_LABELS, WARNING_LABELS=WARNING_LABELS)


class LoginRequired(Exception):
    def __init__(self, next_url: str):
        self.next_url = next_url


class PasswordChangeRequired(Exception):
    pass


async def password_change_handler(request: Request, exc: PasswordChangeRequired) -> Response:
    return RedirectResponse('/doi-mat-khau',status_code=303)


async def login_required_handler(request: Request, exc: LoginRequired) -> Response:
    return RedirectResponse("/dang-nhap?" + urlencode({"next": exc.next_url}), status_code=303)


async def web_error_page(request: Request, exc: BusinessError) -> Response:
    request.state.form_business_error = True
    return render(request, "loi.jinja", error=exc, status=exc.status)


def csrf_token(session_token: str) -> str:
    return hmac.new(get_settings().secret_key.encode(), b"csrf:" + session_token.encode(), hashlib.sha256).hexdigest()[:32]


def web_user(request: Request, session: Session = Depends(get_session)) -> User:
    """Người đang đăng nhập theo cookie. Chưa đăng nhập thì chuyển về trang đăng nhập."""
    request.state.web = True
    token = request.cookies.get(COOKIE, "")
    target = request.url.path + ("?" + request.url.query if request.url.query else "")
    try:
        payload=jwt.decode(token,get_settings().secret_key,algorithms=["HS256"])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise LoginRequired(target)
    user = session.get(User, user_id)
    if not user or not user.active or type(payload.get("tv")) is not int or payload["tv"]!=user.token_version:
        raise LoginRequired(target)
    request.state.user = user
    request.state.csrf = csrf_token(token)
    if user.must_change_password and request.url.path not in ("/doi-mat-khau","/dang-xuat"):
        raise PasswordChangeRequired()
    return user


async def checked_form(request: Request, user: User = Depends(web_user)):
    """Đọc form gửi lên và chặn form giả mạo từ trang khác (kiểm mã csrf)."""
    form = await request.form()
    if not hmac.compare_digest(str(form.get("csrf", "")), request.state.csrf):
        raise BusinessError("BAD_CSRF", "Trang đã cũ, hãy tải lại rồi thử lại.", status=403)
    request.state.form_submission_key = await run_in_threadpool(
        form_keys.reserve, form.get("form_key"), user.id, request.url.path,
    )
    request.state.technician_return = form.get('return_to') == 'tho' and 'technician' in user.roles
    return form


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    from ..config import DEFAULT_VAT_RATE
    ctx.setdefault("default_vat_rate", DEFAULT_VAT_RATE)
    status = ctx.pop("status", 200)
    if ctx.get("error") or ctx.get("error_code"):
        request.state.form_business_error = True
    ctx.setdefault("user", getattr(request.state, "user", None))
    ctx.setdefault("csrf", getattr(request.state, "csrf", ""))
    return templates.TemplateResponse(request, name, ctx, status_code=status)


def _digits(value) -> int:
    s = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(s) if s else 0


def _safe_next(value: str | None) -> str:
    v = value or ""
    return v if v.startswith("/") and not v.startswith("//") and "\\" not in v else "/"


def _error_text(exc: ValidationError) -> str:
    fields = {"name": "Tên", "email": "Email", "address": "Địa chỉ", "code": "Mã hàng", "unit": "Đơn vị tính", "phones": "Số điện thoại", "reason": "Lý do dùng chung số"}
    for err in exc.errors():
        loc = [str(x) for x in err["loc"]]
        label = next((fields[x] for x in reversed(loc) if x in fields), "Dữ liệu")
        if loc and loc[-1] == "code" and err["type"] == "string_pattern_mismatch":
            return "Mã hàng phải có dạng TD-0001."
        if err["type"] in ("string_too_short", "missing"):
            return f"{label} không được để trống."
        if err["type"] == "string_too_long":
            return f"{label} quá dài."
    return "Dữ liệu chưa hợp lệ, hãy kiểm tra lại."


def check_navigation(request: Request, session: Session = Depends(get_session)):
    if request.url.path not in nav.PUBLIC_PATHS:
        user = web_user(request, session)
        nav.check_access(user, request.url.path, request.query_params)


# Áp dụng trước mọi add_api_route/include_router, cả route danh mục tạo động.
# API và file ảnh vẫn dùng kiểm quyền thực thể ở lõi, không nhận dependency web.
router.dependencies.append(Depends(check_navigation))


# ---------- Đăng nhập ----------


@router.get("/")
def home(request: Request, session: Session = Depends(get_session)):
    try:
        user = web_user(request, session)
    except LoginRequired:
        return RedirectResponse("/dang-nhap", status_code=303)
    return RedirectResponse(nav.home_url(user), status_code=303)


@router.get("/viec-cua-toi")
def my_work_page(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    from .work import page
    return page(request, user, session)


@router.get("/dang-nhap")
def login_page(request: Request, next: str = "/", session: Session=Depends(get_session)):
    if request.cookies.get(COOKIE):
        try:web_user(request,session)
        except LoginRequired:pass
    return render(request, "dang_nhap.jinja", next=_safe_next(next), username="", error=None)


@router.post("/dang-nhap")
async def login_submit(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    username = str(form.get("username", "")).strip().lower()
    next_url = _safe_next(str(form.get("next", "")))
    from .. import users as user_service
    try:user,token=user_service.authenticate(session,username,str(form.get('password','')))
    except BusinessError:
        return render(request, "dang_nhap.jinja", next=next_url, username=username, error="Sai tên đăng nhập hoặc mật khẩu.", status=401)
    s = get_settings()
    # Quay lại trang đang mở trước khi hết phiên nếu vai trò được vào, không thì về trang đầu theo vai trò.
    target = next_url if next_url != "/" and nav.allowed(user, next_url.split("?")[0]) else nav.home_url(user)
    if user.must_change_password:target="/doi-mat-khau"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        COOKIE, token, max_age=s.token_minutes * 60, httponly=True, samesite="lax", secure=s.cookie_secure
    )
    return response


@router.post("/dang-xuat")
def logout(_=Depends(checked_form)):
    response = RedirectResponse("/dang-nhap", status_code=303)
    response.delete_cookie(COOKIE)
    return response


# ---------- Khách hàng ----------


def _customer_form(request: Request, user: User, c: dict, session=None, **ctx) -> HTMLResponse:
    return render(
        request,
        "khach_form.jinja",
        c=c,
        sites=site_svc.search(session, c["code"]) if session is not None and c.get("code") else [],
        can_site_write=has_role(user, *site_svc.WRITERS),
        can_write=has_role(user, *CUSTOMER_WRITERS),
        can_credit=has_role(user, *customer_svc.CREDIT_ROLES),
        **ctx,
    )


def _blank_customer() -> dict:
    return {
        "code": "", "kind": "CA_NHAN", "name": "", "tax_code": "", "phones": [], "email": "", "address": "", "note": "",
        "contacts": [], "allow_debt": False, "credit_limit": 0, "status": "DANG_DUNG", "old_codes": [], "is_test": False, "version": 0,
    }


def _customer_dict(c) -> dict:
    # Ô "Số điện thoại" chỉ chứa số của khách; số của người liên hệ nằm ở từng người liên hệ.
    return {**customer_svc.to_out(c).model_dump(), "phones": [p.phone for p in c.phones if not p.is_contact]}


def _customer_from_form(form) -> dict:
    contacts = []
    for name, ph, note in zip(form.getlist("contact_name"), form.getlist("contact_phones"), form.getlist("contact_note")):
        if str(name).strip() or str(ph).strip():
            contacts.append({"name": str(name).strip(), "phones": [str(ph)], "note": str(note).strip()})
    return {
        "kind": str(form.get("kind", "CA_NHAN")),
        "name": str(form.get("name", "")),
        "tax_code": str(form.get("tax_code", "")),
        "phones": [str(form.get("phones", ""))] if str(form.get("phones", "")).strip() else [],
        "email": str(form.get("email", "")),
        "address": str(form.get("address", "")),
        "note": str(form.get("note", "")),
        "contacts": contacts,
        "allow_debt": form.get("allow_debt") == "1",
        "credit_limit": _digits(form.get("credit_limit")),
        "status": str(form.get("status", "DANG_DUNG")),
    }


def _confirmation(form) -> Confirmation | None:
    if form.get("confirm_separate") != "1":
        return None
    return Confirmation(mode="CREATE_SEPARATE", reason=str(form.get("confirm_reason", "")).strip())


def _saved_url(code: str, warnings) -> str:
    query = {"da_luu": "1"}
    if warnings:
        query["cb"] = ",".join(f"{w.code}:{w.customer}" for w in warnings)
    return f"/khach-hang/{quote(code)}?" + urlencode(query)


@router.get("/khach-hang")
def customer_list(
    request: Request, q: str = "", phan: int = 0, user: User = Depends(web_user), session: Session = Depends(get_session)
):
    rows = [customer_svc.to_out(c) for c in customer_svc.search(session, q[:200])]
    name = "khach_dong.jinja" if phan else "khach_ds.jinja"
    return render(request, name, rows=rows, q=q, can_write=has_role(user, *CUSTOMER_WRITERS))


# Màn đơn mới được quay về sau khi tạo/chọn khách (chỉ các đường dẫn này, chống chuyển hướng ra ngoài).
RETURN_PATHS = ("/dich-vu/moi", "/bao-gia/moi", "/don-ban/moi")


def _return_to(value) -> str:
    value = str(value or "")
    return value if value in RETURN_PATHS else ""


def _back_url(return_to: str, code: str) -> str:
    return f"{return_to}?" + urlencode({"customer_code": code})


def _prefill_customer(q: str) -> dict:
    """Chữ đang gõ ở ô tìm khách: giống SĐT thì vào ô SĐT, giống MST thì vào ô MST, còn lại là tên."""
    q = q.strip()[:300]
    digits = re.sub(r"[ .()+-]", "", q)
    if digits.isdigit() and 9 <= len(digits) <= 11 and not (len(digits) == 10 and digits[0] != "0"):
        return {"phones": [q]}
    if digits.isdigit() and len(digits) in (10, 13):
        return {"tax_code": q}
    return {"name": q} if q else {}


@router.get("/khach-hang/moi")
def customer_new(request: Request, quay_ve: str = "", q: str = "", user: User = Depends(web_user)):
    if not has_role(user, *CUSTOMER_WRITERS):
        raise BusinessError("FORBIDDEN", "Tài khoản không có quyền tạo khách.", status=403)
    return _customer_form(request, user, {**_blank_customer(), **_prefill_customer(q)}, error=None, ask_confirm=None, return_to=_return_to(quay_ve))


@router.get("/khach-hang/kiem-trung")
def customer_duplicates(
    request: Request, phone: str = "", tax: str = "", kind: str = "CA_NHAN", except_code: str = Query("", alias="except"),
    quay_ve: str = "", user: User = Depends(web_user), session: Session = Depends(get_session),
):
    if not has_role(user, *CUSTOMER_WRITERS):
        raise BusinessError("FORBIDDEN", "Tài khoản không có quyền tạo khách.", status=403)
    rows = customer_svc.duplicates(session, phone[:500], tax[:50], except_code[:50])
    for r in rows:
        c = r["customer"]
        # Cùng mức như lúc lưu: trùng MST, hoặc trùng SĐT giữa hai khách cá nhân, thì không lưu được.
        r["blocked"] = r["by"] == "MST" or (c.kind == "CA_NHAN" and kind == "CA_NHAN")
    return render(request, "khach_trung.jinja", rows=rows, return_to=_return_to(quay_ve))


@router.post("/khach-hang/moi")
def customer_create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    if not has_role(user, *CUSTOMER_WRITERS):
        raise BusinessError("FORBIDDEN", "Tài khoản không có quyền tạo khách.", status=403)
    data = _customer_from_form(form)
    return_to = _return_to(form.get("quay_ve"))
    shown = {**_blank_customer(), **data}
    if not has_role(user, *customer_svc.CREDIT_ROLES):
        data.pop("allow_debt"), data.pop("credit_limit")
    data.pop("status")
    try:
        body = CustomerCreate(customer=CustomerIn(**data), confirmation=_confirmation(form))
        c, warnings = customer_svc.create(session, user, body)
    except ValidationError as exc:
        return _customer_form(request, user, shown, error=_error_text(exc), ask_confirm=None, form=form, status=400, return_to=return_to)
    except BusinessError as exc:
        ask = exc.details if exc.code == "PHONE_IN_USE" else None
        return _customer_form(request, user, shown, error=exc.message, error_detail=exc.details, ask_confirm=ask, form=form, status=exc.status, return_to=return_to)
    return RedirectResponse(_back_url(return_to, c.code) if return_to else _saved_url(c.code, warnings), status_code=303)


@router.get("/khach-hang/{code}")
def customer_view(
    request: Request, code: str, da_luu: int = 0, cb: str = "", user: User = Depends(web_user), session: Session = Depends(get_session)
):
    c = customer_svc.load(session, code)
    if c.code != code:
        return RedirectResponse(f"/khach-hang/{quote(c.code)}", status_code=303)
    warnings = [w.split(":", 1) for w in cb.split(",") if ":" in w and w.split(":", 1)[0] in WARNING_LABELS]
    return _customer_form(request, user, _customer_dict(c), session=session, error=None, ask_confirm=None, saved=bool(da_luu), warnings=warnings)


@router.post("/khach-hang/{code}")
def customer_update(request: Request, code: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    if not has_role(user, *CUSTOMER_WRITERS):
        raise BusinessError("FORBIDDEN", "Tài khoản không có quyền sửa khách.", status=403)
    data = _customer_from_form(form)
    # Giữ nguyên version người dùng đang thấy: nếu bị người khác sửa trước, lưu lại vẫn bị chặn cho tới khi tải lại trang.
    shown = {**_customer_dict(customer_svc.load(session, code, resolve_merged=False)), **data, "version": _digits(form.get("version"))}
    # Người không có quyền công nợ không gửi các trường đó, để không bị chặn khi chỉ sửa tên hay số điện thoại.
    if not has_role(user, *customer_svc.CREDIT_ROLES):
        data.pop("allow_debt"), data.pop("credit_limit"), data.pop("status")
    try:
        body = CustomerPatch(version=_digits(form.get("version")), confirmation=_confirmation(form), **data)
        c, warnings = customer_svc.update(session, user, code, body)
    except ValidationError as exc:
        return _customer_form(request, user, shown, session=session, error=_error_text(exc), ask_confirm=None, form=form, status=400)
    except BusinessError as exc:
        ask = exc.details if exc.code == "PHONE_IN_USE" else None
        return _customer_form(request, user, shown, session=session, error=exc.message, error_detail=exc.details, ask_confirm=ask, form=form, status=exc.status)
    return RedirectResponse(_saved_url(c.code, warnings), status_code=303)


# ---------- Hàng hóa ----------


def _product_form(request: Request, user: User, session: Session, p: dict, **ctx) -> HTMLResponse:
    return render(
        request, "hang_form.jinja", p=p, groups=product_svc.groups(session), can_write=has_role(user, *product_svc.WRITERS), **ctx
    )


def _product_from_form(form) -> dict:
    return {
        "name": str(form.get("name", "")),
        "group_name": str(form.get("group_name", "")),
        "unit": str(form.get("unit", "")).strip() or "cái",
        "sale_price": _digits(form.get("sale_price")),
    }


@router.get("/hang-hoa")
def product_list(
    request: Request, q: str = "", tat_ca: int = 0, phan: int = 0, user: User = Depends(web_user), session: Session = Depends(get_session)
):
    rows = product_svc.search(session, q[:200], active_only=not tat_ca)
    name = "hang_dong.jinja" if phan else "hang_ds.jinja"
    return render(request, name, rows=rows, q=q, tat_ca=tat_ca, can_write=has_role(user, *product_svc.WRITERS))


@router.get("/hang-hoa/moi")
def product_new(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    if not has_role(user, *product_svc.WRITERS):
        raise BusinessError("FORBIDDEN", "Chỉ Kho được tạo mã hàng.", status=403)
    p = {"code": product_svc.suggest_code(session), "name": "", "group_name": "", "unit": "cái", "sale_price": 0, "active": True, "version": 0}
    return _product_form(request, user, session, p, is_new=True, error=None)


@router.post("/hang-hoa/moi")
def product_create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    if not has_role(user, *product_svc.WRITERS):
        raise BusinessError("FORBIDDEN", "Chỉ Kho được tạo mã hàng.", status=403)
    data = {"code": str(form.get("code", "")).strip().upper(), **_product_from_form(form)}
    try:
        p = product_svc.create(session, user, ProductIn(**data))
    except ValidationError as exc:
        return _product_form(request, user, session, {**data, "active": True, "version": 0}, is_new=True, error=_error_text(exc), status=400)
    except BusinessError as exc:
        return _product_form(request, user, session, {**data, "active": True, "version": 0}, is_new=True, error=exc.message, status=exc.status)
    return RedirectResponse(f"/hang-hoa/{quote(p.code)}?da_luu=1", status_code=303)


@router.get("/hang-hoa/{code}")
def product_view(request: Request, code: str, da_luu: int = 0, user: User = Depends(web_user), session: Session = Depends(get_session)):
    p = product_svc.load(session, code)
    return _product_form(request, user, session, product_svc.snapshot(p), is_new=False, error=None, saved=bool(da_luu))


@router.post("/hang-hoa/{code}")
def product_update(request: Request, code: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    if not has_role(user, *product_svc.WRITERS):
        raise BusinessError("FORBIDDEN", "Chỉ Kho được sửa mã hàng.", status=403)
    data = {**_product_from_form(form), "active": form.get("active") == "1"}
    shown = {**product_svc.snapshot(product_svc.load(session, code)), **data, "version": _digits(form.get("version"))}
    try:
        p = product_svc.update(session, user, code, ProductPatch(version=_digits(form.get("version")), **data))
    except ValidationError as exc:
        return _product_form(request, user, session, shown, is_new=False, error=_error_text(exc), status=400)
    except BusinessError as exc:
        return _product_form(request, user, session, shown, is_new=False, error=exc.message, status=exc.status)
    return RedirectResponse(f"/hang-hoa/{quote(p.code)}?da_luu=1", status_code=303)


# ---------- Kho và nhà cung cấp ----------


def _catalog_form(request, row, supplier, is_new=False, session=None, **ctx):
    holder = session.get(User, int(row["holder_id"])) if session and str(row.get("holder_id") or "").isdigit() else None
    return render(request, "danh_muc_form.jinja", c=row, supplier=supplier,
                  path="/nha-cung-cap" if supplier else "/kho",
                  label="Nhà cung cấp" if supplier else "Kho", is_new=is_new, holder_name=holder.full_name if holder else "",
                  can_write=has_role(request.state.user, *catalog_svc.WRITERS), **ctx)


def _catalog_data(form, supplier):
    data = {"name": str(form.get("name", "")), "address": str(form.get("address", "")),
            "active": form.get("active") == "1"}
    if supplier:
        data.update(tax_code=str(form.get("tax_code", "")),
                    phones=[p.strip() for p in str(form.get("phones", "")).split(",") if p.strip()],
                    note=str(form.get("note", "")))
    else:
        data.update(kind=str(form.get("kind", "KHO")), holder_id=form.get("holder_id") or None, supplier_hold=form.get("supplier_hold")=="on", recovered_only=form.get("recovered_only")=="on")
    return data


@router.get("/kho/tim-tho")
def pick_warehouse_holder(request: Request, q: str = "", user: User = Depends(web_user), session: Session = Depends(get_session)):
    from ..text import fold
    catalog_svc.check_writer(user)
    rows = [u for u in session.scalars(select(User).where(User.active.is_(True), User.roles.contains(["technician"])).order_by(User.full_name)) if fold(q[:200]) in fold(u.full_name+" "+u.username+" "+str(u.id))][:20]
    return render(request, "kho_chon_tho.jinja", rows=rows)


def _register_catalog(path, model, in_schema, patch_schema):
    supplier = model is Supplier

    def listing(request: Request, q: str = "", phan: int = 0,
                user: User = Depends(web_user), session: Session = Depends(get_session)):
        return render(request, "danh_muc_dong.jinja" if phan else "danh_muc_ds.jinja",
                      rows=catalog_svc.search(session, model, q[:200]), q=q, path=path,
                      supplier=supplier, label="Nhà cung cấp" if supplier else "Kho",
                      can_write=has_role(user, *catalog_svc.WRITERS))

    def new(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
        catalog_svc.check_writer(user)
        row = dict(code="", name="", address="", active=True, version=0, tax_code="", phones=[], note="", kind="KHO", holder_id=None)
        return _catalog_form(request, row, supplier, session=session, is_new=True, error=None)

    def create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
        user = request.state.user
        catalog_svc.check_writer(user)
        data = _catalog_data(form, supplier)
        if not supplier:
            data["code"] = str(form.get("code", "")).strip().upper()
        shown = dict(code="", version=0, **data) if supplier else dict(version=0, **data)
        try:
            row = catalog_svc.create(session, user, model, in_schema(**data))
        except ValidationError as exc:
            return _catalog_form(request, shown, supplier, session=session, is_new=True, error=("Mã kho phải có dạng K58 hoặc K145." if any(e["loc"] == ("code",) for e in exc.errors()) else _error_text(exc)), status=400)
        except BusinessError as exc:
            return _catalog_form(request, shown, supplier, session=session, is_new=True, error=exc.message, status=exc.status)
        return RedirectResponse(f"{path}/{quote(row.code)}?da_luu=1", status_code=303)

    def view(request: Request, code: str, da_luu: int = 0,
             user: User = Depends(web_user), session: Session = Depends(get_session)):
        row = catalog_svc.snapshot(catalog_svc.load(session, model, code))
        return _catalog_form(request, row, supplier, session=session, error=None, saved=bool(da_luu))

    def update(request: Request, code: str, form=Depends(checked_form), session: Session = Depends(get_session)):
        user = request.state.user
        catalog_svc.check_writer(user)
        data = _catalog_data(form, supplier)
        shown = {**catalog_svc.snapshot(catalog_svc.load(session, model, code)), **data,
                 "version": _digits(form.get("version"))}
        try:
            row = catalog_svc.update(session, user, model, code,
                                     patch_schema(version=form.get("version"), **data))
        except ValidationError as exc:
            return _catalog_form(request, shown, supplier, session=session, error=_error_text(exc), status=400)
        except BusinessError as exc:
            return _catalog_form(request, shown, supplier, session=session, error=exc.message, status=exc.status)
        return RedirectResponse(f"{path}/{quote(row.code)}?da_luu=1", status_code=303)

    router.add_api_route(path, listing, methods=["GET"])
    router.add_api_route(path + "/moi", new, methods=["GET"])
    router.add_api_route(path + "/moi", create, methods=["POST"])
    router.add_api_route(path + "/{code}", view, methods=["GET"])
    router.add_api_route(path + "/{code}", update, methods=["POST"])


_register_catalog("/kho", Warehouse, WarehouseIn, WarehousePatch)
_register_catalog("/nha-cung-cap", Supplier, SupplierIn, SupplierPatch)

# Nạp sau các helper dùng chung để màn hình Kho dùng cùng CSRF và render.
from .stock import router as stock_router

router.include_router(stock_router)

from .opening import router as opening_router

router.include_router(opening_router)

from .devices import router as device_router

router.include_router(device_router)

from .charges import router as charges_router

router.include_router(charges_router)

# Báo thu dịch vụ phải đứng trước route tổng quát /dich-vu/{id}/{action}.
from .receipts import router as receipts_router

router.include_router(receipts_router)

from .parts import router as parts_router

router.include_router(parts_router)

from .custody import router as custody_router

router.include_router(custody_router)

from .services import router as services_router

router.include_router(services_router)

from .dispatch import router as dispatch_router

router.include_router(dispatch_router)

from .quotes import router as quotes_router

router.include_router(quotes_router)

from .orders import router as orders_router

from .sales_returns import router as sales_returns_router
router.include_router(sales_returns_router)

router.include_router(orders_router)

from .sales_reports import router as sales_reports_router

router.include_router(sales_reports_router)

from .finance import router as finance_router

router.include_router(finance_router)

from .cash import router as cash_router

router.include_router(cash_router)

from .closings import router as closings_router

router.include_router(closings_router)

from .finance_reports import router as finance_reports_router

router.include_router(finance_reports_router)

from .purchases import router as purchases_router

router.include_router(purchases_router)

from .supplier_debts import router as supplier_debts_router

router.include_router(supplier_debts_router)

from .purchase_returns import router as purchase_returns_router

router.include_router(purchase_returns_router)

from .purchase_reports import router as purchase_reports_router

router.include_router(purchase_reports_router)

from .dashboard import router as dashboard_router

router.include_router(dashboard_router)

from .daily_digest import router as daily_digest_router

router.include_router(daily_digest_router)

from .technician_deliveries import router as technician_deliveries_router
router.include_router(technician_deliveries_router)

from .recovered_parts import router as recovered_parts_router
router.include_router(recovered_parts_router)

from .technician import router as technician_router
router.include_router(technician_router)

from .technician_returns import router as technician_returns_router
router.include_router(technician_returns_router)

from .users import router as users_router
router.include_router(users_router)

from .pending_items import router as pending_items_router
router.include_router(pending_items_router)

from .ui_guide import router as ui_guide_router
router.include_router(ui_guide_router)

from .pending_reports import router as pending_reports_router
router.include_router(pending_reports_router)

from .employees import router as employees_router
router.include_router(employees_router)

from .attendance import router as attendance_router
router.include_router(attendance_router)

from .kpi import router as kpi_router
router.include_router(kpi_router)
