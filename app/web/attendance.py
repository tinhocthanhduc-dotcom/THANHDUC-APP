"""NS2: self-service punches/requests, director approvals, finance read-only board."""
import csv
import io
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter,Depends,Request
from fastapi.responses import RedirectResponse,Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import attendance as svc
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import AttendanceHoliday,AttendanceMonthLock,AttendancePunch,Employee,User
from ..security import has_role
from . import checked_form,render,web_user
from .dispatch import _csv_text

router=APIRouter()
WEEKDAYS=('T2','T3','T4','T5','T6','T7','CN')
TOTAL_LABELS=('Tổng công','Ngày đi làm','Ngày lễ','Ngày phép','Ngày không lương','Ngày không phép','Lần trễ','Phút trễ','Lần về sớm','Phút về sớm','Ngày thiếu giờ ra','Lần ngoài giờ')


def director_user(user:User=Depends(web_user)):svc.director(user);return user

def reader_user(user:User=Depends(web_user)):svc.reader(user);return user


def _month(request):
    value=request.query_params.get('thang','')
    if not value:return svc.today().replace(day=1)
    try:return date.fromisoformat(value+'-01')
    except ValueError:raise BusinessError('BAD_MONTH','Tháng không hợp lệ.')


def _render(request,template,**context):
    cfg=get_settings()
    context.update(kinds=svc.KINDS,statuses=svc.STATUSES,tz=svc.TZ,total_columns=tuple(zip(svc.TOTAL_FIELDS,TOTAL_LABELS)),
        slots=dict(sang=cfg.slot_sang.replace('-','–'),chieu=cfg.slot_chieu.replace('-','–')),grace=cfg.late_grace_minutes,weekday=lambda d:WEEKDAYS[d.weekday()],
        fmt=lambda stamp:stamp.astimezone(svc.TZ).strftime('%H:%M') if stamp else '—')
    response=render(request,template,**context);response.headers['Cache-Control']='no-store';return response


def _home(request,session,user,error=None,data=None):
    day=svc.today();employee=session.scalar(select(Employee).where(Employee.user_id==user.id));month=_month(request)
    current=svc.day_summary(session,user.id,day) if employee else None
    punched=None
    try:ident=int(request.query_params.get('punch',''))
    except ValueError:ident=None
    if ident:punched=session.scalar(select(AttendancePunch).where(AttendancePunch.id==ident,AttendancePunch.user_id==user.id))
    return _render(request,'cham_cong.jinja',today=day,month=month,employee=employee,current=current,
        eligible=bool(employee and user.active and employee.start_date<=day and (not employee.end_date or employee.end_date>=day)),
        locked=svc.is_locked(session.get(AttendanceMonthLock,day.replace(day=1))),
        days=svc.month_days(session,user.id,month) if employee else [],requests=svc.requests(session,user),
        punch_kind='OUT' if current and current['missing_out'] else 'IN',punched=punched,
        d=data or {},error=error.message if error else None,error_code=error.code if error else '',status=error.status if error else 200)


@router.get('/cham-cong')
def home(request:Request,user:User=Depends(web_user),session:Session=Depends(get_session)):return _home(request,session,user)


@router.post('/cham-cong/bam')
def punch(request:Request,form=Depends(checked_form),user:User=Depends(web_user),session:Session=Depends(get_session)):
    try:row=svc.punch(session,user,str(form.get('kind','')),lat=form.get('lat'),lng=form.get('lng'),accuracy=form.get('accuracy'))
    except BusinessError as exc:return _home(request,session,user,exc)
    return RedirectResponse('/cham-cong?'+urlencode({'punch':row.id}),status_code=303)


@router.post('/cham-cong/yeu-cau')
def create_request(request:Request,form=Depends(checked_form),user:User=Depends(web_user),session:Session=Depends(get_session)):
    data={k:str(form.get(k,'')) for k in ('kind','punch_at','from_date','to_date','part','reason')}
    try:svc.request(session,user,**data)
    except BusinessError as exc:return _home(request,session,user,exc,data)
    return RedirectResponse('/cham-cong#yeu-cau',status_code=303)


@router.post('/cham-cong/yeu-cau/{ident}/huy')
def cancel(request:Request,ident:int,form=Depends(checked_form),user:User=Depends(web_user),session:Session=Depends(get_session)):
    svc.cancel(session,user,ident,form.get('version'))
    return RedirectResponse('/cham-cong#yeu-cau',status_code=303)


