"""NS3: self cards, director matrix/targets, and authorized drill-down."""
import csv
import io
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from .. import kpi as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import User
from . import checked_form, render, web_user
from .dispatch import _csv_text
from .money_input import parse_money

router=APIRouter()


def director_user(user:User=Depends(web_user)):
    svc.director(user);return user


def _month(request):
    value=request.query_params.get('thang')
    try:return svc.period(date.fromisoformat(value+'-01') if value else svc._now().astimezone(svc.TZ).date())[0]
    except (ValueError,TypeError):raise BusinessError('BAD_MONTH','Tháng không hợp lệ.')


def _render(request,name,**ctx):
    response=render(request,name,groups=svc.GROUPS,metrics=svc.METRICS,tz=svc.TZ,
                    achievement=svc.achievement,**ctx)
    response.headers['Cache-Control']='private, no-store'
    return response


def _cells(values,targets,uid,previous=None):
    result={}
    for key,actual in values.items():
        metric=svc.BY_KEY[key];target=svc.target_for(targets,uid,key);pct=svc.achievement(actual,target,key)
        prev=(previous or {}).get(key)
        delta=actual-prev if actual is not None and prev is not None else None
        result[key]=dict(metric=metric,actual=actual,target=target,pct=pct,
                         color='none' if pct is None else 'good' if pct>=100 else 'warn' if pct>=70 else 'bad',
                         arrow='→' if delta==0 else '↑' if delta and delta>0 else '↓' if delta and delta<0 else '',
                         improved=None if not delta else (delta>0)==metric['higher'],prev=prev)
    return result


@router.get('/kpi')
def mine(request:Request,user:User=Depends(web_user),session:Session=Depends(get_session)):
    month=_month(request)
    actual=svc.my_kpi(session,user,month);prior=svc.my_kpi(session,user,svc.previous(month))
    return _render(request,'kpi.jinja',month=month,cells=_cells(actual,svc.targets(session,month),user.id,prior),
                   person_id=user.id,cutoff=svc.period(month)[3].astimezone(svc.TZ))


@router.get('/kpi/tong')
def board(request:Request,user:User=Depends(director_user),session:Session=Depends(get_session)):
    month=_month(request);values=svc.board(session,user,month);targets=svc.targets(session,month)
    return _render(request,'kpi_tong.jinja',month=month,people=svc.people(session,month),
                   cells={uid:_cells(row,targets,uid) for uid,row in values.items()})


@router.get('/kpi/tong.csv')
def download(request:Request,user:User=Depends(director_user),session:Session=Depends(get_session)):
    month=_month(request);values=svc.board(session,user,month);targets=svc.targets(session,month)
    output=io.StringIO();writer=csv.writer(output)
    writer.writerow(['Tháng','Mã NV','Họ tên','Nhóm','Chỉ số','Đơn vị','Thực tế','Mục tiêu','% đạt'])
    for person in svc.people(session,month):
        for key,c in _cells(values[person['id']],targets,person['id']).items():
            writer.writerow([month.strftime('%Y-%m'),_csv_text(person['code']),_csv_text(person['full_name']),
                svc.GROUPS[c['metric']['group']],c['metric']['label'],c['metric']['unit'],
                *['' if v is None else v for v in (c['actual'],c['target'],c['pct'])]])
    return Response(('\ufeff'+output.getvalue()).encode(),media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition':f'attachment; filename="kpi-{month:%Y-%m}.csv"','Cache-Control':'private, no-store'})


@router.get('/kpi/chi-tiet')
def detail(request:Request,user_id:int,metric_key:str,user:User=Depends(web_user),session:Session=Depends(get_session)):
    month=_month(request);rows=svc.detail(session,user,month,user_id,metric_key)
    return _render(request,'kpi_chi_tiet.jinja',month=month,rows=rows,metric=svc.BY_KEY[metric_key],
                   person=svc.people(session,month,[user_id])[0],own=user.id==user_id)


def _targets(request,session,month,error=None,form=None):
    return _render(request,'kpi_muc_tieu.jinja',month=month,people=svc.people(session,month),
                   targets=svc.targets(session,month),d=form or {},error=error.message if error else None,
                   error_code=error.code if error else '',status=error.status if error else 200)


@router.get('/kpi/muc-tieu')
def targets(request:Request,user:User=Depends(director_user),session:Session=Depends(get_session)):
    return _targets(request,session,_month(request))


@router.post('/kpi/muc-tieu')
def save(request:Request,form=Depends(checked_form),user:User=Depends(director_user),session:Session=Depends(get_session)):
    month=_month(request)
    try:
        if form.get('action')=='copy':svc.save_targets(session,user,month,[],copy=True)
        else:
            changes=[]
            for name,raw in form.items():
                if not name.startswith('target:') or not str(raw).strip():continue
                try:_,uid,key=name.split(':');uid=None if uid=='common' else int(uid)
                except (ValueError,TypeError):raise BusinessError('BAD_TARGET','Ô mục tiêu không hợp lệ.')
                metric=svc.BY_KEY.get(key)
                if not metric:raise BusinessError('BAD_TARGET','Chỉ số không hợp lệ.')
                value=parse_money(raw,'Mục tiêu') if metric['unit']=='đồng' else raw
                changes.append(dict(user_id=uid,metric_key=key,target=value,version=form.get(name.replace('target:','version:',1))))
            svc.save_targets(session,user,month,changes)
    except BusinessError as exc:return _targets(request,session,month,exc,form)
    return RedirectResponse('/kpi/muc-tieu?thang='+month.strftime('%Y-%m'),status_code=303)
