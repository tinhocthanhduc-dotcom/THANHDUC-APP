"""M1: web gọi cùng lõi đơn mua với API, POST dùng checked_form."""
import re
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import supplier_debts, inventory_catalogs, products, purchase_orders as svc, purchase_receipts as receipts, quotes
from ..config import DEFAULT_VAT_RATE, get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import Product, StockDoc, Supplier, User, Warehouse, VAT_RATES
from ..money_words import money_words
from ..schemas import PurchaseOrderIn, PurchaseOrderUpdate
from ..security import has_role
from . import checked_form, money, render, web_user
from .quotes import _quantity

router = APIRouter()
STATUSES = {'NHAP':'Nháp', 'DA_DAT':'Đã đặt', 'NHAN_MOT_PHAN':'Nhận một phần', 'DA_NHAN':'Đã nhận', 'DA_HUY':'Đã hủy'}


def purchase_user(user: User = Depends(web_user)):
    svc.check_reader(user)
    return user


def _form(request, session, row=None, data=None, error=None, code='', status=200, reason='', receiving=False, receive_data=None, receipt_action='', failed_receipt=None):
    if data is None:
        data = svc.snapshot(row) if row else dict(supplier_code='', order_date=quotes._today().isoformat(),
                            expected_date='', note='', version=0, lines=[])
        for line in data['lines']:
            line['unit_cost'] = money(line['unit_cost'])
    supplier = session.get(Supplier, data.get('supplier_code')) if data.get('supplier_code') else None
    codes = {line.get('product_code') for line in data['lines']}
    history=receipts.list_by_order(session,request.state.user,row.id) if row else []
    if receive_data is None and row:
        receive_data=dict(receipt_date=quotes._today().isoformat(),version=row.version,quantities={str(l.id):_quantity(l.qty-l.received_qty) for l in row.lines if l.received_qty<l.qty})
    return render(request, 'don_mua_form.jinja', o=row, d=data, statuses=STATUSES, vat_rates=VAT_RATES,
        supplier_debt=supplier_debts.order_balance(session,request.state.user,row.id) if row and has_role(request.state.user,'finance') else None,
        history=history,stock_numbers=dict(session.execute(select(StockDoc.id,StockDoc.number).where(StockDoc.id.in_([r.stock_doc_id for r in history]))).all()),
        receiving=receiving,receive_data=receive_data or {},quantity=_quantity,receipt_action=receipt_action,failed_receipt=failed_receipt,
        payable=receipts.payable_by_order(session,request.state.user,row.id) if row else 0,
        supplier_name=supplier.name if supplier else '',
        product_names=dict(session.execute(select(Product.code, Product.name).where(Product.code.in_(codes))).all()),
        warehouses=list(session.scalars(select(Warehouse).order_by(Warehouse.code))),
        can_write=has_role(request.state.user, *svc.WRITERS), editable=row is None or row.status=='NHAP',
        error=error, error_code=code, status=status, reason=reason, saved=request.query_params.get('da_luu')=='1' and not error)


def _version(form):
    try:
        value=int(form.get('version',''))
        if value<1:raise ValueError
        return value
    except (TypeError,ValueError):
        raise BusinessError('VERSION_REQUIRED','Phải gửi phiên bản đơn mua, hãy tải lại.')


def _raw(form):
    fields = {k:form.getlist(k) for k in ('product_code','qty','unit_cost')}
    lines=[]
    for i in range(max((len(v) for v in fields.values()), default=0)):
        line={k:str(v[i]) if i<len(v) else '' for k,v in fields.items()}
        line.update(warehouse_code=str(form.get(f'warehouse_{i}','')), vat_rate=str(form.get(f'vat_{i}',DEFAULT_VAT_RATE)))
        lines.append(line)
    return {**{k:str(form.get(k,'')) for k in ('supplier_code','order_date','expected_date','note','version')}, 'lines':lines}


