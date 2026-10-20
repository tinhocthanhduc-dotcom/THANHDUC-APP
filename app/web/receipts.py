"""B4: form báo thu và hàng chờ kế toán, dùng chung lõi với API."""
import re
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import sales_orders, sales_receipts as svc, service_tickets
from ..db import get_session
from ..errors import BusinessError
from ..models import CashDoc, Customer, SalesOrder, SalesReceipt, ServiceTicket, User
from ..schemas import SalesReceiptReport
from ..security import has_role
from . import checked_form, money, render, web_user

from . import next_steps

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
        if exc.code=='MAPPING_REQUIRED':
            return 'Giám đốc cần ghép quỹ và danh mục ở trang Ghép tài chính',exc.code,exc.status
        if exc.code=='NEGATIVE_CASH':
            d=exc.details
            return f"Quỹ {d['fund']} không đủ số dư: hiện có {money(d['available'])} đồng, số cần {money(d['requested'])} đồng.",exc.code,exc.status
        return exc.message,exc.code,exc.status
    field=exc.errors()[0]['loc'][-1]
    label={'amount':'Số tiền','received_at':'Ngày thu','method':'Cách thu','note':'Ghi chú'}.get(field,'Dữ liệu')
    return label+' chưa hợp lệ, hãy kiểm tra lại.','BAD_FORM',400


def _collection_context(session,user,parent,collection,origin,can_report,reportable,form,decision,failed_id,failed_action):
    available=collection['balance'].get('available',collection['balance']['remaining']-collection['balance']['pending']) if collection else 0
    ids={v for r in collection['receipts'] for v in (r.reported_by,r.decided_by) if v} if collection else set()
    people=dict(session.execute(select(User.id,User.full_name).where(User.id.in_(ids))).all())
    cash_ids={r.cash_doc_id for r in collection['receipts'] if r.cash_doc_id} if collection else set()
    prefix='/dich-vu' if origin=='ticket' else '/don-ban'
    return dict(collection=collection,receipt_next=next_steps.receipt,receipt_report_label=next_steps.ticket_report_label(user,parent) if origin=='ticket' else 'Báo thu',receipt_people=people,receipt_data=form if form is not None else dict(version=parent.version if parent else 0,
                method='TIEN_MAT',amount=money(available),received_at=svc._today().isoformat(),note=''),
                receipt_methods=METHODS,receipt_statuses=STATUSES,can_report=can_report,
                can_decide=has_role(user,'finance'),can_void=has_role(user,'director'),available=available,
                receipt_decision=decision or {},failed_receipt_id=failed_id,failed_receipt_action=failed_action,
                origin=origin,receipt_filters={},receipt_reportable=reportable,
                can_view_receipt_receivables=has_role(user,'dispatcher','finance') if origin=='ticket' else has_role(user,'sales','finance'),
                receipt_report_url=f'{prefix}/{parent.id}/bao-thu' if parent else '',
                receipt_receivables_url=prefix+'/can-thu',
                receipt_cash_docs=dict(session.execute(select(CashDoc.id,CashDoc.number).where(CashDoc.id.in_(cash_ids))).all()))


def order_context(session,user,order,form=None,decision=None,failed_id=None,failed_action=''):
    collection=svc.for_order(session,user,order.id) if order and order.status!='NHAP' else None
    return _collection_context(session,user,order,collection,'order',has_role(user,'sales'),
        bool(order and order.status in ('DA_CHOT','GIAO_MOT_PHAN','DA_GIAO')),form,decision,failed_id,failed_action)


def ticket_context(session,user,ticket,form=None,decision=None,failed_id=None,failed_action=''):
    reportable=ticket.status in ('HOAN_THANH','DA_DONG') and (ticket.receivable or 0)>0
    collection=svc.for_ticket(session,user,ticket.id) if reportable else None
    return _collection_context(session,user,ticket,collection,'ticket',next_steps.permitted(svc.check_ticket_reporter,user,ticket),
        reportable,form,decision,failed_id,failed_action)


@router.post('/don-ban/{order_id}/bao-thu')
def report(request: Request,order_id: int,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    sales_orders.check_writer(user)
    raw={key:str(form.get(key,'')) for key in ('version','method','amount','received_at','note')}
    try:
        body=_report_body(form,raw)
        svc.report(session,user,order_id,body)
    except (BusinessError,ValidationError) as exc:
        from .orders import _form
        message,code,status=_error(exc)
        return _form(request,session,order=sales_orders.load(session,user,order_id),error=message,code=code,status=status,
                     receipt_form=raw)
    return RedirectResponse(f'/don-ban/{order_id}?da_luu=1#thu-tien',status_code=303)


def _report_body(form,raw):
    amount=raw['amount'].strip()
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)',amount):
        raise BusinessError('BAD_AMOUNT','Số tiền phải là số nguyên đồng, có thể dùng dấu chấm hàng nghìn.')
    digits=amount.replace('.','').lstrip('0') or '0'
    if len(digits)>19:
        raise BusinessError('BAD_AMOUNT','Số tiền vượt giới hạn lưu trữ.')
    body=SalesReceiptReport(version=_version(form),method=raw['method'],amount=int(digits),
                           received_at=raw['received_at'] or None,note=raw['note'])
    return body


