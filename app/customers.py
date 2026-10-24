"""Nghiệp vụ Danh bạ khách: tạo, sửa, tìm, chống trùng. Quy tắc giữ đúng như app Danh bạ khách trên Apps Script."""
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from . import audit
from .config import get_settings
from .errors import BusinessError
from .models import Customer, CustomerContact, CustomerPhone, User
from .schemas import Confirmation, CustomerCreate, CustomerOut, CustomerPatch, Warning
from .security import has_role
from .text import digits_query, fold, phones, tax_code

CREDIT_ROLES = ("finance",)  # cộng thêm giám đốc (has_role tự cho phép)


def to_out(c: Customer) -> CustomerOut:
    return CustomerOut(
        code=c.code,
        kind=c.kind,
        name=c.name,
        tax_code=c.tax_code,
        phones=[p.phone for p in c.phones],
        email=c.email,
        address=c.address,
        note=c.note,
        contacts=[{"name": x.name, "phones": list(x.phones), "note": x.note} for x in c.contacts],
        allow_debt=c.allow_debt,
        credit_limit=c.credit_limit,
        status=c.status,
        old_codes=list(c.old_codes or []),
        is_test=c.is_test,
        version=c.version,
    )


def snapshot(c: Customer) -> dict:
    return to_out(c).model_dump()


def _search_text(c: Customer) -> str:
    parts = [c.code, c.name, c.tax_code, c.email, c.address, " ".join(c.old_codes or [])]
    parts += [p.phone for p in c.phones]
    for x in c.contacts:
        parts += [x.name, " ".join(x.phones)]
    return fold(" ".join(parts))


def _all_phones(own: list[str], contacts) -> list[str]:
    out = set(own)
    for x in contacts:
        out.update(x.phones)
    return sorted(out)


