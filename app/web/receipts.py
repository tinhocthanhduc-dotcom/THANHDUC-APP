"""B4: form báo thu và hàng chờ kế toán, dùng chung lõi với API."""
import re
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import sales_orders, sales_receipts as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, SalesOrder, SalesReceipt, User
from ..schemas import SalesReceiptReport
from ..security import has_role
from . import checked_form, money, render, web_user

router=APIRouter()
METHODS={'TIEN_MAT':'Tiền mặt','CHUYEN_KHOAN':'Chuyển khoản'}
STATUSES={'CHO_XAC_NHAN':'Chờ xác nhận','DA_XAC_NHAN':'Đã xác nhận','TU_CHOI':'Từ chối','DA_HUY':'Đã hủy'}


def finance_user(user: User=Depends(web_user)):
    svc.check_finance(user)
    return user


def _version(form):
    try:
        value=int(form.get('version',''))
        if value<1:
            raise ValueError
        return value
    except (ValueError,TypeError):
        raise BusinessError('VERSION_REQUIRED','Phải gửi phiên bản chứng từ, hãy tải lại trang.')


def _error(exc):
    if isinstance(exc,BusinessError):
        return exc.message,exc.code,exc.status
    field=exc.errors()[0]['loc'][-1]
    label={'amount':'Số tiền','received_at':'Ngày thu','method':'Cách thu','note':'Ghi chú'}.get(field,'Dữ liệu')
    return label+' chưa hợp lệ, hãy kiểm tra lại.','BAD_FORM',400


def order_context(session,user,order,form=None,decision=None,failed_id=None,failed_action=''):
    collection=svc.for_order(session,user,order.id) if order and order.status!='NHAP' else None
    available=collection['balance']['remaining']-collection['balance']['pending'] if collection else 0
    ids={v for r in collection['receipts'] for v in (r.reported_by,r.decided_by) if v} if collection else set()
    people=dict(session.execute(select(User.id,User.full_name).where(User.id.in_(ids))).all())
    return dict(collection=collection,receipt_people=people,receipt_data=form if form is not None else dict(version=order.version if order else 0,
                method='TIEN_MAT',amount=money(available),received_at=svc._today().isoformat(),note=''),
                receipt_methods=METHODS,receipt_statuses=STATUSES,can_report=has_role(user,'sales'),
                can_decide=has_role(user,'finance'),can_void=has_role(user,'director'),available=available,
                receipt_decision=decision or {},failed_receipt_id=failed_id,failed_receipt_action=failed_action,
                origin='order',receipt_filters={})


@router.post('/don-ban/{order_id}/bao-thu')
def report(request: Request,order_id: int,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    sales_orders.check_writer(user)
    raw={key:str(form.get(key,'')) for key in ('version','method','amount','received_at','note')}
    try:
        amount=raw['amount'].strip()
        if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)',amount):
            raise BusinessError('BAD_AMOUNT','Số tiền phải là số nguyên đồng, có thể dùng dấu chấm hàng nghìn.')
        digits=amount.replace('.','').lstrip('0') or '0'
        if len(digits)>19:
            raise BusinessError('BAD_AMOUNT','Số tiền vượt giới hạn lưu trữ.')
        body=SalesReceiptReport(version=_version(form),method=raw['method'],amount=int(digits),
                               received_at=raw['received_at'] or None,note=raw['note'])
        svc.report(session,user,order_id,body)
    except (BusinessError,ValidationError) as exc:
        from .orders import _form
        message,code,status=_error(exc)
        return _form(request,session,order=sales_orders.load(session,user,order_id),error=message,code=code,status=status,
                     receipt_form=raw)
    return RedirectResponse(f'/don-ban/{order_id}?da_luu=1#thu-tien',status_code=303)


def _queue(request,session,user,filters=None,error=None,code='',status=200,decision=None,failed=None,action=''):
    filters=filters if filters is not None else {k:request.query_params.get(k,'') for k in ('method','from','to')}
    try:
        try:
            start,end=(date.fromisoformat(filters.get(k,'')) if filters.get(k) else None for k in ('from','to'))
        except ValueError:
            raise BusinessError('BAD_DATE_RANGE','Khoảng ngày chưa hợp lệ, hãy nhập ngày theo lịch.')
        rows=svc.list_pending(session,user,filters.get('method') or None,start,end)
    except BusinessError as exc:
        rows=[]
        error,code,status=_error(exc)
    order_ids={r.order_id for r in rows}
    if failed:
        order_ids.add(failed.order_id)
    orders={o.id:o for o in session.scalars(select(SalesOrder).where(SalesOrder.id.in_(order_ids)))}
    names=dict(session.execute(select(Customer.code,Customer.name).where(Customer.code.in_({o.customer_code for o in orders.values()}))).all())
    return render(request,'bao_thu_ds.jinja',rows=rows,orders=orders,names=names,receipt_filters=filters,
                  receipt_methods=METHODS,receipt_statuses=STATUSES,can_decide=True,can_void=has_role(user,'director'),
                  receipt_people=dict(session.execute(select(User.id,User.full_name).where(User.id.in_({v for r in rows for v in (r.reported_by,r.decided_by) if v}))).all()),
                  origin='queue',error=error,error_code=code,status=status,receipt_decision=decision or {},failed=failed,
                  failed_receipt_id=failed.id if failed else None,failed_receipt_action=action,
                  saved=request.query_params.get('da_luu')=='1' and not error)


@router.get('/bao-thu')
def pending(request: Request,user: User=Depends(finance_user),session: Session=Depends(get_session)):
    return _queue(request,session,user)


@router.post('/bao-thu/{receipt_id}/{action}')
def decide(request: Request,receipt_id: int,action: str,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    if action=='void':
        if not has_role(user,'director'):
            raise BusinessError('FORBIDDEN','Chỉ giám đốc được hủy báo thu đã xác nhận.',status=403)
    else:
        svc.check_finance(user)
    filters={k:str(form.get('filter_'+k,'')) for k in ('method','from','to')}
    try:
        version=_version(form)
        if action=='confirm':
            receipt=svc.confirm(session,user,receipt_id,version)
        elif action=='reject':
            receipt=svc.reject(session,user,receipt_id,str(form.get('reason','')),version)
        elif action=='void':
            receipt=svc.void(session,user,receipt_id,str(form.get('reason','')),version)
        else:
            raise BusinessError('NOT_FOUND','Không tìm thấy thao tác báo thu.',status=404)
    except BusinessError as exc:
        receipt=session.get(SalesReceipt,receipt_id)
        if receipt is None:
            raise exc
        message,code,status=_error(exc)
        if form.get('origin')=='order':
            from .orders import _form
            return _form(request,session,order=sales_orders.load(session,user,receipt.order_id),error=message,code=code,status=status,
                         receipt_decision=dict(form),failed_receipt_id=receipt.id,failed_receipt_action=action)
        return _queue(request,session,user,filters,message,code,status,dict(form),receipt,action)
    if form.get('origin')=='order':
        return RedirectResponse(f'/don-ban/{receipt.order_id}?da_luu=1#thu-tien',status_code=303)
    return RedirectResponse('/bao-thu?'+urlencode({**filters,'da_luu':'1'}),status_code=303)


@router.get('/don-ban/can-thu')
def receivables(request: Request,user: User=Depends(web_user),session: Session=Depends(get_session)):
    rows=svc.receivables(session,user)
    names=dict(session.execute(select(Customer.code,Customer.name).where(Customer.code.in_({r['order'].customer_code for r in rows}))).all())
    return render(request,'don_ban_can_thu.jinja',rows=rows,names=names)
