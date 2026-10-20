"""KT6: mọi POST dùng CSRF/form_key và gọi lõi nghiệp vụ."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import products, service_recovered_parts as svc, service_tickets, service_warranty
from ..db import get_session
from ..errors import BusinessError
from ..models import Product, ServiceTicket, User, Warehouse
from ..schemas import RecoveredPartIn, RecoveredPartReceive, ServiceWarrantyLink
from ..security import has_role
from . import checked_form, nav, render, web_user

router = APIRouter()


def detail_context(session, user, ticket, form=None):
    can_view = has_role(user, 'dispatcher', 'warehouse', 'finance') or 'technician' in user.roles
    return dict(can_view_recovered=can_view, recovered_parts=svc.search(session, user, ticket_id=ticket.id) if can_view else [],
        can_recover=ticket.status in service_tickets.OPEN and ticket.assignee_id is not None and (
            has_role(user, 'dispatcher') or ('technician' in user.roles and ticket.assignee_id == user.id)),
        recovered_statuses=svc.STATUSES, recovered_form=form or {})


def _page(request, session, user, holder=None, ticket_id=None, selected='CHO_NOP', error=None, code='', status=200, form=None, failed_id=None):
    rows = svc.search(session, user, holder_id=holder, ticket_id=ticket_id, pending=selected=='CHO_NOP')
    if selected and selected not in svc.STATUSES: raise BusinessError('BAD_STATUS','Trạng thái chưa hợp lệ.')
    if selected and selected!='CHO_NOP': rows=[r for r in rows if r.status==selected]
    tickets={r.id:session.get(ServiceTicket,r.ticket_id) for r in rows}
    return render(request, 'linh_kien_thu_hoi.jinja', rows=rows, tickets=tickets,
        names=dict(session.execute(select(Product.code,Product.name)).all()), users=dict(session.execute(select(User.id,User.full_name)).all()),
        warehouses=list(session.scalars(select(Warehouse).where(Warehouse.kind=='LOI',Warehouse.active.is_(True)).order_by(Warehouse.code))),
        holder=holder, selected_status=selected, ticket_id=ticket_id, statuses=svc.STATUSES,
        can_receive=has_role(user,'warehouse'), can_ticket=nav.allowed(user,'/dich-vu'),
        can_cancel=lambda row:has_role(user,'dispatcher') or ('technician' in user.roles and row.holder_id==user.id),
        error=error, error_code=code, status=status, form=form or {}, failed_id=failed_id)


@router.get('/linh-kien-thu-hoi')
def listing(request:Request, tho:str='', ticket_id:int | None=None, status:str='CHO_NOP',
            user:User=Depends(web_user), session:Session=Depends(get_session)):
    return _page(request,session,user,int(tho) if tho.isdigit() else None,ticket_id,status)


@router.get('/linh-kien-thu-hoi/tim-hang')
def pick(request:Request,q:str='',user:User=Depends(web_user),session:Session=Depends(get_session)):
    svc.visible(user)
    return render(request,'thu_hoi_chon_hang.jinja',rows=products.search(session,q[:200],active_only=True,limit=20))


@router.post('/linh-kien-thu-hoi/phieu/{ticket_id}/tao')
def create(request:Request,ticket_id:int,form=Depends(checked_form),session:Session=Depends(get_session)):
    from .services import _detail,_message,_version
    user=request.state.user
    raw={key:str(form.get(key,'')) for key in ('product_code','qty','note')}
    try:
        try: qty=int(raw['qty'])
        except ValueError: raise BusinessError('BAD_QTY','Số lượng phải là số nguyên dương.')
        svc.create(session,user,ticket_id,RecoveredPartIn(**{**raw,'qty':qty},version=_version(form)))
    except (BusinessError,ValidationError) as exc:
        error,code,status=_message(exc)
        return _detail(request,session,user,ticket_id,error=error,error_code=code,status=status,recovered_form=raw)
    from .technician import after_post
    return after_post(request,session,request.state.user,ticket_id,f'/dich-vu/{ticket_id}?da_luu=1#recovered-parts')


@router.post('/linh-kien-thu-hoi/phieu/{ticket_id}/bao-hanh')
def relink(request:Request,ticket_id:int,form=Depends(checked_form),session:Session=Depends(get_session)):
    from .services import _detail,_message,_version
    user=request.state.user
    raw=dict(warranty_of_id=str(form.get('warranty_of_id','')),note=str(form.get('note','')))
    try:
        service_warranty.relink(session,user,ticket_id,ServiceWarrantyLink(**{**raw,'warranty_of_id':raw['warranty_of_id'] or None},version=_version(form)))
    except (BusinessError,ValidationError) as exc:
        error,code,status=_message(exc)
        return _detail(request,session,user,ticket_id,error=error,error_code=code,status=status,warranty_form=raw)
    return RedirectResponse(f'/dich-vu/{ticket_id}?da_luu=1',status_code=303)


@router.post('/linh-kien-thu-hoi/{ident}/{action}')
def action(request:Request,ident:int,action:str,form=Depends(checked_form),session:Session=Depends(get_session)):
    from .services import _message,_version
    user=request.state.user
    try:
        version=_version(form)
        if action=='nhan': svc.receive(session,user,ident,RecoveredPartReceive(version=version,warehouse_code=str(form.get('warehouse_code','')) or None))
        elif action=='huy': svc.cancel(session,user,ident,version,str(form.get('reason','')))
        else: raise BusinessError('NOT_FOUND','Không tìm thấy thao tác.',status=404)
    except (BusinessError,ValidationError) as exc:
        error,code,status=_message(exc)
        return _page(request,session,user,error=error,code=code,status=status,form=form,failed_id=ident)
    return RedirectResponse('/linh-kien-thu-hoi?status=',status_code=303)
