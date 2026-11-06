"""Tạo mới nhanh trong cửa sổ (TUẤN duyệt 06/11/2026).

Nút "+ Tạo mới" ở ô tìm khách, mã hàng, nhà cung cấp mở một cửa sổ ngay trên màn đang soạn.
GET trả phần form (không có khung trang), POST lưu bằng đúng service như form đầy đủ:
lưu được thì trả JSON {code, name, ...} để tao-nhanh.js chọn vào phiếu; lỗi thì trả lại phần form kèm câu báo.
Không thêm quyền mới: ai tạo được ở form đầy đủ thì mới thấy nút.
"""
import re
from urllib.parse import urlencode

import jinja2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .. import customers as customer_svc
from .. import form_keys
from .. import inventory_catalogs as catalog_svc
from .. import products as product_svc
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, Product, Supplier, User
from ..routers.customers import WRITERS as CUSTOMER_WRITERS
from ..schemas import CustomerCreate, CustomerIn, ProductIn, SupplierIn
from ..security import has_role
from . import (_RENDER_USER, _confirmation, _customer_from_form, _customer_where, _digits, _error_text, _prefill_customer,
               checked_form, render, templates, web_user)

router = APIRouter()

TITLES = {"khach": "Thêm khách", "hang": "Thêm mã hàng", "ncc": "Thêm nhà cung cấp"}
WARRANTY_CHOICES = (0, 1, 3, 6, 12, 24)


def kinds(user) -> list[str]:
    """Loại được tạo nhanh, theo đúng quyền của form đầy đủ."""
    if not user:
        return []
    out = []
    if has_role(user, *CUSTOMER_WRITERS):
        out.append("khach")
    if has_role(user, *product_svc.WRITERS):
        out.append("hang")
    if has_role(user, *catalog_svc.WRITERS):
        out.append("ncc")
    return out


@jinja2.pass_context
def _quick_kinds(ctx) -> str:
    return " ".join(kinds(ctx.get("user") or _RENDER_USER.get()))


templates.env.globals["quick_create_kinds"] = _quick_kinds


def _check(user, loai: str) -> None:
    if loai not in TITLES:
        raise BusinessError("NOT_FOUND", "Không có loại này.", status=404)
    if loai not in kinds(user):
        msg = {"khach": "Tài khoản không có quyền tạo khách.", "hang": "Chỉ Kho được tạo mã hàng.",
               "ncc": "Chỉ Kho hoặc giám đốc được tạo nhà cung cấp."}[loai]
        raise BusinessError("FORBIDDEN", msg, status=403)


def _prefill_supplier(q: str) -> dict:
    """Như ô tìm khách: giống SĐT vào ô SĐT, giống MST vào ô MST, còn lại là tên."""
    pre = _prefill_customer(q)
    if pre.get("phones"):
        return {"phones": pre["phones"][0]}
    return pre


def _blank(session: Session, loai: str, q: str) -> dict:
    if loai == "khach":
        d = {"kind": "CA_NHAN", "name": "", "phones": "", "tax_code": "", "address": ""}
        pre = _prefill_customer(q)
        if pre.get("phones"):
            pre["phones"] = pre["phones"][0]
        elif pre.get("name"):
            # "Hùng 0909 123 456": tên vào ô Tên, số vào ô SĐT.
            m = re.search(r"(?:\+?84|0)[0-9][0-9 .]{7,12}[0-9]", pre["name"])
            if m and 9 <= len(re.sub(r"\D", "", m.group(0))) <= 11:
                rest = (pre["name"][:m.start()] + " " + pre["name"][m.end():]).strip(" ,-")
                pre = {"name": re.sub(r"\s+", " ", rest), "phones": m.group(0).strip()}
        return {**d, **pre}
    if loai == "hang":
        return {"code": product_svc.suggest_code(session), "name": q.strip()[:300], "group_name": "", "unit": "cái",
                "sale_price": 0, "warranty_months": 0, "track_serial": False}
    return {"name": "", "tax_code": "", "phones": "", "address": "", **_prefill_supplier(q)}


def _form(request: Request, session: Session, loai: str, d: dict, status: int = 200, **ctx):
    return render(request, "tao_nhanh.jinja", loai=loai, title=TITLES[loai], d=d, status=status,
                  groups=product_svc.groups(session) if loai == "hang" else [],
                  warranty_choices=WARRANTY_CHOICES, **ctx)


