"""NS1: strict separation of common and director-only template contexts."""
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import employees as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import Employee, User
from ..security import has_role
from . import checked_form, nav, render, web_user
from .money_input import parse_money

router=APIRouter()
COMMON_FIELDS=('position','start_date','end_date','birth_date','id_number','id_issued_on','address','emergency_name','emergency_phone','note')


def manager_user(user:User=Depends(web_user)):
    svc.manager(user);return user


def director_user(user:User=Depends(web_user)):
    svc.director(user);return user


def _render(request,template,**context):
    response=render(request,template,**context)
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='no-referrer'
    return response


def _common(form):return {k:str(form.get(k,'')) for k in COMMON_FIELDS}


def _error(exc):return dict(error=exc.message,error_code=exc.code,errors=exc.details.get('fields',{}),status=exc.status)


def _new(request,session,actor,data=None,**kwargs):
    data=data or dict(position='Kỹ thuật',start_date=svc.today().isoformat(),user_id=request.query_params.get('user_id',''))
    selected=None
    try:ident=int(data.get('user_id',''))
    except (TypeError,ValueError):ident=None
    if ident:
        row=session.execute(select(User.id,User.username,User.full_name).where(User.id==ident)).mappings().first()
        selected=dict(row) if row else None
    return _render(request,'nhan_su_moi.jinja',d=data,selected=selected,accounts=svc.available_users(session,actor),positions=svc.POSITIONS,**kwargs)


