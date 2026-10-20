"""Admin accounts and self-service password changes."""
from zoneinfo import ZoneInfo

from fastapi import APIRouter,Depends,Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import users as svc, form_keys
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import User,AuditLog
from . import COOKIE,checked_form,nav,render,web_user

router=APIRouter()


def admin_user(user:User=Depends(web_user)):
    svc.check_admin(user)
    return user


def _load(session,ident):
    user=session.get(User,ident)
    if user is None:raise BusinessError('NOT_FOUND','Không tìm thấy tài khoản.',status=404)
    return user


def _render(request,template,**context):
    context.setdefault('role_labels',nav.ROLE_LABELS)
    context.setdefault('user_marker',svc.marker)
    context.setdefault('local_time',lambda dt:dt.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%d/%m/%Y %H:%M') if dt else 'Chưa đăng nhập')
    response=render(request,template,**context)
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='no-referrer'
    return response


def _edit(request,user=None,form=None,error=None,code='',status=200):
    data=dict(form) if form is not None else dict(full_name=user.full_name if user else '',username=user.username if user else '',phone=user.phone if user else '',roles=user.roles if user else [],version=svc.marker(user) if user else '')
    return _render(request,'tai_khoan_sua.jinja',employee=user,d=data,error=error,error_code=code,status=status)


def _secret(request,user,password):
    # A repeated submission redirects to the detail page and never redisplays the password.
    key=getattr(request.state,'form_submission_key',None)
    if key is not None:form_keys.finish(key,f'/tai-khoan/{user.id}')
    return _render(request,'tai_khoan_mat_khau.jinja',employee=user,temp_password=password)


@router.get('/tai-khoan')
def listing(request:Request,q:str='',status:str='',user:User=Depends(admin_user),session:Session=Depends(get_session)):
    return _render(request,'tai_khoan_ds.jinja',rows=svc.search(session,user,q[:200],status),q=q[:200],selected_status=status)


@router.get('/tai-khoan/moi')
def new(request:Request,user:User=Depends(admin_user)):
    return _edit(request)


@router.post('/tai-khoan/moi')
def create(request:Request,form=Depends(checked_form),user:User=Depends(admin_user),session:Session=Depends(get_session)):
    data={key:str(form.get(key,'')) for key in ('username','full_name','phone')};data['roles']=[str(role) for role in form.getlist('roles')]
    try:
        employee,temp=svc.create(session,user,**data)
    except BusinessError as exc:return _edit(request,form=data,error=exc.message,code=exc.code,status=exc.status)
    return _secret(request,employee,temp)


@router.get('/tai-khoan/{user_id}')
def detail(request:Request,user_id:int,user:User=Depends(admin_user),session:Session=Depends(get_session)):
    employee=_load(session,user_id)
    history=list(session.scalars(select(AuditLog).where(AuditLog.entity=='user',AuditLog.entity_id==str(user_id)).order_by(AuditLog.id.desc())))
    actors=dict(session.execute(select(User.id,User.full_name)).all())
    return _render(request,'tai_khoan_chi_tiet.jinja',employee=employee,history=history,actors=actors)


@router.get('/tai-khoan/{user_id}/sua')
def edit(request:Request,user_id:int,user:User=Depends(admin_user),session:Session=Depends(get_session)):
    return _edit(request,_load(session,user_id))


@router.post('/tai-khoan/{user_id}/sua')
def save(request:Request,user_id:int,form=Depends(checked_form),user:User=Depends(admin_user),session:Session=Depends(get_session)):
    employee=_load(session,user_id)
    data={key:str(form.get(key,'')) for key in ('username','full_name','phone','version')};data['roles']=[str(role) for role in form.getlist('roles')]
    try:
        if data['username']!=employee.username:raise BusinessError('USERNAME_IMMUTABLE','Không đổi tên đăng nhập sau khi tạo.')
        svc.update(session,user,user_id,data['roles'],data['version'],data['full_name'],data['phone'])
    except BusinessError as exc:return _edit(request,employee,data,exc.message,exc.code,exc.status)
    target=f'/tai-khoan/{user_id}?da_luu=1' if 'admin' in user.roles else nav.home_url(user)
    return RedirectResponse(target,status_code=303)


def _lock_page(request,session,employee,reason='',ack=False,error=None,code='',status=200):
    return _render(request,'tai_khoan_khoa.jinja',employee=employee,handover=svc.handover_summary(session,employee),reason=reason,ack=ack,error=error,error_code=code,status=status)


@router.get('/tai-khoan/{user_id}/khoa')
def lock_page(request:Request,user_id:int,user:User=Depends(admin_user),session:Session=Depends(get_session)):
    return _lock_page(request,session,_load(session,user_id))


@router.post('/tai-khoan/{user_id}/khoa')
def lock(request:Request,user_id:int,form=Depends(checked_form),user:User=Depends(admin_user),session:Session=Depends(get_session)):
    employee=_load(session,user_id);reason=str(form.get('reason',''));ack=form.get('ack')=='1'
    try:
        if not ack:raise BusinessError('HANDOVER_ACK_REQUIRED','Tích xác nhận đã biết thông tin bàn giao trước khi khóa.')
        svc.lock(session,user,user_id,reason)
    except BusinessError as exc:return _lock_page(request,session,employee,reason,ack,exc.message,exc.code,exc.status)
    return RedirectResponse(f'/tai-khoan/{user_id}?da_luu=1',status_code=303)


@router.post('/tai-khoan/{user_id}/mo-khoa')
def unlock(request:Request,user_id:int,form=Depends(checked_form),user:User=Depends(admin_user),session:Session=Depends(get_session)):
    svc.unlock(session,user,user_id)
    return RedirectResponse(f'/tai-khoan/{user_id}?da_luu=1',status_code=303)


@router.post('/tai-khoan/{user_id}/dat-lai-mat-khau')
def reset(request:Request,user_id:int,form=Depends(checked_form),user:User=Depends(admin_user),session:Session=Depends(get_session)):
    employee=_load(session,user_id);temp=svc.reset_password(session,user,user_id)
    return _secret(request,employee,temp)


@router.get('/doi-mat-khau')
def password_page(request:Request,user:User=Depends(web_user)):
    return _render(request,'doi_mat_khau.jinja',error=None)


@router.post('/doi-mat-khau')
def change_password(request:Request,form=Depends(checked_form),user:User=Depends(web_user),session:Session=Depends(get_session)):
    try:
        new=str(form.get('new_password',''))
        if new!=str(form.get('confirm_password','')):raise BusinessError('PASSWORD_MISMATCH','Hai lần nhập mật khẩu mới chưa giống nhau.')
        token=svc.change_own_password(session,user,str(form.get('old_password','')),new)
    except BusinessError as exc:return _render(request,'doi_mat_khau.jinja',error=exc.message,error_code=exc.code,status=exc.status)
    response=RedirectResponse(nav.home_url(user),status_code=303)
    settings=get_settings();response.set_cookie(COOKIE,token,max_age=settings.token_minutes*60,httponly=True,samesite='lax',secure=settings.cookie_secure)
    response.headers['Cache-Control']='no-store'
    return response
