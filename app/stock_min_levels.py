"""Đặt mức cảnh báo tồn, không ghi số lượng/giá trị tồn hoặc sổ kho."""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from . import audit
from .errors import BusinessError
from .inventory_catalogs import check_writer
from .models import Product, StockMinLevel, User, Warehouse
from .schemas import StockMinLevelIn


def snapshot(level: StockMinLevel) -> dict:
    return dict(warehouse_code=level.warehouse_code, product_code=level.product_code,
                min_qty=str(level.min_qty), version=level.version,
                updated_at=level.updated_at.isoformat(), updated_by=level.updated_by)


def _conflict():
    return BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa mức tồn tối thiểu, hãy tải lại trang.', status=409)


def set_level(session: Session, user: User, warehouse: str, product: str, body: StockMinLevelIn) -> StockMinLevel:
    check_writer(user)
    try:
        if session.get(Warehouse, warehouse) is None:
            raise BusinessError('BAD_WAREHOUSE', 'Kho không tồn tại.', status=404)
        if session.get(Product, product) is None:
            raise BusinessError('BAD_PRODUCT', 'Mã hàng không tồn tại.', status=404)
        level = session.get(StockMinLevel, (warehouse, product))
        current = level.version if level is not None else 0
        if body.version != current:
            raise _conflict()
        before = snapshot(level) if level is not None else None
        if level is None:
            level = StockMinLevel(warehouse_code=warehouse, product_code=product, min_qty=body.min_qty, updated_by=user.id)
            session.add(level)
        else:
            level.min_qty = body.min_qty
            level.updated_by = user.id
            level.version += 1
        session.flush()
        audit.record(session, user, 'stock_min_level.' + ('create' if before is None else 'update'),
                     'stock_min_level', f'{warehouse}/{product}', before, snapshot(level))
        session.commit()
        return level
    except StaleDataError:
        session.rollback()
        raise _conflict()
    except IntegrityError as exc:
        session.rollback()
        # Hai người cùng đặt mức lần đầu: chỉ một người tạo được, người kia cần tải lại.
        if getattr(getattr(exc.orig, 'diag', None), 'constraint_name', None) == 'stock_min_levels_pkey':
            raise _conflict()
        raise
    except Exception:
        session.rollback()
        raise
