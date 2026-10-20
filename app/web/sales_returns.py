"""S5: form trả hàng, in TH và duyệt HT; tất cả POST qua CSRF/form_key."""
import re
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import sales_orders, sales_receipts, sales_returns as svc, sales_refunds
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, SalesOrder, Warehouse
from ..schemas import SalesReturnIn, SalesRefundIn
from ..security import has_role
from . import checked_form, render, web_user
from .orders import _version, _error, _deposit

router=APIRouter()
REFUND_STATUSES={'CHO_DUYET':'Chờ duyệt','DA_CHI':'Đã chi','TU_CHOI':'Từ chối'}


def order_context(session,user,order,raw=None):
    returned=svc.for_order(session,user,order.id)
    refunds=sales_refunds.for_order(session,user,order.id)
    balance=sales_receipts.balance(order)
    available=max(0,balance['advance']-sum(r.amount for r in refunds if r.status=='CHO_DUYET'))
    return dict(returns=returned,refunds=refunds,available=available,refunded=balance['refunded'],
        can_return=has_role(user,'sales','warehouse') and order.status in ('GIAO_MOT_PHAN','DA_GIAO'),
        can_refund=has_role(user,'sales'),statuses=REFUND_STATUSES,raw=raw or {})


def _form(request,session,user,order,raw=None,error=None,code='',status=200):
    rows=[]
    for gh in sales_orders.deliveries(session,order.id):
        if gh.status!='DA_GIAO':continue
        for piece in gh.lines:
            original=next(l for l in order.lines if l.line_no==piece.order_line_no)
            rows.append(dict(gh=gh,line=original,remaining=piece.qty-svc.returned(session,gh.id,piece.order_line_no)[0],key=f'{gh.id}_{piece.order_line_no}'))
    return render(request,'tra_hang_form.jinja',o=order,rows=rows,raw=raw or {},
        warehouses=list(session.scalars(select(Warehouse).where(Warehouse.active.is_(True),Warehouse.kind.in_(('KHO','LOI'))).order_by(Warehouse.code))),
        error=error,error_code=code,status=status)


@router.get('/don-ban/{order_id}/tra-hang')
def form(request: Request,order_id: int,user=Depends(web_user),session: Session=Depends(get_session)):
    svc._writer(user)
    order=sales_orders.load(session,user,order_id)
    sales_orders._status(order,('GIAO_MOT_PHAN','DA_GIAO'))
    return _form(request,session,user,order)


@router.post('/don-ban/{order_id}/tra-hang')
def create(request: Request,order_id: int,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user;svc._writer(user)
    order=sales_orders.load(session,user,order_id);raw=dict(form)
    try:
        parts=[]
        for key,value in form.items():
            match=re.fullmatch(r'qty_([0-9]+)_([0-9]+)',key)
            if not match or not str(value).strip() or str(value).strip()=='0':continue
            suffix=f'{match[1]}_{match[2]}'
            condition=str(form.get('condition_'+suffix,'TOT'))
            warehouse=form.get(('defect_' if condition=='LOI' else 'warehouse_')+suffix) or None
            parts.append(dict(delivery_id=int(match[1]),order_line_no=int(match[2]),qty=str(value).replace(',','.'),condition=condition,warehouse_code=warehouse))
        body=SalesReturnIn(version=_version(form),reason=str(form.get('reason','')),lines=parts)
        row=svc.create(session,user,order_id,body)
    except (BusinessError,ValidationError) as exc:
        session.rollback();message,code,status=_error(exc)
        return _form(request,session,user,sales_orders.load(session,user,order_id),raw,message,code,status)
    return RedirectResponse(f'/tra-hang/{row.id}',status_code=303)


@router.get('/tra-hang/{return_id}')
def detail(request: Request,return_id: int,user=Depends(web_user),session: Session=Depends(get_session)):
    row=svc.load(session,user,return_id)
    return _detail(request,session,user,row)


def _detail(request,session,user,row,error=None,code='',status=200,raw=None,printable=False):
    order=sales_orders.load(session,user,row.order_id)
    return render(request,'tra_hang_in.jinja' if printable else 'tra_hang_chi_tiet.jinja',r=row,o=order,
        lines={l.line_no:l for l in order.lines},customer=session.get(Customer,order.customer_code),company=get_settings(),
        can_void=has_role(user,'director') and row.status=='DA_NHAN',error=error,error_code=code,status=status,raw=raw or {})


@router.get('/tra-hang/{return_id}/in')
def print_return(request: Request,return_id: int,user=Depends(web_user),session: Session=Depends(get_session)):
    return _detail(request,session,user,svc.load(session,user,return_id),printable=True)


@router.post('/tra-hang/{return_id}/void')
def void(request: Request,return_id: int,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    try:
        svc.void(session,user,return_id,_version(form),str(form.get('reason','')))
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _detail(request,session,user,svc.load(session,user,return_id),message,code,status,dict(form))
    return RedirectResponse(f'/tra-hang/{return_id}',status_code=303)


@router.post('/don-ban/{order_id}/hoan-tien')
def request_refund(request: Request,order_id: int,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    try:
        body=SalesRefundIn(version=_version(form),amount=_deposit(form.get('amount','')),method=str(form.get('method','')),reason=str(form.get('reason','')))
        sales_refunds.request(session,user,order_id,body)
    except (BusinessError,ValidationError) as exc:
        from .orders import _form as order_form
        message,code,status=_error(exc)
        return order_form(request,session,order=sales_orders.load(session,user,order_id),error=message,code=code,status=status,refund_form=dict(form))
    return RedirectResponse(f'/don-ban/{order_id}?da_luu=1#hoan-tien',status_code=303)


def refund_queue(request,session,user,error=None,code='',status=200,raw=None):
    rows=sales_refunds.pending(session,user)
    orders={o.id:o for o in session.scalars(select(SalesOrder).where(SalesOrder.id.in_({r.order_id for r in rows})))}
    return render(request,'hoan_tien_ds.jinja',rows=rows,orders=orders,error=error,error_code=code,status=status,raw=raw or {})


@router.post('/hoan-tien/{refund_id}/{action}')
def decide(request: Request,refund_id: int,action: str,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    try:
        if action not in ('approve','reject'):raise BusinessError('NOT_FOUND','Không tìm thấy thao tác.',status=404)
        sales_refunds.decide(session,user,refund_id,_version(form),action=='approve',str(form.get('reason','')))
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return refund_queue(request,session,user,message,code,status,dict(form))
    return RedirectResponse('/bao-thu?tab=hoan-tien&da_luu=1',status_code=303)


@router.get('/hoan-tien')
def refunds_alias(user=Depends(web_user)):
    sales_receipts.check_finance(user)
    return RedirectResponse('/bao-thu?tab=hoan-tien',status_code=303)
