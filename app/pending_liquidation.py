"""CXL2b: nguồn thanh lý và FIFO, dùng chung khóa tồn/giữ hàng của S1–S4."""
from datetime import UTC,datetime
from decimal import Decimal

from sqlalchemy import func,select,tuple_

from .errors import BusinessError
from .models import PendingDisposition,PendingItem,PendingItemMove,StockBalance,StockReservation,Warehouse


def supply_query(exclude_disposition=None):
    request=select(PendingDisposition.item_id,func.sum(PendingDisposition.qty).label('qty')).where(
        PendingDisposition.status=='CHO_DUYET',PendingDisposition.kind.in_(('HUY','TRA_NCC')))
    if exclude_disposition is not None:request=request.where(PendingDisposition.id!=exclude_disposition)
    request=request.group_by(PendingDisposition.item_id).subquery()
    return select(PendingItem.warehouse_code,PendingItem.product_code,
        func.sum(func.greatest(0,PendingItem.qty_left-func.coalesce(request.c.qty,0))).label('qty')).join(Warehouse).outerjoin(
        request,request.c.item_id==PendingItem.id).where(PendingItem.group_code=='THANH_LY',PendingItem.status=='DANG_CHO',
        Warehouse.kind=='LOI',Warehouse.active.is_(True),Warehouse.supplier_hold.is_(False),Warehouse.recovered_only.is_(False)
        ).group_by(PendingItem.warehouse_code,PendingItem.product_code)


def available(session,warehouse,product,exclude_order=None,exclude_disposition=None):
    pool=supply_query(exclude_disposition).subquery()
    qty=session.scalar(select(pool.c.qty).where(pool.c.warehouse_code==warehouse,pool.c.product_code==product)) or Decimal(0)
    held=select(func.coalesce(func.sum(StockReservation.qty),0)).where(StockReservation.status=='GIU',
        StockReservation.warehouse_code==warehouse,StockReservation.product_code==product)
    if exclude_order is not None:held=held.where(StockReservation.order_id!=exclude_order)
    return max(Decimal(0),qty-session.scalar(held))


def check_available(session,item,qty,exclude_disposition=None,exclude_order=None):
    warehouse=session.get(Warehouse,item.warehouse_code)
    if item.group_code!='THANH_LY' or warehouse.recovered_only or warehouse.supplier_hold:return
    free=available(session,item.warehouse_code,item.product_code,exclude_order,exclude_disposition)
    if qty>free:raise BusinessError('CXL_QTY_RESERVED',f'Hàng thanh lý còn xử lý được {free}; đã trừ số giữ cho đơn và phiếu chờ duyệt.',status=409)


def check_prices(session,user,order):
    from .security import has_role
    for line in order.lines:
        wh=session.get(Warehouse,line.warehouse_code) if line.warehouse_code else None
        if wh and wh.kind=='LOI':
            balance=session.get(StockBalance,(wh.code,line.product_code),populate_existing=True)
            if balance and line.unit_price<balance.avg_cost and not has_role(user,'director'):
                raise BusinessError('CAN_GIAM_DOC_DUYET_GIA','Giá thanh lý dưới giá vốn, cần giám đốc chốt đơn.',status=403)


def deliver_in_tx(session,user,delivery,line,qty,warehouse=None):
    from . import doc_numbers,pending_dispositions as svc
    wh=warehouse or line.warehouse_code
    if qty>available(session,wh,line.product_code,delivery.order_id):
        raise BusinessError('CXL_NOT_ENOUGH','Số giao vượt hàng Thanh lý còn chờ, sau khi trừ giữ của đơn khác và phiếu chờ duyệt.',status=409)
    rows=list(session.scalars(select(PendingItem).where(PendingItem.warehouse_code==wh,PendingItem.product_code==line.product_code,
        PendingItem.status=='DANG_CHO',PendingItem.group_code=='THANH_LY').order_by(PendingItem.entered_at,PendingItem.id)
        .with_for_update().execution_options(populate_existing=True)))
    remaining=qty;result=[]
    for item in rows:
        take=min(remaining,max(Decimal(0),item.qty_left-svc.reserved(session,item.id)))
        if not take:continue
        row=PendingDisposition(number=doc_numbers.next_number(session,'XL',delivery.delivered_at.astimezone(svc.TZ).date()),
            item_id=item.id,kind='THANH_LY',qty=take,status='CHO_DUYET',reason=f'Giao thanh lý {delivery.number}',
            requested_by=user.id,requested_at=datetime.now(UTC),cost_value=0,amount=0,vat_rate=0,vat_amount=0,total=0,
            delivery_id=delivery.id,order_line_no=line.line_no)
        session.add(row);svc.record(session,user,row,'request');svc._post(session,user,row,item.version)
        result.append(row);remaining-=take
        if not remaining:break
    if remaining:raise BusinessError('CXL_NOT_ENOUGH','Hồ sơ Thanh lý không đủ số giao.',status=409)
    return result


def delivery_rows(session,delivery_id):
    return list(session.scalars(select(PendingDisposition).where(PendingDisposition.delivery_id==delivery_id,
        PendingDisposition.kind=='THANH_LY').order_by(PendingDisposition.id)))


def void_delivery_in_tx(session,user,delivery_id,reason):
    from . import pending_dispositions as svc,stock_posting
    for row in delivery_rows(session,delivery_id):
        if row.status!='DA_GHI':continue
        before=svc.snapshot(row)
        stock_posting.void_in_tx(session,user,row.stock_doc_id,reason,None,_pending_disposition=True)
        row.status='DA_HUY';row.void_reason=reason;row.voided_at=datetime.now(UTC);svc.record(session,user,row,'void',before,reason)


def sold_quantities(session,item_ids):
    return dict(session.execute(select(PendingItemMove.item_id,func.sum(PendingItemMove.qty)).join(
        PendingDisposition,PendingDisposition.id==PendingItemMove.disposition_id).where(PendingItemMove.item_id.in_(item_ids),
        PendingDisposition.kind=='THANH_LY',PendingDisposition.status=='DA_GHI').group_by(PendingItemMove.item_id)).all())


def check_fulfillment(session,order,for_technician=False):
    if (order.fulfillment=='THO_GIAO' or for_technician) and any(line.kind=='HANG' and line.warehouse_code and (warehouse:=session.get(Warehouse,line.warehouse_code)) and warehouse.kind=='LOI' for line in order.lines):
        raise BusinessError('THANH_LY_KHONG_GIAO_THO','Hàng thanh lý giao tại kho hoặc kho giao, không giao qua thợ.',status=409)


def choices(session,product_codes=None):
    from .stock_reservations import quantities
    query=supply_query().subquery()
    stmt=select(query.c.warehouse_code,query.c.product_code,Warehouse.name).join(Warehouse,Warehouse.code==query.c.warehouse_code).where(query.c.qty>0)
    if product_codes is not None:stmt=stmt.where(query.c.product_code.in_(product_codes))
    rows=session.execute(stmt.order_by(query.c.warehouse_code)).all()
    stock=quantities(session,[(w,p) for w,p,_ in rows]);result={}
    for warehouse,product,name in rows:
        free=max(Decimal(0),stock.get((warehouse,product),{}).get('available',0))
        whole,_,fraction=format(free,'f').partition('.')
        display=f'{int(whole):,}'.replace(',','.')+(','+fraction.rstrip('0') if fraction.rstrip('0') else '')
        result.setdefault(product,[]).append(dict(code=warehouse,name=name,available=display))
    return result
