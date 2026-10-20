"""Danh mục kho và nhà cung cấp: dùng chung cho API và web."""
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from . import audit
from .config import get_settings
from .customers import like_pattern
from .errors import BusinessError
from .models import StockBalance, Supplier, User, Warehouse
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
        if constraint == "uq_warehouses_active_holder":
            raise BusinessError("DUPLICATE_HOLDER", "Thợ đã có một kho đang sử dụng.", status=409)
        raise
    return row


def create(session: Session, user, model, body):
    check_writer(user)
    data = _normalize(model, body.model_dump())
    if model is Supplier:
        n = session.execute(text("select nextval('ncc_seq')")).scalar_one()
        data["code"] = f"NCC-{n:04d}"
    row = _insert(session,model,data)
    return _save(session, user, row, None)


def _insert(session,model,data):
    row=model(**data)
    if model is Warehouse:_validate_warehouse(session,row)
    if model is Supplier:row.search_text=_search_text(row)
    session.add(row)
    return row


def technician_warehouse_in_tx(session,admin,holder):
    """Account lifecycle only; caller owns the user transaction. No inventory permissions granted."""
    if 'admin' not in admin.roles:
        raise BusinessError('FORBIDDEN','Chỉ quản trị được tạo kho theo tài khoản.',status=403)
    rows=list(session.scalars(select(Warehouse).where(Warehouse.kind=='THO',Warehouse.holder_id==holder.id).order_by(Warehouse.active.desc(),Warehouse.code).with_for_update()))
    if rows:
        row=rows[0]
        if not row.active:
            before=snapshot(row);row.active=True;row.version+=1
            _validate_warehouse(session,row);session.flush()
            audit.record(session,admin,'warehouse.update','warehouse',row.code,before,snapshot(row))
        return row
    # Existing catalog validation also checks holder role and the unique active warehouse.
    from .schemas import WarehouseIn
    stem=500000000+holder.id
    code=f'K{stem}'
    while session.get(Warehouse,code) is not None:
        stem+=1;code=f'K{stem}'
    body=WarehouseIn(code=code,name='Kho thợ '+holder.full_name,kind='THO',holder_id=holder.id)
    row=_insert(session,Warehouse,body.model_dump())
    session.flush();audit.record(session,admin,'warehouse.create','warehouse',row.code,None,snapshot(row))
    return row


def update(session: Session, user, model, code, body):
    check_writer(user)
    row = session.scalar(select(model).where(model.code == code).with_for_update().execution_options(populate_existing=True)) if model is Warehouse else load(session, model, code)
    if row is None:
        raise BusinessError('NOT_FOUND','Không tìm thấy kho.',status=404)
    if row.version != body.version:
        raise BusinessError("VERSION_CONFLICT", "Người khác vừa sửa, hãy tải lại.", {"current": row.version}, status=409)
    before = snapshot(row)
    changes = _normalize(model, body.model_dump(exclude_unset=True, exclude={"version"}))
    if model is Warehouse:
        candidate = Warehouse(**{**before, **changes})
        _validate_warehouse(session, candidate)
        if candidate.kind != row.kind:
            from .models import PendingItem
            if session.scalar(select(PendingItem.id).where(PendingItem.warehouse_code==code).limit(1)):
                raise BusinessError('WAREHOUSE_HAS_PENDING','Kho có lịch sử Chờ xử lý, không đổi được loại kho.',status=409)
        ownership_changed = candidate.kind != row.kind or candidate.holder_id != row.holder_id
        if ownership_changed or (row.kind == 'THO' and not candidate.active):
            if session.scalar(select(StockBalance.product_code).where(StockBalance.warehouse_code == code, StockBalance.qty != 0).limit(1)):
                raise BusinessError('WAREHOUSE_NOT_EMPTY','Kho còn hàng, hãy chuyển hết hàng trước khi ngừng dùng hoặc đổi người giữ.',status=409)
    for key, value in changes.items():
        setattr(row, key, value)
    if model is Supplier:
        row.search_text = _search_text(row)
    # Kể cả lần lưu không đổi dữ liệu cũng là một phiên bản được ghi nhật ký.
    row.version += 1
    return _save(session, user, row, before)


def _validate_warehouse(session, row):
    if row.kind in ('KHO','LOI') and row.holder_id is not None:
        raise BusinessError('BAD_HOLDER','Kho công ty không có người giữ riêng.')
    if row.kind == 'THO':
        holder = session.scalar(select(User).where(User.id == row.holder_id).with_for_update(read=True)) if row.holder_id else None
        if holder is None or 'technician' not in holder.roles:
            raise BusinessError('BAD_HOLDER','Kho của thợ phải chọn một kỹ thuật viên.')
        if row.active and session.scalar(select(Warehouse.code).where(Warehouse.kind == 'THO', Warehouse.active.is_(True),
                    Warehouse.holder_id == row.holder_id, Warehouse.code != row.code).limit(1)):
            raise BusinessError('DUPLICATE_HOLDER','Thợ đã có một kho đang sử dụng.',status=409)
