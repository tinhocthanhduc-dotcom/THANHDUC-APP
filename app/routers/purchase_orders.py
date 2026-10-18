from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import purchase_orders as svc
from ..db import get_session
from ..models import User
from ..schemas import PurchaseOrderIn, PurchaseOrderUpdate, PurchaseOrderOut, PurchaseOrderVersion, PurchaseOrderCancel
from ..security import current_user

router = APIRouter(prefix='/purchase-orders', tags=['Đơn mua'])


@router.get('', response_model=list[PurchaseOrderOut])
def search(q: str = '', status: str | None = None, supplier_code: str | None = None,
           user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.search(session, user, q, status, supplier_code)


@router.get('/suggest-cost/{product_code}')
def suggest(product_code: str, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return {'unit_cost': svc.suggest_cost(session, user, product_code)}


@router.get('/{order_id}', response_model=PurchaseOrderOut)
def load(order_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.load(session, user, order_id)


@router.post('', response_model=PurchaseOrderOut, status_code=201)
def create(body: PurchaseOrderIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.create(session, user, body)


@router.put('/{order_id}', response_model=PurchaseOrderOut)
def update(order_id: int, body: PurchaseOrderUpdate, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.update(session, user, order_id, body)


@router.post('/{order_id}/order', response_model=PurchaseOrderOut)
def order(order_id: int, body: PurchaseOrderVersion, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.order(session, user, order_id, body.version)


@router.post('/{order_id}/cancel', response_model=PurchaseOrderOut)
def cancel(order_id: int, body: PurchaseOrderCancel, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.cancel(session, user, order_id, body.reason, body.version)
