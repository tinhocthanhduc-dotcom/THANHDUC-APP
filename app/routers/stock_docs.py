from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import stock_docs as svc
from .. import stock_posting
from ..api_read import require_web_read
from ..db import get_session
from ..models import User
from ..schemas import StockDocCancel, StockDocIn, StockDocOut, StockDocPatch, StockDocPost, StockKind, StockStatus
from ..security import require

router = APIRouter(prefix="/stock-docs", tags=["Phiếu kho"])


@router.get("", response_model=list[StockDocOut])
def search(
    kind: StockKind | None = None,
    warehouse: str | None = None,
    status: StockStatus | None = None,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    _: User = Depends(require_web_read('/phieu-kho')),
    session: Session = Depends(get_session),
):
    return svc.search(session, kind, warehouse, status, from_date, to_date)


@router.get("/{doc_id}", response_model=StockDocOut)
def get(doc_id: int, _: User = Depends(require_web_read('/phieu-kho')), session: Session = Depends(get_session)):
    return svc.load(session, doc_id)


@router.post("", response_model=StockDocOut, status_code=201)
def create(body: StockDocIn, user: User = Depends(require(*svc.WRITERS)), session: Session = Depends(get_session)):
    return svc.create(session, user, body)


@router.patch("/{doc_id}", response_model=StockDocOut)
def update(doc_id: int, body: StockDocPatch, user: User = Depends(require(*svc.WRITERS)), session: Session = Depends(get_session)):
    return svc.update(session, user, doc_id, body)


@router.post("/{doc_id}/cancel", response_model=StockDocOut)
def cancel(doc_id: int, body: StockDocCancel, user: User = Depends(require(*svc.WRITERS)), session: Session = Depends(get_session)):
    return svc.cancel(session, user, doc_id, body)


@router.post("/{doc_id}/post", response_model=StockDocOut)
def post(doc_id: int, body: StockDocPost, user: User = Depends(require(*svc.WRITERS)), session: Session = Depends(get_session)):
    return stock_posting.post(session, user, doc_id, body.version)


@router.post("/{doc_id}/void", response_model=StockDocOut)
def void(doc_id: int, body: StockDocCancel, user: User = Depends(require(*svc.WRITERS)), session: Session = Depends(get_session)):
    return stock_posting.void(session, user, doc_id, body.reason, body.version)
