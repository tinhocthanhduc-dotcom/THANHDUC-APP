"""Phiếu kho nháp. Cấp số, lưu dòng và audit trong cùng transaction."""
from contextlib import contextmanager
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from . import audit
from .doc_numbers import next_number
from .errors import BusinessError
from .inventory_catalogs import check_writer
from .models import Customer, Product, StockDoc, StockDocLine, Supplier, User, Warehouse
from .schemas import StockDocCancel, StockDocIn, StockDocOut, StockDocPatch

WRITERS = ("warehouse",)
HEADER_FIELDS = ("kind", "warehouse_code", "to_warehouse_code", "doc_date", "supplier_code", "customer_code", "reason", "note")


def snapshot(doc: StockDoc) -> dict:
    # JSONB cần ngày, thời gian và Decimal ở dạng JSON tương thích.
    return StockDocOut.model_validate(doc).model_dump(mode="json")


@contextmanager
def _transaction(session: Session):
    try:
        yield
        session.commit()
    except StaleDataError:
        session.rollback()
        raise BusinessError("VERSION_CONFLICT", "Người khác vừa sửa phiếu, hãy tải lại.", status=409)
    except Exception:
        session.rollback()
        raise


def load(session: Session, doc_id: int) -> StockDoc:
    doc = session.get(StockDoc, doc_id, options=[selectinload(StockDoc.lines)])
    if doc is None:
        raise BusinessError("NOT_FOUND", "Không tìm thấy phiếu kho.", status=404)
    return doc


def search(session: Session, kind=None, warehouse=None, status=None, from_date: date | None = None, to_date: date | None = None):
    stmt = select(StockDoc).options(selectinload(StockDoc.lines))
    for column, value in ((StockDoc.kind, kind), (StockDoc.warehouse_code, warehouse), (StockDoc.status, status)):
        if value is not None:
            stmt = stmt.where(column == value)
    if from_date is not None:
        stmt = stmt.where(StockDoc.doc_date >= from_date)
    if to_date is not None:
        stmt = stmt.where(StockDoc.doc_date <= to_date)
    return list(session.scalars(stmt.order_by(StockDoc.doc_date.desc(), StockDoc.id.desc())))


def _require_draft(doc: StockDoc, version: int | None):
    if doc.status == "DA_GHI":
        raise BusinessError("DOC_POSTED", "Phiếu đã ghi sổ, không được sửa hoặc hủy nháp.", status=409)
    if doc.status == "DA_HUY":
        raise BusinessError("DOC_VOID", "Phiếu đã hủy, không được sửa hoặc hủy lại.", status=409)
    if version is not None and doc.version != version:
        raise BusinessError("VERSION_CONFLICT", "Người khác vừa sửa phiếu, hãy tải lại.", {"current": doc.version}, status=409)


def _active(session: Session, model, code):
    # Giữ trạng thái danh mục tới lúc commit, tránh bị ngừng giữa kiểm tra và lưu.
    return session.scalars(select(model).where(model.code == code, model.active.is_(True)).with_for_update(read=True)).first()


