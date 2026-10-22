from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import customers as svc
from ..api_read import require_web_read
from ..db import get_session
from ..models import AuditLog, User
from ..schemas import CustomerCreate, CustomerOut, CustomerPatch, CustomerResult
from ..security import require

router = APIRouter(prefix="/customers", tags=["Khách hàng"])
WRITERS = ("sales", "reception", "dispatcher", "finance")


@router.get("", response_model=list[CustomerOut])
def search(
    q: str = Query(default="", max_length=200),
    include_test: bool = True,
    _: User = Depends(require_web_read('/khach-hang')),
    session: Session = Depends(get_session),
):
    return [svc.to_out(c) for c in svc.search(session, q, include_test)]


@router.get("/{code}", response_model=CustomerOut)
def get(code: str, _: User = Depends(require_web_read('/khach-hang')), session: Session = Depends(get_session)):
    return svc.to_out(svc.load(session, code))


@router.post("", response_model=CustomerResult, status_code=201)
def create(body: CustomerCreate, user: User = Depends(require(*WRITERS)), session: Session = Depends(get_session)):
    c, warnings = svc.create(session, user, body)
    return CustomerResult(customer=svc.to_out(c), warnings=warnings)


@router.patch("/{code}", response_model=CustomerResult)
def update(code: str, body: CustomerPatch, user: User = Depends(require(*WRITERS)), session: Session = Depends(get_session)):
    c, warnings = svc.update(session, user, code, body)
    return CustomerResult(customer=svc.to_out(c), warnings=warnings)


@router.get("/{code}/history")
def history(code: str, _: User = Depends(require("finance")), session: Session = Depends(get_session)):
    rows = session.scalars(
        select(AuditLog).where(AuditLog.entity == "customer", AuditLog.entity_id == code).order_by(AuditLog.id.desc()).limit(200)
    )
    return [
        {"at": r.at, "user_id": r.user_id, "action": r.action, "before": r.before, "after": r.after, "reason": r.reason}
        for r in rows
    ]
