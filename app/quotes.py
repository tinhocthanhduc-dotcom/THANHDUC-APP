"""B1: báo giá, luồng trạng thái và tiền trong cùng transaction cấp số/audit."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from . import audit
from .config import get_settings
from .customers import like_pattern
from .doc_numbers import next_number
from .errors import BusinessError
from .models import Customer, Product, Quote, QuoteLine, QUOTE_STATUSES
from .schemas import QuoteIn, QuoteLineIn, QuoteOut, QuoteUpdate
from .security import has_role
from .text import fold

WRITERS = ('sales',)
VIEWERS = ('sales', 'finance')
MAX_MONEY = 9_223_372_036_854_775_807


def _today():
    return datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date()


def check_reader(user):
    if not has_role(user, *VIEWERS):
        raise BusinessError('FORBIDDEN', 'Chỉ kinh doanh, kế toán hoặc giám đốc được xem báo giá.', status=403)


def check_writer(user):
    if not has_role(user, *WRITERS):
        raise BusinessError('FORBIDDEN', 'Chỉ kinh doanh hoặc giám đốc được sửa báo giá.', status=403)


@contextmanager
def _transaction(session):
    try:
        yield
        session.commit()
    except StaleDataError:
        session.rollback()
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa báo giá, hãy tải lại.', status=409)
    except Exception:
        session.rollback()
        raise


def snapshot(quote):
    return QuoteOut.model_validate(quote).model_dump(mode='json')


def _load(session, quote_id, lock=False):
    stmt = select(Quote).where(Quote.id == quote_id).options(selectinload(Quote.lines))
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(stmt)
    if row is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy báo giá.', status=404)
    return row


def load(session: Session, user, quote_id):
    check_reader(user)
    return _load(session, quote_id)


def search(session: Session, user, q='', status=None):
    check_reader(user)
    if status and status not in QUOTE_STATUSES:
        raise BusinessError('BAD_STATUS', 'Trạng thái báo giá không hợp lệ.')
    stmt = select(Quote).options(selectinload(Quote.lines)).join(Customer, Quote.customer_code == Customer.code)
    needle = fold(q)
    if needle:
        stmt = stmt.where(or_(Quote.number.ilike(like_pattern(needle)), Customer.search_text.like(like_pattern(needle))))
    if status:
        stmt = stmt.where(Quote.status == status)
    return list(session.scalars(stmt.order_by(Quote.id.desc()).limit(get_settings().search_limit * 5)))


def _version(row, version):
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise BusinessError('VERSION_REQUIRED', 'Phải gửi phiên bản báo giá, hãy tải lại trang.')
    if row.version != version:
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa báo giá, hãy tải lại.', {'current': row.version}, status=409)


def _status(row, allowed):
    if row.status not in allowed:
        raise BusinessError('BAD_STATUS', 'Không thực hiện được ở trạng thái báo giá hiện tại.', {'status': row.status}, status=409)


def _customer(session, code):
    customer = session.scalar(select(Customer).where(Customer.code == code).with_for_update(read=True)
                              .execution_options(populate_existing=True))
    if customer is None or customer.status != 'DANG_DUNG':
        raise BusinessError('BAD_CUSTOMER', 'Khách hàng không tồn tại hoặc đã ngừng/gộp.')
    return customer


def _round(value):
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _money_limit(value):
    if value < 0 or value > MAX_MONEY:
        raise BusinessError('AMOUNT_TOO_LARGE', 'Số tiền vượt giới hạn lưu trữ, hãy giảm số lượng hoặc đơn giá.')
    return value


def _lines(session, lines):
    codes = sorted({line.product_code for line in lines if line.product_code})
    products = {p.code: p for p in session.scalars(select(Product).where(Product.code.in_(codes))
                .order_by(Product.code).with_for_update(read=True).execution_options(populate_existing=True))}
    bad = [code for code in codes if code not in products or not products[code].active]
    if bad:
        raise BusinessError('BAD_PRODUCT', 'Mã hàng không tồn tại hoặc đã ngừng bán: ' + ', '.join(bad), {'invalid_codes': bad})
    result = []
    for i, line in enumerate(lines, 1):
        product = products.get(line.product_code)
        price = line.unit_price if line.unit_price is not None else product.sale_price if product else None
        if price is None:
            raise BusinessError('PRICE_REQUIRED', f'Dòng tự do {i} phải nhập đơn giá.')
        description = line.description.strip() or (product.name if product else '')
        if not description:
            raise BusinessError('DESCRIPTION_REQUIRED', f'Dòng tự do {i} phải nhập mô tả.')
        with localcontext() as context:
            context.prec = 64
            amount = _money_limit(_round(line.qty * price))
            vat_amount = _money_limit(_round(Decimal(amount) * line.vat_rate / 100))
        result.append(QuoteLine(line_no=i, product_code=line.product_code, description=description,
                               qty=line.qty, unit=line.unit.strip() or (product.unit if product else 'cái'),
                               unit_price=price, vat_rate=line.vat_rate, amount=amount, vat_amount=vat_amount))
    return result


def _totals(row):
    row.subtotal = _money_limit(sum(line.amount for line in row.lines))
    row.vat_total = _money_limit(sum(line.vat_amount for line in row.lines))
    row.total = _money_limit(row.subtotal + row.vat_total)


def _record(session, user, row, action, before, reason=''):
    session.flush()
    audit.record(session, user, 'quote.' + action, 'quote', str(row.id), before, snapshot(row), reason)


def _new(session, user, body):
    _customer(session, body.customer_code)
    lines = _lines(session, body.lines)
    row = Quote(number=next_number(session, 'BG', _today()), customer_code=body.customer_code,
                contact_name=body.contact_name, valid_until=body.valid_until or _today() + timedelta(days=7),
                note=body.note, status='NHAP', cancel_reason='', created_by=user.id, lines=lines)
    _totals(row)
    session.add(row)
    _record(session, user, row, 'create', None)
    return row


def create(session: Session, user, body: QuoteIn):
    check_writer(user)
    with _transaction(session):
        row = _new(session, user, body)
    return row


def update_lines(session: Session, user, quote_id, body: QuoteUpdate):
    check_writer(user)
    with _transaction(session):
        row = _load(session, quote_id, lock=True)
        _version(row, body.version)
        if row.status != 'NHAP':
            raise BusinessError('PRICE_LOCKED', 'Báo giá đã gửi hoặc đã đóng, không sửa dòng được.', status=409)
        before = snapshot(row)
        customer_code = body.customer_code if body.customer_code is not None else row.customer_code
        _customer(session, customer_code)
        lines = _lines(session, body.lines)
        row.lines.clear()
        session.flush()  # Xóa dòng cũ trước khi dùng lại khóa (quote_id, line_no).
        row.lines = lines
        row.customer_code = customer_code
        for field in ('contact_name', 'valid_until', 'note'):
            value = getattr(body, field)
            if value is not None:
                setattr(row, field, value)
        row.version += 1
        _totals(row)
        _record(session, user, row, 'update_lines', before)
    return row


def _transition(session, user, quote_id, version, target, allowed, action, reason=''):
    check_writer(user)
    with _transaction(session):
        row = _load(session, quote_id, lock=True)
        _version(row, version)
        _status(row, allowed)
        if target == 'DA_HUY' and not reason.strip():
            raise BusinessError('REASON_REQUIRED', 'Phải nhập lý do hủy báo giá.')
        if target == 'DA_GUI':
            if not row.lines:
                raise BusinessError('EMPTY_LINES', 'Báo giá phải có ít nhất một dòng trước khi gửi.')
            _customer(session, row.customer_code)
            _lines(session, [QuoteLineIn.model_validate(line, from_attributes=True) for line in row.lines])
        before = snapshot(row)
        row.status = target
        if target == 'DA_HUY':
            row.cancel_reason = reason.strip()
        _record(session, user, row, action, before, reason.strip())
    return row


def send(session, user, quote_id, version):
    return _transition(session, user, quote_id, version, 'DA_GUI', ('NHAP',), 'send')


def accept(session, user, quote_id, version):
    return _transition(session, user, quote_id, version, 'CHAP_NHAN', ('DA_GUI',), 'accept')


def reject(session, user, quote_id, version):
    return _transition(session, user, quote_id, version, 'TU_CHOI', ('DA_GUI',), 'reject')


def cancel(session, user, quote_id, reason, version):
    return _transition(session, user, quote_id, version, 'DA_HUY', ('NHAP', 'DA_GUI'), 'cancel', reason or '')


def revise(session: Session, user, quote_id, version):
    check_writer(user)
    with _transaction(session):
        old = _load(session, quote_id, lock=True)
        _version(old, version)
        _status(old, ('DA_GUI',))
        before = snapshot(old)
        new = _new(session, user, QuoteIn(customer_code=old.customer_code, contact_name=old.contact_name,
                   note=old.note, lines=[QuoteLineIn.model_validate(line, from_attributes=True) for line in old.lines]))
        old.status, old.replaced_by_id = 'DA_HUY', new.id
        old.cancel_reason = 'Thay bằng ' + new.number
        _record(session, user, old, 'revise', before, old.cancel_reason)
    return new
