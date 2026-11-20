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


def _vnd(n):
    return f'{n:,}'.replace(',','.')


TABS=(('chua','Chưa chốt'),('da','Đã chốt'),('tat-ca','Tất cả'))


def _in_tab(row,tab):
    return tab=='tat-ca' or (row['status']=='DA_CHOT')==(tab=='da')


def _page(request,session,error=None,code='',status=200,failed_day=None,reason='',failed_many=()):
    # Chốt sổ mới (mẫu 6jyhDqwoSRFLCrHS1bb4dS, TUẤN duyệt 20/11/2026): tab, bảng một dòng một ngày, gom ngày không phát sinh,
    # chốt nhiều ngày đủ điều kiện, khung chi tiết ngày. Luật chốt/mở khóa giữ nguyên ở day_closings.
    user=request.state.user;qp=request.query_params
    rows=days.recent(session,user);today=days._today()
    tab=qp.get('tab') if qp.get('tab') in dict(TABS) else 'chua'
    for r in rows:
        r['empty']=not (r['income'] or r['expense'] or r['drafts'] or r['docs'])
        r['eligible']=r['status']!='DA_CHOT' and not r['drafts'] and r['day']<today
        r['shown']=_in_tab(r,tab)
    picked=None
    try:
        picked=date.fromisoformat(qp.get('ngay','')) if qp.get('ngay') else None
    except ValueError:
        picked=None
    if failed_day:picked=failed_day
    if picked not in {r['day'] for r in rows}:
        picked=None
    sel=picked or next((r['day'] for r in rows if r['shown'] and not r['empty']),None) or next((r['day'] for r in rows if r['shown']),rows[0]['day'])
    open_rows=[r for r in rows if r['status']!='DA_CHOT']
    counts={'chua':len(open_rows),'da':len(rows)-len(open_rows),'tat-ca':len(rows)}
    tabs=[dict(label=label,n=counts[key],href='/chot-so'+('' if key=='chua' else f'?tab={key}'),on=key==tab) for key,label in TABS]
    draft_rows=[r for r in rows if r['drafts']]
    income=sum(r['income'] for r in rows);expense=sum(r['expense'] for r in rows)
    tiles=[dict(label='Ngày chưa chốt',n=f"{len(open_rows)} ngày",tone='cho' if open_rows else '',
                sub=(f"{sum(1 for r in open_rows if r['empty'])} ngày không phát sinh · cũ nhất {open_rows[-1]['day'].strftime('%d/%m')}" if open_rows else 'Đã chốt hết')),
           dict(label='Phiếu nháp chặn chốt',n=sum(r['drafts'] for r in rows),tone='huy' if draft_rows else '',
                sub=', '.join(r['day'].strftime('%d/%m') for r in draft_rows[:4]) if draft_rows else 'Không còn phiếu nháp'),
           dict(label='Tổng thu 30 ngày',n=_vnd(income),sub='không gồm chuyển quỹ, đầu kỳ'),
           dict(label='Tổng chi 30 ngày',n=_vnd(expense),sub='chênh lệch '+('+' if income>=expense else '−')+_vnd(abs(income-expense)))]
    return render(request,'chot_so.jinja',rows=rows,tab=tab,tabs=tabs,tiles=tiles,sel=sel,picked=bool(picked),today=today,
        detail=days.detail(session,user,sel),zero_open=qp.get('trong')=='1',
        eligible=[r for r in rows if r['eligible']],
        director=has_role(user,'director'),error=error,error_code=code,status=status,
        failed_day=failed_day,reason=reason,failed_many=failed_many,
        saved_day=_saved_day(qp) if not error else None,saved_many=qp.get('da_chot','') if not error else '')


def _saved_day(qp):
    if qp.get('da_luu')!='1':return None
    try:return date.fromisoformat(qp.get('ngay',''))
    except ValueError:return 'x'


def _back(form,day=None,**extra):
    from urllib.parse import urlencode
    q={}
    tab=str(form.get('tab',''))
    if tab in ('da','tat-ca'):q['tab']=tab
    if day:q['ngay']=day.isoformat()
    q.update(extra)
    return '/chot-so?'+urlencode(q)


@router.get('/chot-so')
def page(request:Request,user:User=Depends(finance_user),session:Session=Depends(get_session)):
    return _page(request,session)


@router.post('/chot-so/chot-nhieu')
def close_many(request:Request,form=Depends(checked_form),session:Session=Depends(get_session)):
    user=request.state.user
    docs.check_writer(user)
    items=[]
    for raw in form.getlist('day'):
        try:
            day=date.fromisoformat(str(raw));version=str(form.get(f'version_{raw}',''))
        except ValueError:
            continue
        if version.isdigit():items.append((day,int(version)))
    if not items:
        return _page(request,session,error='Chưa có ngày nào đủ điều kiện chốt.',code='NOTHING_TO_CLOSE',status=400)
    done,failed=days.close_many(session,user,items)
    if failed:
        msg=f"Đã chốt {len(done)} ngày. Chưa chốt được {len(failed)} ngày, xem danh sách bên dưới."
        return _page(request,session,error=msg,code='CLOSE_MANY_PARTIAL',status=409,failed_many=failed)
    return RedirectResponse(_back(form,da_chot=str(len(done))),status_code=303)


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
    return RedirectResponse(_back(form,day,da_luu='1'),status_code=303)


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
