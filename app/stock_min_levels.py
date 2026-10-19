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
        wh = session.get(Warehouse, warehouse)
        if wh is None:
            raise BusinessError('BAD_WAREHOUSE', 'Kho không tồn tại.', status=404)
        if wh.kind != 'KHO':
            raise BusinessError('BAD_WAREHOUSE','Kho của thợ không đặt mức cảnh báo tồn.',status=409)
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


def low_stock_query():
    """Cùng truy vấn chỉ đọc cho Sắp hết và Việc của tôi, kể cả chưa có balance."""
    from sqlalchemy import and_, func, select
    from .models import StockBalance
    qty = func.coalesce(StockBalance.qty, 0)
    return select(StockMinLevel.warehouse_code, StockMinLevel.product_code, Product.name,
        qty.label('qty'), func.coalesce(StockBalance.value, 0).label('value'),
        func.coalesce(StockBalance.avg_cost, 0).label('avg_cost'), StockMinLevel.min_qty,
        (StockMinLevel.min_qty-qty).label('shortage')).select_from(StockMinLevel).outerjoin(
            StockBalance, and_(StockBalance.warehouse_code == StockMinLevel.warehouse_code,
                              StockBalance.product_code == StockMinLevel.product_code)
        ).join(Product, Product.code == StockMinLevel.product_code).join(Warehouse, Warehouse.code == StockMinLevel.warehouse_code).where(
            Warehouse.kind == 'KHO',
            StockMinLevel.min_qty > 0, qty <= StockMinLevel.min_qty
        ).order_by((qty > 0), StockMinLevel.warehouse_code, StockMinLevel.product_code)
