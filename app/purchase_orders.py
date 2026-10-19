"""M1: đơn mua, chưa nhận hàng hoặc ghi sổ kho/quỹ."""
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, localcontext

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError

from . import audit, quotes
from .config import get_settings
from .customers import like_pattern
from .doc_numbers import next_number
from .errors import BusinessError
from .models import Product, PurchaseOrder, PurchaseOrderLine, PURCHASE_ORDER_STATUSES, Supplier, Warehouse
from .schemas import PurchaseOrderLineIn, PurchaseOrderOut
from .security import has_role

WRITERS = ('warehouse',)
VIEWERS = ('warehouse', 'finance')


def check_reader(user):
    if not has_role(user, *VIEWERS):
        raise BusinessError('FORBIDDEN', 'Chỉ kho, kế toán hoặc giám đốc được xem đơn mua.', status=403)


def check_writer(user):
    if not has_role(user, *WRITERS):
        raise BusinessError('FORBIDDEN', 'Chỉ kho hoặc giám đốc được sửa đơn mua.', status=403)


@contextmanager
def _transaction(session):
    try:
        yield
        session.commit()
    except StaleDataError:
        session.rollback()
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa đơn mua, hãy tải lại.', status=409)
    except Exception:
        session.rollback()
        raise


def snapshot(row):
    return PurchaseOrderOut.model_validate(row).model_dump(mode='json')


def _version(row, version):
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise BusinessError('VERSION_REQUIRED', 'Phải gửi phiên bản đơn mua, hãy tải lại.')
    if row.version != version:
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa đơn mua, hãy tải lại.', {'current': row.version}, status=409)


def _load(session, order_id, lock=False):
    stmt = select(PurchaseOrder).where(PurchaseOrder.id == order_id).options(selectinload(PurchaseOrder.lines))
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(stmt)
    if row is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy đơn mua.', status=404)
    return row


def load(session, user, order_id):
    check_reader(user)
    return _load(session, order_id)


def search(session, user, q='', status=None, supplier_code=None):
    check_reader(user)
    if status and status not in (*PURCHASE_ORDER_STATUSES, 'CHO_NHAN'):
        raise BusinessError('BAD_STATUS', 'Trạng thái đơn mua không hợp lệ.')
    stmt = select(PurchaseOrder).options(selectinload(PurchaseOrder.lines))
    if q.strip():
        stmt = stmt.where(PurchaseOrder.number.ilike(like_pattern(q.strip())))
    if status == 'CHO_NHAN':
        stmt = stmt.where(PurchaseOrder.status.in_(('DA_DAT','NHAN_MOT_PHAN')))
    elif status:
        stmt = stmt.where(PurchaseOrder.status == status)
    if supplier_code:
        stmt = stmt.where(PurchaseOrder.supplier_code == supplier_code)
    return list(session.scalars(stmt.order_by(PurchaseOrder.id.desc()).limit(get_settings().search_limit * 5)))


def _supplier(session, code):
    row = session.scalar(select(Supplier).where(Supplier.code == code).with_for_update(read=True).execution_options(populate_existing=True))
    if row is None or not row.active:
        raise BusinessError('BAD_SUPPLIER', 'Nhà cung cấp không tồn tại hoặc đã ngừng dùng.')


def _lines(session, lines):
    if not lines:
        raise BusinessError('EMPTY_ORDER', 'Đơn mua phải có ít nhất một dòng hàng.')
    for model, attr, code, message in [(Product, 'product_code', 'BAD_PRODUCT', 'Mã hàng không tồn tại hoặc đã ngừng bán: '),
                                      (Warehouse, 'warehouse_code', 'BAD_WAREHOUSE', 'Kho không tồn tại hoặc đã ngừng dùng: ')]:
        codes = sorted({getattr(line, attr) for line in lines})
        rows = {r.code: r for r in session.scalars(select(model).where(model.code.in_(codes)).order_by(model.code)
                    .with_for_update(read=True).execution_options(populate_existing=True))}
        bad = [c for c in codes if c not in rows or not rows[c].active or (model is Warehouse and rows[c].kind != 'KHO')]
        if bad:
            raise BusinessError(code, message + ', '.join(bad), {'invalid_codes': bad})
    result = []
    for n, line in enumerate(lines, 1):
        with localcontext() as context:
            context.prec = 64
            amount = quotes._money_limit(quotes._round(line.qty * line.unit_cost))
            vat = quotes._money_limit(quotes._round(Decimal(amount) * line.vat_rate / 100))
        result.append(PurchaseOrderLine(line_no=n, **line.model_dump(), amount=amount, vat_amount=vat, received_qty=0))
    return result