def like_pattern(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


def _next_code(session: Session) -> str:
    n = session.execute(text("select nextval('ktd_seq')")).scalar_one()
    return f"KTD-{n:06d}"


def _check_conflicts(
    session: Session, kind: str, tax: str, all_phones: list[str], name: str | None, except_code: str | None, confirmation: Confirmation | None
) -> list[Warning]:
    warnings: list[Warning] = []
    active = Customer.status != "DA_GOP"
    not_self = Customer.code != except_code if except_code else True

    # Khóa theo từng số điện thoại để hai người tạo cùng lúc không lọt trùng.
    for p in all_phones:
        session.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": "phone:" + p})
    if tax:
        session.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": "tax:" + tax})
        dup = session.scalars(select(Customer).where(active, not_self, Customer.tax_code == tax)).first()
        if dup:
            raise BusinessError("DUPLICATE_TAX", "MST đã thuộc khách khác.", {"existing": dup.code, "name": dup.name}, status=409)

    if all_phones:
        owners = session.scalars(
            select(Customer)
            .join(CustomerPhone)
            .where(active, not_self, CustomerPhone.phone.in_(all_phones))
            .distinct()
        ).all()
        for owner in owners:
            if owner.kind == "CA_NHAN" and kind == "CA_NHAN":
                raise BusinessError(
                    "DUPLICATE_PHONE", "Số điện thoại đã thuộc khách cá nhân khác.", {"existing": owner.code, "name": owner.name}, status=409
                )
            if confirmation is None:
                raise BusinessError(
                    "PHONE_IN_USE",
                    "Số điện thoại đã có ở khách khác. Xác nhận tạo riêng và ghi lý do nếu đúng là khách khác.",
                    {"existing": owner.code, "name": owner.name, "choices": ["CREATE_SEPARATE"]},
                    status=409,
                )
            warnings.append(Warning(code="PHONE_SHARED_CONFIRMED", customer=owner.code))

    if name is not None:
        folded = fold(name)
        for other in session.scalars(select(Customer).where(active, not_self, Customer.search_text.like(like_pattern(folded))).limit(50)):
            if fold(other.name) == folded:
                warnings.append(Warning(code="SIMILAR_NAME", customer=other.code))
    return warnings


def _set_children(c: Customer, own_phones: list[str], contacts) -> None:
    c.phones = [CustomerPhone(phone=p, is_contact=p not in own_phones) for p in _all_phones(own_phones, contacts)]
    c.contacts = [CustomerContact(name=x.name.strip(), phones=phones(x.phones), note=x.note.strip()) for x in contacts]


def create(session: Session, user: User, body: CustomerCreate) -> tuple[Customer, list[Warning]]:
    data = body.customer
    if (data.allow_debt or data.credit_limit) and not has_role(user, *CREDIT_ROLES):
        raise BusinessError("FIELD_FORBIDDEN", "Chỉ kế toán hoặc giám đốc được đặt công nợ.", status=403)
    own = phones(data.phones)
    contacts = [x.model_copy(update={"phones": phones(x.phones)}) for x in data.contacts]
    tax = tax_code(data.tax_code)
    if data.kind == "CONG_TY" and not tax and not own:
        raise BusinessError("MISSING_IDENTITY", "Khách công ty cần MST hoặc số điện thoại.")
    if data.kind == "CA_NHAN" and not own:
        raise BusinessError("MISSING_PHONE", "Khách cá nhân cần ít nhất một số điện thoại.")
    warnings = _check_conflicts(session, data.kind, tax, _all_phones(own, contacts), data.name, None, body.confirmation)

    c = Customer(
        code=_next_code(session),
        kind=data.kind,
        name=data.name.strip(),
        tax_code=tax,
        email=data.email.strip(),
        address=data.address.strip(),
        note=data.note.strip(),
        allow_debt=data.allow_debt,
        credit_limit=data.credit_limit,
        status="DANG_DUNG",
        old_codes=[],
        is_test=data.is_test,
        created_by=user.id,
    )
    _set_children(c, own, contacts)
    c.search_text = _search_text(c)
    session.add(c)
    reason = body.confirmation.reason if body.confirmation else ""
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise BusinessError("DUPLICATE_TAX", "MST đã thuộc khách khác.", status=409)
    audit.record(session, user, "customer.create", "customer", c.code, None, snapshot(c), reason)
    session.commit()
    return c, warnings


def update(session: Session, user: User, code: str, body: CustomerPatch) -> tuple[Customer, list[Warning]]:
    c = load(session, code, resolve_merged=False)
    if c.status == "DA_GOP":
        raise BusinessError("MERGED", "Khách đã gộp, hãy sửa khách đích.", {"merged_into": c.merged_into}, status=409)
    if body.version != c.version:
        raise BusinessError("VERSION_CONFLICT", "Người khác vừa sửa khách này, hãy tải lại.", {"current": c.version}, status=409)
    changes = body.model_dump(exclude_unset=True, exclude={"version", "confirmation", "reason"})
    if {"allow_debt", "credit_limit", "status"} & changes.keys() and not has_role(user, *CREDIT_ROLES):
        raise BusinessError("FIELD_FORBIDDEN", "Chỉ kế toán hoặc giám đốc được sửa công nợ hoặc ngừng khách.", status=403)
    before = snapshot(c)

    kind = changes.get("kind", c.kind)
    tax = tax_code(changes["tax_code"]) if "tax_code" in changes else c.tax_code
    own = phones(changes["phones"]) if "phones" in changes else [p.phone for p in c.phones if not p.is_contact]
    if body.contacts is not None:
        contacts = [x.model_copy(update={"phones": phones(x.phones)}) for x in body.contacts]
    else:
        contacts = list(c.contacts)
    old_phones = {p.phone for p in c.phones}
    # Đổi loại khách thì kiểm lại mọi số, vì quy tắc trùng khác nhau giữa cá nhân và công ty.
    new_phones = [p for p in _all_phones(own, contacts) if kind != c.kind or p not in old_phones]
    if kind == "CA_NHAN" and not own:
        raise BusinessError("MISSING_PHONE", "Khách cá nhân cần ít nhất một số điện thoại.")
    warnings = _check_conflicts(
        session, kind, tax if tax != c.tax_code else "", new_phones, changes.get("name"), c.code, body.confirmation
    )

    for field in ("kind", "email", "address", "note", "allow_debt", "credit_limit", "status"):
        if field in changes:
            v = changes[field]
            setattr(c, field, v.strip() if isinstance(v, str) else v)
    if "name" in changes:
        c.name = changes["name"].strip()
    c.tax_code = tax
    if "phones" in changes or body.contacts is not None:
        if body.contacts is not None:
            _set_children(c, own, contacts)
        else:
            c.phones = [CustomerPhone(phone=p, is_contact=p not in own) for p in _all_phones(own, c.contacts)]
    c.search_text = _search_text(c)
    try:
        session.flush()
    except StaleDataError:
        session.rollback()
        raise BusinessError("VERSION_CONFLICT", "Người khác vừa sửa khách này, hãy tải lại.", status=409)
    except IntegrityError:
        session.rollback()
        raise BusinessError("DUPLICATE_TAX", "MST đã thuộc khách khác.", status=409)
    reason = body.reason or (body.confirmation.reason if body.confirmation else "")
    audit.record(session, user, "customer.update", "customer", c.code, before, snapshot(c), reason)
    session.commit()
    return c, warnings


def load(session: Session, code: str, resolve_merged: bool = True) -> Customer:
    opts = (selectinload(Customer.phones), selectinload(Customer.contacts))
    c = session.get(Customer, code, options=opts)
    if c is None:
        c = session.scalars(select(Customer).options(*opts).where(Customer.old_codes.any(code))).first()
    if c is None:
        raise BusinessError("NOT_FOUND", "Không tìm thấy khách.", status=404)
    seen = set()
    while resolve_merged and c.status == "DA_GOP":
        if c.code in seen:
            raise BusinessError("MERGE_CYCLE", "Dữ liệu gộp khách bị vòng lặp.", status=500)
        seen.add(c.code)
        c = session.get(Customer, c.merged_into, options=opts)
    return c


def search(session: Session, q: str, include_test: bool = True) -> list[Customer]:
    limit = get_settings().search_limit
    stmt = select(Customer).options(selectinload(Customer.phones), selectinload(Customer.contacts)).where(Customer.status != "DA_GOP")
    if not include_test:
        stmt = stmt.where(Customer.is_test.is_(False))
    stmt = stmt.where(*matches(q))
    stmt = stmt.order_by(Customer.updated_at.desc()).limit(limit)
    return list(session.scalars(stmt).all())


def matches(q: str) -> list:
    """Điều kiện ô tìm khách (tên/mã/MST/SĐT, không dấu); dùng chung cho ô chọn khách và danh sách."""
    needle = fold(q)
    digits = digits_query(q)
    if not needle:
        return []
    conds = [Customer.search_text.like(like_pattern(needle))]
    if digits and len(digits) >= 3 and not any(ch.isalpha() for ch in needle):
        conds.append(Customer.code.in_(select(CustomerPhone.customer_code).where(CustomerPhone.phone.like("%" + digits + "%"))))
        conds.append(Customer.tax_code.like("%" + digits + "%"))
    return [or_(*conds)]


def duplicates(session: Session, phone_text: str = "", tax_text: str = "", except_code: str = "", limit: int = 5) -> list[dict]:
    """Báo trùng sớm khi đang gõ (chỉ đọc). Cùng chuẩn hóa và cùng phạm vi khách như _check_conflicts lúc lưu.

    Số chưa hợp lệ thì bỏ qua, không báo lỗi: người dùng còn đang gõ.
    """
    found: dict[str, dict] = {}
    active = Customer.status != "DA_GOP"
    not_self = Customer.code != except_code if except_code else True
    try:
        tax = tax_code(tax_text)
    except BusinessError:
        tax = ""
    if tax:
        for c in session.scalars(select(Customer).where(active, not_self, Customer.tax_code == tax).limit(limit)):
            found[c.code] = {"customer": c, "by": "MST"}
    numbers = []
    for raw in str(phone_text or "").replace(";", ",").replace("/", ",").split(","):
        try:
            numbers += phones(raw)
        except BusinessError:
            continue
    if numbers:
        stmt = (
            select(Customer)
            .join(CustomerPhone)
            .where(active, not_self, CustomerPhone.phone.in_(numbers))
            .distinct()
            .limit(limit)
        )
        for c in session.scalars(stmt):
            found.setdefault(c.code, {"customer": c, "by": "SĐT"})
    return list(found.values())[:limit]
