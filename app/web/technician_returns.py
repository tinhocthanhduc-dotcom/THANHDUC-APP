"""Yêu cầu trả kho, kho xác nhận số thực nhận qua lõi CK."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from types import SimpleNamespace
from sqlalchemy.orm import Session
from .. import attachments, technician_returns as svc, technician_stock
from ..db import get_session
from ..errors import BusinessError
from ..models import PartRequest, Product, TechnicianReturn, StockDoc, User, Warehouse
from ..security import has_role
from . import checked_form, render, web_user
router = APIRouter()


def detail(request, session, user, ident, error=None, code='', status=200, shown=None):
    row = svc.load(session,user,ident)
    photos={line.line_no:attachments.list_for(session,user,'technician_return_line',f'{row.id}:{line.line_no}') for line in row.lines}
    def doc_link(ident):
        return f'/phieu-kho/{ident}' if has_role(user,'warehouse') else f'/hang-toi-giu/phieu/{ident}' if 'technician' in user.roles else None
    return render(request,'tho_tra_hang.jinja',photos=photos,doc_link=doc_link,doc_numbers=dict(session.execute(select(StockDoc.id,StockDoc.number).where(StockDoc.id.in_([row.stock_doc_id,row.defect_stock_doc_id]))).all()),row=row,names=dict(session.execute(select(Product.code,Product.name)).all()),
        doc_url=(f'/phieu-kho/{row.stock_doc_id}' if has_role(user,'warehouse') else f'/hang-toi-giu/phieu/{row.stock_doc_id}' if 'technician' in user.roles else None),statuses=svc.STATUSES,can_receive=has_role(user,'warehouse'),error=error,error_code=code,status=status,d=shown or {},saved=request.query_params.get('da_luu')=='1')


def new_form(request,session,user,shown=None,error=None,code='',status=200):
    if 'technician' not in user.roles: raise BusinessError('FORBIDDEN','Chỉ thợ được tạo yêu cầu trả hàng mình giữ.',status=403)
    # Không có phiếu: chọn kho công ty từ lần cấp gần nhất của kho thợ.
    data=svc.proposal(session,SimpleNamespace(assignee_id=user.id,id=None))
    if data['warehouse']:
        latest=session.scalar(select(PartRequest).where(PartRequest.to_warehouse_code==data['warehouse'].code,PartRequest.status=='DA_CAP').order_by(PartRequest.decided_at.desc(),PartRequest.id.desc()))
        if latest:
            for row in data['rows']:row['to_warehouse_code']=latest.from_warehouse_code
    rows=shown if shown is not None else data['rows']
    return render(request,'tho_tra_hang_moi.jinja',standalone_return=True,return_rows=rows,return_warehouses=data['warehouses'],
        return_names=dict(session.execute(select(Product.code,Product.name).where(Product.code.in_([row['product_code'] for row in rows]))).all()),
        error=error,error_code=code,status=status)


@router.get('/tho-tra-hang/moi')
def new(request:Request,user:User=Depends(web_user),session:Session=Depends(get_session)):
    return new_form(request,session,user)


@router.post('/tho-tra-hang/moi')
def create(request:Request,form=Depends(checked_form),session:Session=Depends(get_session)):
    from .services import return_lines, _message
    user=request.state.user
    try:
        rows=svc.create(session,user,return_lines(form))
    except BusinessError as exc:
        message,code,status=_message(exc)
        return new_form(request,session,user,return_lines(form,photos=False),message,code,status)
    return RedirectResponse(f'/tho-tra-hang/{rows[0].id}?da_luu=1',status_code=303)


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
            quantities={(int(k[9:]) if k.startswith('qty_line_') and k[9:].isdigit() else k[4:]):str(v) for k,v in form.items() if k.startswith('qty_')}
            conditions={int(k[10:]):str(v) for k,v in form.items() if k.startswith('condition_') and k[10:].isdigit()}
            reasons={int(k[14:]):str(v) for k,v in form.items() if k.startswith('damage_reason_') and k[14:].isdigit()}
            svc.receive(session,user,ident,_version(form),quantities,str(form.get('note','')),conditions,reasons)
        elif action=='huy': svc.cancel(session,user,ident,_version(form),str(form.get('reason','')))
        else: raise BusinessError('NOT_FOUND','Không tìm thấy thao tác.',status=404)
    except BusinessError as exc:
        message,code,status=_message(exc)
        return detail(request,session,user,ident,message,code,status,shown)
    return RedirectResponse(f'/tho-tra-hang/{ident}?da_luu=1',status_code=303)


@router.post('/tho-tra-hang/{ident}/anh/{line_no}')
def upload(request:Request,ident:int,line_no:int,form=Depends(checked_form),session:Session=Depends(get_session)):
    from starlette.datastructures import UploadFile
    from ..config import get_settings
    from .services import _message
    user=request.state.user
    try:
        file=form.get('file')
        if not isinstance(file,UploadFile):raise BusinessError('EMPTY_FILE','Chọn ảnh hư hỏng.')
        attachments.add(session,user,'technician_return_line',f'{ident}:{line_no}','KHAC',file.filename or 'anh',file.file.read(get_settings().max_upload_mb*1024*1024+1))
    except BusinessError as exc:
        message,code,status=_message(exc)
        return detail(request,session,user,ident,message,code,status)
    return RedirectResponse(f'/tho-tra-hang/{ident}?da_luu=1',status_code=303)
