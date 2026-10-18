from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import supplier_debts as svc
from ..db import get_session
from ..models import User
from ..schemas import SupplierPaymentIn, SupplierPaymentOut, SupplierOpeningDebtIn, SupplierOpeningDebtOut, PurchaseOrderCancel
from ..security import current_user

router=APIRouter(tags=['Công nợ NCC'])


@router.get('/supplier-debts')
def listing(q:str='',user:User=Depends(current_user),session:Session=Depends(get_session)):
    return {'rows':svc.search(session,user,q),'totals':svc.totals(session,user,q)}


@router.get('/supplier-debts/{code}')
def detail(code:str,user:User=Depends(current_user),session:Session=Depends(get_session)):
    current=svc.balance(session,user,code)
    initial=svc.opening(session,user,code)
    return {'balance':current,'timeline':svc.timeline(session,user,code),
            'opening':SupplierOpeningDebtOut.model_validate(initial) if initial else None,
            'payments':[SupplierPaymentOut.model_validate(row) for row in svc.payments(session,user,code)]}


@router.post('/supplier-debts/{code}/pay',response_model=SupplierPaymentOut,status_code=201)
def pay(code:str,body:SupplierPaymentIn,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.pay(session,user,code,**body.model_dump())


@router.put('/supplier-debts/{code}/opening',response_model=SupplierOpeningDebtOut)
def opening(code:str,body:SupplierOpeningDebtIn,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.set_opening(session,user,code,**body.model_dump())


@router.post('/supplier-payments/{payment_id}/void',response_model=SupplierPaymentOut)
def void(payment_id:int,body:PurchaseOrderCancel,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.void(session,user,payment_id,body.reason,body.version)
