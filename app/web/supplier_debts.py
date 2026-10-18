"""M3: công nợ và trả NCC, CSRF cho mọi POST."""
import re
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import cash_docs, inventory_catalogs, supplier_debts as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import CashDoc, Fund, Supplier, SupplierPayment, User
from ..schemas import SupplierOpeningDebtIn, SupplierPaymentIn
from ..security import has_role
from . import checked_form, money, render, web_user
from .cash import _error

router=APIRouter()
LABELS={'opening':'Nợ đầu kỳ','receive':'Nhận hàng','void_receive':'Hủy nhận hàng','payment':'Trả NCC','void_payment':'Hủy trả NCC'}


def debt_user(user:User=Depends(web_user)):
    svc.check_reader(user)
    return user


def _amount(value,zero=False):
    text=str(value).strip()
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)',text):
        raise BusinessError('BAD_AMOUNT','Số tiền phải là số nguyên đồng, dùng dấu chấm để ngăn hàng nghìn.')
    result=int(text.replace('.',''))
    if result<0 or (result==0 and not zero):raise BusinessError('BAD_AMOUNT','Số tiền trả phải lớn hơn 0.')
    return result


def _version(form,zero=False):
    text=str(form.get('version',''))
    if not text.isdigit() or (not zero and int(text)==0):raise BusinessError('VERSION_REQUIRED','Phải gửi phiên bản, hãy tải lại.')
    return int(text)


def _order_id(value):
    if not str(value or '').strip():return None
    if not str(value).isdigit() or int(value)<1:raise BusinessError('BAD_ORDER','Mã đơn mua không hợp lệ.')
    return int(value)


def _detail(request,session,code,error=None,error_code='',status=200,pay_form=None,opening_form=None,void_id=None,reason=''):
    supplier=session.get(Supplier,code)
    if supplier is None:raise BusinessError('NOT_FOUND','Không tìm thấy nhà cung cấp.',status=404)
    user=request.state.user;balance=svc.balance(session,user,code)
    order_id=_order_id((pay_form or {}).get('order_id',request.query_params.get('don','')))
    order=svc.order_balance(session,user,order_id) if order_id else None
    if order and order['supplier_code']!=code:raise BusinessError('BAD_ORDER','Đơn mua không thuộc nhà cung cấp này.')
    maximum=min(balance['remaining'],order['remaining']) if order else balance['remaining']
    data=pay_form if pay_form is not None else dict(fund_code='',amount=money(max(0,maximum)),paid_at=cash_docs.today().isoformat(),note='',order_id=order_id or '')
    initial=svc.opening(session,user,code)
    shown=opening_form if opening_form is not None else dict(amount=money(initial.amount if initial else 0),as_of=initial.as_of.isoformat() if initial else cash_docs.today().isoformat(),note=initial.note if initial else '',version=initial.version if initial else 0)
    history=svc.payments(session,user,code)
    return render(request,'cong_no_ncc_chi_tiet.jinja',supplier=supplier,balance=balance,order=order,pay=data,opening=shown,
        timeline=svc.timeline(session,user,code),labels=LABELS,history=history,
        cash_numbers=dict(session.execute(select(CashDoc.id,CashDoc.number).where(CashDoc.id.in_([p.cash_doc_id for p in history]))).all()),
        funds=list(session.scalars(select(Fund).where(Fund.active.is_(True)).order_by(Fund.code))),director=has_role(user,'director'),
        error=error,error_code=error_code,status=status,void_id=void_id,reason=reason,saved=request.query_params.get('da_luu')=='1' and not error)


@router.get('/cong-no-ncc')
def listing(request:Request,q:str='',user:User=Depends(debt_user),session:Session=Depends(get_session)):
    return render(request,'cong_no_ncc_ds.jinja',q=q,rows=svc.search(session,user,q[:200]),totals=svc.totals(session,user,q[:200]))


@router.get('/cong-no-ncc/ket-qua')
def results(request:Request,q:str='',user:User=Depends(debt_user),session:Session=Depends(get_session)):
    return render(request,'cong_no_ncc_dong.jinja',rows=svc.search(session,user,q[:200]),totals=svc.totals(session,user,q[:200]))


@router.get('/cong-no-ncc/tim')
def picker(request:Request,q:str='',user:User=Depends(debt_user),session:Session=Depends(get_session)):
    return render(request,'cong_no_ncc_chon.jinja',rows=inventory_catalogs.search(session,Supplier,q[:200]))


@router.get('/cong-no-ncc/{code}')
def detail(request:Request,code:str,user:User=Depends(debt_user),session:Session=Depends(get_session)):
    return _detail(request,session,code)


@router.post('/cong-no-ncc/{code}/tra')
def pay(request:Request,code:str,form=Depends(checked_form),session:Session=Depends(get_session)):
    user=request.state.user;svc.check_writer(user)
    raw={k:str(form.get(k,'')) for k in ('fund_code','amount','paid_at','order_id','note')}
    try:
        body=SupplierPaymentIn(**{**raw,'amount':_amount(raw['amount']),'order_id':_order_id(raw['order_id'])})
        svc.pay(session,user,code,**body.model_dump())
    except (BusinessError,ValidationError) as exc:
        return _detail(request,session,code,error=_error(exc),error_code=getattr(exc,'code','BAD_FORM'),status=getattr(exc,'status',400),pay_form=raw)
    query=urlencode({'da_luu':'1',**({'don':body.order_id} if body.order_id else {})})
    return RedirectResponse('/cong-no-ncc/'+quote(code,safe='')+'?'+query+'#tra-tien',status_code=303)


@router.post('/cong-no-ncc/{code}/dau-ky')
def opening(request:Request,code:str,form=Depends(checked_form),session:Session=Depends(get_session)):
    user=request.state.user;svc.check_reader(user)
    if not has_role(user,'director'):raise BusinessError('FORBIDDEN','Chỉ giám đốc được đặt nợ đầu kỳ NCC.',status=403)
    raw={k:str(form.get(k,'')) for k in ('amount','as_of','note','version')}
    try:
        body=SupplierOpeningDebtIn(**{**raw,'amount':_amount(raw['amount'],True),'version':_version(form,True)})
        svc.set_opening(session,user,code,**body.model_dump())
    except (BusinessError,ValidationError) as exc:
        return _detail(request,session,code,error=_error(exc),error_code=getattr(exc,'code','BAD_FORM'),status=getattr(exc,'status',400),opening_form=raw)
    return RedirectResponse('/cong-no-ncc/'+quote(code,safe='')+'?da_luu=1#no-dau-ky',status_code=303)


@router.post('/cong-no-ncc/tra/{payment_id}/huy')
def void(request:Request,payment_id:int,form=Depends(checked_form),session:Session=Depends(get_session)):
    user=request.state.user;svc.check_writer(user)
    row=session.get(SupplierPayment,payment_id)
    if row is None:raise BusinessError('NOT_FOUND','Không tìm thấy lần trả NCC.',status=404)
    code=row.supplier_code;reason=str(form.get('reason',''))
    try:svc.void(session,user,payment_id,reason,_version(form))
    except BusinessError as exc:
        return _detail(request,session,code,error=_error(exc),error_code=exc.code,status=exc.status,void_id=payment_id,reason=reason)
    return RedirectResponse('/cong-no-ncc/'+quote(code,safe='')+'?da_luu=1#tra-tien',status_code=303)
