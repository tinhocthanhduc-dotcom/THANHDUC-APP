"""BH1: serial enrollment only; financial and stock posting remain in their cores."""
from datetime import date
from zoneinfo import ZoneInfo
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from . import audit, equipment
from .errors import BusinessError
from .models import Equipment, Product, SalesDeliverySerial, SalesDelivery, SalesOrderLine, SalesReturn, SalesReturnLine, ServiceTicket, Warehouse
from .service_warranty import add_months

FIELDS=('customer_code','site_id','warranty_until','support_until','condition','active')


def parse_serials(value):
    import re
    return [s.strip() for s in re.split(r'[,\r\n]+',value or '') if s.strip()]


def _snapshot(row):
    return {k:(v.isoformat() if isinstance(v,date) else v) for k in FIELDS for v in [getattr(row,k)]}


def _lock(session, serial):
    # Includes serials not created yet; sorted acquisition at call sites prevents cycles.
    session.execute(text('select pg_advisory_xact_lock(hashtextextended(:s, 0))'),{'s':'sales_serial:'+serial})
    return session.scalar(select(Equipment).where(Equipment.serial==serial).with_for_update().execution_options(populate_existing=True))


def _save(session,user,row,action,before):
    row.search_text=equipment._validate_and_search_text(session,dict(product_code=row.product_code,customer_code=row.customer_code,site_id=row.site_id),row.serial)
    session.flush()
    audit.record(session,user,action,'equipment',row.serial,before,equipment.snapshot(row))


def checked_serials(serials, qty, required):
    """Shared normalization/count/duplicate rules for delivery and production receipts."""
    values = [equipment.normalize_serial(s) for s in serials]
    if required:
        if qty != int(qty) or len(values) != int(qty) or any(not s or len(s)>64 for s in values):
            raise BusinessError('SERIAL_REQUIRED', 'Cần đúng số serial cho số máy, mỗi serial tối đa 64 ký tự.')
    elif values:
        raise BusinessError('SERIAL_REQUIRED', 'Chỉ nhập serial cho mã hàng có cấu hình bảo hành.')
    if len(set(values)) != len(values):
        raise BusinessError('SERIAL_DUPLICATE', 'Serial bị trùng.')
    return values


def enroll_in_tx(session,user,order,delivery,parts,quantities,inputs):
    by_no={p.order_line_no:p.serials for p in (inputs or [])}
    jobs=[];seen=set()
    for line in parts:
        p=session.scalar(select(Product).where(Product.code==line.product_code).with_for_update(read=True)) if line.kind=='HANG' else None
        qty=quantities[line.line_no]
        values=checked_serials(by_no.get(line.line_no,[]),qty,bool(p and p.warranty_months>0))
        for serial in values:
            if serial in seen:raise BusinessError('SERIAL_DUPLICATE','Serial bị trùng trong lần giao.')
            seen.add(serial);jobs.append((serial,line,p))
    day=delivery.delivered_at.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date()
    for serial,line,p in sorted(jobs,key=lambda j:j[0]):
        row=_lock(session,serial);created=row is None
        if row and (row.customer_code is not None or row.product_code!=p.code or row.custody_location!='TAI_KHACH' or row.custody_ticket_id is not None):
            raise BusinessError('SERIAL_IN_USE',f'Serial {serial} đã gắn khách hoặc không đúng mã hàng.',status=409)
        before=equipment.snapshot(row) if row else None
        original=_snapshot(row) if row else None
        if created:
            row=Equipment(serial=serial,product_code=p.code,created_by=user.id,note='',search_text='')
            session.add(row)
        row.customer_code=order.customer_code;row.site_id=None;row.active=True
        row.condition='CU' if session.get(Warehouse,line.warehouse_code).kind=='LOI' else 'MOI'
        row.warranty_until=add_months(day,p.warranty_months);row.support_until=add_months(day,1)
        try:_save(session,user,row,'equipment.sale',before)
        except IntegrityError as exc:
            if getattr(getattr(exc.orig,'diag',None),'constraint_name',None)=='equipment_pkey':
                raise BusinessError('SERIAL_IN_USE',f'Serial {serial} vừa được tạo, hãy tải lại.',status=409) from exc
            raise
        session.add(SalesDeliverySerial(delivery_id=delivery.id,order_line_id=line.line_no,serial=serial,
            created_equipment=created,equipment_before=original,equipment_after=_snapshot(row)))
    session.flush()