@router.get('/cham-cong/duyet')
def queue(request:Request,user:User=Depends(director_user),session:Session=Depends(get_session)):
    return _render(request,'cham_cong_duyet.jinja',rows=svc.requests(session,user,True))


@router.post('/cham-cong/duyet/{ident}/{action}')
def decide(request:Request,ident:int,action:str,form=Depends(checked_form),user:User=Depends(director_user),session:Session=Depends(get_session)):
    try:
        if action=='duyet':svc.approve(session,user,ident,form.get('version'),str(form.get('note','')))
        elif action=='tu-choi':svc.reject(session,user,ident,form.get('version'),str(form.get('note','')))
        else:raise BusinessError('NOT_FOUND','Thao tác không tồn tại.',status=404)
    except BusinessError as exc:return _render(request,'cham_cong_duyet.jinja',rows=svc.requests(session,user,True),error=exc.message,error_code=exc.code,status=exc.status)
    return RedirectResponse('/cham-cong/duyet#cc-'+str(ident),status_code=303)


@router.get('/cham-cong/bang')
def board(request:Request,user:User=Depends(reader_user),session:Session=Depends(get_session)):
    month=_month(request)
    is_director=has_role(user,'director')
    return _render(request,'cham_cong_bang.jinja',month=month,rows=svc.board(session,user,month),
        dates=list(svc._days(month,svc.month_end(month))),locked=svc.is_locked(session.get(AttendanceMonthLock,month)),
        lock=session.get(AttendanceMonthLock,month),is_director=is_director,today=svc.today(),
        pending=svc.pending_count(session,user) if is_director else 0)


@router.get('/cham-cong/bang.csv')
def download(request:Request,user:User=Depends(reader_user),session:Session=Depends(get_session)):
    month=_month(request);rows=svc.board(session,user,month);output=io.StringIO();writer=csv.writer(output)
    writer.writerow(['Mã NV','Họ tên',*[d.isoformat() for d in svc._days(month,svc.month_end(month))],*TOTAL_LABELS])
    for r in rows:writer.writerow([_csv_text(r['code']),_csv_text(r['full_name']),*[d['symbol'] for d in r['days']],*[r['total'][k] for k in svc.TOTAL_FIELDS]])
    return Response(('\ufeff'+output.getvalue()).encode('utf-8'),media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition':f'attachment; filename="cham-cong-{month:%Y-%m}.csv"','Cache-Control':'private, no-store'})


@router.get('/cham-cong/ngay')
def day(request:Request,user_id:int,ngay:date,user:User=Depends(web_user),session:Session=Depends(get_session)):
    if user.id!=user_id:svc.reader(user)
    account=session.execute(select(User.id,User.full_name).where(User.id==user_id)).mappings().first()
    if not account:raise BusinessError('NOT_FOUND','Không tìm thấy nhân viên.',status=404)
    return _render(request,'cham_cong_ngay.jinja',person=account,r=svc.day_summary(session,user_id,ngay))


@router.post('/cham-cong/bang/{action}')
def locking(request:Request,action:str,form=Depends(checked_form),user:User=Depends(director_user),session:Session=Depends(get_session)):
    month=_month(request)
    if action=='khoa':svc.lock_month(session,user,month)
    elif action=='mo-khoa':svc.unlock_month(session,user,month,str(form.get('reason','')))
    else:raise BusinessError('NOT_FOUND','Thao tác không tồn tại.',status=404)
    return RedirectResponse('/cham-cong/bang?thang='+month.strftime('%Y-%m'),status_code=303)


@router.get('/cham-cong/ngay-le')
def holidays(request:Request,user:User=Depends(director_user),session:Session=Depends(get_session)):
    return _render(request,'cham_cong_ngay_le.jinja',rows=list(session.scalars(select(AttendanceHoliday).order_by(AttendanceHoliday.day.desc()))))


@router.post('/cham-cong/ngay-le/{action}')
def holiday_save(request:Request,action:str,form=Depends(checked_form),user:User=Depends(director_user),session:Session=Depends(get_session)):
    if action=='them':svc.set_holiday(session,user,str(form.get('day','')),str(form.get('name','')))
    elif action=='xoa':svc.remove_holiday(session,user,str(form.get('day','')))
    else:raise BusinessError('NOT_FOUND','Thao tác không tồn tại.',status=404)
    return RedirectResponse('/cham-cong/ngay-le',status_code=303)
