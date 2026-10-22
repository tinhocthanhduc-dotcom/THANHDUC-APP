from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import inventory_catalogs as svc
from ..api_read import require_web_read
from ..db import get_session
from ..models import Supplier, User
from ..schemas import SupplierIn, SupplierOut, SupplierPatch
from ..security import require

router = APIRouter(prefix="/suppliers", tags=["Nhà cung cấp"])


@router.get("", response_model=list[SupplierOut])
def search(q: str = Query(default="", max_length=200), _: User = Depends(require_web_read('/nha-cung-cap')), session: Session = Depends(get_session)):
    return svc.search(session, Supplier, q)


@router.get("/{code}", response_model=SupplierOut)
def get(code: str, _: User = Depends(require_web_read('/nha-cung-cap')), session: Session = Depends(get_session)):
    return svc.load(session, Supplier, code)


@router.post("", response_model=SupplierOut, status_code=201)
def create(body: SupplierIn, user: User = Depends(require(*svc.WRITERS)), session: Session = Depends(get_session)):
    return svc.create(session, user, Supplier, body)


@router.patch("/{code}", response_model=SupplierOut)
def update(code: str, body: SupplierPatch, user: User = Depends(require(*svc.WRITERS)), session: Session = Depends(get_session)):
    return svc.update(session, user, Supplier, code, body)
