"""KT2: chỉ đọc kho thợ; mọi di chuyển vẫn qua phiếu kho và lõi ghi sổ."""
from sqlalchemy import func, select

from .errors import BusinessError
from .models import Product, StockReservation, SalesOrder, StockBalance, StockDoc, StockLedger, User, Warehouse
from .security import has_role


def own_warehouse(session, user, lock=False):
    stmt = select(Warehouse).where(Warehouse.kind == 'THO', Warehouse.active.is_(True), Warehouse.holder_id == user.id)
    if lock:stmt = stmt.with_for_update(read=True)
    return session.scalar(stmt.execution_options(populate_existing=True))


def restricted_warehouse(session, user, lock=False):
    if 'technician' in user.roles and not has_role(user, 'dispatcher'):
        return own_warehouse(session, user, lock)
    return None


def held(session, user, holder_id=None):
    with session.no_autoflush:
        if not has_role(user, 'warehouse'):
            if 'technician' not in user.roles or (holder_id is not None and holder_id != user.id):
                raise BusinessError('FORBIDDEN','Bạn chỉ được xem kho của mình.',status=403)
            holder_id = user.id
        holder = session.get(User, holder_id) if holder_id else None
        warehouse = session.scalar(select(Warehouse).where(Warehouse.holder_id == holder_id,
            Warehouse.kind == 'THO', Warehouse.active.is_(True))) if holder_id else None
        rows = []
        if warehouse:
            recent = select(StockLedger.product_code, StockDoc.id.label('doc_id'), StockDoc.number,
                StockDoc.doc_date, func.row_number().over(partition_by=StockLedger.product_code,
                order_by=(StockLedger.at.desc(), StockLedger.id.desc())).label('rank')).join(
                StockDoc, StockDoc.id == StockLedger.doc_id).where(StockLedger.warehouse_code == warehouse.code,
                StockDoc.kind == 'CK', StockDoc.status == 'DA_GHI', StockLedger.is_reversal.is_(False)).subquery()
            rows = list(session.execute(select(StockBalance.product_code, Product.name, StockBalance.qty,
                recent.c.doc_id, recent.c.number, recent.c.doc_date).join(Product, Product.code == StockBalance.product_code)
                .outerjoin(recent, (recent.c.product_code == StockBalance.product_code) & (recent.c.rank == 1))
                .where(StockBalance.warehouse_code == warehouse.code, StockBalance.qty > 0)
                .order_by(StockBalance.product_code)).mappings())
        reserved={}
        if warehouse:
            for code,number,order_id,qty in session.execute(select(StockReservation.product_code,SalesOrder.number,SalesOrder.id,StockReservation.qty).join(
                SalesOrder,SalesOrder.id==StockReservation.order_id).where(StockReservation.warehouse_code==warehouse.code,StockReservation.status=='GIU')):
                reserved.setdefault(code,[]).append(dict(number=number,id=order_id,qty=qty))
        from .service_recovered_parts import search
        recovered = search(session, user, holder_id=holder_id, pending=True)
        return dict(recovered_parts=recovered, warehouse=warehouse, holder=holder, rows=rows, reservations=reserved)