@router.post('/dich-vu/{ticket_id}/bao-thu')
def report_ticket(request: Request,ticket_id: int,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    raw={key:str(form.get(key,'')) for key in ('version','method','amount','received_at','note')}
    try:
        svc.report_for_ticket(session,user,ticket_id,_report_body(form,raw))
    except (BusinessError,ValidationError) as exc:
        if isinstance(exc,BusinessError) and exc.code=='FORBIDDEN':
            return render(request,'loi.jinja',error=exc,status=exc.status)
        from .services import _detail
        message,code,status=_error(exc)
        return _detail(request,session,user,ticket_id,error=message,error_code=code,status=status,receipt_form=raw)
    from .technician import after_post
    return after_post(request,session,request.state.user,ticket_id,f'/dich-vu/{ticket_id}?da_luu=1#thu-tien')


@router.get('/tien-toi-giu')
def held(request: Request,user: User=Depends(web_user),session: Session=Depends(get_session)):
    return render(request,'tien_toi_giu.jinja',held=svc.held_by(session,user))


def _queue(request,session,user,filters=None,error=None,code='',status=200,decision=None,failed=None,action='',selected=None,group_form=None):
    filters=filters if filters is not None else {k:request.query_params.get(k,'') for k in ('method','from','to')}
    try:
        try:
            start,end=(date.fromisoformat(filters.get(k,'')) if filters.get(k) else None for k in ('from','to'))
        except ValueError:
            raise BusinessError('BAD_DATE_RANGE','Khoảng ngày chưa hợp lệ, hãy nhập ngày theo lịch.')
        rows=[selected] if selected is not None else svc.list_pending(session,user,filters.get('method') or None,start,end)
    except BusinessError as exc:
        rows=[]
        error,code,status=_error(exc)
    sources=rows+([failed] if failed else [])
    orders={o.id:o for o in session.scalars(select(SalesOrder).where(SalesOrder.id.in_({r.order_id for r in sources if r.order_id})))}
    tickets={t.id:t for t in session.scalars(select(ServiceTicket).where(ServiceTicket.id.in_({r.ticket_id for r in sources if r.ticket_id})))}
    parents={r.id:(orders[r.order_id] if r.order_id else tickets[r.ticket_id]) for r in sources}
    receipt_sources={r.id:dict(number=parents[r.id].number,customer_code=parents[r.id].customer_code,
                              url=f"/don-ban/{r.order_id}#thu-tien" if r.order_id else f"/dich-vu/{r.ticket_id}#thu-tien") for r in sources}
    names=dict(session.execute(select(Customer.code,Customer.name).where(Customer.code.in_({p.customer_code for p in parents.values()}))).all())
    cash_groups={}
    for row in rows:
        if row.method=='TIEN_MAT':
            group=cash_groups.setdefault(row.reported_by,dict(reporter_id=row.reported_by,rows=[],eligible=[],total=0))
            group['rows'].append(row);group['total']+=row.amount
            if row.reported_by!=user.id:group['eligible'].append(row)
    totals={method:sum(row.amount for row in rows if row.method==method) for method in METHODS}
    return render(request,'bao_thu_chi_tiet.jinja' if selected else 'bao_thu_ds.jinja',cash_groups=list(cash_groups.values()),pending_totals=totals,group_form=group_form or {},rows=rows,receipt_sources=receipt_sources,names=names,receipt_filters=filters,
                  receipt_methods=METHODS,receipt_statuses=STATUSES,can_decide=True,can_void=has_role(user,'director'),
                  receipt_people=dict(session.execute(select(User.id,User.full_name).where(User.id.in_({v for r in rows for v in (r.reported_by,r.decided_by) if v}))).all()),
                  receipt_cash_docs=dict(session.execute(select(CashDoc.id,CashDoc.number).where(CashDoc.id.in_({r.cash_doc_id for r in sources if r.cash_doc_id}))).all()),
                  receipt_next=next_steps.receipt,origin='queue',error=error,error_code=code,status=status,receipt_decision=decision or {},failed=failed,
                  failed_receipt_id=failed.id if failed else None,failed_receipt_action=action,
                  saved=request.query_params.get('da_luu')=='1' and not error)


@router.get('/bao-thu')
def pending(request: Request,user: User=Depends(finance_user),session: Session=Depends(get_session)):
    if request.query_params.get('tab')=='hoan-tien':
        from .sales_returns import refund_queue
        return refund_queue(request,session,user)
    return _queue(request,session,user)


def _queue_redirect(filters,anchor=''):
    safe=anchor if re.fullmatch(r'(?:bt|cash-group)-[0-9]+',anchor or '') else 'pending-receipts'
    return RedirectResponse('/bao-thu?'+urlencode({**filters,'da_luu':'1'})+'#'+safe,status_code=303)


@router.post('/bao-thu/xac-nhan-nhom')
def confirm_group(request:Request,form=Depends(checked_form),session:Session=Depends(get_session)):
    user=request.state.user
    svc.check_finance(user)
    filters={k:str(form.get('filter_'+k,'')) for k in ('method','from','to')}
    raw=dict(form)
    try:
        identifiers=form.getlist('receipt_id')
        if not identifiers:raise BusinessError('BAD_GROUP','Nhóm chưa có báo thu.')
        items=[]
        for value in identifiers:
            value=str(value)
            if not value.isdigit() or len(value)>19 or not 0<int(value)<=9_223_372_036_854_775_807:raise BusinessError('BAD_GROUP','Mã báo thu trong nhóm chưa hợp lệ.')
            ident=int(value);version=_version({'version':form.get('version_'+value)})
            items.append((ident,version))
        reporter=str(form.get('reporter_id',''))
        if not reporter.isdigit() or len(reporter)>19 or not 0<int(reporter)<=9_223_372_036_854_775_807:raise BusinessError('BAD_GROUP','Người nộp chưa hợp lệ.')
        amount=str(form.get('counted_amount','')).strip()
        if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)',amount) or len(amount.replace('.',''))>19:
            raise BusinessError('BAD_AMOUNT','Phải nhập số tiền đã đếm bằng số nguyên đồng.')
        svc.confirm_cash_group(session,user,items,int(amount.replace('.','')),int(reporter))
    except BusinessError as exc:
        message,code,status=_error(exc)
        if exc.details.get('number') and not message.startswith(exc.details['number']+':'):
            message=exc.details['number']+': '+message
        failed=session.get(SalesReceipt,exc.details['receipt_id']) if exc.details.get('receipt_id') else None
        return _queue(request,session,user,filters,message,code,status,failed=failed,group_form=raw)
    return _queue_redirect(filters,'cash-group-'+reporter)


