"""B3: màn hình đơn bán; mọi thay đổi do lõi B2 thực hiện."""
import re
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import customers, products, quotes, sales_orders as svc, stock_reservations
from ..config import get_settings
from ..config import DEFAULT_VAT_RATE
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, Product, SalesOrder, SalesDelivery, StockDoc, User, VAT_RATES, Warehouse
from ..money_words import money_words
from ..schemas import SalesOrderFromQuote, SalesOrderIn, SalesOrderOut, SalesOrderUpdate, SalesOrderPromise, SalesDeliveryIn
from ..security import has_role
from . import checked_form, money, render, web_user
from .quotes import _quantity
from .receipts import order_context

from . import next_steps

router = APIRouter()
STATUSES = {'NHAP':'Nháp','DA_CHOT':'Đã chốt','GIAO_MOT_PHAN':'Giao một phần','DA_GIAO':'Đã giao','DA_HUY':'Đã hủy'}


def order_user(user: User = Depends(web_user)):
    if not has_role(user,'sales','warehouse','finance'):
        raise BusinessError('FORBIDDEN','Chỉ kinh doanh, kho, kế toán hoặc giám đốc được xem đơn bán.',status=403)
    return user


def _writer(user):
    if not has_role(user,'sales'):
        raise BusinessError('FORBIDDEN','Chỉ kinh doanh hoặc giám đốc được sửa đơn bán.',status=403)


def _version(form):
    try:
        value = int(form.get('version',''))
        if value < 1:
            raise ValueError
        return value
    except (TypeError,ValueError):
        raise BusinessError('VERSION_REQUIRED','Phải gửi phiên bản chứng từ, hãy tải lại trang.')


def _reservation_id(form):
    value=str(form.get('reservation_id',''))
    if not value.isdigit() or len(value)>19 or not 0<int(value)<=9_223_372_036_854_775_807:
        raise BusinessError('BAD_RESERVATION','Dòng giữ hàng chưa hợp lệ, hãy tải lại trang.')
    return int(value)


def _error(exc):
    if isinstance(exc,BusinessError):
        message = exc.message
        if exc.code == 'NEGATIVE_STOCK':
            d=exc.details
            message += f" Mã hàng {d.get('product','')}, kho {d.get('warehouse','')}: tồn {_quantity(Decimal(d.get('available','0')))}, cần {_quantity(Decimal(d.get('requested','0')))}."
        return message,exc.code,exc.status
    loc=exc.errors()[0]['loc']
    labels={'qty':'Số lượng','unit_price':'Đơn giá','vat_rate':'VAT','kind':'Loại dòng','warehouse_code':'Kho',
            'customer_code':'Khách hàng','contact_name':'Người liên hệ','delivery_address':'Địa chỉ giao',
            'description':'Mô tả','unit':'Đơn vị','product_code':'Mã hàng','lines':'Dòng đơn bán','warehouses':'Kho'}
    message=labels.get(str(loc[-1]),'Dữ liệu')+' chưa hợp lệ, hãy kiểm tra lại.'
    if len(loc)>1 and isinstance(loc[1],int) and loc[0]=='lines':
        message=f'Dòng {loc[1]+1}: '+message
    return message,'BAD_FORM',400


def _warehouses(session):
    return list(session.scalars(select(Warehouse).where(Warehouse.kind == 'KHO').order_by(Warehouse.code)))


def _active_order(session, quote_id):
    return session.execute(select(SalesOrder.id,SalesOrder.number).where(SalesOrder.quote_id==quote_id,
                           SalesOrder.status!='DA_HUY')).first()


