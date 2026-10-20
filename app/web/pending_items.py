"""CXL1: routes web, quyền thực thể và CSRF/form_key theo luồng chung."""
from decimal import Decimal,InvalidOperation
from pathlib import Path

from fastapi import APIRouter,Depends,Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import attachments,alerts,pending_items as svc,products,stock_docs
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import AuditLog,PendingItem,Product,SalesReturn,ServiceTicket,StockDoc,Supplier,TechnicianReturn,User,Warehouse
from ..security import has_role
from . import checked_form,render,web_user

router=APIRouter()


def qty_text(value):
    whole,_,fraction=format(Decimal(value),'f').partition('.')
    return f'{int(whole):,}'.replace(',','.')+(','+fraction.rstrip('0') if fraction.rstrip('0') else '')


def context(user):return dict(groups=svc.GROUPS,sources=svc.SOURCES,can_edit=has_role(user,'warehouse'),can_value=svc.can_value(user),qty_text=qty_text)


def source_links(session,items):
    refs={row.source_ref for row in items};links={}
    for model,prefix in ((ServiceTicket,'/dich-vu/'),(TechnicianReturn,'/tho-tra-hang/'),(SalesReturn,'/tra-hang/'),(StockDoc,'/phieu-kho/')):
        links.update({number:prefix+str(ident) for number,ident in session.execute(select(model.number,model.id).where(model.number.in_(refs)))})
    return links


def _qty(raw):
    # Dấu chấm hàng nghìn; dấu phẩy phân cách phần lẻ, tối đa 3 số.
    import re
    value=str(raw).strip()
    if not re.fullmatch(r'(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d{1,3})?',value):raise BusinessError('BAD_QTY','Số lượng chưa hợp lệ; dùng dấu chấm hàng nghìn, dấu phẩy phần lẻ.')
    return svc.quantity(value.replace('.','').replace(',','.'))


@router.get('/cho-xu-ly')
def listing(request:Request,group:str='',warehouse:str='',min_days:str='',q:str='',status:str='DANG_CHO',user:User=Depends(web_user),session:Session=Depends(get_session)):
    try:days=int(min_days) if min_days.strip() else None
    except ValueError:raise BusinessError('BAD_DAYS','Số ngày lọc chưa hợp lệ.')
    rows=svc.search(session,user,group or None,warehouse or None,days,q[:200],status)
    totals=svc.totals(session,user)
    notices=alerts.pending_notices(session,user)
    return render(request,'cho_xu_ly_ds.jinja',**context(user),rows=rows,totals=totals,source_links=source_links(session,[row['item'] for row in rows]),
        warehouses=list(session.scalars(select(Warehouse).where(Warehouse.kind=='LOI').order_by(Warehouse.code))),
        selected_group=group,selected_warehouse=warehouse,min_days=days,q=q[:200],selected_status=status,notices=notices,config_pending_days=get_settings().pending_alert_days)


def transfer_form(request,session,user,data=None,error=None,code='',status=200):
    svc.writer(user);data=data or {}
    return render(request,'cho_xu_ly_chuyen.jinja',**context(user),d=data,error=error,error_code=code,status=status,
        warehouses=list(session.scalars(select(Warehouse).where(Warehouse.active.is_(True),Warehouse.kind.in_(('KHO','LOI'))).order_by(Warehouse.code))),
        suppliers=list(session.scalars(select(Supplier).where(Supplier.active.is_(True)).order_by(Supplier.code))))


@router.get('/cho-xu-ly/chuyen')
def transfer_page(request:Request,warehouse:str='',product:str='',user:User=Depends(web_user),session:Session=Depends(get_session)):
    svc.writer(user);p=session.get(Product,product) if product else None
    return transfer_form(request,session,user,dict(from_warehouse_code=warehouse,product_code=p.code if p else '',product_query=f'{p.code} · {p.name}' if p else '',group_code='HANG_LOI'))


@router.get('/cho-xu-ly/tim-hang')
def picker(request:Request,q:str='',user:User=Depends(web_user),session:Session=Depends(get_session)):
    svc.writer(user)
    return render(request,'cho_xu_ly_chon_hang.jinja',rows=products.search(session,q[:200],limit=20))


