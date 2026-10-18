from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import purchase_receipts as svc
from ..db import get_session
from ..models import User
from ..schemas import PurchaseReceiveIn, PurchaseReceiptOut, PurchaseOrderCancel, PurchaseOrderOut
from ..security import current_user

router=APIRouter(tags=['Nhận hàng đơn mua'])


@router.get('/purchase-orders/{order_id}/receipts',response_model=list[PurchaseReceiptOut])
def listing(order_id:int,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.list_by_order(session,user,order_id)


@router.post('/purchase-orders/{order_id}/receive',response_model=list[PurchaseReceiptOut],status_code=201)
def receive(order_id:int,body:PurchaseReceiveIn,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.receive(session,user,order_id,body.version,body.receipt_date,body.lines)


@router.post('/purchase-receipts/{receipt_id}/void',response_model=PurchaseReceiptOut)
def void(receipt_id:int,body:PurchaseOrderCancel,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.void(session,user,receipt_id,body.reason,body.version)


@router.post('/purchase-orders/{order_id}/close',response_model=PurchaseOrderOut)
def close(order_id:int,body:PurchaseOrderCancel,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.close(session,user,order_id,body.reason,body.version)
