"""CXL1: routes web, quyền thực thể và CSRF/form_key theo luồng chung."""
from decimal import Decimal,InvalidOperation
from pathlib import Path

from fastapi import APIRouter,Depends,Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import attachments,alerts,pending_items as svc,pending_dispositions as dispositions,products,stock_docs,pending_liquidation
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import AuditLog,PendingDisposition,PendingItem,Product,SalesReturn,ServiceTicket,StockDoc,Supplier,TechnicianReturn,User,Warehouse
from ..security import has_role
from . import checked_form,money,nav,render,web_user

router=APIRouter()


def qty_text(value):
    whole,_,fraction=format(Decimal(value),'f').partition('.')
    return f'{int(whole):,}'.replace(',','.')+(','+fraction.rstrip('0') if fraction.rstrip('0') else '')


def context(user):return dict(groups=svc.GROUPS,sources=svc.SOURCES,can_edit=has_role(user,'warehouse'),can_value=svc.can_value(user),qty_text=qty_text)


def source_links(session,items,user=None):
    # F2: chỉ trả link người xem mở được; thiếu link thì template hiện số chứng từ dạng chữ.
    refs={row.source_ref for row in items};links={}
    for model,prefix in ((ServiceTicket,'/dich-vu/'),(TechnicianReturn,'/tho-tra-hang/'),(SalesReturn,'/tra-hang/'),(StockDoc,'/phieu-kho/')):
        if user is not None and not nav.allowed(user,prefix.rstrip('/')):
            if model is ServiceTicket and nav.allowed(user,'/xem-dich-vu'):prefix='/xem-dich-vu/'
            else:continue
        links.update({number:prefix+str(ident) for number,ident in session.execute(select(model.number,model.id).where(model.number.in_(refs)))})
    return links


def _qty(raw):
    # Dấu chấm hàng nghìn; dấu phẩy phân cách phần lẻ, tối đa 3 số.
    import re
    value=str(raw).strip()
    if not re.fullmatch(r'(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d{1,3})?',value):raise BusinessError('BAD_QTY','Số lượng chưa hợp lệ; dùng dấu chấm hàng nghìn, dấu phẩy phần lẻ.')
    return svc.quantity(value.replace('.','').replace(',','.'))


TABS=(('Đang chờ','DANG_CHO',''),('Đang ở NCC','DANG_CHO','ncc'),('Chờ duyệt hủy','DANG_CHO','huy'),('Xong','XONG',''),('Đã hủy','DA_HUY',''),('Tất cả','tat-ca',''))
TILE_GROUPS=('HANG_LOI','CHO_BH_NCC','THANH_LY','CHUA_PHAN_LOAI')


@router.get('/cho-xu-ly')
def listing(request:Request,group:str='',warehouse:str='',min_days:str='',q:str='',status:str='DANG_CHO',view:str='',trang:str='',so:str='',user:User=Depends(web_user),session:Session=Depends(get_session)):
    from . import paging
    try:days=int(min_days) if min_days.strip() else None
    except ValueError:raise BusinessError('BAD_DAYS','Số ngày lọc chưa hợp lệ.')
    # "Tất cả" gửi status=tat-ca vì ô rỗng bị bỏ khỏi địa chỉ và mặc định là Đang chờ.
    wanted='' if status=='tat-ca' else status
    view=view if view in ('ncc','huy') else ''
    rows=svc.search(session,user,group or None,warehouse or None,days,q[:200],wanted,view)
    # Tab đếm theo cùng nhóm, kho, số ngày, ô tìm.
    every=svc.search(session,user,group or None,warehouse or None,days,q[:200],'','')
    by_view={v:len(svc.search(session,user,group or None,warehouse or None,days,q[:200],'DANG_CHO',v)) for v in ('ncc','huy')}
    def tab_count(st,v):
        return by_view[v] if v else len(every) if st=='tat-ca' else sum(1 for e in every if e['item'].status==st)
    query=dict(group=group,warehouse=warehouse,min_days='' if days is None else days,q=q[:200],status='' if status=='DANG_CHO' else status,view=view)
    page,shown=paging.slice_rows(rows,trang,so,query)
    link=paging.linker('/cho-xu-ly',query)
    tabs=[dict(label=label,n=tab_count(st,v),href=link(status='' if st=='DANG_CHO' else st,view=v),on=(status==st and view==v)) for label,st,v in TABS]
    totals=svc.totals(session,user)
    can_value=svc.can_value(user)
    tiles=[dict(label=svc.GROUPS[code],n=totals.get(code,{}).get('count',0),
                sub=(money(totals.get(code,{}).get('value',0))+' đ') if can_value else f"{totals.get(code,{}).get('overdue',0)} hồ sơ quá hạn",
                href=link(group=code,status='',view=''),on=group==code,tone='huy' if totals.get(code,{}).get('overdue') else '') for code in TILE_GROUPS]
    chips=[dict(label=label,href=link(**{key:''})) for key,label in (('group',svc.GROUPS.get(group,'')),('warehouse','Kho '+warehouse),
        ('min_days',f'Nằm kho quá {days} ngày')) if query.get(key) not in ('',None)]
    notices=alerts.pending_notices(session,user)
    return render(request,'cho_xu_ly_ds.jinja',**context(user),rows=shown,page=page,tabs=tabs,tiles=tiles,chips=chips,link=link,totals=totals,
        source_links=source_links(session,[row['item'] for row in shown],user),
        warehouses=list(session.scalars(select(Warehouse).where(Warehouse.kind=='LOI').order_by(Warehouse.code))),
        sold_quantities=pending_liquidation.sold_quantities(session,[r['item'].id for r in shown]),view=view,supplier_names=dict(session.execute(select(Supplier.code,Supplier.name)).all()),
        selected_group=group,selected_warehouse=warehouse,min_days=days,q=q[:200],selected_status=status,notices=notices,config_pending_days=get_settings().pending_alert_days,
        value_sum=sum(e['item'].qty_left*e['item'].unit_cost for e in rows) if can_value else 0,
        can_approve_returns=has_role(user,'finance','director'))