def _restore(row,values):
    for k,v in values.items():
        setattr(row,k,date.fromisoformat(v) if k in ('warranty_until','support_until') and v else v)


def void_delivery_in_tx(session,user,delivery):
    links=list(session.scalars(select(SalesDeliverySerial).where(SalesDeliverySerial.delivery_id==delivery.id).order_by(SalesDeliverySerial.serial)))
    for link in links:
        row=_lock(session,link.serial)
        if session.scalar(select(ServiceTicket.id).where(ServiceTicket.serial==link.serial).limit(1)):
            raise BusinessError('SERIAL_HAS_SERVICE',f'Serial {link.serial} đã có phiếu dịch vụ, không hủy giao được.',status=409)
        if row.customer_code!=link.equipment_after['customer_code'] or row.custody_location!='TAI_KHACH':
            raise BusinessError('SERIAL_IN_USE',f'Serial {link.serial} đã thay đổi người giữ.',status=409)
        before=equipment.snapshot(row)
        _restore(row,link.equipment_before or dict(customer_code=None,site_id=None,warranty_until=None,support_until=None,active=False,condition=row.condition))
        _save(session,user,row,'equipment.sale_void',before)


def return_in_tx(session,user,order,line,piece,qty,serials,used):
    links={r.serial:r for r in session.scalars(select(SalesDeliverySerial).where(SalesDeliverySerial.delivery_id==piece.delivery_id,SalesDeliverySerial.order_line_id==line.line_no))}
    values=[equipment.normalize_serial(s) for s in serials]
    if not links:
        if values:raise BusinessError('BAD_SERIAL','Serial không thuộc lần giao này.')
        return [] # legacy deliveries had no serial tracking
    if qty!=int(qty) or len(values)!=int(qty):raise BusinessError('SERIAL_REQUIRED','Chọn đúng số serial máy trả lại.')
    previous={s for arr in session.scalars(select(SalesReturnLine.serials).join(SalesReturn).where(SalesReturn.status=='DA_NHAN',SalesReturnLine.delivery_id==piece.delivery_id)) for s in arr}
    for serial in sorted(values):
        if serial not in links or serial in previous or serial in used:
            raise BusinessError('BAD_SERIAL',f'Serial {serial} không thuộc máy còn được trả của lần giao.',status=409)
        used.add(serial);row=_lock(session,serial)
        if row.customer_code!=order.customer_code or row.custody_location!='TAI_KHACH' or row.custody_ticket_id is not None:
            raise BusinessError('SERIAL_IN_USE',f'Máy {serial} đang được giữ hoặc đã đổi khách.',status=409)
        before=equipment.snapshot(row)
        row.customer_code=None;row.site_id=None;row.warranty_until=None;row.support_until=None
        _save(session,user,row,'equipment.sales_return',before)
    return values


def void_return_in_tx(session,user,returned):
    for part in returned.lines:
        for serial in sorted(part.serials):
            row=_lock(session,serial)
            if row.customer_code is not None or row.custody_location!='TAI_KHACH' or row.custody_ticket_id is not None:
                raise BusinessError('SERIAL_IN_USE',f'Máy {serial} đã được dùng lại, không hủy trả hàng được.',status=409)
            link=session.scalar(select(SalesDeliverySerial).where(SalesDeliverySerial.delivery_id==part.delivery_id,SalesDeliverySerial.serial==serial))
            before=equipment.snapshot(row);_restore(row,link.equipment_after)
            _save(session,user,row,'equipment.sales_return_void',before)


def for_order(session,order_id):
    return session.execute(select(SalesDeliverySerial,Equipment,SalesOrderLine.line_no).join(Equipment,Equipment.serial==SalesDeliverySerial.serial)
        .join(SalesDelivery,SalesDelivery.id==SalesDeliverySerial.delivery_id).join(SalesOrderLine,(SalesOrderLine.order_id==SalesDelivery.order_id)&(SalesOrderLine.line_no==SalesDeliverySerial.order_line_id)).where(SalesOrderLine.order_id==order_id,SalesDelivery.status=='DA_GIAO')
        .order_by(SalesDeliverySerial.delivery_id,SalesOrderLine.line_no,SalesDeliverySerial.serial)).all()
