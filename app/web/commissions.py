"""NS4: projected/locked commissions with strict sales cost redaction."""
import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import commissions as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import CommissionMonth, CommissionRule, Employee, User
from ..security import has_role
from . import checked_form, render, web_user
from .dispatch import _csv_text
from .money_input import parse_money

router=APIRouter()
KIND_LABELS={'SAN_XUAT':'Sản xuất','GIAO_DON':'Giao đơn bán','SUA_TAI_CHO':'Sửa tại chỗ','MANG_VE':'Mang về','BAO_HANH':'Bảo hành','MUC_DINH_KY':'Mực định kỳ'}


def director_user(user:User=Depends(web_user)):svc._director(user);return user


def reader_user(user:User=Depends(web_user)):
    if not has_role(user,'finance'):raise BusinessError('FORBIDDEN',status=403)
    return user


def _month(request,session,user):
    raw=request.query_params.get('thang')
    if raw:
        try:return date.fromisoformat(raw+'-01')
        except ValueError:raise BusinessError('BAD_MONTH','Tháng không hợp lệ.')
    if has_role(user,'finance') and not has_role(user,'director') and request.url.path!='/hoa-hong':
        last=session.scalar(select(func.max(CommissionMonth.month)))
        if last:return last
        if request.url.path=='/hoa-hong/thang':return None  # chưa có tháng khóa: màn trạng thái chờ, không 403
    return svc._now().astimezone(svc.TZ).date().replace(day=1)


def _render(request,template,**ctx):
    response=render(request,template,tz=svc.TZ,targets=svc.TARGETS,kinds=KIND_LABELS,**ctx)
    response.headers['Cache-Control']='private, no-store';return response


def _accounts(session):
    return list(session.execute(select(User.id,User.full_name,User.roles,Employee.code).outerjoin(Employee,Employee.user_id==User.id)
        .where(User.roles.overlap(['sales','technician','director'])).order_by(User.full_name)).mappings())


def _total(rows):return sum(r['amount'] for r in rows)


@router.get('/hoa-hong')
def mine(request:Request,user:User=Depends(web_user),session:Session=Depends(get_session)):
    month=_month(request,session,user);rows=svc.mine(session,user,month)
    return _render(request,'hoa_hong.jinja',month=month,rows=rows,total=_total(rows),locked=session.get(CommissionMonth,month))


def _board(request,session,user,month,error=None,form=None):
    raw=request.query_params.get('user_id');uid=None
    if raw:
        try:uid=int(raw)
        except ValueError:raise BusinessError('BAD_USER','Nhân viên không hợp lệ.')
    rows=svc.month_view(session,user,month,uid);summary={}
    for r in rows:
        p=summary.setdefault(r['user_id'],dict(id=r['user_id'],name=r['full_name'],amount=0,missing=False))
        p['amount']+=r['amount'];p['missing']|=r.get('missing_rule',False)
    gd=has_role(user,'director')
    return _render(request,'hoa_hong_thang.jinja',month=month,rows=rows,total=_total(rows),summary=summary.values(),
        selected=uid,locked=session.get(CommissionMonth,month),is_director=gd,accounts=_accounts(session) if gd else [],
        d=form or {},error=error.message if error else None,error_code=error.code if error else '',status=error.status if error else 200)


@router.get('/hoa-hong/thang')
def board(request:Request,user:User=Depends(reader_user),session:Session=Depends(get_session)):
    month=_month(request,session,user)
    if month is None:
        return _render(request,'hoa_hong_thang.jinja',month=svc._now().astimezone(svc.TZ).date().replace(day=1),rows=[],total=0,summary=[],selected=None,locked=None,is_director=False,accounts=[],d={},error=None,error_code='',status=200,waiting_lock=True)
    return _board(request,session,user,month)


