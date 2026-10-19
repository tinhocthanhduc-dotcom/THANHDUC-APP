from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import part_requests as svc
from ..db import get_session
from ..models import User
from ..schemas import PartDecision, PartRequestIn, PartRequestOut
from ..security import current_user

router = APIRouter(tags=['Yêu cầu linh kiện'])


@router.post('/service-tickets/{ticket_id}/part-requests', response_model=PartRequestOut, status_code=201)
def create(ticket_id: int, body: PartRequestIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.create(session, user, ticket_id, body)


@router.get('/part-requests', response_model=list[PartRequestOut])
def listing(status: str = 'CHO_CAP', user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.search(session, user, status)


@router.get('/service-tickets/{ticket_id}/part-requests', response_model=list[PartRequestOut])
def for_ticket(ticket_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.for_ticket(session, user, ticket_id)


@router.post('/part-requests/{ident}/supply', response_model=PartRequestOut)
def supply(ident: int, body: PartDecision, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.supply(session, user, ident, body.warehouse_code, body.version)


@router.post('/part-requests/{ident}/out-of-stock', response_model=PartRequestOut)
def out_of_stock(ident: int, body: PartDecision, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.out_of_stock(session, user, ident, body.note, body.version)


@router.post('/part-requests/{ident}/cancel', response_model=PartRequestOut)
def cancel(ident: int, body: PartDecision, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.cancel(session, user, ident, body.note, body.version)
