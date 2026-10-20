from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import sales_returns as svc, sales_refunds
from ..db import get_session
from ..models import User
from ..schemas import SalesReturnIn, SalesRefundIn, SalesOrderCancel, SalesOrderVersion
from ..security import current_user

router=APIRouter(tags=['Trả hàng và hoàn tiền'])


@router.post('/sales-orders/{order_id}/returns',status_code=201)
def create(order_id: int,body: SalesReturnIn,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return svc.snapshot(svc.create(session,user,order_id,body))


@router.get('/sales-orders/{order_id}/returns')
def for_order(order_id: int,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return [svc.snapshot(r) for r in svc.for_order(session,user,order_id)]


@router.get('/sales-returns/{return_id}')
def load(return_id: int,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return svc.snapshot(svc.load(session,user,return_id))


@router.post('/sales-returns/{return_id}/void')
def void(return_id: int,body: SalesOrderCancel,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return svc.snapshot(svc.void(session,user,return_id,body.version,body.reason))


@router.post('/sales-orders/{order_id}/refunds',status_code=201)
def request_refund(order_id: int,body: SalesRefundIn,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return sales_refunds.snapshot(sales_refunds.request(session,user,order_id,body))


@router.get('/sales-orders/{order_id}/refunds')
def refunds_for_order(order_id: int,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return [sales_refunds.snapshot(r) for r in sales_refunds.for_order(session,user,order_id)]


@router.get('/sales-refunds')
def pending(user: User=Depends(current_user),session: Session=Depends(get_session)):
    return [sales_refunds.snapshot(r) for r in sales_refunds.pending(session,user)]


@router.post('/sales-refunds/{refund_id}/approve')
def approve(refund_id: int,body: SalesOrderVersion,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return sales_refunds.snapshot(sales_refunds.decide(session,user,refund_id,body.version,True))


@router.post('/sales-refunds/{refund_id}/reject')
def reject(refund_id: int,body: SalesOrderCancel,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return sales_refunds.snapshot(sales_refunds.decide(session,user,refund_id,body.version,False,body.reason))