@router.get("/tao-nhanh/{loai}")
def quick_new(request: Request, loai: str, q: str = "", user: User = Depends(web_user), session: Session = Depends(get_session)):
    _check(user, loai)
    return _form(request, session, loai, _blank(session, loai, q))


@router.post("/tao-nhanh/{loai}")
def quick_create(request: Request, loai: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    _check(user, loai)
    if loai == "khach":
        return _create_customer(request, session, user, form)
    if loai == "hang":
        return _create_product(request, session, user, form)
    return _create_supplier(request, session, user, form)


def _create_customer(request, session, user, form):
    data = _customer_from_form(form)
    data["contacts"] = []
    shown = {"kind": data["kind"], "name": data["name"], "phones": str(form.get("phones", "")), "tax_code": data["tax_code"],
             "address": data["address"]}
    data.pop("status"), data.pop("allow_debt"), data.pop("credit_limit")
    try:
        c, _ = customer_svc.create(session, user, CustomerCreate(customer=CustomerIn(**data), confirmation=_confirmation(form)))
    except ValidationError as exc:
        return _form(request, session, "khach", shown, status=400, error=_error_text(exc), field_errors=_customer_where(exc))
    except BusinessError as exc:
        ask = exc.details if exc.code == "PHONE_IN_USE" else None
        return _form(request, session, "khach", shown, status=exc.status, error=exc.message, error_detail=exc.details,
                     ask_confirm=ask, field_errors=_customer_where(exc))
    return _saved(request, "khach", c)


def _create_product(request, session, user, form):
    director = "director" in user.roles
    warranty = str(form.get("warranty_months") or "0")
    data = {
        "code": str(form.get("code", "")).strip().upper(),
        "name": str(form.get("name", "")),
        "group_name": str(form.get("group_name", "")),
        "unit": str(form.get("unit", "")).strip() or "cái",
        "sale_price": _digits(form.get("sale_price")),
    }
    if director:
        data["warranty_months"] = int(warranty) if warranty.isdigit() else 0
        data["track_serial"] = form.get("track_serial") == "1"
    shown = {"warranty_months": 0, "track_serial": False, **data}
    try:
        p = product_svc.create(session, user, ProductIn(**data))
    except ValidationError as exc:
        return _form(request, session, "hang", shown, status=400, error=_error_text(exc))
    except BusinessError as exc:
        return _form(request, session, "hang", shown, status=exc.status, error=exc.message)
    return _saved(request, "hang", p)


def _create_supplier(request, session, user, form):
    data = {
        "name": str(form.get("name", "")),
        "tax_code": str(form.get("tax_code", "")),
        "phones": [x.strip() for x in str(form.get("phones", "")).split(",") if x.strip()],
        "address": str(form.get("address", "")),
        "note": "",
        "active": True,
    }
    shown = {**data, "phones": str(form.get("phones", ""))}
    try:
        row = catalog_svc.create(session, user, Supplier, SupplierIn(**data))
    except ValidationError as exc:
        return _form(request, session, "ncc", shown, status=400, error=_error_text(exc))
    except BusinessError as exc:
        return _form(request, session, "ncc", shown, status=exc.status, error=exc.message)
    return _saved(request, "ncc", row)


def _record(loai: str, row) -> dict:
    out = {"code": row.code, "name": row.name, "label": f"{row.code} · {row.name}"}
    if loai == "hang":
        out.update(unit=row.unit, sale_price=row.sale_price, warranty_months=row.warranty_months, track_serial=row.track_serial)
    return out


def _saved(request: Request, loai: str, row) -> JSONResponse:
    """Chốt khóa form như một lần lưu xong: gửi lại cùng khóa (mạng chập chờn) chỉ đọc lại bản đã tạo, không tạo trùng."""
    key = getattr(request.state, "form_submission_key", None)
    url = f"/tao-nhanh/{loai}/da-tao?" + urlencode({"code": row.code})
    if key is not None:
        form_keys.finish(key, url)
    return JSONResponse(_record(loai, row))


@router.get("/tao-nhanh/{loai}/da-tao")
def quick_saved(loai: str, code: str = "", user: User = Depends(web_user), session: Session = Depends(get_session)):
    _check(user, loai)
    model = {"khach": Customer, "hang": Product, "ncc": Supplier}[loai]
    row = session.get(model, code[:50])
    if row is None:
        raise BusinessError("NOT_FOUND", "Không tìm thấy bản vừa tạo.", status=404)
    return JSONResponse(_record(loai, row))
