"""NS5: director drafting, finance payment, private slips and advance workflow."""
import csv
import io
from datetime import date

from fastapi import APIRouter,Depends,Request
from fastapi.responses import RedirectResponse,Response
from sqlalchemy import select,func
from sqlalchemy.orm import Session

from .. import payroll as svc,attendance,employees,cash_docs
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import User,Fund,PayrollMonth,AttendanceMonthLock,CommissionMonth,Employee
from ..security import has_role
from . import web_user,checked_form,render
from .money_input import parse_money
from .dispatch import _csv_text

router=APIRouter()


def director_user(user:User=Depends(web_user)):svc.director(user);return user

def reader_user(user:User=Depends(web_user)):svc.reader(user);return user


def _month(request,session=None,latest=False):
    raw=request.query_params.get('thang')
    if raw:
        try:return date.fromisoformat(raw+'-01')
        except ValueError:raise BusinessError('BAD_MONTH','Tháng không hợp lệ.')
    if latest:
        last=session.scalar(select(func.max(PayrollMonth.month)))
        if last:return last
    return svc._now().astimezone(svc.TZ).date().replace(day=1)


def _funds(session):return list(session.scalars(select(Fund).where(Fund.active.is_(True)).order_by(Fund.code)))


def _render(request,template,**ctx):
    response=render(request,template,labels=svc.LABELS,statuses=svc.STATUSES,tz=svc.TZ,**ctx)
    response.headers['Cache-Control']='private, no-store';return response


def _board(request,session,user,error=None,data=None):
    month=_month(request);rows=svc.draft(session,user,month)
    return _render(request,'luong.jinja',month=month,rows=rows,total=sum(r['net'] for r in rows),
        approved=session.get(PayrollMonth,month),attendance_locked=attendance.is_locked(session.get(AttendanceMonthLock,month)),
        commission_required=month>=attendance.month_day(get_settings().commission_start),commission_locked=session.get(CommissionMonth,month),
        d=data or {},error=error.message if error else None,error_code=error.code if error else '',status=error.status if error else 200)


@router.get('/luong')
def board(request:Request,user:User=Depends(director_user),session:Session=Depends(get_session)):return _board(request,session,user)


@router.post('/luong/luu')
def save(request:Request,form=Depends(checked_form),user:User=Depends(director_user),session:Session=Depends(get_session)):
    month=_month(request)
    try:
        rows=[]
        for ident in form.getlist('employee_id'):
            values={k:parse_money(form.get(k+':'+ident),'Mục nhập') for k in svc.FIELDS}
            rows.append(dict(employee_id=ident,version=form.get('version:'+ident),note=form.get('note:'+ident,''),**values))
        svc.save_inputs(session,user,month,rows)
    except BusinessError as exc:return _board(request,session,user,exc,form)
    return RedirectResponse('/luong?thang='+month.strftime('%Y-%m'),status_code=303)


@router.post('/luong/duyet')
def approve(request:Request,form=Depends(checked_form),user:User=Depends(director_user),session:Session=Depends(get_session)):
    try:svc.approve(session,user,_month(request))
    except BusinessError as exc:return _board(request,session,user,exc,form)
    return RedirectResponse('/luong?thang='+_month(request).strftime('%Y-%m'),status_code=303)


