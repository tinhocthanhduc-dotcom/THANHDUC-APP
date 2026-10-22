"""S4: màn hình giao đơn riêng; không mở menu đơn bán cho thợ."""
import re
from datetime import timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import attachments, attendance, sales_orders, sales_receipts, technician_deliveries as svc
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
REASONS={'KHACH_VANG':'Khách vắng','KHACH_TU_CHOI':'Khách từ chối','SAI_DIA_CHI':'Sai địa chỉ','HEN_LAI':'Hẹn lại','KHAC':'Khác','DE_NGHI_HUY':'Thợ đề nghị hủy đơn'}
# Màn thợ chỉ còn 3 lựa chọn (TUẤN 2026-10-23); các mã cũ vẫn nhận để giữ lịch sử và màn bàn giấy.
THO_REASONS={'HEN_LAI':'Khách hẹn lại','SAI_DIA_CHI':'Sai địa chỉ giao','DE_NGHI_HUY':'Hủy đơn'}
SLOTS={'SANG':'buổi sáng','CHIEU':'buổi chiều','TOI':'buổi tối'}


def context(session,user,order):
    state=svc.state(session,user,order)
    d0=attendance.today()
    state.update(order=order,reason_labels=REASONS,tho_reasons=THO_REASONS,slots=SLOTS,
        quick_dates=[('Hôm nay',d0),('Ngày mai',d0+timedelta(days=1)),('Ngày kia',d0+timedelta(days=2))],customer=session.get(Customer,order.customer_code),
        technicians=list(session.scalars(select(User).where(User.active.is_(True),User.roles.contains(['technician'])).order_by(User.full_name))) if has_role(user,'dispatcher') else [],
        service_ticket=session.get(ServiceTicket,order.service_ticket_id) if order.service_ticket_id else None,
        open_tickets=list(session.scalars(select(ServiceTicket).where(ServiceTicket.customer_code==order.customer_code,
            ServiceTicket.status.in_(('MOI','DA_GIAO','DA_NHAN','DA_HEN','DANG_LAM','CHO_LINH_KIEN','TAM_DUNG'))).order_by(ServiceTicket.id.desc()))) if has_role(user,'dispatcher') else [],
        remaining=sales_orders.remaining_lines(session,order),balance=sales_receipts.balance(order),
        is_assignee=order.delivery_assignee_id==user.id and has_role(user,'technician'),
        can_assign=has_role(user,'dispatcher'),can_move=has_role(user,'warehouse'),can_use=has_role(user,'warehouse','dispatcher'))
    return state


def tho_lines(order,ctx):
    # UX5c: thợ giao từng dòng; số mặc định = min(còn giao, đang giữ cho dòng), giá đã gồm VAT để tự tính tiền cần thu.
    rows=[]
    for line in order.lines:
        remaining=ctx['remaining'][line.line_no]['remaining']
        held=sum((r.qty for r in ctx['held'] if r.order_line_id==line.line_no),Decimal(0))
        if remaining<=0:continue
        gross=(line.amount+line.vat_amount)/line.qty if line.qty else 0
        rows.append(dict(line_no=line.line_no,code=line.product_code or '',name=line.description,unit=line.unit,remaining=remaining,held=held,
            qty=min(remaining,held),unit_gross=float(gross)))
    return rows


def page(request,session,user,order,raw=None,error=None,code='',status=200):
    shipments=sales_orders.deliveries(session,order.id)
    photos={gh.id:attachments.list_for(session,user,'sales_delivery',str(gh.id)) for gh in shipments}
    technician_view=('technician' in user.roles and order.delivery_assignee_id==user.id and (request.query_params.get('return_to')=='tho' or getattr(request.state,'technician_return',False)))
    ctx=context(session,user,order)
    if technician_view and ctx['is_assignee']:
        from .technician import contact
        lines=tho_lines(order,ctx)
        available=ctx['balance']['available']
        full=all(row['qty']>=row['remaining'] for row in lines)
        suggested=available if full else min(available,round(sum(float(row['qty'])*row['unit_gross'] for row in lines)))
        st=ctx['service_ticket']
        trip_ticket=st if st is not None and st.assignee_id==user.id and st.status in ('DA_GIAO','DA_NHAN','DA_HEN','DANG_LAM','CHO_LINH_KIEN','TAM_DUNG') else None
        return render(request,'tho_giao_don.jinja',technician_view=True,**ctx,shipments=shipments,photos=photos,lines=lines,suggested_amount=suggested,trip_ticket=trip_ticket,
            raw=raw or {},error=error,error_code=code,status=status,**contact(ctx['customer'],address=order.delivery_address))
    return render(request,'giao_don.jinja',technician_view=technician_view,**ctx,shipments=shipments,photos=photos,
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
            choice=str(form.get('service_ticket_id') or '').strip()
            svc.assign(session,user,order_id,SalesDeliveryAssign(version=version,assignee_id=form.get('assignee_id'),
                service_ticket_id=choice if choice not in ('','NEW') else None,
                new_service_symptom=str(form.get('new_service_symptom','')).strip() if choice=='NEW' else None))
        elif action in ('transfer','use','return'):
            svc.move(session,user,order_id,action,SalesDeliveryMove(version=version,lines=lines))
        elif action=='failed':
            reason=form.get('reason');note=str(form.get('note','')).strip()
            slot=str(form.get('slot','')).strip() if reason=='HEN_LAI' else ''
            if slot in SLOTS:note='Hẹn '+SLOTS[slot]+('; '+note if note else '')
            svc.failed(session,user,order_id,SalesDeliveryFailed(version=version,reason=reason,note=note,
                promised_date=form.get('promised_date') or None,new_address=str(form.get('new_address','')).strip()))
        elif action=='report':
            sales_receipts.report(session,user,order_id,_report_body(form,raw))
        elif action=='deliver':
            # UX5c: thợ chọn Chưa thu thì bỏ qua số tiền điền sẵn.
            receipt=_report_body(form,raw) if str(form.get('amount','')).strip() and form.get('collect')!='NO' else None
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
