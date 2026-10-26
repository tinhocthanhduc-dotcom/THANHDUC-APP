"""Thiết bị theo serial, địa điểm phải thuộc đúng khách hàng đang gán."""
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from . import audit
from . import search as tim
from .customer_sites import check_writer
from .customers import like_pattern
from .errors import BusinessError
from .models import Customer, CustomerSite, Equipment, Product
from .schemas import EquipmentOut
from .text import fold


def normalize_serial(value: str) -> str:
    return ''.join(value.split()).upper()


def snapshot(row):
    return EquipmentOut.model_validate(row).model_dump(mode='json')


def load(session: Session, serial: str) -> Equipment:
    row = session.get(Equipment, normalize_serial(serial))
    if row is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy thiết bị.', status=404)
    return row


def name_column():
    """Tên máy để hiện: tên hàng trong danh mục, hoặc tên tự nhập với máy ngoài danh mục (dùng sau outerjoin Product)."""
    return func.coalesce(Product.name, Equipment.model_name)


def display_name(row, product=None) -> str:
    return product.name if product is not None else (row.model_name if row is not None else '')


def search(session: Session, q='', active_only=False, limit: int | None = 50):
    stmt = select(Equipment).outerjoin(Product, Product.code == Equipment.product_code).outerjoin(Customer, Customer.code == Equipment.customer_code)
    if active_only:
        stmt = stmt.where(Equipment.active.is_(True))
    if fold(q):
        stmt = stmt.where(or_(and_(*tim.like_all(Equipment.search_text, q)), Equipment.serial.ilike(like_pattern(normalize_serial(q))),
                             and_(*tim.like_all(Product.search_text, q)), and_(*tim.like_all(Customer.search_text, q))))
    return list(session.scalars(stmt.order_by(Equipment.serial).limit(limit)))


def _validate_and_search_text(session, data, serial):
    model_name = str(data.get('model_name') or '').strip()
    data['model_name'] = model_name
    product = session.get(Product, data['product_code']) if data.get('product_code') else None
    if data.get('product_code') and product is None:
        raise BusinessError('BAD_PRODUCT', 'Mã hàng không có trong danh mục.')
    if product is None and not model_name:
        raise BusinessError('PRODUCT_NOT_PICKED', 'Chưa chọn hàng: bấm chọn một dòng trong danh sách, hoặc chọn "Không có trong danh mục" và nhập tên máy.')
    if product is not None:
        data['model_name'] = ''
    customer = session.get(Customer, data['customer_code']) if data['customer_code'] else None
    if data['customer_code'] and customer is None:
        raise BusinessError('BAD_CUSTOMER', 'Mã khách không tồn tại.')
    if data['site_id'] is not None:
        site = session.get(CustomerSite, data['site_id'])
        if site is None or not customer or site.customer_code != customer.code:
            raise BusinessError('BAD_SITE', 'Địa điểm phải thuộc đúng khách hàng đã chọn.')
    return fold(' '.join([serial, product.name if product else model_name, customer.name if customer else '']))


def _save(session, user, row, before):
    try:
        session.flush()
        audit.record(session, user, 'equipment.' + ('create' if before is None else 'update'),
                     'equipment', row.serial, before, snapshot(row))
        session.commit()
    except StaleDataError:
        session.rollback()
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa thiết bị, hãy tải lại.', status=409)
    except IntegrityError as exc:
        session.rollback()
        if getattr(getattr(exc.orig, 'diag', None), 'constraint_name', None) == 'equipment_pkey':
            raise BusinessError('DUPLICATE_SERIAL', 'Số sê-ri đã có trong danh mục thiết bị.', status=409)
        raise
    except Exception:
        session.rollback()
        raise
    return row


def create(session: Session, user, body):
    from .stock_docs import _transaction
    with _transaction(session):
        return create_in_tx(session, user, body)


def create_in_tx(session: Session, user, body, check=True):
    """Caller owns the transaction, including rollback on a duplicate serial."""
    if check:check_writer(user)
    data = body.model_dump()
    data['serial'] = normalize_serial(data['serial'])
    if session.get(Equipment, data['serial']) is not None:
        raise BusinessError('DUPLICATE_SERIAL', 'Số sê-ri đã có trong danh mục thiết bị.', status=409)
    data['search_text'] = _validate_and_search_text(session, data, data['serial'])
    row = Equipment(**data, created_by=user.id)
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        if getattr(getattr(exc.orig, 'diag', None), 'constraint_name', None) == 'equipment_pkey':
            raise BusinessError('DUPLICATE_SERIAL', 'Số sê-ri đã có trong danh mục thiết bị.', status=409) from exc
        raise
    audit.record(session, user, 'equipment.create', 'equipment', row.serial, None, snapshot(row))
    return row


def update(session: Session, user, serial, body):
    check_writer(user)
    row = load(session, serial)
    if row.version != body.version:
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa thiết bị, hãy tải lại.', status=409)
    before = snapshot(row)
    data = {field: getattr(row, field) for field in ('product_code', 'model_name', 'customer_code', 'site_id', 'warranty_until', 'support_until', 'condition', 'note', 'active')}
    data.update(body.model_dump(exclude_unset=True, exclude={'version'}))
    search_text = _validate_and_search_text(session, data, row.serial)
    for field, value in data.items():
        setattr(row, field, value)
    row.search_text = search_text
    row.version += 1
    return _save(session, user, row, before)