@router.post('/cho-xu-ly/chuyen')
def transfer(request:Request,form=Depends(checked_form),user:User=Depends(web_user),session:Session=Depends(get_session)):
    svc.writer(user)
    data={key:str(form.get(key,'')) for key in ('from_warehouse_code','warehouse_code','product_code','product_query','qty','group_code','reason','supplier_code')}
    saved=[]
    try:
        upload=form.get('photo');photo=None
        if upload and getattr(upload,'filename',''):
            photo=upload.file.read(get_settings().max_upload_mb*1024*1024+1)
            if len(photo)>get_settings().max_upload_mb*1024*1024:raise BusinessError('FILE_TOO_LARGE','Ảnh quá lớn.')
            if not attachments._prepare(photo)[1].startswith('image/'):raise BusinessError('BAD_FILE_TYPE','Chỉ nhận ảnh JPEG, PNG hoặc WEBP.')
        with stock_docs._transaction(session):
            rows=svc.transfer_in(session,user,data['from_warehouse_code'],[dict(product_code=data['product_code'],qty=_qty(data['qty']))],
                data['group_code'],data['reason'],data['supplier_code'] or None,warehouse_code=data['warehouse_code'] or None,_in_tx=True)
            if photo:saved.append(attachments.add(session,user,'pending_item',str(rows[0].id),'KHAC',upload.filename,photo,_in_tx=True))
    except Exception as exc:
        for a in saved:(Path(get_settings().files_dir)/a.path).unlink(missing_ok=True)
        if isinstance(exc,BusinessError):return transfer_form(request,session,user,data,exc.message,exc.code,exc.status)
        raise
    return RedirectResponse(f'/cho-xu-ly/{rows[0].id}',status_code=303)


def detail_page(request,session,user,ident,error=None,code='',status=200):
    row=svc.load(session,user,ident)
    history=list(session.scalars(select(AuditLog).where(AuditLog.entity=='pending_item',AuditLog.entity_id==str(row.id)).order_by(AuditLog.id.desc())))
    return render(request,'cho_xu_ly_chi_tiet.jinja',**context(user),row=row,product=session.get(Product,row.product_code),
        photos=attachments.list_for(session,user,'pending_item',str(ident)),history=history,source_links=source_links(session,[row]),
        suppliers=list(session.scalars(select(Supplier).where(Supplier.active.is_(True)).order_by(Supplier.code))),
        stock_doc=session.get(StockDoc,row.stock_doc_id) if row.stock_doc_id else None,
        creator=session.scalar(select(User.full_name).where(User.id==row.created_by)) if row.created_by else None,
        error=error,error_code=code,status=status)


@router.get('/cho-xu-ly/{ident}')
def detail(request:Request,ident:int,user:User=Depends(web_user),session:Session=Depends(get_session)):
    return detail_page(request,session,user,ident)


@router.post('/cho-xu-ly/{ident}/nhom')
def change_group(request:Request,ident:int,form=Depends(checked_form),user:User=Depends(web_user),session:Session=Depends(get_session)):
    svc.writer(user)
    try:svc.set_group(session,user,ident,str(form.get('group_code','')),int(form.get('version','0')),str(form.get('reason','')),str(form.get('supplier_code','')) or None)
    except ValueError:return detail_page(request,session,user,ident,'Trang đã cũ, hãy tải lại.','VERSION_CONFLICT',409)
    except BusinessError as exc:return detail_page(request,session,user,ident,exc.message,exc.code,exc.status)
    return RedirectResponse(f'/cho-xu-ly/{ident}',status_code=303)


@router.post('/cho-xu-ly/{ident}/tach')
def split(request:Request,ident:int,form=Depends(checked_form),user:User=Depends(web_user),session:Session=Depends(get_session)):
    svc.writer(user)
    try:child=svc.split(session,user,ident,_qty(form.get('qty','')),str(form.get('group_code','')),int(form.get('version','0')))
    except ValueError:return detail_page(request,session,user,ident,'Trang đã cũ, hãy tải lại.','VERSION_CONFLICT',409)
    except BusinessError as exc:return detail_page(request,session,user,ident,exc.message,exc.code,exc.status)
    return RedirectResponse(f'/cho-xu-ly/{child.id}',status_code=303)


@router.post('/cho-xu-ly/{ident}/anh')
def photo(request:Request,ident:int,form=Depends(checked_form),user:User=Depends(web_user),session:Session=Depends(get_session)):
    svc.writer(user);upload=form.get('photo')
    try:
        if not upload or not getattr(upload,'filename',''):raise BusinessError('EMPTY_FILE','Chọn ảnh để đính kèm.')
        data=upload.file.read(get_settings().max_upload_mb*1024*1024+1)
        if not attachments._prepare(data)[1].startswith('image/'):raise BusinessError('BAD_FILE_TYPE','Chỉ nhận ảnh JPEG, PNG hoặc WEBP.')
        attachments.add(session,user,'pending_item',str(ident),'KHAC',upload.filename,data)
    except BusinessError as exc:return detail_page(request,session,user,ident,exc.message,exc.code,exc.status)
    return RedirectResponse(f'/cho-xu-ly/{ident}',status_code=303)