def _detail(request,session,actor,ident,**kwargs):
    profile=svc.get(session,actor,ident)
    # This branch is decided BEFORE creating context; admin gets no pay/salary keys.
    context=dict(profile=profile,d={k:profile[k] for k in COMMON_FIELDS},positions=svc.POSITIONS,
        is_director=has_role(actor,'director'),history=svc.history(session,actor,ident),
        actors=dict(session.execute(select(User.id,User.full_name)).all()),
        local_time=lambda dt:dt.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%d/%m/%Y %H:%M'))
    if context['is_director']:
        context.update(money_fields=tuple(zip(svc.MONEY_FIELDS,svc.MONEY_LABELS)),today=svc.today())
    context.update(kwargs)
    return _render(request,'nhan_su_chi_tiet.jinja',**context)


@router.get('/nhan-su')
def listing(request:Request,q:str='',position:str='',status:str='',user:User=Depends(manager_user),session:Session=Depends(get_session)):
    return _render(request,'nhan_su_ds.jinja',rows=svc.search(session,user,q[:200],position,status),q=q[:200],
        positions=svc.POSITIONS,position=position,selected_status=status,is_director=has_role(user,'director'))


@router.get('/nhan-su/tim-tai-khoan')
def accounts(request:Request,q:str='',user:User=Depends(manager_user),session:Session=Depends(get_session)):
    return _render(request,'nhan_su_chon.jinja',accounts=svc.available_users(session,user,q[:200]))


@router.get('/nhan-su/moi')
def new(request:Request,user:User=Depends(manager_user),session:Session=Depends(get_session)):
    return _new(request,session,user)


@router.post('/nhan-su/moi')
def create(request:Request,form=Depends(checked_form),user:User=Depends(manager_user),session:Session=Depends(get_session)):
    data={**_common(form),'user_id':str(form.get('user_id',''))}
    try:
        try:ident=int(data['user_id'])
        except ValueError:raise BusinessError('BAD_USER','Hãy tìm và chọn tài khoản.',{'fields':{'user_id':'Hãy chọn tài khoản.'}})
        row=svc.create(session,user,ident,data)
    except BusinessError as exc:return _new(request,session,user,data,**_error(exc))
    return RedirectResponse(f'/nhan-su/{row.id}?da_luu=1',status_code=303)


@router.get('/nhan-su/{employee_id}')
def detail(request:Request,employee_id:int,user:User=Depends(manager_user),session:Session=Depends(get_session)):
    return _detail(request,session,user,employee_id)


@router.post('/nhan-su/{employee_id}')
def update(request:Request,employee_id:int,form=Depends(checked_form),user:User=Depends(manager_user),session:Session=Depends(get_session)):
    data=_common(form)
    try:svc.update(session,user,employee_id,data,form.get('version'))
    except BusinessError as exc:return _detail(request,session,user,employee_id,d=data,**_error(exc))
    return RedirectResponse(f'/nhan-su/{employee_id}?da_luu=1',status_code=303)


@router.post('/nhan-su/{employee_id}/ngan-hang')
def set_pay(request:Request,employee_id:int,form=Depends(checked_form),user:User=Depends(director_user),session:Session=Depends(get_session)):
    data={k:str(form.get(k,'')) for k in ('bank_name','bank_account','bank_holder')}
    try:svc.set_pay(session,user,employee_id,data,form.get('version'))
    except BusinessError as exc:return _detail(request,session,user,employee_id,pay_form=data,**_error(exc))
    return RedirectResponse(f'/nhan-su/{employee_id}?da_luu=1#ngan-hang',status_code=303)


@router.post('/nhan-su/{employee_id}/luong')
def add_salary(request:Request,employee_id:int,form=Depends(checked_form),user:User=Depends(director_user),session:Session=Depends(get_session)):
    data={k:str(form.get(k,'')) for k in ('effective_from',*svc.MONEY_FIELDS,'standard_days','reason','replaces_id')}
    try:
        values=dict(data)
        for key,label in zip(svc.MONEY_FIELDS,svc.MONEY_LABELS):
            try:values[key]=parse_money(data[key],label)
            except BusinessError as exc:
                exc.details={'fields':{key:exc.message}};raise
        svc.add_salary(session,user,employee_id,values)
    except BusinessError as exc:return _detail(request,session,user,employee_id,salary_form=data,**_error(exc))
    return RedirectResponse(f'/nhan-su/{employee_id}?da_luu=1#luong',status_code=303)


# Khuôn 8: trang Cá nhân gom việc của riêng mỗi người (chấm công, lương, KPI, tài khoản).
PERSONAL = (('/cham-cong','Chấm công','Vào ca, ra ca, xin nghỉ hoặc bổ sung công.'),
            ('/phieu-luong','Phiếu lương','Lương từng tháng đã duyệt.'),
            ('/tam-ung','Tạm ứng','Xin tạm ứng và xem khoản đã chi.'),
            ('/kpi','KPI của tôi','Chỉ số tháng này, tạm tính.'),
            ('/hoa-hong','Hoa hồng của tôi','Hoa hồng theo quy tắc của vị trí.'))


@router.get('/ca-nhan')
def personal(request:Request,user:User=Depends(web_user)):
    links=[dict(href=h,label=l,hint=t) for h,l,t in PERSONAL if nav.allowed(user,h) and (h not in ('/phieu-luong','/tam-ung') or request.state.payroll_account)]
    return _render(request,'ca_nhan.jinja',links=links)


@router.get('/ca-nhan/thong-bao')
def notices(user:User=Depends(web_user),session:Session=Depends(get_session)):
    from fastapi.responses import JSONResponse
    from .. import notifications
    rows=notifications.items(session,user)
    return JSONResponse(dict(items=rows,count=len(rows)),headers={'Cache-Control':'no-store'})


async def _push_form(request:Request):
    # Bật/tắt thông báo là thao tác lặp lại được (ghi đè theo endpoint) nên chỉ kiểm csrf, không cần form_key.
    import hmac
    form=await request.form()
    if not hmac.compare_digest(str(form.get('csrf','')),request.state.csrf):
        raise BusinessError('BAD_CSRF','Trang đã cũ, hãy tải lại rồi thử lại.',status=403)
    return form


@router.get('/ca-nhan/thong-bao/khoa')
def push_key(user:User=Depends(web_user),session:Session=Depends(get_session)):
    from fastapi.responses import JSONResponse
    from .. import web_push
    return JSONResponse(dict(public_key=web_push.keys(session).public_key),headers={'Cache-Control':'no-store'})


@router.post('/ca-nhan/thong-bao/dang-ky')
async def push_subscribe(request:Request,user:User=Depends(web_user),session:Session=Depends(get_session)):
    import json
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
    from .. import web_push
    form=await _push_form(request)
    try:data=json.loads(str(form.get('subscription','')))
    except ValueError:data=None
    await run_in_threadpool(web_push.subscribe,session,user,data)
    return JSONResponse(dict(ok=True))


@router.post('/ca-nhan/thong-bao/huy')
async def push_unsubscribe(request:Request,user:User=Depends(web_user),session:Session=Depends(get_session)):
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
    from .. import web_push
    form=await _push_form(request)
    await run_in_threadpool(web_push.unsubscribe,session,user,str(form.get('endpoint','')))
    return JSONResponse(dict(ok=True))


@router.get('/ho-so-cua-toi')
def own(request:Request,user:User=Depends(web_user),session:Session=Depends(get_session)):
    # Even directors see only their common profile on this route.
    return _render(request,'ho_so_cua_toi.jinja',profile=svc.own(session,user))
