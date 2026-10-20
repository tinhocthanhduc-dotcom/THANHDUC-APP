from datetime import date

from fastapi import APIRouter, Depends, Query, File, Form, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .. import sales_orders as svc, sales_receipts, stock_reservations, technician_deliveries as tech
from ..config import get_settings
from ..errors import BusinessError
from ..db import get_session
from ..models import User
from ..schemas import (SalesOrderCancel, SalesOrderFromQuote, SalesOrderIn, SalesOrderOut,
                       SalesOrderUpdate, SalesOrderVersion, SalesOrderPromise, SalesDeliveryIn, SalesDeliveryOut, SalesOrderConfirm, SalesOrderOptions, SalesCounterSale, SalesDeliveryAssign, SalesDeliveryMove, SalesDeliveryFailed, TechnicianDeliveryIn)
from ..security import current_user

router = APIRouter(prefix='/sales-orders', tags=['Đơn bán'])


@router.get('', response_model=list[SalesOrderOut])
def search(q: str = '', status: str | None = None, customer_code: str | None = None,
           from_date: date | None = Query(default=None, alias='from'),
           to_date: date | None = Query(default=None, alias='to'),
           holding: bool = False, fulfillment: str | None = None, overdue: bool = False, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.search(session, user, q, status, customer_code, from_date, to_date, holding, fulfillment, overdue)


@router.get('/{order_id}', response_model=SalesOrderOut)
def load(order_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.load(session, user, order_id)


@router.post('', response_model=SalesOrderOut, status_code=201)
def create(body: SalesOrderIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.create(session, user, body)


@router.post('/from-quote/{quote_id}', response_model=SalesOrderOut, status_code=201)
def from_quote(quote_id: int, body: SalesOrderFromQuote, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.create_from_quote(session, user, quote_id, body)


@router.put('/{order_id}/lines', response_model=SalesOrderOut)
def update_lines(order_id: int, body: SalesOrderUpdate, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.update_lines(session, user, order_id, body)


@router.post('/{order_id}/confirm', response_model=SalesOrderOut)
def confirm(order_id: int, body: SalesOrderConfirm, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.confirm(session, user, order_id, body.version, body.credit_override_reason)


@router.post('/{order_id}/deliver', response_model=SalesOrderOut)
def deliver(order_id: int, body: SalesDeliveryIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.deliver(session, user, order_id, body.version, body.lines, body.receiver_name, body.note, body.carrier_name, body.tracking_code)


@router.post('/{order_id}/cancel', response_model=SalesOrderOut)
def cancel(order_id: int, body: SalesOrderCancel, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.cancel(session, user, order_id, body.reason, body.version)


@router.post('/{order_id}/reserve', response_model=SalesOrderOut)
def keep_more(order_id: int, body: SalesOrderVersion, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return stock_reservations.keep_more(session, user, order_id, body.version)


@router.post('/{order_id}/reservations/{reservation_id}/release', response_model=SalesOrderOut)
def release(order_id: int, reservation_id: int, body: SalesOrderCancel, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return stock_reservations.release(session, user, order_id, reservation_id, body.reason, body.version)


@router.patch('/{order_id}/promised-date', response_model=SalesOrderOut)
def promise(order_id: int, body: SalesOrderPromise, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.set_promised_date(session, user, order_id, body.promised_date, body.version)


@router.post('/{order_id}/close-remaining', response_model=SalesOrderOut)
def close_remaining(order_id: int, body: SalesOrderCancel, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.close_remaining(session,user,order_id,body.reason,body.version)


@router.post('/deliveries/{delivery_id}/void', response_model=SalesOrderOut)
def void_delivery(delivery_id: int, body: SalesOrderCancel, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.void_delivery(session,user,delivery_id,body.reason,body.version)


@router.get('/{order_id}/deliveries', response_model=list[SalesDeliveryOut])
def deliveries(order_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    svc.load(session,user,order_id)
    return svc.deliveries(session,order_id)


@router.patch('/{order_id}/options', response_model=SalesOrderOut)
def options(order_id: int, body: SalesOrderOptions, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.change_options(session,user,order_id,body)


@router.post('/{order_id}/counter-sale', response_model=SalesOrderOut)
def counter_sale(order_id: int, body: SalesCounterSale, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.counter_sale(session,user,order_id,body)


def technician_view(session,user,order):
    state=tech.state(session,user,order)
    from ..models import Customer
    customer=session.get(Customer,order.customer_code)
    return dict(id=order.id,number=order.number,version=order.version,status=order.status,
        customer=customer.name,delivery_address=order.delivery_address,contact_name=order.contact_name,
        promised_date=order.promised_date,label=state['label'],service_ticket_id=order.service_ticket_id,
        amount_due=sales_receipts.balance(order)['available'],
        lines=[dict(line_no=l.line_no,description=l.description,product_code=l.product_code,qty=l.qty,
            held=sum(r.qty for r in state['held'] if r.order_line_id==l.line_no)) for l in order.lines])


@router.get('/{order_id}/technician-delivery')
def technician_load(order_id: int,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return technician_view(session,user,tech.load(session,user,order_id))


@router.post('/{order_id}/assign-delivery',response_model=SalesOrderOut)
def assign_delivery(order_id: int,body: SalesDeliveryAssign,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return tech.assign(session,user,order_id,body)


@router.post('/{order_id}/handover/{mode}',response_model=SalesOrderOut)
def handover(order_id: int,mode: str,body: SalesDeliveryMove,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return tech.move(session,user,order_id,mode,body)


@router.post('/{order_id}/failed-delivery')
def failed_delivery(order_id: int,body: SalesDeliveryFailed,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return technician_view(session,user,tech.failed(session,user,order_id,body))


@router.post('/{order_id}/technician-deliver')
async def technician_deliver(order_id: int,body: str=Form(),photos: list[UploadFile]=File(default=[]),
        user: User=Depends(current_user),session: Session=Depends(get_session)):
    try:
        parsed=TechnicianDeliveryIn.model_validate_json(body)
    except ValidationError:
        raise BusinessError('BAD_FORM','Nội dung JSON giao hàng chưa hợp lệ.')
    limit=get_settings().max_upload_mb*1024*1024
    if len(photos)>10:
        raise BusinessError('FILE_TOO_LARGE','Mỗi lần giao tối đa 10 ảnh.')
    data=[(photo.filename or 'anh',await photo.read(limit+1)) for photo in photos]
    return technician_view(session,user,tech.deliver(session,user,order_id,parsed,data))
