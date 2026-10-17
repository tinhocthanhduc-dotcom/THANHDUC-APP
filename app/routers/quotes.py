from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import quotes as svc
from ..db import get_session
from ..models import User
from ..schemas import QuoteCancel, QuoteIn, QuoteOut, QuoteUpdate, QuoteVersion
from ..security import current_user

router = APIRouter(prefix='/quotes', tags=['Báo giá'])


@router.get('', response_model=list[QuoteOut])
def search(q: str = '', status: str | None = None, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.search(session, user, q, status)


@router.get('/{quote_id}', response_model=QuoteOut)
def load(quote_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.load(session, user, quote_id)


@router.post('', response_model=QuoteOut, status_code=201)
def create(body: QuoteIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.create(session, user, body)


@router.patch('/{quote_id}', response_model=QuoteOut)
def update(quote_id: int, body: QuoteUpdate, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.update_lines(session, user, quote_id, body)


@router.post('/{quote_id}/send', response_model=QuoteOut)
def send(quote_id: int, body: QuoteVersion, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.send(session, user, quote_id, body.version)


@router.post('/{quote_id}/accept', response_model=QuoteOut)
def accept(quote_id: int, body: QuoteVersion, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.accept(session, user, quote_id, body.version)


@router.post('/{quote_id}/reject', response_model=QuoteOut)
def reject(quote_id: int, body: QuoteVersion, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.reject(session, user, quote_id, body.version)


@router.post('/{quote_id}/cancel', response_model=QuoteOut)
def cancel(quote_id: int, body: QuoteCancel, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.cancel(session, user, quote_id, body.reason, body.version)


@router.post('/{quote_id}/revise', response_model=QuoteOut, status_code=201)
def revise(quote_id: int, body: QuoteVersion, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.revise(session, user, quote_id, body.version)