@router.get('/hoa-hong/thang.csv')
def download(request:Request,user:User=Depends(reader_user),session:Session=Depends(get_session)):
    month=_month(request,session,user);rows=svc.month_view(session,user,month);gd=has_role(user,'director')
    output=io.StringIO();writer=csv.writer(output)
    columns=['Tháng','Nhân viên','Nguồn','Chứng từ','Tỷ lệ đã thu','Hoa hồng','Ghi chú']
    if gd:columns+=['Cơ sở','%','Cố định','Lũy kế','Đã khóa trước']
    writer.writerow(columns)
    for r in rows:
        values=[month.strftime('%Y-%m'),_csv_text(r['full_name']),r['source'],_csv_text(r['number']),r.get('paid_ratio',''),r['amount'],_csv_text(r['note'])]
        if gd:values += [r[k] for k in ('base','pct','fixed_amount','entitled','previous')]
        writer.writerow(values)
    writer.writerow(['TỔNG','','','','',_total(rows)])
    return Response(('\ufeff'+output.getvalue()).encode(),media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition':f'attachment; filename="hoa-hong-{month:%Y-%m}.csv"','Cache-Control':'private, no-store'})


@router.post('/hoa-hong/thang/khoa')
def lock(request:Request,form=Depends(checked_form),user:User=Depends(director_user),session:Session=Depends(get_session)):
    month=_month(request,session,user)
    try:svc.lock(session,user,month)
    except BusinessError as exc:return _board(request,session,user,month,exc,form)
    return RedirectResponse('/hoa-hong/thang?thang='+month.strftime('%Y-%m'),status_code=303)


@router.post('/hoa-hong/thang/dieu-chinh')
def adjust(request:Request,form=Depends(checked_form),user:User=Depends(director_user),session:Session=Depends(get_session)):
    month=_month(request,session,user)
    try:
        raw=str(form.get('amount','')).strip();negative=raw.startswith('-')
        amount=parse_money(raw[1:] if negative else raw,'Điều chỉnh',allow_zero=False)*(-1 if negative else 1)
        svc.add_adjustment(session,user,month,form.get('user_id'),amount,str(form.get('note','')))
    except BusinessError as exc:return _board(request,session,user,month,exc,form)
    return RedirectResponse('/hoa-hong/thang?thang='+month.strftime('%Y-%m'),status_code=303)


def _rules(request,session,user,error=None,form=None):
    selected=None;data=form or {}
    if not form and request.query_params.get('thay'):
        try:ident=int(request.query_params['thay'])
        except ValueError:raise BusinessError('BAD_RULE','Quy tắc không hợp lệ.')
        selected=session.get(CommissionRule,ident)
        if selected is None:raise BusinessError('NOT_FOUND',status=404)
        data={c.key:getattr(selected,c.key) for c in selected.__table__.columns}
        data.update(replaces_id=selected.id,reason='')
    return _render(request,'hoa_hong_quy_tac.jinja',rows=svc.rules(session,user),accounts=_accounts(session),d=data,
        error=error.message if error else None,error_code=error.code if error else '',status=error.status if error else 200)


@router.get('/hoa-hong/quy-tac')
def rules(request:Request,user:User=Depends(director_user),session:Session=Depends(get_session)):
    return _rules(request,session,user)


@router.post('/hoa-hong/quy-tac')
def save_rule(request:Request,form=Depends(checked_form),user:User=Depends(director_user),session:Session=Depends(get_session)):
    data={k:form.get(k,'') for k in ('target','user_id','service_kind','pct','effective_from','reason','fixed_amount','replaces_id')}
    try:
        data['fixed_amount']=parse_money(data['fixed_amount'],'Tiền cố định')
        ident=data['replaces_id']
        if ident:
            try:ident=int(ident)
            except ValueError:raise BusinessError('BAD_RULE','Quy tắc cần thay không hợp lệ.')
            svc.replace_rule(session,user,ident,data)
        else:svc.add_rule(session,user,data)
    except BusinessError as exc:return _rules(request,session,user,exc,{**dict(form),'fixed_amount':form.get('fixed_amount','')})
    return RedirectResponse('/hoa-hong/quy-tac',status_code=303)
