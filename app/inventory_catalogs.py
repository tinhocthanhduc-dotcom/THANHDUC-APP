"""Danh mục kho và nhà cung cấp: dùng chung cho API và web."""
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from . import audit
from .config import get_settings
from .customers import like_pattern
from .errors import BusinessError
from .models import Supplier, Warehouse
from .schemas import SupplierOut, WarehouseOut
from .security import has_role
from .text import fold, phones, tax_code

WRITERS = ("warehouse",)


def check_writer(user):
    if not has_role(user, *WRITERS):
        raise BusinessError("FORBIDDEN", "Chỉ Kho hoặc giám đốc được sửa danh mục này.", status=403)


def snapshot(row):
    schema = SupplierOut if isinstance(row, Supplier) else WarehouseOut
    return schema.model_validate(row).model_dump()


def load(session: Session, model, code):
    row = session.get(model, code)
    if row is None:
        raise BusinessError("NOT_FOUND", "Không tìm thấy kho hoặc nhà cung cấp.", status=404)
    return row


def search(session: Session, model, q=""):
    stmt = select(model)
    if model is Supplier and fold(q):
        stmt = stmt.where(Supplier.search_text.like(like_pattern(fold(q))))
    stmt = stmt.order_by(model.code)
    if model is Supplier:
        stmt = stmt.limit(get_settings().search_limit)
    return list(session.scalars(stmt))


def _normalize(model, data):
    if model is Supplier:
        if "tax_code" in data:
            data["tax_code"] = tax_code(data["tax_code"])
        if "phones" in data:
            data["phones"] = phones(data["phones"])
    return data


def _search_text(row):
    return fold(" ".join([row.code, row.name, row.tax_code, *row.phones, row.address]))


def _save(session, user, row, before):
    entity = "supplier" if isinstance(row, Supplier) else "warehouse"
    try:
        session.flush()
        audit.record(session, user, entity + (".create" if before is None else ".update"),
                     entity, row.code, before, snapshot(row))
        session.commit()
    except StaleDataError:
        session.rollback()
        raise BusinessError("VERSION_CONFLICT", "Người khác vừa sửa, hãy tải lại.", status=409)
    except IntegrityError as exc:
        session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_suppliers_tax":
            raise BusinessError("DUPLICATE_TAX", "MST đã thuộc nhà cung cấp khác.", status=409)
        if constraint == "warehouses_pkey":
            raise BusinessError("DUPLICATE_CODE", "Mã kho đã có.", status=409)
        raise
    return row


def create(session: Session, user, model, body):
    check_writer(user)
    data = _normalize(model, body.model_dump())
    if model is Supplier:
        n = session.execute(text("select nextval('ncc_seq')")).scalar_one()
        data["code"] = f"NCC-{n:04d}"
    row = model(**data)
    if model is Supplier:
        row.search_text = _search_text(row)
    session.add(row)
    return _save(session, user, row, None)


def update(session: Session, user, model, code, body):
    check_writer(user)
    row = load(session, model, code)
    if row.version != body.version:
        raise BusinessError("VERSION_CONFLICT", "Người khác vừa sửa, hãy tải lại.", {"current": row.version}, status=409)
    before = snapshot(row)
    changes = _normalize(model, body.model_dump(exclude_unset=True, exclude={"version"}))
    for key, value in changes.items():
        setattr(row, key, value)
    if model is Supplier:
        row.search_text = _search_text(row)
    # Kể cả lần lưu không đổi dữ liệu cũng là một phiên bản được ghi nhật ký.
    row.version += 1
    return _save(session, user, row, before)
