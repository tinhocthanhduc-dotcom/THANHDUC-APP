"""KT4: thao tác giữ máy tách khỏi quyền xem/sửa tiền và trạng thái phiếu."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from .. import attachments, equipment_custody as svc, service_tickets
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, EquipmentCustody, User
from ..schemas import CustodyHandover, CustodyIntake
from ..security import has_role
from . import checked_form, render, web_user

router=APIRouter()


def context(session,user,t,shown=None):
    if not t.serial:return dict(custody_machine=None)
    eq=svc._machine(session,t.serial)
    row=svc.latest(session,eq.serial);pending=svc.pending(session,eq.serial)
    allowed=svc.can_view(session,user,t,eq)
    own=eq.custody_location=='THO_GIU' and eq.custody_holder_id==user.id and 'technician' in user.roles
    return dict(custody_machine=eq,custody_current=row,custody_pending=pending,
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


def _accessory_values(form):
    values=[str(v) for v in form.getlist('accessories')]
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


@router.get('/may-dang-giu')
def held(request:Request,location:str='',user:User=Depends(web_user),session:Session=Depends(get_session)):
    rows=svc.held(session,user,location)
    return render(request,'may_dang_giu.jinja',rows=rows,locations=svc.LOCATIONS,selected_location=location,
        users=dict(session.execute(select(User.id,User.full_name)).all()))
