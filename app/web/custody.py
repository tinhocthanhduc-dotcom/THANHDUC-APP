"""KT4: thao tác giữ máy tách khỏi quyền xem/sửa tiền và trạng thái phiếu."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from .. import attachments, equipment, equipment_custody as svc, service_tickets
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, Equipment, EquipmentCustody, Product, User
from ..schemas import CustodyHandover, CustodyIntake
from ..security import has_role
from . import checked_form, render, web_user
from .. import search as tim

router=APIRouter()


def context(session,user,t,shown=None):
    if not t.serial:return dict(custody_machine=None)
    eq=svc._machine(session,t.serial)
    row=svc.latest(session,eq.serial);pending=svc.pending(session,eq.serial)
    allowed=svc.can_view(session,user,t,eq)
    own=eq.custody_location=='THO_GIU' and eq.custody_holder_id==user.id and 'technician' in user.roles
    from ..models import EquipmentCustody
    intake=session.scalar(select(EquipmentCustody).where(EquipmentCustody.ticket_id==t.id,EquipmentCustody.status=='DA_NHAN',
        EquipmentCustody.location=='THO_GIU').order_by(EquipmentCustody.id).limit(1))
    return dict(custody_machine=eq,custody_current=row,custody_pending=pending,custody_intake=intake,
        custody_ticket=dict(id=t.id,number=t.number,serial=t.serial),custody_locations=svc.LOCATIONS,
        custody_accessories=svc.ACCESSORIES,custody_statuses=svc.STATUSES,
        custody_users=dict(session.execute(select(User.id,User.full_name)).all()),
        custody_techs=list(session.scalars(select(User).where(User.active.is_(True),User.roles.any('technician')).order_by(User.full_name,User.id))),
        custody_history=svc.history(session,eq.serial),custody_shown=shown or {},
        custody_files=attachments.list_for(session,user,'equipment_custody',str(t.id)) if allowed else [],
        can_custody_receive=allowed and not pending and eq.custody_location=='TAI_KHACH' and t.status in service_tickets.OPEN and
            (has_role(user,'dispatcher') or ('technician' in user.roles and t.assignee_id==user.id)),
        can_custody_handover=allowed and not pending and eq.custody_location!='TAI_KHACH' and eq.custody_ticket_id==t.id and
            (has_role(user,'dispatcher') or svc._is_holder(user,eq)),
        can_custody_confirm=allowed and pending and pending.ticket_id==t.id and svc.can_confirm(user,pending),
        can_custody_cancel=allowed and pending and pending.ticket_id==t.id and pending.created_by==user.id,
        can_custody_return=allowed and own and not pending and eq.custody_ticket_id==t.id,
        can_custody_photo_receive=allowed and t.status in service_tickets.OPEN and eq.custody_location=='TAI_KHACH' and
            (has_role(user,'dispatcher') or ('technician' in user.roles and t.assignee_id==user.id)),
        can_custody_photo_return=allowed and own and eq.custody_ticket_id==t.id)


def _page(request,session,user,ticket_id,shown=None,error=None,code='',status=200):
    if getattr(request.state,'technician_return',False):
        from .services import _detail
        return _detail(request,session,user,ticket_id,custody_shown=shown,error=error,error_code=code,status=status)
    t=svc.load(session,user,ticket_id)
    return render(request,'giu_may.jinja',**context(session,user,t,shown),
        custody_customer=session.get(Customer,t.customer_code),error=error,error_code=code,status=status,
        saved=request.query_params.get('da_luu')=='1')


@router.get('/giu-may/{ticket_id}')
def detail(request:Request,ticket_id:int,user:User=Depends(web_user),session:Session=Depends(get_session)):
    return _page(request,session,user,ticket_id)


def _accessory_values(form, field='accessories'):
    values=[str(v) for v in form.getlist(field)]
    if not values:raise BusinessError('ACCESSORIES_REQUIRED','Phải chọn phụ kiện hoặc Không kèm phụ kiện.')
    if 'KHONG_CO' in values:
        if len(values)!=1:raise BusinessError('BAD_ACCESSORIES','Không chọn đồng thời phụ kiện và Không kèm phụ kiện.')
        return []
    return values


@router.post('/giu-may/{ticket_id}/{action}')
def action(request:Request,ticket_id:int,action:str,form=Depends(checked_form),session:Session=Depends(get_session)):
    from .services import _version
    user=request.state.user
    shown={k:str(form.get(k,'')) for k in ('version','location','holder_id','partner_name','expected_back','accessories_note','condition_note')}
    shown.update(action=action,accessories=[str(v) for v in form.getlist('accessories')])
    try:
        svc.load(session,user,ticket_id)
        version=_version(form)
        if action in ('nhan','tra-khach'):
            body=CustodyIntake(version=version,accessories=_accessory_values(form),accessories_note=shown['accessories_note'],condition_note=shown['condition_note'])
            (svc.receive if action=='nhan' else svc.return_customer)(session,user,ticket_id,body)
        elif action=='ban-giao':
            svc.handover(session,user,ticket_id,CustodyHandover(version=version,location=shown['location'],holder_id=shown['holder_id'] or None,
                partner_name=shown['partner_name'],expected_back=shown['expected_back'] or None,
                accessories=_accessory_values(form) if 'accessories' in form else None,
                accessories_note=shown['accessories_note'] if 'accessories_note' in form else None,
                condition_note=shown['condition_note'] if 'condition_note' in form else None))
        elif action in ('xac-nhan','huy'):
            try:ident=int(str(form.get('custody_id','')))
            except ValueError:raise BusinessError('NOT_FOUND','Không tìm thấy bàn giao.',status=404)
            row=session.get(EquipmentCustody,ident)
            if not row or row.ticket_id!=ticket_id:raise BusinessError('NOT_FOUND','Bàn giao không thuộc phiếu này.',status=404)
            (svc.confirm if action=='xac-nhan' else svc.cancel)(session,user,ident,version)
        else:raise BusinessError('NOT_FOUND','Không tìm thấy thao tác giữ máy.',status=404)
    except (BusinessError,ValidationError) as exc:
        if isinstance(exc,BusinessError):message,code,status=exc.message,exc.code,exc.status
        else:message,code,status='Dữ liệu giữ máy chưa hợp lệ, hãy kiểm tra lại.','BAD_FORM',400
        if status in (403,404):return render(request,'loi.jinja',error=exc,status=status)
        return _page(request,session,user,ticket_id,shown,message,code,status)
    from .technician import after_post
    return after_post(request,session,request.state.user,ticket_id,f'/giu-may/{ticket_id}?da_luu=1#may-khach')


@router.post('/giu-may/{ticket_id}/anh/tai')
async def photo(request:Request,ticket_id:int,form=Depends(checked_form),session:Session=Depends(get_session)):
    user=request.state.user
    try:
        upload=form.get('file');tag=str(form.get('tag',''))
        if not isinstance(upload,UploadFile) or not upload.filename:raise BusinessError('EMPTY_FILE','Hãy chọn ảnh nhận hoặc trả máy.')
        limit=get_settings().max_upload_mb*1024*1024
        data=await upload.read(limit+1)
        attachments.add(session,user,'equipment_custody',str(ticket_id),tag,upload.filename,data)
    except BusinessError as exc:
        if exc.status in (403,404):return render(request,'loi.jinja',error=exc,status=exc.status)
        return _page(request,session,user,ticket_id,error=exc.message,code=exc.code,status=exc.status)
    from .technician import after_post
    return after_post(request,session,request.state.user,ticket_id,f'/giu-may/{ticket_id}?da_luu=1#may-khach')


# Đợt 6: mỗi máy đủ khách, model, lỗi, nơi giữ, hẹn trả và Bước tiếp · ai làm. Chỉ đọc, quyền xem như cũ.
VIEWS={'cho-nhan':'Chờ nhận','qua-han':'Quá hạn giữ'}


@router.get('/may-dang-giu')
def held(request:Request,location:str='',xem:str='',q:str='',user:User=Depends(web_user),session:Session=Depends(get_session)):
    from .. import service_sla
    from ..models import Product
    from . import nav
    from .customer_overview import _service_next
    location=location if location in svc.LOCATIONS and location!='TAI_KHACH' else ''
    xem=xem if xem in VIEWS else ''
    q=q[:200]
    everything=svc.held(session,user)
    users=dict(session.execute(select(User.id,User.full_name)).all())
    serials=[r['machine'].serial for r in everything]
    models=dict(session.execute(select(Equipment.serial,equipment.name_column()).outerjoin(Product,Product.code==Equipment.product_code).where(Equipment.serial.in_(serials))).all()) if serials else {}
    codes={r['ticket'].customer_code for r in everything}
    customers=dict(session.execute(select(Customer.code,Customer.name).where(Customer.code.in_(codes))).all()) if codes else {}
    pending={c.serial:c for c in session.scalars(select(EquipmentCustody).where(EquipmentCustody.serial.in_(serials),EquipmentCustody.status=='CHO_NHAN'))} if serials else {}
    today=svc._now().astimezone(service_sla.TZ).date()
    see_ticket=nav.allowed(user,'/dich-vu')
    for r in everything:
        eq,t,wait=r['machine'],r['ticket'],pending.get(r['machine'].serial)
        r.update(model=models.get(eq.serial,''),customer=customers.get(t.customer_code,t.customer_code),pending=wait,
            holder=users.get(eq.custody_holder_id,'') if eq.custody_location=='THO_GIU' else (r['row'].partner_name or '') if eq.custody_location=='DOI_TAC' else '',
            return_late=bool(t.promised_return_on and t.promised_return_on<today and t.status in service_tickets.OPEN))
        if wait:
            to={'THO_GIU':'thợ '+users.get(wait.holder_id,''),'KHO_KY_THUAT':'kho','DOI_TAC':'điều phối'}.get(wait.location,'')
            r['next']=(to.strip(),'Nhận máy vào '+svc.LOCATIONS.get(wait.location,'').lower() if wait.location!='THO_GIU' else 'Thợ nhận máy')
            r['action'],r['href']='Nhận máy',f'/giu-may/{t.id}'
        else:
            r['next']=_service_next(t,users,session)
            label='Xử lý' if t.hold_reason=='CHO_SUA_XUONG' and t.status=='TAM_DUNG' else 'Hẹn trả máy' if t.hold_reason=='SUA_XONG_CHO_TRA' and t.status=='TAM_DUNG' else 'Mở phiếu'
            r['action'],r['href']=(label,f'/dich-vu/{t.id}') if see_ticket else ('Xem phiếu',nav.ticket_url(user,t.id)) if nav.ticket_url(user,t.id) else ('Xem máy',f'/giu-may/{t.id}')
    counts={code:sum(1 for r in everything if r['machine'].custody_location==code) for code in svc.LOCATIONS if code!='TAI_KHACH'}
    waiting=sum(1 for r in everything if r['pending']);late=sum(1 for r in everything if r['overdue'])
    rows=[r for r in everything if (not location or r['machine'].custody_location==location) and
        (xem!='cho-nhan' or r['pending']) and (xem!='qua-han' or r['overdue'])]
    if q:
        rows=[r for r in rows if tim.text_matches(q,' '.join((r['machine'].serial,r['ticket'].number,r['customer'],r['model'],r['holder'])))]
    tiles=[dict(label='Đang giữ',n=len(everything),href='/may-dang-giu',on=not location and not xem)]+[
        dict(label=label,n=counts[code],href=f'/may-dang-giu?location={code}',on=location==code) for code,label in svc.LOCATIONS.items() if code!='TAI_KHACH']+[
        dict(label='Chờ nhận',n=waiting,href='/may-dang-giu?xem=cho-nhan',on=xem=='cho-nhan',sub='Bàn giao chưa xác nhận'),
        dict(label='Quá hạn giữ',n=late,href='/may-dang-giu?xem=qua-han',on=xem=='qua-han',tone='huy' if late else '',sub='Quá 7 ngày hoặc quá ngày về')]
    keep={k:v for k,v in dict(location=location,xem=xem).items() if v}
    return render(request,'may_dang_giu.jinja',rows=rows,locations=svc.LOCATIONS,selected_location=location,xem=xem,q=q,keep=keep,
        tiles=tiles,users=users,can_dispatch=nav.allowed(user,'/dieu-phoi'),total=len(everything))
