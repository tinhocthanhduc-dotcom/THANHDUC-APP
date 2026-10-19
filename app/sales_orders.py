"""B2: đơn bán; giao/hủy PX qua lõi kho trong transaction của đơn."""
from contextlib import contextmanager
from zoneinfo import ZoneInfo
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from . import audit, quotes, stock_docs, stock_posting
from .config import get_settings
from .customers import like_pattern
from .doc_numbers import next_number
from .errors import BusinessError
from .models import Customer, SalesOrder, SalesOrderLine, SalesReceipt, SALES_ORDER_STATUSES, Warehouse
from .schemas import (QuoteLineIn, SalesOrderFromQuote, SalesOrderIn, SalesOrderLineIn,
                      SalesOrderOut, SalesOrderUpdate, StockDocIn, StockDocLineIn)
from .security import has_role
from .text import fold

WRITERS = ('sales',)
DELIVERERS = ('sales', 'warehouse')
VIEWERS = ('sales', 'warehouse', 'finance')


def _today():
    return quotes._today()


def check_reader(user):
    if not has_role(user, *VIEWERS):
        raise BusinessError('FORBIDDEN', 'Chỉ kinh doanh, kho, kế toán hoặc giám đốc được xem đơn bán.', status=403)


def check_writer(user):
    if not has_role(user, *WRITERS):
        raise BusinessError('FORBIDDEN', 'Chỉ kinh doanh hoặc giám đốc được sửa đơn bán.', status=403)


def _quote_used():
    return BusinessError('QUOTE_USED', 'Báo giá đã có đơn bán chưa hủy.', status=409)


@contextmanager
def _transaction(session):
    try:
        yield
        session.commit()
    except StaleDataError:
        session.rollback()
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa đơn bán, hãy tải lại.', status=409)
    except IntegrityError as exc:
        session.rollback()
        if getattr(getattr(exc.orig, 'diag', None), 'constraint_name', '') == 'uq_sales_orders_active_quote':
            raise _quote_used() from exc
        raise
    except Exception:
        session.rollback()
        raise


def snapshot(order):
    return SalesOrderOut.model_validate(order).model_dump(mode='json')


def _load(session, order_id, lock=False):
    stmt = select(SalesOrder).where(SalesOrder.id == order_id).options(selectinload(SalesOrder.lines))
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(stmt)
    if row is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy đơn bán.', status=404)
    return row


def load(session: Session, user, order_id):
    check_reader(user)
    return _load(session, order_id)


def search(session: Session, user, q='', status=None, customer_code=None, from_date=None, to_date=None):
    check_reader(user)
    if status and status not in SALES_ORDER_STATUSES:
        raise BusinessError('BAD_STATUS', 'Trạng thái đơn bán không hợp lệ.')
    stmt = select(SalesOrder).options(selectinload(SalesOrder.lines)).join(Customer, SalesOrder.customer_code == Customer.code)
    needle = fold(q)
    if needle:
        stmt = stmt.where(or_(SalesOrder.number.ilike(like_pattern(needle)), Customer.search_text.like(like_pattern(needle))))
    for column, value in ((SalesOrder.status, status), (SalesOrder.customer_code, customer_code)):
        if value:
            stmt = stmt.where(column == value)
    # Khoảng ngày theo giờ Việt Nam, cùng cách cấp số DB.
    if from_date is not None or to_date is not None:
        tz = ZoneInfo('Asia/Ho_Chi_Minh')
        if from_date is not None and to_date is not None and from_date > to_date:
            raise BusinessError('BAD_DATE_RANGE', 'Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')
        if from_date is not None:
            stmt = stmt.where(SalesOrder.created_at >= datetime.combine(from_date, time.min, tz))
        if to_date is not None and to_date < date.max:
            stmt = stmt.where(SalesOrder.created_at < datetime.combine(to_date + timedelta(days=1), time.min, tz))
    return list(session.scalars(stmt.order_by(SalesOrder.id.desc()).limit(get_settings().search_limit * 5)))


def _version(order, version):
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise BusinessError('VERSION_REQUIRED', 'Phải gửi phiên bản đơn bán, hãy tải lại.')
    if order.version != version:
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa đơn bán, hãy tải lại.', {'current': order.version}, status=409)


def _status(order, allowed):
    if order.status not in allowed:
        raise BusinessError('BAD_STATUS', 'Không thực hiện được ở trạng thái đơn bán hiện tại.', {'status': order.status}, status=409)