def _csv(rows,month):
    output=io.StringIO();writer=csv.writer(output);keys=tuple(svc.LABELS)
    writer.writerow(['Tháng','Mã NV','Nhân viên',*[svc.LABELS[k] for k in keys],'Ghi chú','Phiếu chi'])
    for r in rows:writer.writerow([r['month'].strftime('%Y-%m'),_csv_text(r['code']),_csv_text(r['full_name']),*[r[k] for k in keys],_csv_text(r['note']),r.get('cash_number') or ''])
    writer.writerow(['TỔNG','','',*['' if k!='net' else sum(r['net'] for r in rows) for k in keys]])
    return Response(('\ufeff'+output.getvalue()).encode(),media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition':f'attachment; filename="luong-{month:%Y-%m}.csv"','Cache-Control':'private, no-store'})


@router.get('/luong.csv')
def download(request:Request,user:User=Depends(director_user),session:Session=Depends(get_session)):
    month=_month(request);return _csv(svc.draft(session,user,month),month)


def _payments(request,session,user,error=None,data=None):
    month=_month(request,session,True);rows=svc.approved(session,user,month)
    return _render(request,'luong_chi.jinja',month=month,rows=rows,total=sum(r['net'] for r in rows),
        months=list(session.scalars(select(PayrollMonth.month).order_by(PayrollMonth.month.desc()))),funds=_funds(session),
        d=data or {},pay_day=cash_docs.today(),error=error.message if error else None,error_code=error.code if error else '',status=error.status if error else 200)


@router.get('/luong/chi')
def payments(request:Request,user:User=Depends(reader_user),session:Session=Depends(get_session)):return _payments(request,session,user)


@router.get('/luong/chi.csv')
def payment_csv(request:Request,user:User=Depends(reader_user),session:Session=Depends(get_session)):
    month=_month(request,session,True);return _csv(svc.approved(session,user,month),month)


@router.post('/luong/chi')
def pay(request:Request,form=Depends(checked_form),user:User=Depends(reader_user),session:Session=Depends(get_session)):
    try:svc.pay(session,user,form.getlist('line_ids'),str(form.get('fund_code','')),str(form.get('pay_date','')))
    except BusinessError as exc:return _payments(request,session,user,exc,form)
    return RedirectResponse('/luong/chi?thang='+_month(request,session,True).strftime('%Y-%m'),status_code=303)


@router.get('/phieu-luong')
def mine(request:Request,user:User=Depends(web_user),session:Session=Depends(get_session)):
    return _render(request,'phieu_luong.jinja',rows=svc.my_payslips(session,user))


@router.get('/phieu-luong/{ident}')
def slip(request:Request,ident:int,user:User=Depends(web_user),session:Session=Depends(get_session)):
    row=svc.payslip(session,user,ident)
    from ..models import SalaryAdvance
    advances=list(session.scalars(select(SalaryAdvance).where(SalaryAdvance.payroll_line_id==ident).order_by(SalaryAdvance.id)))
    return _render(request,'phieu_luong_chi_tiet.jinja',r=row,advances=advances)


def _advances(request,session,user,error=None,data=None):
    rows=svc.advances(session,user);employee=session.scalar(select(Employee).where(Employee.user_id==user.id))
    today=svc._now().astimezone(svc.TZ).date()
    eligible=bool(employee and employee.start_date<=today and (not employee.end_date or employee.end_date>=today) and employees.salary_on(session,employee.id,today))
    return _render(request,'tam_ung.jinja',rows=rows,can_request=eligible,is_director=has_role(user,'director'),is_finance=has_role(user,'finance'),
        max_pct=get_settings().advance_max_pct,funds=_funds(session) if has_role(user,'finance') else [],pay_day=cash_docs.today(),
        d=data or {},error=error.message if error else None,error_code=error.code if error else '',status=error.status if error else 200)


@router.get('/tam-ung')
def advances(request:Request,user:User=Depends(web_user),session:Session=Depends(get_session)):return _advances(request,session,user)


@router.post('/tam-ung')
def request_advance(request:Request,form=Depends(checked_form),user:User=Depends(web_user),session:Session=Depends(get_session)):
    try:svc.request_advance(session,user,parse_money(form.get('amount'),'Tạm ứng',False),str(form.get('reason','')))
    except BusinessError as exc:return _advances(request,session,user,exc,form)
    return RedirectResponse('/tam-ung',status_code=303)


@router.post('/tam-ung/{ident}/{action}')
def advance_action(request:Request,ident:int,action:str,form=Depends(checked_form),user:User=Depends(web_user),session:Session=Depends(get_session)):
    try:
        if action=='huy':svc.cancel_advance(session,user,ident,form.get('version'))
        elif action in ('duyet','tu-choi'):svc.decide_advance(session,user,ident,form.get('version'),action=='duyet',str(form.get('note','')))
        elif action=='chi':svc.pay_advance(session,user,ident,str(form.get('fund_code','')),str(form.get('pay_date','')),form.get('version'))
        else:raise BusinessError('NOT_FOUND',status=404)
    except BusinessError as exc:return _advances(request,session,user,exc,form)
    return RedirectResponse('/tam-ung#tu-'+str(ident),status_code=303)
