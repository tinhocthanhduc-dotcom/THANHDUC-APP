"""T4: chốt/mở ngày và duyệt yêu cầu điều chỉnh, CSRF dùng chung."""
from datetime import date

from fastapi import APIRouter,Depends,Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import cash_docs as docs,cash_corrections as corrections,day_closings as days
from ..db import get_session
from ..errors import BusinessError
from ..models import CashDoc,User
from ..schemas import CashCorrectionIn
from ..security import has_role
from . import checked_form,render
from .cash import _body,_error,_form
from .finance import finance_user,_version

router=APIRouter()


def _closing_version(form):
    value=str(form.get('version',''))
    if not value.isdigit():
        raise BusinessError('VERSION_REQUIRED','Phải gửi phiên bản ngày, hãy tải lại.')
    return int(value)


def _page(request,session,error=None,code='',status=200,failed_day=None,reason=''):
    return render(request,'chot_so.jinja',rows=days.recent(session,request.state.user),
        director=has_role(request.state.user,'director'),error=error,error_code=code,status=status,
        failed_day=failed_day,reason=reason,saved=request.query_params.get('da_luu')=='1' and not error)


@router.get('/chot-so')
def page(request:Request,user:User=Depends(finance_user),session:Session=Depends(get_session)):
    return _page(request,session)


@router.post('/chot-so/{day}/{action}')
def change_day(request:Request,day:date,action:str,form=Depends(checked_form),session:Session=Depends(get_session)):
    user=request.state.user
    docs.check_writer(user)
    reason=str(form.get('reason',''))
    try:
        version=_closing_version(form)
        if action=='close':days.close(session,user,day,version)
        elif action=='unlock':days.unlock(session,user,day,reason,version)
        else:raise BusinessError('NOT_FOUND','Không có thao tác này.',status=404)
    except BusinessError as exc:
        return _page(request,session,error=exc.message,code=exc.code,status=exc.status,failed_day=day,reason=reason)
    return RedirectResponse('/chot-so?da_luu=1',status_code=303)


@router.post('/thu-chi/{doc_id}/dieu-chinh')
def request_correction(request:Request,doc_id:int,form=Depends(checked_form),session:Session=Depends(get_session)):
    user=request.state.user;docs.check_writer(user)
    raw={k:str(form.get(k,'')) for k in ('version','reason','new_amount','new_fund_code','new_category_code','new_description')}
    doc=docs.load(session,user,doc_id)
    try:
        if not raw['reason'].strip():raise BusinessError('REASON_REQUIRED','Yêu cầu điều chỉnh phải có lý do.')
        amount=_body(dict(kind=doc.kind,doc_date=docs.today().isoformat(),fund_code=raw['new_fund_code'],
                          category_code=raw['new_category_code'],amount=raw['new_amount'])).amount
        body=CashCorrectionIn(**{**raw,'version':_version(form),'new_amount':amount})
        corrections.request(session,user,doc_id,body)
    except (BusinessError,ValidationError) as exc:
        data=docs.snapshot(doc)
        from . import money
        data['amount']=money(doc.amount)
        return _form(request,session,data,doc,error=_error(exc),code=getattr(exc,'code','BAD_FORM'),
                     status=getattr(exc,'status',400),correction_form=raw)
    return RedirectResponse(f'/thu-chi/{doc_id}?da_luu=1',status_code=303)


def _requests(request,session,error=None,code='',status=200,failed_id=None,reason=''):
    rows=corrections.search(session,request.state.user)
    ids={r.doc_id for r in rows}
    old={r.id:r for r in session.scalars(select(CashDoc).where(CashDoc.id.in_(ids)))}
    return render(request,'dieu_chinh.jinja',rows=rows,old=old,director=has_role(request.state.user,'director'),
                  error=error,error_code=code,status=status,failed_id=failed_id,reason=reason,
                  saved=request.query_params.get('da_luu')=='1' and not error)


@router.get('/dieu-chinh')
def requests(request:Request,user:User=Depends(finance_user),session:Session=Depends(get_session)):
    return _requests(request,session)


@router.post('/dieu-chinh/{id}/{action}')
def decide(request:Request,id:int,action:str,form=Depends(checked_form),session:Session=Depends(get_session)):
    user=request.state.user;docs.check_writer(user);reason=str(form.get('reason',''))
    try:
        version=_version(form)
        if action=='approve':corrections.approve(session,user,id,version)
        elif action=='reject':corrections.reject(session,user,id,reason,version)
        else:raise BusinessError('NOT_FOUND','Không có thao tác này.',status=404)
    except BusinessError as exc:
        return _requests(request,session,error=_error(exc),code=exc.code,status=exc.status,failed_id=id,reason=reason)
    return RedirectResponse('/dieu-chinh?da_luu=1',status_code=303)