def _body(data):
    lines=[]
    for n,line in enumerate(data['lines'],1):
        price=line['unit_cost'].strip()
        if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)',price):
            raise BusinessError('BAD_PRICE',f'Đơn giá dòng {n} phải là số nguyên đồng, không âm.')
        try:vat=int(line['vat_rate'])
        except ValueError:raise BusinessError('BAD_VAT',f'VAT dòng {n} phải là 0, 5, 8 hoặc 10%.')
        lines.append({**line,'qty':line['qty'].replace(',','.'),'unit_cost':int(price.replace('.','')),'vat_rate':vat})
    return dict(supplier_code=data['supplier_code'], order_date=data['order_date'], expected_date=data['expected_date'] or None,
                note=data['note'], lines=lines)


def _error(exc):
    if isinstance(exc, BusinessError):
        message=exc.message
        if exc.code=='NEGATIVE_STOCK':
            d=exc.details
            message+=f" Mã {d.get('product','')}, kho {d.get('warehouse','')}: tồn {d.get('available','0')}, cần {d.get('requested','0')}."
        return message,exc.code,exc.status
    loc=exc.errors()[0]['loc']; field=loc[-1]
    labels={'qty':'Số lượng','unit_cost':'Đơn giá','vat_rate':'VAT','warehouse_code':'Kho nhận','product_code':'Mã hàng',
            'supplier_code':'Nhà cung cấp','order_date':'Ngày đơn','expected_date':'Ngày dự kiến','version':'Phiên bản'}
    return labels.get(field,'Dữ liệu')+' chưa hợp lệ, hãy kiểm tra lại.','BAD_FORM',400


@router.get('/don-mua')
def listing(request: Request, q: str='', status: str='', supplier_code: str='', user: User=Depends(purchase_user), session: Session=Depends(get_session)):
    rows=svc.search(session,user,q[:200],status or None,supplier_code or None)
    return render(request,'don_mua_ds.jinja',rows=rows,q=q,selected_status=status,statuses=STATUSES,supplier_code=supplier_code,
        suppliers=list(session.scalars(select(Supplier).order_by(Supplier.code))),can_write=has_role(user,*svc.WRITERS))


@router.get('/don-mua/tim-ncc')
def pick_supplier(request: Request, q: str='', user: User=Depends(purchase_user), session: Session=Depends(get_session)):
    return render(request,'don_mua_chon.jinja',kind='supplier',rows=[s for s in inventory_catalogs.search(session,Supplier,q[:200]) if s.active],costs={})


@router.get('/don-mua/tim-hang')
def pick_product(request: Request, q: str='', user: User=Depends(purchase_user), session: Session=Depends(get_session)):
    rows=products.search(session,q[:200],active_only=True,limit=20)
    return render(request,'don_mua_chon.jinja',kind='product',rows=rows,costs={p.code:svc.suggest_cost(session,user,p.code) for p in rows})


@router.get('/don-mua/moi')
def new(request: Request, user: User=Depends(purchase_user), session: Session=Depends(get_session)):
    svc.check_writer(user)
    return _form(request,session)


@router.post('/don-mua/moi')
def create(request: Request, form=Depends(checked_form), session: Session=Depends(get_session)):
    user=request.state.user;svc.check_writer(user);data=_raw(form)
    try:row=svc.create(session,user,PurchaseOrderIn(**_body(data)))
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _form(request,session,data=data,error=message,code=code,status=status)
    return RedirectResponse(f'/don-mua/{row.id}?da_luu=1',status_code=303)


@router.get('/don-mua/{order_id}')
def view(request: Request, order_id: int, user: User=Depends(purchase_user), session: Session=Depends(get_session)):
    return _form(request,session,row=svc.load(session,user,order_id))


@router.post('/don-mua/{order_id}')
def update(request: Request, order_id: int, form=Depends(checked_form), session: Session=Depends(get_session)):
    user=request.state.user;svc.check_writer(user);data=_raw(form)
    try:svc.update(session,user,order_id,PurchaseOrderUpdate(version=_version(form),**_body(data)))
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _form(request,session,row=svc.load(session,user,order_id),data=data,error=message,code=code,status=status)
    return RedirectResponse(f'/don-mua/{order_id}?da_luu=1',status_code=303)


