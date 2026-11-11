"""Quản trị › Hạn mức duyệt (TUẤN duyệt mẫu 11/11/2026). Chỉ giám đốc xem và sửa."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import approval_limits as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import User
from . import checked_form, render, web_user

router = APIRouter()


def _page(request, session, user, values=None, error=None, saved=False, status=200):
    svc.director(user)
    return render(request, 'han_muc_duyet.jinja', limits=values or svc.limits(session), labels=svc.LABELS,
                  leave_picks=svc.LEAVE_PICKS, saved=saved, error=error.message if error else None,
                  error_code=error.code if error else '', status=status)


@router.get('/han-muc-duyet')
def page(request: Request, luu: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _page(request, session, user, saved=luu == '1')


@router.post('/han-muc-duyet')
def save(request: Request, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    try:
        svc.save(session, user, {k: form.get(k, '') for k in svc.KEYS})
    except BusinessError as exc:
        session.rollback()
        if exc.status == 403:
            raise
        values = {k: str(form.get(k, '')) for k in svc.KEYS}
        return _page(request, session, user, values, exc, status=exc.status)
    return RedirectResponse('/han-muc-duyet?luu=1', status_code=303)