def _lines(session, lines, require_warehouse=False):
    for i, line in enumerate(lines, 1):
        if line.kind == 'HANG':
            if not line.product_code:
                raise BusinessError('BAD_LINE', 'Dòng hàng phải có mã hàng.', {'line_no': i})
            if require_warehouse and not line.warehouse_code:
                raise BusinessError('WAREHOUSE_REQUIRED', 'Dòng hàng phải chọn kho xuất.', {'line_no': i})
        elif line.product_code or line.warehouse_code:
            raise BusinessError('BAD_LINE', 'Dòng khác không có mã hàng hoặc kho.', {'line_no': i})
    codes = sorted({line.warehouse_code for line in lines if line.kind == 'HANG' and line.warehouse_code})
    valid = set(session.scalars(select(Warehouse.code).where(Warehouse.code.in_(codes), Warehouse.active.is_(True), Warehouse.kind == 'KHO')
                               .order_by(Warehouse.code).with_for_update(read=True)))
    if set(codes) - valid:
        raise BusinessError('BAD_WAREHOUSE', 'Kho không tồn tại hoặc đã ngừng sử dụng.', {'codes': sorted(set(codes) - valid)})
    # Dùng nguyên phần tính dòng B1 (_round/_money_limit, VAT, giá bán và kiểm mã hàng).
    priced = quotes._lines(session, [QuoteLineIn(**line.model_dump(exclude={'kind', 'warehouse_code'})) for line in lines])
    return [SalesOrderLine(kind=source.kind, warehouse_code=source.warehouse_code,
                          **{field: getattr(line, field) for field in ('line_no', 'product_code', 'description', 'qty',
                             'unit', 'unit_price', 'vat_rate', 'amount', 'vat_amount')})
            for source, line in zip(lines, priced)]


def _record(session, user, order, action, before, reason=''):
    session.flush()
    audit.record(session, user, 'sales_order.' + action, 'sales_order', str(order.id), before, snapshot(order), reason)


def _new(session, user, body, quote_id=None, action='create'):
    customer = quotes._customer(session, body.customer_code)
    lines = _lines(session, body.lines)
    order = SalesOrder(number=next_number(session, 'DB', _today()), customer_code=body.customer_code,
                       quote_id=quote_id, contact_name=body.contact_name,
                       delivery_address=customer.address if body.delivery_address is None else body.delivery_address,
                       note=body.note, status='NHAP', cancel_reason='', created_by=user.id, lines=lines)
    quotes._totals(order)
    session.add(order)
    _record(session, user, order, action, None)
    return order


def create(session: Session, user, body: SalesOrderIn):
    check_writer(user)
    with _transaction(session):
        order = _new(session, user, body)
    return order


def create_from_quote(session: Session, user, quote_id, body: SalesOrderFromQuote):
    check_writer(user)
    with _transaction(session):
        # Khóa nguồn để hai yêu cầu cùng lúc không dùng cùng báo giá.
        quote = quotes._load(session, quote_id, lock=True)
        quotes._version(quote, body.version)
        if quote.status != 'CHAP_NHAN':
            raise BusinessError('BAD_STATUS', 'Chỉ lập đơn bán từ báo giá đã được khách chấp nhận.', status=409)
        if session.scalar(select(SalesOrder.id).where(SalesOrder.quote_id == quote_id, SalesOrder.status != 'DA_HUY')) is not None:
            raise _quote_used()
        goods = {line.line_no for line in quote.lines if line.product_code}
        if set(body.warehouses) - goods:
            raise BusinessError('BAD_LINE', 'Chỉ chọn kho cho các dòng hàng trong báo giá.', {'line_nos': sorted(set(body.warehouses)-goods)})
        lines = [SalesOrderLineIn(kind='HANG' if line.product_code else 'KHAC',
                 warehouse_code=body.warehouses.get(line.line_no),
                 **{field: getattr(line, field) for field in ('product_code','description','qty','unit','unit_price','vat_rate')})
                 for line in quote.lines]
        order = _new(session, user, SalesOrderIn(customer_code=quote.customer_code,
                     contact_name=quote.contact_name if body.contact_name is None else body.contact_name,
                     delivery_address=body.delivery_address, note=quote.note if body.note is None else body.note,
                     lines=lines), quote_id, 'create_from_quote')
    return order


