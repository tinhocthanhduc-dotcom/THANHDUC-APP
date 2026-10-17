from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import sales_orders as svc
from ..db import get_session
from ..models import User
from ..schemas import (SalesOrderCancel, SalesOrderFromQuote, SalesOrderIn, SalesOrderOut,
                       SalesOrderUpdate, SalesOrderVersion)
from ..security import current_user

router = APIRouter(prefix='/sales-orders', tags=['Đơn bán'])


@router.get('', response_model=list[SalesOrderOut])
def search(q: str = '', status: str | None = None, customer_code: str | None = None,
           from_date: date | None = Query(default=None, alias='from'),
           to_date: date | None = Query(default=None, alias='to'),
           user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.search(session, user, q, status, customer_code, from_date, to_date)


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
def confirm(order_id: int, body: SalesOrderVersion, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.confirm(session, user, order_id, body.version)


@router.post('/{order_id}/deliver', response_model=SalesOrderOut)
def deliver(order_id: int, body: SalesOrderVersion, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.deliver(session, user, order_id, body.version)


@router.post('/{order_id}/cancel', response_model=SalesOrderOut)
def cancel(order_id: int, body: SalesOrderCancel, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.cancel(session, user, order_id, body.reason, body.version)
