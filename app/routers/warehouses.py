from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import inventory_catalogs as svc
from ..db import get_session
from ..models import Warehouse, User
from ..schemas import WarehouseIn, WarehouseOut, WarehousePatch
from ..security import current_user, require

router = APIRouter(prefix="/warehouses", tags=["Kho"])


@router.get("", response_model=list[WarehouseOut])
def search(q: str = Query(default="", max_length=200), _: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.search(session, Warehouse, q)


@router.get("/{code}", response_model=WarehouseOut)
def get(code: str, _: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.load(session, Warehouse, code)


@router.post("", response_model=WarehouseOut, status_code=201)
def create(body: WarehouseIn, user: User = Depends(require(*svc.WRITERS)), session: Session = Depends(get_session)):
    return svc.create(session, user, Warehouse, body)


@router.patch("/{code}", response_model=WarehouseOut)
def update(code: str, body: WarehousePatch, user: User = Depends(require(*svc.WRITERS)), session: Session = Depends(get_session)):
    return svc.update(session, user, Warehouse, code, body)