def _validate(session: Session, body: StockDocIn):
    if body.kind == "CK":
        if not body.to_warehouse_code or body.to_warehouse_code == body.warehouse_code:
            raise BusinessError("BAD_TRANSFER", "Phiếu chuyển kho cần kho đích khác kho nguồn.")
    elif body.to_warehouse_code is not None:
        raise BusinessError("BAD_TRANSFER", "Chỉ phiếu chuyển kho được có kho đích.")
    # Khóa kho theo mã cố định để các phiếu chuyển ngược chiều không khóa chéo nhau.
    for code in sorted({body.warehouse_code, body.to_warehouse_code} - {None}):
        if not _active(session, Warehouse, code):
            raise BusinessError("BAD_WAREHOUSE", "Kho không tồn tại hoặc đã ngừng sử dụng.", {"code": code})
    if body.kind == "PN" and not body.supplier_code:
        raise BusinessError("BAD_SUPPLIER", "Phiếu nhập cần nhà cung cấp đang sử dụng.")
    if body.supplier_code is not None and not _active(session, Supplier, body.supplier_code):
        raise BusinessError("BAD_SUPPLIER", "Nhà cung cấp không tồn tại hoặc đã ngừng sử dụng.")
    if body.customer_code is not None:
        if body.kind != "PX" or session.get(Customer, body.customer_code) is None:
            raise BusinessError("BAD_CUSTOMER", "Khách hàng phải tồn tại và chỉ dùng cho phiếu xuất.")
    codes = sorted({line.product_code for line in body.lines})
    valid = set(session.scalars(select(Product.code).where(Product.code.in_(codes), Product.active.is_(True))
                                .order_by(Product.code).with_for_update(read=True)))
    bad = sorted(set(codes) - valid)
    if bad:
        raise BusinessError("BAD_PRODUCT", "Mã hàng không tồn tại hoặc đã ngừng bán.", {"invalid_codes": bad})
    for i, line in enumerate(body.lines, 1):
        if body.kind != "KK" and line.qty <= 0:
            raise BusinessError("BAD_QTY", "Số lượng phải lớn hơn 0, trừ phiếu kiểm kê được bằng 0.", {"line_no": i})
        if body.kind == "PN" and line.unit_cost is None:
            raise BusinessError("BAD_UNIT_COST", "Phiếu nhập bắt buộc có đơn giá nguyên đồng, không âm.", {"line_no": i})
        if body.kind in ("PX", "CK") and line.unit_cost is not None:
            raise BusinessError("BAD_UNIT_COST", "Phiếu xuất và chuyển kho không được gửi đơn giá; đơn giá sẽ được tự tính khi ghi sổ.", {"line_no": i})


def _lines(body: StockDocIn):
    return [StockDocLine(line_no=i, **line.model_dump()) for i, line in enumerate(body.lines, 1)]


def _record(session, user, doc, action, before, reason=""):
    session.flush()
    audit.record(session, user, "stock_doc." + action, "stock_doc", str(doc.id), before, snapshot(doc), reason)


def create(session: Session, user: User, body: StockDocIn) -> StockDoc:
    check_writer(user)
    with _transaction(session):
        doc = insert(session, user, body)
    return doc


def insert(session: Session, user: User, body: StockDocIn) -> StockDoc:
    """Lập phiếu trong transaction của nơi gọi, không kiểm vai trò (nơi gọi đã kiểm). Dùng cho phiếu sinh tự động."""
    _validate(session, body)
    doc = StockDoc(**body.model_dump(exclude={"lines"}), created_by=user.id,
                   number=next_number(session, body.kind, body.doc_date), lines=_lines(body))
    session.add(doc)
    _record(session, user, doc, "create", None)
    return doc


def update(session: Session, user: User, doc_id: int, body: StockDocPatch) -> StockDoc:
    check_writer(user)
    with _transaction(session):
        doc = load(session, doc_id)
        _require_draft(doc, body.version)
        before = snapshot(doc)
        data = {field: getattr(doc, field) for field in HEADER_FIELDS}
        data["lines"] = [{key: getattr(line, key) for key in ("product_code", "qty", "unit_cost", "serial", "note")} for line in doc.lines]
        data.update(body.model_dump(exclude_unset=True, exclude={"version"}))
        candidate = StockDocIn(**data)
        if doc.kind != candidate.kind or doc.doc_date.year != candidate.doc_date.year:
            raise BusinessError(
                "NUMBER_LOCKED", "Đổi loại phiếu hoặc sang năm khác thì hủy nháp và lập phiếu mới.", status=409
            )
        _validate(session, candidate)
        for field in HEADER_FIELDS:
            setattr(doc, field, getattr(candidate, field))
        doc.version += 1
        if "lines" in body.model_fields_set:
            doc.lines.clear()
            session.flush()  # Xóa dòng cũ trước khi dùng lại line_no có unique constraint.
            doc.lines = _lines(candidate)
        _record(session, user, doc, "update", before)
    return doc


def cancel(session: Session, user: User, doc_id: int, body: StockDocCancel) -> StockDoc:
    check_writer(user)
    with _transaction(session):
        doc = load(session, doc_id)
        _require_draft(doc, body.version)
        before = snapshot(doc)
        doc.status = "DA_HUY"
        doc.void_reason = body.reason
        _record(session, user, doc, "cancel", before, body.reason)
    return doc