def _form(request,session,data=None,order=None,error=None,code='',status=200,action_form=None,action='',receipt_form=None,receipt_decision=None,failed_receipt_id=None,failed_receipt_action=''):
    if data is None:
        data=SalesOrderOut.model_validate(order).model_dump(mode='json') if order else dict(
            customer_code='',contact_name='',delivery_address='',note='',promised_date='',version=0,lines=[])
        for line in data['lines']:
            line['unit_price']=money(line['unit_price'])
    customer=session.get(Customer,data['customer_code']) if data['customer_code'] else None
    codes={l.get('product_code') for l in data['lines'] if l.get('product_code')}
    shipments=svc.deliveries(session,order.id) if order else []
    ids={l.stock_doc_id for g in shipments for l in g.lines if l.stock_doc_id}
    source=quotes.load(session,request.state.user,order.quote_id) if order and order.quote_id and has_role(request.state.user,'sales','finance') else None
    receipt_ctx=order_context(session,request.state.user,order,receipt_form,receipt_decision,failed_receipt_id,failed_receipt_action)
    return render(request,'don_ban_form.jinja',deliveries=shipments,delivery_state=svc.remaining_lines(session,order) if order else {},
                  can_void_delivery=has_role(request.state.user,'director'),
                  can_close=bool(order and (has_role(request.state.user,'director') or (has_role(request.state.user,'sales') and order.created_by==request.state.user.id))),o=order,d=data,statuses=STATUSES,vat_rates=VAT_RATES,
                  customer_name=customer.name if customer else '',source=source,
                  reservations=stock_reservations.for_order(session,order) if order else {},
                  stock_values=stock_reservations.quantities(session,[(l.get('warehouse_code'),l.get('product_code')) for l in data['lines'] if l.get('product_code') and l.get('warehouse_code')]),
                  can_keep=bool(order and stock_reservations.can_keep(request.state.user,order)),can_release=has_role(request.state.user,'warehouse'),
                  product_names=dict(session.execute(select(Product.code,Product.name).where(Product.code.in_(codes))).all()),
                  next_step=next_steps.order(request.state.user,order,receipt_ctx['available']),stock_numbers=dict(session.execute(select(StockDoc.id,StockDoc.number).where(StockDoc.id.in_(ids))).all()),
                  warehouses=_warehouses(session),can_write=has_role(request.state.user,'sales'),
                  can_deliver=has_role(request.state.user,'sales','warehouse'),editable=order is None or order.status=='NHAP',
                  error=error,error_code=code,status=status,action_form=action_form or {},failed_action=action,
                  saved=request.query_params.get('da_luu')=='1' and not error,unsaved=bool(error),**receipt_ctx)


def _raw(form):
    fields={key:form.getlist(key) for key in ('product_code','description','qty','unit','unit_price')}
    lines=[]
    for i in range(max((len(v) for v in fields.values()),default=0)):
        line={key:str(values[i]) if i<len(values) else '' for key,values in fields.items()}
        line.update(kind=str(form.get(f'kind_{i}','HANG')),warehouse_code=str(form.get(f'warehouse_{i}','')),
                    vat_rate=str(form.get(f'vat_{i}',str(DEFAULT_VAT_RATE))))
        lines.append(line)
    return dict(customer_code=str(form.get('customer_code','')),contact_name=str(form.get('contact_name','')),
                delivery_address=str(form.get('delivery_address','')),note=str(form.get('note','')),promised_date=str(form.get('promised_date','')),
                version=str(form.get('version','')),lines=lines)


def _body(data):
    lines=[]
    for i,line in enumerate(data['lines'],1):
        price=line['unit_price'].strip()
        if price and not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)',price):
            raise BusinessError('BAD_PRICE',f'Đơn giá dòng {i} chưa hợp lệ; nhập số nguyên đồng.')
        try:
            vat=int(line['vat_rate'])
        except ValueError:
            raise BusinessError('BAD_VAT',f'VAT dòng {i} phải là 0, 5, 8 hoặc 10%.')
        lines.append({**line,'qty':line['qty'].replace(',','.'),'unit_price':int(price.replace('.','')) if price else None,'vat_rate':vat})
    return dict(contact_name=data['contact_name'],delivery_address=data['delivery_address'],note=data['note'],lines=lines,
                promised_date=data.get('promised_date') or None)


@router.get('/don-ban')
def listing(request: Request,q: str='',status: str='',phan: int=0,
            from_date: str=Query(default='',alias='from'),to_date: str=Query(default='',alias='to'),holding: bool=False,
            user: User=Depends(order_user),session: Session=Depends(get_session)):
    error=code=None
    http_status=200
    try:
        try:
            start,end=(date.fromisoformat(v) if v else None for v in (from_date,to_date))
        except ValueError:
            raise BusinessError('BAD_DATE_RANGE','Khoảng ngày chưa hợp lệ, hãy nhập ngày theo lịch.')
        rows=svc.search(session,user,q[:200],status or None,from_date=start,to_date=end,holding=holding)
    except BusinessError as exc:
        rows=[]
        error,code,http_status=_error(exc)
    names=dict(session.execute(select(Customer.code,Customer.name).where(Customer.code.in_({r.customer_code for r in rows}))).all())
    return render(request,'don_ban_dong.jinja' if phan else 'don_ban_ds.jinja',rows=rows,names=names,q=q,
                  selected_status=status,statuses=STATUSES,from_date=from_date,to_date=to_date,holding=holding,today=svc._today(),
                  can_write=has_role(user,'sales'),can_receivables=has_role(user,'sales','finance'),error=error,error_code=code,status=http_status)


