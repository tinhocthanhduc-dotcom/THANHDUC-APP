"""Chi phí thợ ứng (TUẤN 30/10/2026, mẫu DuHangChiPhi 2c): kế toán duyệt từng khoản, gộp theo thợ lập phiếu chi hoàn tiền."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import service_expenses as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import Attachment, Fund, User
from . import checked_form, render, web_user
from .lists import _today

router = APIRouter()
TABS = {'cho-duyet': ('CHO_DUYET', 'Chờ duyệt'), 'cho-hoan': ('DA_DUYET', 'Chờ hoàn tiền'), 'da-hoan': ('DA_HOAN', 'Đã hoàn'), 'tu-choi': ('TU_CHOI', 'Từ chối')}


def finance_user(user: User = Depends(web_user)):
    svc.check_finance(user)
    return user


def page(request, session, user, tab='cho-duyet', error=None, code='', status=200, form=None):
    tab = tab if tab in TABS else 'cho-duyet'
    rows = svc.listing(session, user, TABS[tab][0])
    photos = {}
    for a in session.scalars(select(Attachment).where(Attachment.entity == 'service_expense', Attachment.entity_id.in_([str(r['row'].id) for r in rows]),
                                                      Attachment.hidden.is_(False)).order_by(Attachment.id)):
        photos.setdefault(int(a.entity_id), a.id)
    groups = []
    if tab == 'cho-hoan':  # gộp theo thợ: một phiếu chi cho nhiều khoản
        for r in rows:
            if not groups or groups[-1]['technician_id'] != r['row'].technician_id:
                groups.append(dict(technician_id=r['row'].technician_id, technician=r['technician'], rows=[]))
            groups[-1]['rows'].append(r)
    counts = svc.counts(session)
    funds = list(session.scalars(select(Fund).where(Fund.active.is_(True)).order_by(Fund.code)))
    return render(request, 'chi_phi_tho.jinja', tab=tab, tabs=[dict(label=v[1], n=counts.get(v[0], 0), href='/chi-phi-tho?tab=' + k, on=k == tab) for k, v in TABS.items()],
                  rows=rows, groups=groups, photos=photos, kinds=svc.KINDS, funds=funds, pay_day=_today().isoformat(),
                  total=sum(r['row'].amount for r in rows), error=error, error_code=code, status=status, d=form or {})


@router.get('/chi-phi-tho')
def expenses(request: Request, tab: str = 'cho-duyet', user: User = Depends(finance_user), session: Session = Depends(get_session)):
    return page(request, session, user, tab)


@router.post('/chi-phi-tho/{ident}/{action}')
def decide(request: Request, ident: int, action: str, form=Depends(checked_form), user: User = Depends(finance_user), session: Session = Depends(get_session)):
    from .services import _version
    if action not in ('duyet', 'tu-choi'):
        raise BusinessError('NOT_FOUND', 'Không có thao tác này.', status=404)
    try:
        svc.decide(session, user, ident, _version(form), action == 'duyet', str(form.get('note', '')))
    except BusinessError as exc:
        return page(request, session, user, 'cho-duyet', exc.message, exc.code, exc.status, dict(form))
    return RedirectResponse('/chi-phi-tho?tab=cho-duyet', status_code=303)


@router.post('/chi-phi-tho/hoan-tien')
def pay(request: Request, form=Depends(checked_form), user: User = Depends(finance_user), session: Session = Depends(get_session)):
    try:
        doc = svc.pay(session, user, form.getlist('expense_ids'), str(form.get('fund_code', '')), str(form.get('pay_date', '')))
    except BusinessError as exc:
        return page(request, session, user, 'cho-hoan', exc.message, exc.code, exc.status, dict(form))
    return RedirectResponse(f'/thu-chi/{doc.id}', status_code=303)