def update_lines(session: Session, user, order_id, body: SalesOrderUpdate):
    check_writer(user)
    with _transaction(session):
        order = _load(session, order_id, lock=True)
        _version(order, body.version)
        if order.status != 'NHAP':
            raise BusinessError('PRICE_LOCKED', 'Đơn bán đã chốt hoặc đã hủy, không sửa dòng được.', status=409)
        quotes._customer(session, order.customer_code)
        lines = _lines(session, body.lines)
        before = snapshot(order)
        order.lines.clear()
        session.flush()
        order.lines = lines
        for field in ('contact_name', 'delivery_address', 'note'):
            value = getattr(body, field)
            if value is not None:
                setattr(order, field, value)
        order.version += 1
        quotes._totals(order)
        _record(session, user, order, 'update_lines', before)
    return order


def confirm(session: Session, user, order_id, version):
    check_writer(user)
    with _transaction(session):
        order = _load(session, order_id, lock=True)
        _version(order, version)
        _status(order, ('NHAP',))
        if not order.lines:
            raise BusinessError('EMPTY_LINES', 'Đơn bán phải có ít nhất một dòng trước khi chốt.')
        quotes._customer(session, order.customer_code)
        _lines(session, [SalesOrderLineIn.model_validate(line, from_attributes=True) for line in order.lines], require_warehouse=True)
        before = snapshot(order)
        quotes._totals(order)
        order.status, order.receivable = 'DA_CHOT', order.total
        order.confirmed_at, order.confirmed_by = datetime.now(UTC), user.id
        _record(session, user, order, 'confirm', before)
    return order


def deliver(session: Session, user, order_id, version):
    if not has_role(user, *DELIVERERS):
        raise BusinessError('FORBIDDEN', 'Chỉ kinh doanh, kho hoặc giám đốc được giao đơn bán.', status=403)
    with _transaction(session):
        order = _load(session, order_id, lock=True)
        _version(order, version)
        _status(order, ('DA_CHOT',))
        _lines(session, [SalesOrderLineIn.model_validate(line, from_attributes=True) for line in order.lines], require_warehouse=True)
        before = snapshot(order)
        by_wh = {}
        for line in order.lines:
            if line.kind == 'HANG':
                by_wh.setdefault(line.warehouse_code, []).append(line)
        for wh in sorted(by_wh):
            parts = by_wh[wh]
            doc = stock_docs.insert(session, user, StockDocIn(kind='PX', warehouse_code=wh, doc_date=_today(),
                     customer_code=order.customer_code, reason=f'Giao đơn {order.number}',
                     lines=[StockDocLineIn(product_code=line.product_code, qty=line.qty,
                            note=f'{order.number} dòng {line.line_no}') for line in parts]))
            stock_posting.post_in_tx(session, user, doc.id, None)
            for line in parts:
                line.stock_doc_id = doc.id
        order.status = 'DA_GIAO'
        order.delivered_at, order.delivered_by = datetime.now(UTC), user.id
        _record(session, user, order, 'deliver', before)
    return order


def cancel(session: Session, user, order_id, reason, version):
    check_writer(user)
    with _transaction(session):
        order = _load(session, order_id, lock=True)
        _version(order, version)
        _status(order, ('NHAP', 'DA_CHOT', 'DA_GIAO'))
        reason = (reason or '').strip()
        if not reason:
            raise BusinessError('REASON_REQUIRED', 'Phải nhập lý do hủy đơn bán.')
        if session.scalar(select(SalesReceipt.id).where(SalesReceipt.order_id == order.id,
                          SalesReceipt.status.in_(('CHO_XAC_NHAN', 'DA_XAC_NHAN'))).limit(1)) is not None:
            raise BusinessError('HAS_RECEIPTS', 'Tài chính phải từ chối hoặc hủy báo thu trước', status=409)
        before = snapshot(order)
        if order.status == 'DA_GIAO':
            for doc_id in sorted({line.stock_doc_id for line in order.lines if line.stock_doc_id is not None}):
                stock_posting.void_in_tx(session, user, doc_id, f'{order.number}: {reason}', None)
        order.status, order.cancel_reason = 'DA_HUY', reason
        if order.receivable is not None:
            order.receivable = 0
        # Giữ tổng và liên kết PX để xem lại lịch sử sau hủy; không còn số phải thu.
        _record(session, user, order, 'cancel', before, reason)
    return order
