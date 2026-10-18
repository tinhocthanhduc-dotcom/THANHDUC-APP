from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import purchase_returns as svc
from ..db import get_session
from ..models import User
from ..schemas import PurchaseReturnIn, PurchaseReturnOut, PurchaseOrderCancel
from ..security import current_user

router=APIRouter(tags=['Trả hàng NCC'])


@router.get('/purchase-orders/{order_id}/returns',response_model=list[PurchaseReturnOut])
def listing(order_id:int,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.list_by_order(session,user,order_id)


@router.post('/purchase-receipts/{receipt_id}/returns',response_model=PurchaseReturnOut,status_code=201)
def create(receipt_id:int,body:PurchaseReturnIn,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.create(session,user,receipt_id,body.version,body.return_date,body.reason,body.lines)


@router.post('/purchase-returns/{return_id}/void',response_model=PurchaseReturnOut)
def void(return_id:int,body:PurchaseOrderCancel,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.void(session,user,return_id,body.reason,body.version)
