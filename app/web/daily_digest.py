"""R3: xem và lưu bản tóm tắt ngày, dùng CSRF chung của web."""
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import alerts, daily_digest as svc, dashboard
from ..db import get_session
from ..models import User
from . import checked_form, render, web_user

router = APIRouter()


@router.get('/tong-quan/ngay')
def listing(request: Request, page: int = Query(1, ge=1), user: User = Depends(web_user),
            session: Session = Depends(get_session)):
    rows, total = svc.listing(session, user, page)
    return render(request, 'tom_tat_ngay_list.jinja', rows=rows, page=page, total=total)


@router.post('/tong-quan/ngay')
def save(form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    row = svc.save(session, user, dashboard._now().date())
    return RedirectResponse('/tong-quan/ngay/' + row.day.isoformat(), status_code=303)


@router.get('/tong-quan/ngay/{day}')
def detail(request: Request, day: date, user: User = Depends(web_user), session: Session = Depends(get_session)):
    row = svc.load(session, user, day)
    return render(request, 'tom_tat_ngay.jinja', digest=row, s=row['data'], alert_labels=alerts.LABELS)
