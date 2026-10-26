"""Minimum SX1 UI. Business validation and transactions live in production.py."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from .. import production as svc, sales_warranty
from ..db import get_session
from ..errors import BusinessError
from ..models import Product, User, Warehouse, AuditLog
from ..schemas import ProductionIn, ProductionAssign, ProductionVersion, ProductionComplete, ProductionReceive, ProductionVoid
from ..security import has_role
from . import checked_form, render, web_user, paging
from .services import _message

router = APIRouter()


def _choices(session, user):
    return dict(products=list(session.scalars(select(Product).where(Product.active.is_(True)).order_by(Product.code))),
        warehouses=list(session.scalars(select(Warehouse).where(Warehouse.active.is_(True), Warehouse.kind.in_(('THO','KHO'))).order_by(Warehouse.code))),
        technicians=list(session.scalars(select(User).where(User.active.is_(True), User.roles.contains(['technician'])).order_by(User.full_name))),
        tech_only=svc._tech_only(user), kinds=svc.KINDS, statuses=svc.STATUSES)


def _detail(request, session, user, number, d=None, error='', code='', status=200):
    row = svc.load(session, user, number)
    return render(request, 'san_xuat_xem.jinja', row=svc.public(user, row),
        history=list(session.execute(select(AuditLog.at,AuditLog.action,User.full_name).join(User,User.id==AuditLog.user_id).where(AuditLog.entity=='production_order',AuditLog.entity_id==str(row.id)).order_by(AuditLog.id))),
        product=session.get(Product, row.product_code), can_assign=has_role(user,'dispatcher','warehouse'),
        can_start=has_role(user,'dispatcher') or row.technician_id==user.id,
        can_complete=has_role(user,'director') or ('technician' in user.roles and row.technician_id==user.id),
        can_receive=has_role(user,'warehouse') and (has_role(user,'director') or not user.warehouse_codes or row.to_warehouse_code in user.warehouse_codes),
        can_void=has_role(user,'dispatcher') or user.id in (row.created_by,row.technician_id),
        d=d or {},error=error,error_code=code,status=status,**_choices(session,user))


@router.get('/san-xuat')
def listing(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    status = request.query_params.get('status','')
    stmt=svc.search(session,user,status)
    total=session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    page=paging.build(request.query_params.get('trang'),request.query_params.get('so'),total,dict(status=status))
    rows=list(session.scalars(stmt.offset(page['offset']).limit(page['size'])))
    counts = dict(session.execute(select(svc.Order.status,func.count()).where(
        svc.Order.technician_id==user.id if svc._tech_only(user) else True).group_by(svc.Order.status)).all())
    return render(request,'san_xuat_ds.jinja',rows=[svc.public(user,r) for r in rows],statuses=svc.STATUSES,kinds=svc.KINDS,selected=status,counts=counts,p=page)


@router.get('/san-xuat/moi')
def new(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    svc._require(user,*svc.READERS)
    return render(request,'san_xuat_moi.jinja',d={},error='',**_choices(session,user))


@router.post('/san-xuat/moi')
def create(request: Request, form=Depends(checked_form), session: Session=Depends(get_session)):
    user=request.state.user
    svc._require(user,*svc.READERS)
    try:
        if len(form.getlist('material_product'))!=len(form.getlist('material_plan')): raise BusinessError('BAD_FORM','Dòng vật tư dự kiến chưa đủ dữ liệu.')
        data={k:str(form.get(k,'')).strip() for k in ('kind','product_code','qty_plan','to_warehouse_code','note')}
        data.update(technician_id=form.get('technician_id') or None,from_warehouse_code=form.get('from_warehouse_code') or None,
            lines=[dict(product_code=c,qty_plan=q.replace(',','.')) for c,q in zip(form.getlist('material_product'),form.getlist('material_plan')) if c])
        row=svc.create(session,user,ProductionIn(**data))
    except (BusinessError,ValidationError) as exc:
        message,code,status=_message(exc)
        return render(request,'san_xuat_moi.jinja',d=form,error=message,error_code=code,status=status,**_choices(session,user))
    return RedirectResponse('/san-xuat/'+row.number,status_code=303)


@router.get('/san-xuat/{number}')
def detail(request: Request, number: str, user: User=Depends(web_user), session: Session=Depends(get_session)):
    return _detail(request,session,user,number)


@router.post('/san-xuat/{number}/{action}')
def action(request: Request, number: str, action: str, form=Depends(checked_form), session: Session=Depends(get_session)):
    user=request.state.user
    try:
        version=ProductionVersion(version=form.get('version')).version
        if action=='giao':
            body=ProductionAssign(version=version,technician_id=form.get('technician_id'));svc.assign(session,user,number,body.technician_id,version)
        elif action=='bat-dau':svc.start(session,user,number,version)
        elif action=='hoan-thanh':
            if len({len(form.getlist(k)) for k in ('line_no','material_product','qty_used')})!=1: raise BusinessError('BAD_FORM','Dòng vật tư thực dùng chưa đủ dữ liệu.')
            lines=[dict(line_no=n or None,product_code=c or None,qty_used=q.replace(',','.')) for n,c,q in zip(form.getlist('line_no'),form.getlist('material_product'),form.getlist('qty_used')) if n or c]
            svc.complete(session,user,number,ProductionComplete(version=version,qty_good=form.get('qty_good'),qty_scrap=form.get('qty_scrap'),void_reason=form.get('void_reason',''),lines=lines))
        elif action=='nhan-kho':svc.receive(session,user,number,ProductionReceive(version=version,qty_received=form.get('qty_received'),note=form.get('note',''),serials=sales_warranty.parse_serials(form.get('serials',''))))
        elif action=='huy':
            body=ProductionVoid(version=version,reason=form.get('reason',''));svc.void(session,user,number,body.reason,version)
        else:raise BusinessError('NOT_FOUND','Không tìm thấy thao tác.',status=404)
    except (BusinessError,ValidationError) as exc:
        message,code,status=_message(exc)
        return _detail(request,session,user,number,form,message,code,status)
    return RedirectResponse('/san-xuat/'+number+'?da_luu=1',status_code=303)