@router.post('/don-mua/{order_id}/{action}')
def action(request: Request, order_id: int, action: str, form=Depends(checked_form), session: Session=Depends(get_session)):
    user=request.state.user;svc.check_writer(user)
    try:
        if action=='order':svc.order(session,user,order_id,_version(form))
        elif action=='close':receipts.close(session,user,order_id,str(form.get('reason','')),_version(form))
        elif action=='cancel':svc.cancel(session,user,order_id,str(form.get('reason','')),_version(form))
        elif action=='nhan':return _receive(request,session,user,order_id,form)
        else:raise BusinessError('NOT_FOUND','Không có thao tác này.',status=404)
    except BusinessError as exc:
        return _form(request,session,row=svc.load(session,user,order_id),error=exc.message,code=exc.code,status=exc.status,reason=str(form.get('reason','')),receipt_action=action)
    return RedirectResponse(f'/don-mua/{order_id}?da_luu=1',status_code=303)


@router.get('/don-mua/{order_id}/in')
def print_order(request: Request, order_id: int, user: User=Depends(purchase_user), session: Session=Depends(get_session)):
    row=svc.load(session,user,order_id)
    codes={line.product_code for line in row.lines}
    return render(request,'don_mua_in.jinja',o=row,supplier=session.get(Supplier,row.supplier_code),company=get_settings(),
        products={p.code:p for p in session.scalars(select(Product).where(Product.code.in_(codes)))},
        words=money_words(row.total),quantity=_quantity,statuses=STATUSES)


@router.get('/don-mua/{order_id}/nhan')
def receive_form(request: Request, order_id:int, user:User=Depends(purchase_user), session:Session=Depends(get_session)):
    svc.check_writer(user)
    row=svc.load(session,user,order_id)
    if row.status not in ('DA_DAT','NHAN_MOT_PHAN'):
        raise BusinessError('BAD_STATUS','Chỉ nhận hàng cho đơn Đã đặt hoặc Nhận một phần.',status=409)
    return _form(request,session,row=row,receiving=True)


def _receive(request,session,user,order_id,form):
    from ..schemas import PurchaseReceiveIn
    raw=dict(receipt_date=str(form.get('receipt_date','')),version=str(form.get('version','')),
             quantities={str(key)[4:]:str(value) for key,value in form.items() if str(key).startswith('qty_')})
    try:
        lines=[]
        for ident,value in raw['quantities'].items():
            try:
                qty=Decimal(value.replace(',','.'))
                if not qty.is_finite() or qty<0:raise InvalidOperation
                line_id=int(ident)
            except (ValueError,InvalidOperation):
                raise BusinessError('BAD_QTY','Số lượng nhận phải là số không âm, tối đa 3 chữ số lẻ; nhập 0 để bỏ qua.')
            if qty:lines.append(dict(order_line_id=line_id,qty=qty))
        if not lines:raise BusinessError('EMPTY_RECEIPT','Chọn ít nhất một dòng có số lượng nhận lớn hơn 0.')
        body=PurchaseReceiveIn(version=_version(form),receipt_date=raw['receipt_date'],lines=lines)
        receipts.receive(session,user,order_id,body.version,body.receipt_date,body.lines)
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _form(request,session,row=svc.load(session,user,order_id),error=message,code=code,status=status,
                     receiving=True,receive_data=raw)
    return RedirectResponse(f'/don-mua/{order_id}?da_luu=1#lan-nhan',status_code=303)


@router.post('/don-mua/lan-nhan/{receipt_id}/huy')
def void_receipt(request:Request,receipt_id:int,form=Depends(checked_form),session:Session=Depends(get_session)):
    from ..models import PurchaseReceipt
    user=request.state.user;svc.check_writer(user)
    row=session.get(PurchaseReceipt,receipt_id)
    if row is None:raise BusinessError('NOT_FOUND','Không tìm thấy lần nhận.',status=404)
    order_id=row.order_id
    reason=str(form.get('reason',''))
    try:receipts.void(session,user,receipt_id,reason,_version(form))
    except BusinessError as exc:
        message,code,status=_error(exc)
        return _form(request,session,row=svc.load(session,user,order_id),error=message,code=code,status=status,
                     reason=reason,receipt_action='void',failed_receipt=receipt_id)
    return RedirectResponse(f'/don-mua/{order_id}?da_luu=1#lan-nhan',status_code=303)
