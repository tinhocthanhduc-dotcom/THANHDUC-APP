"""Màn công việc và hoàn thành gộp cho kỹ thuật viên."""
import re
from datetime import datetime, time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import attachments, service_tickets, technician_stock
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, CustomerSite, Equipment, Product, SalesOrder, ServiceTicket, User
from . import checked_form, next_steps, render, web_user

router = APIRouter()


def technician_user(user: User = Depends(web_user)):
    if 'technician' not in user.roles:
        raise BusinessError('FORBIDDEN','Chỉ kỹ thuật viên được vào màn thợ.',status=403)
    return user


def owned(session, user, ticket_id):
    technician_user(user)
    ticket = session.get(ServiceTicket,ticket_id)
    if ticket is None: raise BusinessError('NOT_FOUND','Không tìm thấy phiếu.',status=404)
    if ticket.assignee_id != user.id:
        raise BusinessError('FORBIDDEN','Phiếu không được giao cho bạn.',status=403)
    return ticket


def after_post(request, session, user, ticket_id, fallback):
    if getattr(request.state,'technician_return',False):
        ticket = session.get(ServiceTicket,ticket_id)
        if ticket is None or ticket.assignee_id!=user.id: return RedirectResponse('/tho',status_code=303)
        target = f'/tho/phieu/{ticket.id}?da_luu=1' if ticket.status in service_tickets.OPEN or next_steps.service(session,user,ticket) else '/tho'
        return RedirectResponse(target,status_code=303)
    return RedirectResponse(fallback,status_code=303)


def contact(customer, site=None, address=None):
    location = (address if address is not None else (site.address if site else '') or customer.address).strip()
    phones = sorted(customer.phones,key=lambda p:(not p.is_contact,p.phone))
    number = (site.contact_phone if site else '') or (phones[0].phone if phones else '')
    number = re.sub(r'[^+0-9]','',number)
    return dict(address=location,phone=number,maps='https://www.google.com/maps/search/?'+urlencode({'api':1,'query':location}) if location else '')


def detail_context(session, user, ticket, ctx):
    owned(session,user,ticket.id)
    warehouse = technician_stock.own_warehouse(session,user)
    ctx['charge_warehouses'] = [warehouse] if warehouse else []
    ctx['charge_default_warehouse'] = warehouse.code if warehouse else ''
    ctx.update(can_assign=False,can_hide=False,can_relink=False,can_decide=False,can_void=False,can_view_receipt_receivables=False)
    eq = session.get(Equipment,ticket.serial) if ticket.serial else None
    product = session.get(Product,eq.product_code) if eq else None
    checks = [dict(key='photo',label='Có ảnh sau sửa',ok=attachments.has_visible(session,'service_ticket',ticket.id,'SAU')),
        dict(key='billing',label='Chọn cách thu và lý do nếu miễn phí',ok=ctx['charges']['billing'] in ('THU_TIEN','MIEN_PHI') and (ctx['charges']['billing']!='MIEN_PHI' or len(ctx['charges']['free_reason'].strip())>=3))]
    if ticket.kind=='MANG_VE' or (eq and eq.custody_location!='TAI_KHACH'):
        checks.append(dict(key='machine',label='Máy đã trả khách',ok=not eq or (eq.custody_location=='TAI_KHACH' and not ctx.get('custody_pending'))))
    checks.append(dict(key='warehouses',label='Vật tư đã chọn kho của bạn',ok=all(warehouse and line.get('warehouse_code')==warehouse.code for line in ctx['charges']['lines'] if line['kind']=='VAT_TU')))
    target = {'DA_GIAO':'DA_NHAN','DA_NHAN':'DA_HEN','DA_HEN':'DANG_LAM','DANG_LAM':'HOAN_THANH',
              'CHO_LINH_KIEN':'DANG_LAM','TAM_DUNG':'DANG_LAM'}.get(ticket.status)
    labels={'DA_GIAO':'Nhận đơn','DA_NHAN':'Hẹn khách','DA_HEN':'Đã đến khách','DANG_LAM':'Hoàn thành','CHO_LINH_KIEN':'Tiếp tục làm','TAM_DUNG':'Tiếp tục làm'}
    primary = dict(form='service-complete' if ticket.status=='DANG_LAM' else 'service-'+target,label=labels[ticket.status]) if target else (ctx.get('next_step') or {}).get('primary')
    exceptions=[s for s in ctx['transitions'] if s!=target and s not in ('DA_HUY','DA_DONG')]
    stage = {'DA_GIAO':0,'DA_NHAN':1,'DA_HEN':2,'DANG_LAM':2,'CHO_LINH_KIEN':2,'TAM_DUNG':2}.get(ticket.status,3)
    return dict(technician_view=True,checks=checks,completion_ready=all(c['ok'] for c in checks),
        tech_primary=primary,current_target=target,exception_targets=exceptions,step_index=stage,
        machine_name=product.name if product else '',**contact(ctx['customer'],ctx['site']))