@router.get('/don-ban/tim-khach')
def pick_customer(request: Request,q: str='',user: User=Depends(order_user),session: Session=Depends(get_session)):
    return render(request,'don_ban_chon.jinja',kind='customer',rows=[c for c in customers.search(session,q[:200]) if c.status=='DANG_DUNG'])


@router.get('/don-ban/tim-hang')
def pick_product(request: Request,q: str='',warehouse: str='',user: User=Depends(order_user),session: Session=Depends(get_session)):
    rows=products.search(session,q[:200],active_only=True,limit=20)
    return render(request,'don_ban_chon.jinja',kind='product',rows=rows,warehouse=warehouse,availability=stock_reservations.picker(session,rows,warehouse))


@router.get('/don-ban/moi')
def new(request: Request,user: User=Depends(order_user),session: Session=Depends(get_session)):
    _writer(user)
    return _form(request,session)


@router.post('/don-ban/moi')
def create(request: Request,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    _writer(user)
    raw=_raw(form)
    try:
        body=_body(raw)
        body['delivery_address']=body['delivery_address'] or None
        order=svc.create(session,user,SalesOrderIn(customer_code=raw['customer_code'],**body))
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _form(request,session,raw,error=message,code=code,status=status)
    return RedirectResponse(f'/don-ban/{order.id}?da_luu=1',status_code=303)


def _quote_form(request,session,quote,data=None,error=None,code='',status=200):
    customer=session.get(Customer,quote.customer_code)
    data=data if data is not None else dict(version=quote.version,contact_name=quote.contact_name,
        delivery_address=customer.address,note=quote.note,promised_date='',warehouses={})
    return render(request,'don_ban_tu_bao_gia.jinja',q=quote,d=data,customer=customer,quantity=_quantity,
                  warehouses=_warehouses(session),linked=_active_order(session,quote.id),
                  next_step=next_steps.block(next_steps.form('Tạo đơn bán','order-from-quote',f'/don-ban/tu-bao-gia/{quote.id}')) if quote.status=='CHAP_NHAN' and not _active_order(session,quote.id) else None,
                  error=error,error_code=code,status=status)


@router.get('/don-ban/tu-bao-gia/{quote_id}')
def from_quote_form(request: Request,quote_id: int,user: User=Depends(order_user),session: Session=Depends(get_session)):
    _writer(user)
    quote=quotes.load(session,user,quote_id)
    linked=_active_order(session,quote_id)
    if linked:
        return RedirectResponse(f'/don-ban/{linked.id}',status_code=303)
    return _quote_form(request,session,quote)


@router.post('/don-ban/tu-bao-gia/{quote_id}')
def from_quote(request: Request,quote_id: int,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    _writer(user)
    source=quotes.load(session,user,quote_id)
    customer=session.get(Customer,source.customer_code)
    raw=dict(version=str(form.get('version','')),contact_name=str(form.get('contact_name',source.contact_name)),
             delivery_address=str(form.get('delivery_address',customer.address)),note=str(form.get('note',source.note)),promised_date=str(form.get('promised_date','')),
             warehouses={int(k[10:]):str(v) for k,v in form.items() if re.fullmatch(r'warehouse_[0-9]+',k)})
    try:
        body=SalesOrderFromQuote(**{**raw,'version':_version(form),'promised_date':raw['promised_date'] or None})
        order=svc.create_from_quote(session,user,quote_id,body)
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _quote_form(request,session,quotes.load(session,user,quote_id),raw,error=message,code=code,status=status)
    return RedirectResponse(f'/don-ban/{order.id}?da_luu=1',status_code=303)


@router.get('/don-ban/{order_id}')
def view(request: Request,order_id: int,user: User=Depends(order_user),session: Session=Depends(get_session)):
    return _form(request,session,order=svc.load(session,user,order_id))


@router.post('/don-ban/{order_id}')
def update(request: Request,order_id: int,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    _writer(user)
    order=svc.load(session,user,order_id)
    raw=_raw(form)
    raw['customer_code']=order.customer_code  # B2 không cho đổi khách của đơn đã lập.
    try:
        svc.update_lines(session,user,order_id,SalesOrderUpdate(version=_version(form),**_body(raw)))
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _form(request,session,raw,order=svc.load(session,user,order_id),error=message,code=code,status=status)
    return RedirectResponse(f'/don-ban/{order_id}?da_luu=1',status_code=303)


@router.post('/don-ban/{order_id}/{action}')
def action(request: Request,order_id: int,action: str,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    if action in ('reserve','release','close-remaining','void-delivery'):
        pass  # quyền theo người lập đơn/kho được kiểm trong module giữ hàng
    elif action=='deliver':
        if not has_role(user,'sales','warehouse'):
            raise BusinessError('FORBIDDEN','Chỉ kinh doanh, kho hoặc giám đốc được giao đơn bán.',status=403)
    else:
        _writer(user)
    try:
        version=_version(form)
        if action=='void-delivery':
            delivery_id=_reservation_id({'reservation_id':form.get('delivery_id')})
            gh=session.get(SalesDelivery,delivery_id)
            if gh is None or gh.order_id!=order_id:
                raise BusinessError('NOT_FOUND','Không tìm thấy lần giao của đơn.',status=404)
            svc.void_delivery(session,user,delivery_id,str(form.get('reason','')),version)
        elif action=='close-remaining':
            svc.close_remaining(session,user,order_id,str(form.get('reason','')),version)
        elif action=='reserve':
            stock_reservations.keep_more(session,user,order_id,version)
        elif action=='release':
            stock_reservations.release(session,user,order_id,_reservation_id(form),str(form.get('reason','')),version)
        elif action=='promise':
            body=SalesOrderPromise(version=version,promised_date=form.get('promised_date') or None)
            svc.set_promised_date(session,user,order_id,body.promised_date,version)
        elif action=='cancel':
            svc.cancel(session,user,order_id,str(form.get('reason','')),version)
        elif action=='confirm':
            svc.confirm(session,user,order_id,version)
        elif action=='deliver':
            rows=[dict(order_line_no=int(k[8:]),qty=str(v).replace(',','.')) for k,v in form.items() if re.fullmatch(r'deliver_[0-9]+',k)]
            body=SalesDeliveryIn(version=version,lines=rows if rows or 'delivery_form' in form else None,
                receiver_name=str(form.get('receiver_name','')),note=str(form.get('note','')))
            svc.deliver(session,user,order_id,version,body.lines,body.receiver_name,body.note)
        else:
            raise BusinessError('NOT_FOUND','Không tìm thấy thao tác đơn bán.',status=404)
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _form(request,session,order=svc.load(session,user,order_id),error=message,code=code,status=status,
                     action_form=dict(form),action=action)
    return RedirectResponse(f'/don-ban/{order_id}?da_luu=1',status_code=303)


@router.get('/don-ban/{order_id}/in-giao')
def latest_print(order_id: int,user: User=Depends(order_user),session: Session=Depends(get_session)):
    order=svc.load(session,user,order_id)
    rows=[g for g in svc.deliveries(session,order.id) if g.status=='DA_GIAO']
    if not rows:
        raise BusinessError('BAD_STATUS','Đơn chưa có lần giao để in.',status=409)
    return RedirectResponse(f'/don-ban/{order_id}/giao/{rows[-1].id}/in',status_code=303)


@router.get('/don-ban/{order_id}/giao/{delivery_id}/in')
def print_delivery(request: Request,order_id: int,delivery_id: int,user: User=Depends(order_user),session: Session=Depends(get_session)):
    order=svc.load(session,user,order_id)
    gh=next((g for g in svc.deliveries(session,order.id) if g.id==delivery_id),None)
    if gh is None:
        raise BusinessError('NOT_FOUND','Không tìm thấy lần giao của đơn.',status=404)
    original={l.line_no:l for l in order.lines}
    lines=[dict(line_no=l.order_line_no,product_code=original[l.order_line_no].product_code,
        description=original[l.order_line_no].description,qty=l.qty,unit=original[l.order_line_no].unit,
        unit_price=original[l.order_line_no].unit_price,vat_rate=original[l.order_line_no].vat_rate,
        amount=l.amount) for l in gh.lines]
    return render(request,'don_ban_in.jinja',o=order,gh=gh,lines=lines,customer=session.get(Customer,order.customer_code),
                  company=get_settings(),quantity=_quantity,words=money_words(gh.total),statuses=STATUSES,
                  issued=gh.delivered_at.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%d/%m/%Y'))


@router.get('/don-ban/giao/{delivery_id}')
def delivery_order(delivery_id: int,user: User=Depends(order_user),session: Session=Depends(get_session)):
    gh=session.get(SalesDelivery,delivery_id)
    if gh is None:
        raise BusinessError('NOT_FOUND','Không tìm thấy lần giao.',status=404)
    svc.load(session,user,gh.order_id)
    return RedirectResponse(f'/don-ban/{gh.order_id}',status_code=303)