def transfer_form(request,session,user,data=None,error=None,code='',status=200):
    svc.writer(user);data=data or {}
    return render(request,'cho_xu_ly_chuyen.jinja',**context(user),d=data,error=error,error_code=code,status=status,
        warehouses=list(session.scalars(select(Warehouse).where(Warehouse.active.is_(True),Warehouse.kind.in_(('KHO','LOI')),Warehouse.supplier_hold.is_(False),Warehouse.recovered_only.is_(False)).order_by(Warehouse.code))),
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
    warehouse=session.get(Warehouse,row.warehouse_code)
    treatments=list(session.scalars(select(PendingDisposition).where(PendingDisposition.item_id==row.id).order_by(PendingDisposition.id.desc())))
    disposition_photos={xl.id:attachments.list_for(session,user,'pending_disposition',str(xl.id)) for xl in treatments}
    return render(request,'cho_xu_ly_chi_tiet.jinja',**context(user),row=row,holding=warehouse.supplier_hold,recovered=warehouse.recovered_only,available=max(0,row.qty_left-dispositions.reserved(session,row.id)),
        treatments=treatments,disposition_photos=disposition_photos,disposition_kinds=dispositions.KINDS,disposition_statuses=dispositions.STATUSES,can_decide_return=has_role(user,'finance'),can_approve=has_role(user,'director'),
        destinations=list(session.scalars(select(Warehouse).where(Warehouse.active.is_(True),Warehouse.kind.in_(('KHO','LOI'))).order_by(Warehouse.code))),
        default_warehouse=get_settings().sales_default_warehouse,product=session.get(Product,row.product_code),
        photos=attachments.list_for(session,user,'pending_item',str(ident)),history=history,source_links=source_links(session,[row],user),
        suppliers=list(session.scalars(select(Supplier).where(Supplier.active.is_(True)).order_by(Supplier.code))),
        stock_doc=session.get(StockDoc,row.stock_doc_id) if row.stock_doc_id else None,
        creator=session.scalar(select(User.full_name).where(User.id==row.created_by)) if row.created_by else None,
        error=error,error_code=code,status=status)


@router.get('/cho-xu-ly/tim-ncc')
def supplier_picker(request:Request,q:str='',user:User=Depends(web_user),session:Session=Depends(get_session)):
    from .. import inventory_catalogs
    svc.writer(user)
    return render(request,'cho_xu_ly_chon_ncc.jinja',rows=[row for row in inventory_catalogs.search(session,Supplier,q[:200]) if row.active][:20])


@router.get('/cho-xu-ly/duyet-huy')
def approvals(request:Request,user:User=Depends(web_user),session:Session=Depends(get_session)):
    from ..models import StockBalance
    dispositions.director(user)
    rows=session.execute(select(PendingDisposition,PendingItem,Product.name,StockBalance.avg_cost).join(PendingItem,PendingItem.id==PendingDisposition.item_id).join(Product,Product.code==PendingItem.product_code).outerjoin(
        StockBalance,(StockBalance.warehouse_code==PendingItem.warehouse_code)&(StockBalance.product_code==PendingItem.product_code)).where(PendingDisposition.kind=='HUY',PendingDisposition.status=='CHO_DUYET').order_by(PendingDisposition.requested_at,PendingDisposition.id)).all()
    photos={xl.id:attachments.list_for(session,user,'pending_disposition',str(xl.id)) for xl,item,name,cost in rows}
    return render(request,'cho_xu_ly_duyet_huy.jinja',rows=rows,photos=photos,qty_text=qty_text)


@router.post('/cho-xu-ly/{ident}/xu-ly')
def process(request:Request,ident:int,form=Depends(checked_form),user:User=Depends(web_user),session:Session=Depends(get_session)):
    svc.writer(user);paths=[]
    try:
        upload=form.get('photo');photo=None
        if upload and getattr(upload,'filename',''):
            photo=upload.file.read(get_settings().max_upload_mb*1024*1024+1)
            if len(photo)>get_settings().max_upload_mb*1024*1024:raise BusinessError('FILE_TOO_LARGE','Ảnh quá lớn.')
            if not attachments._prepare(photo)[1].startswith('image/'):raise BusinessError('BAD_FILE_TYPE','Chỉ nhận ảnh JPEG, PNG hoặc WEBP.')
        with stock_docs._transaction(session):
            row=dispositions.create(session,user,ident,str(form.get('kind','')),_qty(form.get('qty','')),int(form.get('version','0')),
                to_warehouse_code=str(form.get('to_warehouse_code','')) or None,supplier_code=(str(form.get('supplier_code','')) if form.get('kind') in ('GUI_NCC','TRA_NCC') else None),
                reason=str(form.get('reason','')),amount=str(form.get('amount','0')).replace('.',''),vat_rate=str(form.get('vat_rate','0')),_in_tx=True)
            if photo:
                a=attachments.add(session,user,'pending_disposition',str(row.id),'KHAC',upload.filename,photo,_in_tx=True)
                paths.append(Path(get_settings().files_dir)/a.path)
    except Exception as exc:
        for path in paths:path.unlink(missing_ok=True)
        if isinstance(exc,ValueError):return detail_page(request,session,user,ident,'Trang đã cũ, hãy tải lại.','VERSION_CONFLICT',409)
        if isinstance(exc,BusinessError):return detail_page(request,session,user,ident,exc.message,exc.code,exc.status)
        raise
    return RedirectResponse(f'/cho-xu-ly/{ident}#xl-{row.id}',status_code=303)


@router.post('/cho-xu-ly/xl/{ident}/duyet')
def approve(request:Request,ident:int,form=Depends(checked_form),user:User=Depends(web_user),session:Session=Depends(get_session)):
    dispositions._decider(session,user,ident)
    kind=session.get(PendingDisposition,ident).kind
    try:dispositions.approve(session,user,ident,int(form.get('version','0')))
    except ValueError:raise BusinessError('VERSION_CONFLICT','Trang đã cũ, hãy tải lại.',status=409)
    return RedirectResponse('/cho-xu-ly/duyet-tra-ncc' if kind=='TRA_NCC' else '/cho-xu-ly/duyet-huy',status_code=303)


@router.post('/cho-xu-ly/xl/{ident}/tu-choi')
def reject(request:Request,ident:int,form=Depends(checked_form),user:User=Depends(web_user),session:Session=Depends(get_session)):
    dispositions._decider(session,user,ident)
    kind=session.get(PendingDisposition,ident).kind
    try:dispositions.reject(session,user,ident,int(form.get('version','0')),str(form.get('reason','')))
    except ValueError:raise BusinessError('VERSION_CONFLICT','Trang đã cũ, hãy tải lại.',status=409)
    return RedirectResponse('/cho-xu-ly/duyet-tra-ncc' if kind=='TRA_NCC' else '/cho-xu-ly/duyet-huy',status_code=303)


@router.post('/cho-xu-ly/xl/{ident}/huy')
def void_disposition(request:Request,ident:int,form=Depends(checked_form),user:User=Depends(web_user),session:Session=Depends(get_session)):
    dispositions._decider(session,user,ident)
    try:row=dispositions.void(session,user,ident,int(form.get('version','0')),str(form.get('reason','')))
    except ValueError:raise BusinessError('VERSION_CONFLICT','Trang đã cũ, hãy tải lại.',status=409)
    return RedirectResponse(f'/cho-xu-ly/{row.item_id}#xl-{ident}',status_code=303)


@router.get('/cho-xu-ly/duyet-tra-ncc')
def supplier_return_approvals(request:Request,user:User=Depends(web_user),session:Session=Depends(get_session)):
    from ..models import StockBalance
    if not has_role(user,'finance'):raise BusinessError('FORBIDDEN','Chỉ kế toán hoặc giám đốc duyệt trả NCC.',status=403)
    rows=session.execute(select(PendingDisposition,PendingItem,Product.name,Supplier.name).join(PendingItem,PendingItem.id==PendingDisposition.item_id).join(Product,Product.code==PendingItem.product_code).join(Supplier,Supplier.code==PendingDisposition.supplier_code).where(PendingDisposition.kind=='TRA_NCC',PendingDisposition.status=='CHO_DUYET').order_by(PendingDisposition.requested_at,PendingDisposition.id)).all()
    return render(request,'cho_xu_ly_duyet_tra_ncc.jinja',rows=rows,qty_text=qty_text)


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
