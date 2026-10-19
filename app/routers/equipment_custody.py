from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import equipment_custody as svc
from ..db import get_session
from ..models import User
from ..schemas import CustodyHandover, CustodyIntake, CustodyOut, CustodyVersion
from ..security import current_user

router = APIRouter(tags=['Giữ máy khách'])


@router.get('/service-tickets/{ticket_id}/custody',response_model=list[CustodyOut])
def history(ticket_id:int,user:User=Depends(current_user),session:Session=Depends(get_session)):
    ticket=svc.load(session,user,ticket_id)
    return svc.history(session,ticket.serial)


@router.post('/service-tickets/{ticket_id}/custody/receive',response_model=CustodyOut,status_code=201)
def receive(ticket_id:int,body:CustodyIntake,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.receive(session,user,ticket_id,body)


@router.post('/service-tickets/{ticket_id}/custody/handover',response_model=CustodyOut,status_code=201)
def handover(ticket_id:int,body:CustodyHandover,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.handover(session,user,ticket_id,body)


@router.post('/service-tickets/{ticket_id}/custody/return',response_model=CustodyOut,status_code=201)
def return_customer(ticket_id:int,body:CustodyIntake,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.return_customer(session,user,ticket_id,body)


@router.post('/equipment-custody/{ident}/confirm',response_model=CustodyOut)
def confirm(ident:int,body:CustodyVersion,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.confirm(session,user,ident,body.version)


@router.post('/equipment-custody/{ident}/cancel',response_model=CustodyOut)
def cancel(ident:int,body:CustodyVersion,user:User=Depends(current_user),session:Session=Depends(get_session)):
    return svc.cancel(session,user,ident,body.version)