def _schedule(appointment,status,now):
    local = appointment.astimezone(service_tickets.service_sla.TZ) if appointment else None
    if status=='DA_HEN' and local and local.date()<=now.astimezone(service_tickets.service_sla.TZ).date(): return (0,local)
    if status in ('DANG_LAM','CHO_LINH_KIEN','TAM_DUNG'):return (1,local or now)
    return (2 if local else 3,local or now)


@router.get('/tho')
def today(request:Request,user:User=Depends(technician_user),session:Session=Depends(get_session)):
    now=service_tickets._now(); jobs=[]
    rows=session.execute(select(ServiceTicket,Customer,CustomerSite,Product.name).join(Customer,Customer.code==ServiceTicket.customer_code)
        .outerjoin(CustomerSite,CustomerSite.id==ServiceTicket.site_id).outerjoin(Equipment,Equipment.serial==ServiceTicket.serial)
        .outerjoin(Product,Product.code==Equipment.product_code).where(ServiceTicket.assignee_id==user.id,ServiceTicket.status.in_(service_tickets.OPEN))).all()
    for ticket,customer,site,machine in rows:
        action=next_steps.service(session,user,ticket)
        jobs.append(dict(kind='service',id=ticket.id,number=ticket.number,priority=ticket.priority,customer=customer.name,
            machine=machine or '',serial=ticket.serial or '',symptom=ticket.symptom,appointment=ticket.appointment_at,
            overdue=(ticket.status=='DA_HEN' and ticket.appointment_at is not None and ticket.appointment_at<now) or (ticket.due_at is not None and ticket.due_at<now),
            href=f'/tho/phieu/{ticket.id}',action=action['primary'] if action else None,
            sort=_schedule(ticket.appointment_at,ticket.status,now),**contact(customer,site)))
    orders=session.scalars(select(SalesOrder).where(SalesOrder.delivery_assignee_id==user.id,SalesOrder.fulfillment=='THO_GIAO',SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN'))))
    for order in orders:
        customer=session.get(Customer,order.customer_code)
        appointment=datetime.combine(order.promised_date,time.min,service_tickets.service_sla.TZ) if order.promised_date else None
        action=next_steps.delivery(session,user,order)
        jobs.append(dict(kind='delivery',id=order.id,number=order.number,priority='',customer=customer.name,machine='',serial='',
            symptom='Giao đơn bán',appointment=appointment,overdue=bool(order.promised_date and order.promised_date<now.astimezone(service_tickets.service_sla.TZ).date()),
            href=f'/giao-don/{order.id}?return_to=tho',action=action['primary'],sort=_schedule(appointment,'DA_HEN',now),**contact(customer,address=order.delivery_address)))
    jobs.sort(key=lambda row:(row['sort'],row['kind'],row['id']))
    return render(request,'tho_hom_nay.jinja',jobs=jobs,today=now.astimezone(service_tickets.service_sla.TZ).date())


@router.get('/tho/phieu/{ticket_id}')
def ticket(request:Request,ticket_id:int,user:User=Depends(technician_user),session:Session=Depends(get_session)):
    from .services import _detail
    owned(session,user,ticket_id)
    return _detail(request,session,user,ticket_id,technician_view=True)


@router.post('/tho/phieu/{ticket_id}/hoan-thanh')
def complete(request: Request, ticket_id: int, form=Depends(checked_form),
             user: User = Depends(technician_user), session: Session = Depends(get_session)):
    from pydantic import ValidationError
    from .. import service_completion
    from ..schemas import SalesReceiptReport, ServiceTransition
    from .charges import _raw, _body
    from .services import _detail, _message, _version
    owned(session, user, ticket_id)
    request.state.technician_return = True
    raw = _raw(form)
    shown = {key: str(form.get(key, '')) for key in ('note', 'amount', 'method')}
    try:
        version = _version(form)
        charges = _body(raw, version)
        note = ServiceTransition(to_status='HOAN_THANH', version=version, note=shown['note']).note
        amount = shown['amount'].strip()
        if amount and not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)', amount):
            raise BusinessError('BAD_AMOUNT', 'Số tiền phải là số nguyên đồng, có thể dùng dấu chấm hàng nghìn.')
        digits = amount.replace('.', '').lstrip('0') or '0'
        if len(digits) > 19:
            raise BusinessError('BAD_AMOUNT', 'Số tiền vượt giới hạn lưu trữ.')
        receipt = SalesReceiptReport(version=version, method=shown['method'], amount=int(digits), note=note) if int(digits) else None
        service_completion.complete(session, user, ticket_id, charges, note, receipt)
    except (BusinessError, ValidationError) as exc:
        message, code, status = _message(exc)
        return _detail(request, session, user, ticket_id, error=message, error_code=code, status=status,
                       charge_shown=raw, completion_form=shown, technician_view=True)
    return after_post(request, session, user, ticket_id, '/tho')
