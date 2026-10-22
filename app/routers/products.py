from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import products as svc
from ..api_read import require_web_read
from ..db import get_session
from ..models import User
from ..schemas import ProductIn, ProductOut, ProductPatch
from ..security import require

router = APIRouter(prefix="/products", tags=["Hàng hóa"])


@router.get("", response_model=list[ProductOut])
def search(
    q: str = Query(default="", max_length=200),
    active_only: bool = True,
    _: User = Depends(require_web_read('/hang-hoa')),
    session: Session = Depends(get_session),
):
    return svc.search(session, q, active_only)


@router.get("/{code}", response_model=ProductOut)
def get(code: str, _: User = Depends(require_web_read('/hang-hoa')), session: Session = Depends(get_session)):
    return svc.load(session, code)


@router.post("", response_model=ProductOut, status_code=201)
def create(body: ProductIn, user: User = Depends(require(*svc.WRITERS)), session: Session = Depends(get_session)):
    return svc.create(session, user, body)


@router.patch("/{code}", response_model=ProductOut)
def update(code: str, body: ProductPatch, user: User = Depends(require(*svc.WRITERS)), session: Session = Depends(get_session)):
    return svc.update(session, user, code, body)
