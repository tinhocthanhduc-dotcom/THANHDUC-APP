"""S4: màn hình giao đơn riêng; không mở menu đơn bán cho thợ."""
import re
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import attachments, sales_orders, sales_receipts, technician_deliveries as svc
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, SalesOrder, ServiceTicket, User
from ..schemas import SalesDeliveryAssign, SalesDeliveryMove, SalesDeliveryFailed, TechnicianDeliveryIn
from ..security import has_role
from . import checked_form, render, web_user
from .orders import _error, _version
from .receipts import _report_body

router=APIRouter()
REASONS={'KHACH_VANG':'Khách vắng','KHACH_TU_CHOI':'Khách từ chối','SAI_DIA_CHI':'Sai địa chỉ','HEN_LAI':'Hẹn lại','KHAC':'Khác'}


def context(session,user,order):
    state=svc.state(session,user,order)
    state.update(order=order,reason_labels=REASONS,customer=session.get(Customer,order.customer_code),
        technicians=list(session.scalars(select(User).where(User.active.is_(True),User.roles.contains(['technician'])).order_by(User.full_name))) if has_role(user,'dispatcher') else [],
        service_ticket=session.get(ServiceTicket,order.service_ticket_id) if order.service_ticket_id else None,
        remaining=sales_orders.remaining_lines(session,order),balance=sales_receipts.balance(order),
        is_assignee=order.delivery_assignee_id==user.id and has_role(user,'technician'),
        can_assign=has_role(user,'dispatcher'),can_move=has_role(user,'warehouse'),can_use=has_role(user,'warehouse','dispatcher'))
    return state


def page(request,session,user,order,raw=None,error=None,code='',status=200):
    shipments=sales_orders.deliveries(session,order.id)
    photos={gh.id:attachments.list_for(session,user,'sales_delivery',str(gh.id)) for gh in shipments}
    return render(request,'giao_don.jinja',technician_view=('technician' in user.roles and order.delivery_assignee_id==user.id and (request.query_params.get('return_to')=='tho' or getattr(request.state,'technician_return',False))),**context(session,user,order),shipments=shipments,photos=photos,
        raw=raw or {},error=error,error_code=code,status=status)


@router.get('/giao-don/{order_id}')
def view(request: Request,order_id: int,user: User=Depends(web_user),session: Session=Depends(get_session)):
    return page(request,session,user,svc.load(session,user,order_id))


@router.post('/giao-don/{order_id}/{action}')
async def action(request: Request,order_id: int,action: str,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    order=svc.load(session,user,order_id)
    raw={k:str(v) for k,v in form.items() if k!='photos'}
    for key in ('amount','method','received_at','note'):
        raw.setdefault(key,'')
    try:
        version=_version(form)
        lines=[dict(order_line_no=int(k[4:]),qty=str(v).replace(',','.')) for k,v in form.items()
            if re.fullmatch(r'qty_[0-9]+',k) and str(v).strip() and str(v).strip() not in ('0','0,0','0.0')]
        if action=='assign':
            svc.assign(session,user,order_id,SalesDeliveryAssign(version=version,assignee_id=form.get('assignee_id'),service_ticket_id=form.get('service_ticket_id') or None))
        elif action in ('transfer','use','return'):
            svc.move(session,user,order_id,action,SalesDeliveryMove(version=version,lines=lines))
        elif action=='failed':
            svc.failed(session,user,order_id,SalesDeliveryFailed(version=version,reason=form.get('reason'),note=form.get('note',''),promised_date=form.get('promised_date') or None))
        elif action=='report':
            sales_receipts.report(session,user,order_id,_report_body(form,raw))
        elif action=='deliver':
            receipt=_report_body(form,raw) if str(form.get('amount','')).strip() else None
            body=TechnicianDeliveryIn(version=version,lines=lines,receiver_name=form.get('receiver_name',''),note=form.get('note',''),receipt=receipt)
            uploads=[p for p in form.getlist('photos') if getattr(p,'filename',None)]
            if len(uploads)>10:raise BusinessError('FILE_TOO_LARGE','Mỗi lần giao tối đa 10 ảnh.')
            limit=get_settings().max_upload_mb*1024*1024
            data=[(p.filename,await p.read(limit+1)) for p in uploads]
            svc.deliver(session,user,order_id,body,data)
        else:
            raise BusinessError('NOT_FOUND','Không tìm thấy thao tác.',status=404)
    except (BusinessError,ValidationError) as exc:
        session.rollback()
        message,code,status=_error(exc)
        return page(request,session,user,svc.load(session,user,order_id),raw,message,code,status)
    if getattr(request.state,'technician_return',False):
        return RedirectResponse('/tho',status_code=303)
    return RedirectResponse(f'/giao-don/{order_id}?da_luu=1',status_code=303)