def _totals(row):
    row.subtotal = quotes._money_limit(sum(line.amount for line in row.lines))
    row.vat_total = quotes._money_limit(sum(line.vat_amount for line in row.lines))
    row.total = quotes._money_limit(row.subtotal + row.vat_total)


def _record(session, user, row, action, before, reason=''):
    session.flush()
    audit.record(session, user, 'purchase_order.' + action, 'purchase_order', str(row.id), before, snapshot(row), reason)


def create(session, user, body):
    check_writer(user)
    with _transaction(session):
        _supplier(session, body.supplier_code)
        lines = _lines(session, body.lines)
        row = PurchaseOrder(number=next_number(session, 'DM', body.order_date), created_by=user.id,
            **body.model_dump(exclude={'lines'}), lines=lines)
        _totals(row); session.add(row)
        _record(session, user, row, 'create', None)
    return row


def update(session, user, order_id, body):
    check_writer(user)
    with _transaction(session):
        row = _load(session, order_id, True); _version(row, body.version)
        if row.status != 'NHAP':
            raise BusinessError('PRICE_LOCKED', 'Đơn mua đã đặt hoặc đã đóng, không sửa được.', status=409)
        before = snapshot(row)
        _supplier(session, body.supplier_code)
        lines = _lines(session, body.lines)
        row.lines.clear(); session.flush()
        row.lines = lines
        for key, value in body.model_dump(exclude={'lines', 'version'}).items():
            setattr(row, key, value)
        row.version += 1
        _totals(row); _record(session, user, row, 'update', before)
    return row


def order(session, user, order_id, version):
    check_writer(user)
    with _transaction(session):
        row = _load(session, order_id, True); _version(row, version)
        if row.status != 'NHAP':
            raise BusinessError('BAD_STATUS', 'Chỉ đặt đơn mua đang Nháp.', status=409)
        _supplier(session, row.supplier_code)
        _lines(session, [PurchaseOrderLineIn.model_validate(line, from_attributes=True) for line in row.lines])
        before = snapshot(row)
        row.status, row.ordered_by, row.ordered_at = 'DA_DAT', user.id, datetime.now(UTC)
        _record(session, user, row, 'order', before)
    return row


def cancel(session, user, order_id, reason, version):
    check_writer(user)
    with _transaction(session):
        row = _load(session, order_id, True); _version(row, version)
        reason = (reason or '').strip()
        if not reason or len(reason) > 2000:
            raise BusinessError('REASON_REQUIRED', 'Hủy đơn mua phải nhập lý do (tối đa 2000 ký tự).')
        if row.status not in ('NHAP', 'DA_DAT') or any(line.received_qty > 0 for line in row.lines):
            raise BusinessError('CANNOT_CANCEL', 'Không hủy được đơn đã nhận hàng hoặc đã đóng.', status=409)
        before = snapshot(row)
        row.status, row.cancel_reason = 'DA_HUY', reason
        _record(session, user, row, 'cancel', before, reason)
    return row


def suggest_cost(session, user, product_code):
    check_reader(user)
    return session.scalar(select(PurchaseOrderLine.unit_cost).join(PurchaseOrder)
        .where(PurchaseOrderLine.product_code == product_code, PurchaseOrder.status.not_in(('NHAP', 'DA_HUY')))
        .order_by(PurchaseOrder.ordered_at.desc().nulls_last(), PurchaseOrder.id.desc(), PurchaseOrderLine.line_no.desc()).limit(1))
