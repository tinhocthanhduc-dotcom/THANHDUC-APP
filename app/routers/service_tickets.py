from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import service_charges
from .. import service_tickets as svc
from ..db import get_session
from ..models import User
from ..schemas import (
    ServiceAssign,
    ServiceCharges,
    ServiceReason,
    ServiceStatus,
    ServiceTicketDetail,
    ServiceTicketIn,
    ServiceTicketOut,
    ServiceReceivableOut,
    ServiceTransition,
)
from ..security import current_user

router = APIRouter(prefix="/service-tickets", tags=["Phiếu dịch vụ"])


@router.get("", response_model=list[ServiceTicketOut])
def search(
    status: ServiceStatus | None = None,
    assignee_id: int | None = None,
    overdue: bool = False,
    q: str | None = None,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    return svc.search(session, user, status, assignee_id, overdue, q)


@router.get("/receivables", response_model=list[ServiceReceivableOut])
def receivables(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    return service_charges.receivables(session, user, from_date, to_date)


@router.get("/{ticket_id}", response_model=ServiceTicketDetail)
def get(ticket_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.load(session, user, ticket_id)


@router.post("", response_model=ServiceTicketOut, status_code=201)
def create(body: ServiceTicketIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.create(session, user, body)


@router.post("/{ticket_id}/assign", response_model=ServiceTicketOut)
def assign(ticket_id: int, body: ServiceAssign, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.assign(session, user, ticket_id, body.assignee_id, body.version, body.note)


@router.post("/{ticket_id}/transition", response_model=ServiceTicketOut)
def transition(ticket_id: int, body: ServiceTransition, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.transition(session, user, ticket_id, body.to_status, body.version, body.note, body.appointment_at)


@router.post("/{ticket_id}/transfer-request", response_model=ServiceTicketOut)
def transfer_request(ticket_id: int, body: ServiceReason, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.request_transfer(session, user, ticket_id, body.reason, body.version)


@router.post("/{ticket_id}/transfer-reject", response_model=ServiceTicketOut)
def transfer_reject(ticket_id: int, body: ServiceReason, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.reject_transfer(session, user, ticket_id, body.reason, body.version)


@router.post("/{ticket_id}/charges", response_model=ServiceTicketDetail)
def charges(ticket_id: int, body: ServiceCharges, user: User = Depends(current_user), session: Session = Depends(get_session)):
    service_charges.set_charges(session, user, ticket_id, body)
    return svc.load(session, user, ticket_id)
