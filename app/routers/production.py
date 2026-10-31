from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from .. import production as svc
from ..db import get_session
from ..models import User
from ..schemas import ProductionIn, ProductionVersion, ProductionAssign, ProductionComplete, ProductionMaterials, ProductionReceive, ProductionVoid
from ..security import current_user

router = APIRouter(prefix='/production-orders', tags=['Sản xuất'])


@router.get('')
def listing(status: str | None = None, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return [svc.public(user, row) for row in session.scalars(svc.search(session, user, status))]


@router.post('', status_code=201)
def create(body: ProductionIn, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.public(user, svc.create(session, user, body))


@router.get('/{number}')
def detail(number: str, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.public(user, svc.load(session, user, number))


@router.post('/{number}/giao')
def assign(number: str, body: ProductionAssign, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.public(user, svc.assign(session, user, number, body.technician_id, body.version))


@router.post('/{number}/bat-dau')
def start(number: str, body: ProductionVersion, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.public(user, svc.start(session, user, number, body.version))


@router.post('/{number}/hoan-thanh')
def complete(number: str, body: ProductionComplete, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.public(user, svc.complete(session, user, number, body))


@router.post('/{number}/bo-sung-vat-tu')
def add_materials(number: str, body: ProductionMaterials, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.public(user, svc.add_materials(session, user, number, body))


@router.post('/{number}/nhan-kho')
def receive(number: str, body: ProductionReceive, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.public(user, svc.receive(session, user, number, body))


@router.post('/{number}/huy')
def void(number: str, body: ProductionVoid, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.public(user, svc.void(session, user, number, body.reason, body.version))
