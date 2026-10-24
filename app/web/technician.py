"""Màn công việc và hoàn thành gộp cho kỹ thuật viên."""
import re
from collections import defaultdict
from decimal import Decimal
from datetime import datetime, time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import attachments, attendance, my_work, service_appointments, service_tickets, technician_stock, work_watch
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, CustomerSite, Employee, Equipment, Product, SalesOrder, ServiceTicket, User
from . import checked_form, next_steps, render, watch_response, web_user

router = APIRouter()

# Báo cáo sửa chữa gọn: thợ chọn kết quả, lỗi gặp và việc đã làm bằng nút; server ghép thành chữ cho phiếu.
REPAIR_RESULTS={'XONG':'Đã sửa xong','MOT_PHAN':'Xong một phần'}
FAULT_CHIPS=('Kẹt giấy','Lỗi nguồn','Hết mực','Mòn trục ép','Hỏng drum','Lỗi kết nối','Bản in mờ, sọc')
WORK_CHIPS=('Vệ sinh máy','Thay drum','Thay trục ép','Nạp mực','Thay gạt mực','Cài lại driver','Chỉnh cài đặt')


def report_fields(form):
    """Trả (lỗi gặp, cách xử lý, dữ liệu vẽ lại form). Form cũ gửi thẳng fault_found/fix_done vẫn nhận."""
    result=str(form.get('repair_result','')).strip()
    faults=[v for v in form.getlist('fault') if v in FAULT_CHIPS]
    work=[v for v in form.getlist('work') if v in WORK_CHIPS]
    extra=str(form.get('fix_extra','')).strip()
    shown=dict(repair_result=result,fault=faults,work=work,fix_extra=extra)
    if not result and not faults and not work and not extra:
        return str(form.get('fault_found','')),str(form.get('fix_done','')),shown
    if result not in REPAIR_RESULTS:return ', '.join(faults),'',shown  # lõi báo FIX_REQUIRED, form giữ lựa chọn
    done=', '.join(work+([extra] if extra else []))
    return ', '.join(faults),REPAIR_RESULTS[result]+(': '+done if done else ''),shown


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
    from .. import technician_work
    from ..config import get_settings,DEFAULT_VAT_RATE
    from ..models import StockBalance
    shown=ctx.get('completion_form') or {}
    fee=sum(line.amount+line.vat_amount for line in ticket.lines)
    if ctx.get('charge_unsaved'):
        from decimal import InvalidOperation
        from ..service_charges import _round
        try:
            fee=sum(_round(Decimal(str(line['qty']).replace(',','.'))*int(str(line['unit_price']).replace('.','')))+_round(Decimal(_round(Decimal(str(line['qty']).replace(',','.'))*int(str(line['unit_price']).replace('.',''))))*int(line['vat_rate'])/100) for line in ctx['charges']['lines'])
        except (ValueError,InvalidOperation,KeyError):pass
    mode=shown.get('collection_mode','FREE' if ticket.billing=='MIEN_PHI' or (ticket.billing is None and ticket.warranty_basis in ('PHIEU_TRUOC','MAY_CON_HAN')) else '')
    if ctx.get('charge_unsaved') and ctx['charges']['billing']=='MIEN_PHI':mode='FREE'
    amount=shown.get('customer_amount',str(fee) if fee else '')
    reason=shown.get('free_reason',(ctx['charges'].get('free_reason','') if ctx.get('charge_unsaved') else '') or ticket.free_reason or ('Bảo hành '+ticket.warranty_basis if ticket.warranty_basis in ('PHIEU_TRUOC','MAY_CON_HAN') else ''))
    try:actual=sum(technician_work.net_from_gross(technician_work.money(amount)))
    except BusinessError:actual=0
    held=list(session.execute(select(Product,StockBalance.qty).join(StockBalance,StockBalance.product_code==Product.code).where(StockBalance.warehouse_code==warehouse.code if warehouse else False,StockBalance.qty>0,Product.active.is_(True)).order_by(Product.code)))
    selected={line.product_code:str(line.qty) for line in ticket.lines if line.kind=='VAT_TU' and line.stock_doc_id is None and warehouse and line.warehouse_code==warehouse.code}
    if 'materials' in shown:selected={line['product_code']:line['qty'] for line in shown['materials']}
    if 'materials' in ctx.get('workshop_form',{}):
        selected={line['product_code']:line['qty'] for line in ctx['workshop_form']['materials']}
    from . import quantity
    for code,value in selected.items():
        try:selected[code]=quantity(str(value).replace(',','.'))
        except (ValueError,ArithmeticError):pass
    # UX5a: chỉ hiện sẵn mã đã cấp cho phiếu này hoặc đã chọn; mã khác thợ tìm thêm.
    from ..models import PartRequest
    issued=defaultdict(Decimal)
    for code,qty in session.execute(select(PartRequest.product_code,PartRequest.qty).where(PartRequest.ticket_id==ticket.id,PartRequest.status=='DA_CAP',PartRequest.to_warehouse_code==warehouse.code if warehouse else False)):issued[code]+=qty
    def chosen(value):
        try:return Decimal(str(value).replace(',','.') or '0')>0
        except ArithmeticError:return True
    materials=[dict(code=p.code,name=p.name,held=qty,qty=selected.get(p.code,'0'),issued=issued.get(p.code,0),extra=not issued.get(p.code) and not chosen(selected.get(p.code,'0'))) for p,qty in held]
    materials.sort(key=lambda item:(item['extra'],item['code']))
    ctx.update(tech_money=dict(customer_amount=amount,actual=actual,mode=mode,reason=reason,partial=shown.get('partial_amount',shown.get('amount','')),method=shown.get('method','TIEN_MAT')),tech_materials=materials,fee_suggestions=list(session.scalars(select(Product).where(Product.active.is_(True),Product.group_name==get_settings().service_fee_group).order_by(Product.code))),tech_vat=DEFAULT_VAT_RATE,transfer_technicians=list(session.scalars(select(User).where(User.active.is_(True),User.roles.contains(['technician']),User.id!=user.id).order_by(User.full_name))),can_tech_transfer=ticket.status in ('DA_GIAO','DA_NHAN','DA_HEN'))
    from .. import service_charges
    return_job=service_charges.return_locked(ticket)
    machine_held=bool(eq and eq.custody_ticket_id==ticket.id and eq.custody_location in ('THO_GIU','KHO_KY_THUAT'))
    workshop_repair=bool(ticket.status=='DANG_LAM' and machine_held and not return_job)
    can_bring=bool(ticket.status=='DANG_LAM' and ticket.kind in (None,'SUA_TAI_CHO','BAO_HANH','MANG_VE') and not return_job and
        (not eq or (eq.custody_location=='TAI_KHACH' and not ctx.get('custody_pending'))))
    billing_ready=(mode=='FREE' and len(reason.strip())>=3) or (mode!='FREE' and actual>0)
    if workshop_repair:billing_ready=billing_ready and mode in ('FULL','FREE')
    # Đơn giao cùng chuyến còn phải thu: để thợ nhập "Khách trả tổng" rồi app tự chia (đơn hàng trước, dư vào phiếu).
    from .. import sales_receipts
    trips=[]
    for order in session.scalars(select(SalesOrder).where(SalesOrder.service_ticket_id==ticket.id,SalesOrder.delivery_assignee_id==user.id,
            SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN','DA_GIAO'))).order_by(SalesOrder.id)):
        due=sales_receipts.balance(order)['available']
        if due>0:trips.append(dict(id=order.id,number=order.number,due=due,delivered=order.status=='DA_GIAO',href=f'/giao-don/{order.id}?return_to=tho'))
    ctx['trip_orders']=trips
    from ..models import ServiceDraft
    draft=session.get(ServiceDraft,(ticket.id,user.id)) if ticket.status=='DANG_LAM' and not ctx.get('completion_form') else None
    ctx['tech_draft']=draft.data if draft else None
    # Kết quả đã chọn (form vừa gửi) hoặc đã lưu trên phiếu (chữ bắt đầu bằng nhãn kết quả).
    result=shown.get('repair_result') or next((k for k,v in REPAIR_RESULTS.items() if (ticket.fix_done or '').startswith(v)),'')
    shown=dict(shown,repair_result=result)
    ctx['completion_form']=dict(ctx.get('completion_form') or {},repair_result=result)
    report_ok=result in REPAIR_RESULTS
    ctx.update(repair_results=REPAIR_RESULTS,fault_chips=FAULT_CHIPS,work_chips=WORK_CHIPS)
    checks = [dict(key='kind',label='Đã chọn loại dịch vụ',ok=ticket.kind is not None),
        dict(key='report',label='Đã chọn kết quả sửa',ok=report_ok),
        dict(key='photo' ,label='Có ảnh nghiệm thu',ok=attachments.has_visible(session,'service_ticket',ticket.id,'SAU')),
        dict(key='billing',label='Đã nhập tiền thu khách hoặc chọn Miễn phí' if billing_ready else 'Chưa nhập tiền thu khách hoặc chọn Miễn phí',ok=billing_ready)]
    if not workshop_repair and (ticket.kind=='MANG_VE' or (eq and eq.custody_location!='TAI_KHACH')):
        checks.append(dict(key='machine',label='Máy đã trả khách',ok=not eq or (eq.custody_location=='TAI_KHACH' and not ctx.get('custody_pending'))))
    checks.append(dict(key='warehouses',label='Vật tư đã chọn kho của bạn',ok=all(line.get('stock_doc_id') or (warehouse and line.get('warehouse_code')==warehouse.code) or any(x.kind=='VAT_TU' and x.product_code==line.get('product_code') and x.warehouse_code==line.get('warehouse_code') for x in ticket.lines) for line in ctx['charges']['lines'] if line['kind']=='VAT_TU')))
    target = {'DA_GIAO':'DA_NHAN','DA_NHAN':'DA_HEN','DA_HEN':'DANG_LAM','DANG_LAM':'HOAN_THANH',
              'CHO_LINH_KIEN':'DANG_LAM','TAM_DUNG':'DANG_LAM'}.get(ticket.status)
    if ticket.status=='TAM_DUNG' and ticket.hold_reason=='SUA_XONG_CHO_TRA':target=None
    if ticket.status=='DA_NHAN' and ticket.appointment_at: target='DANG_LAM'
    labels={'DA_GIAO':'Nhận đơn','DA_NHAN':'Hẹn khách','DA_HEN':'Đã đến khách','DANG_LAM':'Hoàn thành','CHO_LINH_KIEN':'Tiếp tục làm','TAM_DUNG':'Tiếp tục làm'}
    if workshop_repair:labels['DANG_LAM']='Sửa xong, chờ trả máy'
    if ticket.status=='DA_NHAN' and ticket.appointment_at: labels['DA_NHAN']='Đã đến khách'
    primary = dict(form='service-complete' if ticket.status=='DANG_LAM' else 'service-'+target,label=labels[ticket.status]) if target else (ctx.get('next_step') or {}).get('primary')
    exceptions=[s for s in ctx['transitions'] if s!=target and s in (
        ('DA_HEN','TAM_DUNG','KHONG_LAM_DUOC','DA_HUY') if ticket.status=='DANG_LAM'
        else ('DA_HUY','KHONG_LAM_DUOC') if ticket.status in ('CHO_LINH_KIEN','TAM_DUNG') else ())]
    if return_job:
        ctx['can_request_parts']=False
        ctx['can_tech_transfer']=False
        exceptions=[s for s in exceptions if s!='KHONG_LAM_DUOC']
    if return_job and ticket.status=='DANG_LAM':
        returned=bool(eq and eq.custody_location=='TAI_KHACH' and not ctx.get('custody_pending'))
        checks=[dict(key='return-photo',label='Có ảnh trả máy',ok=returned or any(x.tag=='TRA_MAY' for x in ctx.get('files',[])+ctx.get('custody_files',[]))),
            dict(key='holder',label='Đã nhận máy để trả khách',ok=returned or bool(ctx.get('can_custody_return')))]
    # Phiếu Mang về, máy còn ở chỗ khách: bước này là nhận máy về xưởng, chưa báo cáo/thu tiền.
    bring_stage=bool(can_bring and ticket.kind=='MANG_VE')
    if bring_stage:
        checks=[dict(key='machine',label='Chọn máy trước khi mang về',ok=bool(ticket.serial))]
        primary=dict(form='workshop-bring' if ticket.serial else None,label='Mang máy về xưởng')
    issued_rows=[dict(product=x.product_code,name=x.description or x.product_code,warehouse=x.warehouse_code,qty=x.qty) for x in ticket.lines if x.stock_doc_id is not None]
    stage = {'DA_GIAO':0,'DA_NHAN':1,'DA_HEN':2,'DANG_LAM':2,'CHO_LINH_KIEN':2,'TAM_DUNG':2}.get(ticket.status,3)
    if ticket.appointment_at: stage = 0 if ticket.status=='DA_GIAO' else (1 if ticket.status in service_tickets.OPEN else 2)
    return dict(technician_view=True,return_job=return_job,workshop_repair=workshop_repair,can_bring_workshop=can_bring and not bring_stage,bring_stage=bring_stage,**({'can_custody_receive':False} if bring_stage else {}),workshop_materials=materials,issued_materials=issued_rows,checks=checks,completion_ready=all(c['ok'] for c in checks),
        tech_primary=primary,current_target=target,exception_targets=exceptions,step_index=stage,
        machine_name=product.name if product else '',**contact(ctx['customer'],ctx['site']))


def _schedule(appointment,status,now):
    local = appointment.astimezone(service_tickets.service_sla.TZ) if appointment else None
    if status in ('DA_NHAN','DA_HEN') and local and local.date()<=now.astimezone(service_tickets.service_sla.TZ).date(): return (0,local)
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
        if action and ticket.status=='DA_GIAO':action={**action,'primary':{**action['primary'],'label':'Nhận đơn'}}
        jobs.append(dict(kind='service',service_kind=ticket.kind,id=ticket.id,number=ticket.number,priority=ticket.priority,customer=customer.name,
            machine=machine or '',serial=ticket.serial or '',symptom=ticket.symptom,appointment=ticket.appointment_at,
            appointment_slot=ticket.appointment_slot, overdue=(ticket.status in ('DA_HEN','DA_NHAN') and ticket.appointment_at is not None and service_appointments.end(ticket.appointment_at,ticket.appointment_slot)<now) or (ticket.due_at is not None and ticket.due_at<now),
            href=f'/tho/phieu/{ticket.id}',action=action['primary'] if action else None,status=ticket.status,created=ticket.created_at,
            group='active' if ticket.status in ('DANG_LAM','CHO_LINH_KIEN','TAM_DUNG') else 'upcoming',
            sort=_schedule(ticket.appointment_at,ticket.status,now),**contact(customer,site)))
    orders=session.scalars(select(SalesOrder).where(SalesOrder.delivery_assignee_id==user.id,SalesOrder.fulfillment=='THO_GIAO',SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN'))))
    by_ticket={job['id']:job for job in jobs if job['kind']=='service'}
    for order in orders:
        action=next_steps.delivery(session,user,order)
        cancel_asked=action['primary']['label'].startswith('Đã đề nghị hủy')
        if order.service_ticket_id in by_ticket:
            # Đơn giao cùng chuyến với phiếu của thợ: gộp vào thẻ phiếu, không thành dòng riêng.
            by_ticket[order.service_ticket_id].setdefault('trips',[]).append(dict(number=order.number,href=f'/giao-don/{order.id}?return_to=tho',action=action['primary']))
            continue
        customer=session.get(Customer,order.customer_code)
        appointment=datetime.combine(order.promised_date,time.min,service_tickets.service_sla.TZ) if order.promised_date else None
        jobs.append(dict(kind='delivery',id=order.id,number=order.number,priority='',customer=customer.name,machine='',serial='',
            symptom='Chờ bán hàng hủy đơn' if cancel_asked else 'Giao đơn bán',appointment=appointment,overdue=bool(order.promised_date and order.promised_date<now.astimezone(service_tickets.service_sla.TZ).date()),
            href=f'/giao-don/{order.id}?return_to=tho',action=action['primary'],status='CHO_HUY' if cancel_asked else 'GIAO_DON',created=order.created_at,group='upcoming',sort=_schedule(appointment,'DA_HEN',now),**contact(customer,address=order.delivery_address)))
    jobs.sort(key=lambda row:(row['sort'],row['kind'],row['id']))
    day=now.astimezone(service_tickets.service_sla.TZ).date()
    # Chỉ nhắc, không chặn: thợ có hồ sơ nhân sự mà hôm nay chưa bấm Vào ca.
    not_clocked_in=session.scalar(select(Employee.id).where(Employee.user_id==user.id)) is not None and not attendance._punches(session,user.id,day)
    # Phiếu đã xong/đã đóng còn tiền chưa báo: cùng điều kiện nhóm service_unreported của Việc của tôi.
    t=ServiceTicket;due=my_work._available(t)
    unreported=[dict(id=row.id,number=row.number,status=row.status,customer=row.name,due=int(row.due),href=f'/tho/phieu/{row.id}#thu-tien')
        for row in session.execute(select(t.id,t.number,t.status,Customer.name,due.label('due')).join(Customer,Customer.code==t.customer_code).where(
            t.assignee_id==user.id,t.status.in_(('HOAN_THANH','DA_DONG')),t.billing=='THU_TIEN',t.priced_at.is_not(None),due>0).order_by(t.id))]
    return render(request,'tho_hom_nay.jinja',jobs=jobs,today=day,not_clocked_in=not_clocked_in,unreported=unreported,
        watch=dict(url='/tho/moi',stamp=work_watch.technician(session,user)))


@router.get('/tho/moi')
def today_stamp(user:User=Depends(technician_user),session:Session=Depends(get_session)):
    return watch_response(work_watch.technician(session,user))


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
    from .. import service_charges
    from .services import _detail, _message, _version
    owned(session, user, ticket_id)
    request.state.technician_return = True
    from .. import technician_work
    if service_charges.return_locked(session.get(ServiceTicket,ticket_id)):
        shown={key:str(form.get(key,'')) for key in ('customer_amount','collection_mode','partial_amount','method','note')}
        shown['materials']=[dict(product_code=str(code),qty=str(qty)) for code,qty in zip(form.getlist('material_product'),form.getlist('material_qty'))]
        try:
            from .custody import _accessory_values
            from ..schemas import CustodyIntake
            body=CustodyIntake(version=form.get('machine_version'),accessories=_accessory_values(form),accessories_note=str(form.get('accessories_note',''))) if form.get('return_machine')=='1' else None
            if form.getlist('material_product') or form.getlist('line_kind'):
                raise BusinessError('CHARGES_LOCKED','Tiền và vật tư đã chốt, không sửa khi trả máy.',status=409)
            technician_work.complete(session,user,ticket_id,_version(form),shown['customer_amount'],shown['collection_mode'],str(form.get('free_reason','')),[],shown['partial_amount'],shown['method'],shown['note'],return_machine=body)
        except (BusinessError,ValidationError) as exc:
            message,code,status=_message(exc)
            return _detail(request,session,user,ticket_id,form=form,error=message,error_code=code,status=status,completion_form=shown,technician_view=True)
        return after_post(request,session,user,ticket_id,'/tho')
    if 'customer_amount' in form:
        from .. import technician_work
        shown={key:str(form.get(key,'')) for key in ('customer_amount','collection_mode','free_reason','partial_amount','method','note','fault_found','fix_done')}
        shown['materials']=[dict(product_code=str(code),qty=str(qty)) for code,qty in zip(form.getlist('material_product'),form.getlist('material_qty'))]
        try:
            shown['fault_found'],shown['fix_done'],report=report_fields(form);shown.update(report)
            if len(form.getlist('material_product'))!=len(form.getlist('material_qty')):raise BusinessError('BAD_FORM','Dòng vật tư chưa đủ dữ liệu.')
            technician_work.complete(session,user,ticket_id,_version(form),shown['customer_amount'],shown['collection_mode'],shown['free_reason'],shown['materials'],shown['partial_amount'],shown['method'],shown['note'],shown['fault_found'],shown['fix_done'])
        except (BusinessError,ValidationError) as exc:
            message,code,status=_message(exc)
            return _detail(request,session,user,ticket_id,error=message,error_code=code,status=status,completion_form=shown,technician_view=True)
        return after_post(request,session,user,ticket_id,'/tho')
    raw = _raw(form)
    shown = {key: str(form.get(key, '')) for key in ('note', 'amount', 'method', 'fault_found', 'fix_done')}
    try:
        shown['fault_found'], shown['fix_done'], report = report_fields(form); shown.update(report)
        version = _version(form)
        charges = _body(raw, version)
        normalized=[]
        for line in charges.lines:
            if line.kind=='VAT_TU':
                normalized.append(line.model_copy(update={'unit_price':0,'vat_rate':0}))
                if line.unit_price:
                    normalized.append(dict(kind='PHI',description='Phí dịch vụ (gồm công và vật tư)',qty=1,unit_price=service_charges._round(line.qty*line.unit_price),vat_rate=line.vat_rate))
            else:normalized.append(line)
        charges=type(charges)(**{**charges.model_dump(),'lines':normalized})
        note = ServiceTransition(to_status='HOAN_THANH', version=version, note=shown['note']).note
        amount = shown['amount'].strip()
        if amount and not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)', amount):
            raise BusinessError('BAD_AMOUNT', 'Số tiền phải là số nguyên đồng, có thể dùng dấu chấm hàng nghìn.')
        digits = amount.replace('.', '').lstrip('0') or '0'
        if len(digits) > 19:
            raise BusinessError('BAD_AMOUNT', 'Số tiền vượt giới hạn lưu trữ.')
        receipt = SalesReceiptReport(version=version, method=shown['method'], amount=int(digits), note=note) if int(digits) else None
        service_completion.complete(session, user, ticket_id, charges, note, receipt, shown['fault_found'], shown['fix_done'])
    except (BusinessError, ValidationError) as exc:
        message, code, status = _message(exc)
        return _detail(request, session, user, ticket_id, error=message, error_code=code, status=status,
                       charge_shown=raw, completion_form=shown, technician_view=True)
    return after_post(request, session, user, ticket_id, '/tho')


@router.post('/tho/phieu/{ticket_id}/chuyen-tho')
def transfer_ticket(request:Request,ticket_id:int,form=Depends(checked_form),user:User=Depends(technician_user),session:Session=Depends(get_session)):
    from .services import _detail,_message,_version,return_lines
    from .. import technician_work
    from pydantic import ValidationError
    owned(session,user,ticket_id);request.state.technician_return=True
    try:
        ident=str(form.get('assignee_id',''))
        if not ident.isascii() or not ident.isdigit() or len(ident)>18:raise BusinessError('BAD_ASSIGNEE','Chọn thợ nhận.')
        technician_work.transfer(session,user,ticket_id,int(ident),_version(form),str(form.get('reason','')),form.get('failed')=='1',return_lines(form) if form.getlist('return_product') else None)
    except (BusinessError,ValidationError) as exc:
        message,code,status=_message(exc)
        return _detail(request,session,user,ticket_id,form=dict(form),failed_action='tech-transfer',error=message,error_code=code,status=status,technician_view=True,return_form=return_lines(form,photos=False) if form.getlist('return_product') else None)
    return RedirectResponse('/tho?da_luu=1',status_code=303)


@router.post('/tho/phieu/{ticket_id}/anh')
async def upload_photo(request:Request,ticket_id:int,form=Depends(checked_form),user:User=Depends(technician_user),session:Session=Depends(get_session)):
    from starlette.datastructures import UploadFile
    from fastapi.responses import JSONResponse
    from ..config import get_settings
    from .. import form_keys
    t=owned(session,user,ticket_id)
    t=service_tickets._get(session,user,ticket_id,lock=True)
    if t.status not in service_tickets.OPEN:raise BusinessError('BAD_STATUS','Phiếu đã hoàn thành hoặc đóng.',status=409)
    tag=form.get('tag') if form.get('tag') in ('TRUOC','BIEN_BAN','NHAN_MAY','TRA_MAY') else 'SAU'
    upload=form.get('file')
    try:
        if not isinstance(upload,UploadFile):raise BusinessError('EMPTY_FILE','Chọn ảnh cần tải.')
        data=await upload.read(get_settings().max_upload_mb*1024*1024+1)
        if data.startswith(b'%PDF-'):raise BusinessError('BAD_FILE_TYPE','Ảnh nghiệm thu phải là ảnh.')
        a=attachments.add(session,user,'service_ticket',str(t.id),tag,upload.filename or 'anh.jpg',data)
    except BusinessError as exc:
        request.state.form_business_error=True
        return JSONResponse(dict(error=exc.message,code=exc.code),status_code=exc.status)
    return JSONResponse(dict(id=a.id,version=a.version,url=f'/attachments/{a.id}/file',form_key=form_keys.form_key()),status_code=201)


@router.post('/tho/phieu/{ticket_id}/anh/{attachment_id}/xoa')
def delete_photo(request:Request,ticket_id:int,attachment_id:int,form=Depends(checked_form),user:User=Depends(technician_user),session:Session=Depends(get_session)):
    from fastapi.responses import JSONResponse
    from .services import _version
    from ..models import Attachment
    t=owned(session,user,ticket_id)
    t=service_tickets._get(session,user,ticket_id,lock=True)
    a=session.get(Attachment,attachment_id)
    if not a or a.entity!='service_ticket' or a.entity_id!=str(t.id):raise BusinessError('NOT_FOUND','Không tìm thấy ảnh của phiếu.',status=404)
    if a.created_by!=user.id:raise BusinessError('FORBIDDEN','Chỉ xóa ảnh do chính mình tải.',status=403)
    if t.status not in service_tickets.OPEN:raise BusinessError('BAD_STATUS','Không xóa ảnh sau khi hoàn thành.',status=409)
    attachments.hide(session,user,a.id,'Thợ xóa trước khi hoàn thành',_version(form))
    return JSONResponse(dict(hidden=True))


@router.post('/tho/phieu/{ticket_id}/nhap')
async def save_draft(request:Request,ticket_id:int,user:User=Depends(technician_user),session:Session=Depends(get_session)):
    """Thẻ B: tự lưu nháp báo cáo. Lưu lại cùng nội dung không sao nên không cần mã gửi một lần."""
    import hmac, json
    from fastapi.responses import JSONResponse
    from .. import technician_work
    form=await request.form()
    if not hmac.compare_digest(str(form.get('csrf','')),request.state.csrf):
        return JSONResponse(dict(error='Trang đã cũ, hãy tải lại rồi thử lại.',code='BAD_CSRF'),status_code=403)
    try:
        owned(session,user,ticket_id)
        try:data=json.loads(str(form.get('data','')))
        except ValueError:raise BusinessError('BAD_DRAFT','Nháp không hợp lệ.')
        technician_work.save_draft(session,user,ticket_id,data)
    except BusinessError as exc:
        return JSONResponse(dict(error=exc.message,code=exc.code),status_code=exc.status)
    return JSONResponse(dict(ok=True))
