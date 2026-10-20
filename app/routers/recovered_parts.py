from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import service_recovered_parts as svc
from ..db import get_session
from ..models import User
from ..schemas import RecoveredPartOut, RecoveredPartReceive, ServiceReason
from ..security import current_user

router = APIRouter(prefix='/recovered-parts', tags=['Linh kiện thu hồi'])


@router.get('', response_model=list[RecoveredPartOut])
def listing(holder_id: int | None = None, pending: bool = False, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.search(session, user, holder_id=holder_id, pending=pending)


@router.post('/{ident}/receive', response_model=RecoveredPartOut)
def receive(ident: int, body: RecoveredPartReceive, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.receive(session, user, ident, body)


@router.post('/{ident}/cancel', response_model=RecoveredPartOut)
def cancel(ident: int, body: ServiceReason, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.cancel(session, user, ident, body.version, body.reason)