@router.get('/bao-thu/{receipt_id}')
def detail(request: Request,receipt_id: int,user: User=Depends(finance_user),session: Session=Depends(get_session)):
    receipt=session.get(SalesReceipt,receipt_id)
    if receipt is None:raise BusinessError('NOT_FOUND','Không tìm thấy báo thu.',status=404)
    return _queue(request,session,user,selected=receipt)


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
        if receipt.ticket_id and form.get('origin')=='ticket':
            from .services import _detail
            return _detail(request,session,user,receipt.ticket_id,error=message,error_code=code,status=status,
                           receipt_decision=dict(form),failed_receipt_id=receipt.id,failed_receipt_action=action)
        if receipt.order_id and form.get('origin')=='order':
            from .orders import _form
            return _form(request,session,order=sales_orders.load(session,user,receipt.order_id),error=message,code=code,status=status,
                         receipt_decision=dict(form),failed_receipt_id=receipt.id,failed_receipt_action=action)
        return _queue(request,session,user,filters,message,code,status,dict(form),receipt,action)
    if form.get('return_to')=='work':
        return RedirectResponse('/viec-cua-toi?xong='+('confirmed' if action=='confirm' else 'rejected' if action=='reject' else 'done'),status_code=303)
    if receipt.ticket_id and form.get('origin')=='ticket':
        return RedirectResponse(f'/dich-vu/{receipt.ticket_id}?da_luu=1#thu-tien',status_code=303)
    if receipt.order_id and form.get('origin')=='order':
        return RedirectResponse(f'/don-ban/{receipt.order_id}?da_luu=1#thu-tien',status_code=303)
    return _queue_redirect(filters,str(form.get('return_anchor','')))


@router.get('/don-ban/can-thu')
def receivables(request: Request,user: User=Depends(web_user),session: Session=Depends(get_session)):
    rows=svc.receivables(session,user)
    names=dict(session.execute(select(Customer.code,Customer.name).where(Customer.code.in_({r['order'].customer_code for r in rows}))).all())
    return render(request,'don_ban_can_thu.jinja',rows=rows,names=names)
