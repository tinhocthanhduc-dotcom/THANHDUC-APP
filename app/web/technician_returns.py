"""Yêu cầu trả kho, kho xác nhận số thực nhận qua lõi CK."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from .. import technician_returns as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import Product, TechnicianReturn, User
from ..security import has_role
from . import checked_form, render, web_user
router = APIRouter()


def detail(request, session, user, ident, error=None, code='', status=200, shown=None):
    row = svc.load(session,user,ident)
    return render(request,'tho_tra_hang.jinja',row=row,names=dict(session.execute(select(Product.code,Product.name)).all()),
        doc_url=(f'/phieu-kho/{row.stock_doc_id}' if has_role(user,'warehouse') else f'/hang-toi-giu/phieu/{row.stock_doc_id}' if 'technician' in user.roles else None),statuses=svc.STATUSES,can_receive=has_role(user,'warehouse'),error=error,error_code=code,status=status,d=shown or {},saved=request.query_params.get('da_luu')=='1')


@router.get('/tho-tra-hang/{ident}')
def get(request:Request,ident:int,user:User=Depends(web_user),session:Session=Depends(get_session)):
    return detail(request,session,user,ident)


@router.post('/tho-tra-hang/{ident}/{action}')
def action(request:Request,ident:int,action:str,form=Depends(checked_form),session:Session=Depends(get_session)):
    from .services import _version, _message
    user=request.state.user
    svc.load(session,user,ident)
    shown={k:str(v) for k,v in form.items()}
    try:
        if action=='nhan':
            quantities={k[4:]:str(v) for k,v in form.items() if k.startswith('qty_')}
            svc.receive(session,user,ident,_version(form),quantities,str(form.get('note','')))
        elif action=='huy': svc.cancel(session,user,ident,_version(form),str(form.get('reason','')))
        else: raise BusinessError('NOT_FOUND','Không tìm thấy thao tác.',status=404)
    except BusinessError as exc:
        message,code,status=_message(exc)
        return detail(request,session,user,ident,message,code,status,shown)
    return RedirectResponse(f'/tho-tra-hang/{ident}?da_luu=1',status_code=303)
