"""Màn hình B1 dùng cùng lõi báo giá với API."""
import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import customers, products, quotes as svc, sales_orders
from ..config import get_settings
from ..config import DEFAULT_VAT_RATE
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, CustomerPhone, Product, Quote, SalesOrder, User, VAT_RATES
from ..money_words import money_words
from ..schemas import QuoteIn, QuoteOut, QuoteUpdate
from ..security import has_role
from . import checked_form, money, render, web_user

from . import next_steps, picked, steps

router = APIRouter()
STATUSES = {'NHAP':'Nháp', 'DA_GUI':'Đã gửi', 'CHAP_NHAN':'Chấp nhận', 'TU_CHOI':'Từ chối', 'DA_HUY':'Đã hủy'}


def quote_user(user: User = Depends(web_user)):
    svc.check_reader(user)
    return user


def _quantity(value):
    text = format(value, 'f')
    return (text.rstrip('0').rstrip('.') if '.' in text else text).replace('.', ',')


def _expired(q):
    return q.status == 'DA_GUI' and q.valid_until < svc._today()


def _blank():
    return dict(customer_code='', contact_name='', valid_until=(svc._today()+timedelta(days=7)).isoformat(),
                note='', version=0, lines=[])


def _form(request, session, data=None, quote=None, error=None, code='', status=200, action_form=None, action=''):
    if data is None:
        data = QuoteOut.model_validate(quote).model_dump(mode='json') if quote else _blank()
        for line in data['lines']:
            line['unit_price'] = money(line['unit_price'])
    customer = session.get(Customer, data.get('customer_code')) if data.get('customer_code') else None
    card = picked.customer_cards(session, request.state.user, [customer]).get(customer.code) if customer else None
    if card and not data.get('contact_name', '').strip(): data['contact_name'] = card['contact']

    codes = {line.get('product_code') for line in data['lines'] if line.get('product_code')}
    names = dict(session.execute(select(Product.code,Product.name).where(Product.code.in_(codes))).all())
    replacement = svc.load(session, request.state.user, quote.replaced_by_id) if quote and quote.replaced_by_id else None
    linked_order = session.execute(select(SalesOrder.id, SalesOrder.number).where(SalesOrder.quote_id == quote.id,
                                  SalesOrder.status != 'DA_HUY')).first() if quote else None
    editable = quote is None or quote.status == 'NHAP'
    can_write = has_role(request.state.user,*svc.WRITERS)
    browse = products.search(session,'',active_only=True,limit=40) if editable and can_write else []
    vn_created = quote.created_at.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date() if quote else svc._today()
    return render(request, 'bao_gia_form.jinja', browse=browse, groups=products.groups(session) if browse else [],
                  browse_prices=picked.last_prices(session,data.get('customer_code'),browse), quote_date=vn_created,
                  workflow=steps.quote(quote,linked_order),primary_checks=steps.quote_checks(data) if quote is None or quote.status=='NHAP' else [],today=svc._today().isoformat(), q=quote, d=data, line_errors=picked.line_errors(data['lines'],error,code), customer_card=card, customer_name=customer.name if customer else '',
                  product_names=names, statuses=STATUSES, vat_rates=VAT_RATES,
                  can_write=has_role(request.state.user,*svc.WRITERS), is_new=quote is None,
                  next_step=next_steps.quote(request.state.user,quote,linked_order),linked_order=linked_order, editable=quote is None or quote.status == 'NHAP', replaced=replacement,
                  error=error, error_code=code, status=status, action_form=action_form or {}, failed_action=action,
                  saved=request.query_params.get('da_luu') == '1' and not error, unsaved=error is not None)


def _version(form):
    try:
        value = int(form.get('version', ''))
        if value < 1:
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise BusinessError('VERSION_REQUIRED','Phải gửi phiên bản báo giá, hãy tải lại trang.')


def _raw(form):
    fields = {key: form.getlist(key) for key in ('product_code','description','qty','unit','unit_price')}
    lines = []
    for i in range(max((len(values) for values in fields.values()),default=0)):
        row = {key: str(values[i]) if i < len(values) else '' for key, values in fields.items()}
        row['vat_rate'] = str(form.get(f'vat_{i}', str(DEFAULT_VAT_RATE)))
        lines.append(row)
    return dict(customer_code=str(form.get('customer_code','')), contact_name=str(form.get('contact_name','')),
                valid_until=str(form.get('valid_until','')), note=str(form.get('note','')),
                version=str(form.get('version','')), lines=lines)


def _body(data):
    lines = []
    for i, line in enumerate(data['lines'],1):
        price = line['unit_price'].strip()
        if price and not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)',price):
            raise BusinessError('BAD_PRICE',f'Đơn giá dòng {i} chưa hợp lệ; nhập số nguyên đồng.')
        try:
            vat = int(line['vat_rate'])
        except ValueError:
            raise BusinessError('BAD_VAT',f'VAT dòng {i} phải là 0, 5, 8 hoặc 10%.')
        lines.append({**line, 'unit_price':int(price.replace('.','')) if price else None,
                      'qty':line['qty'].replace(',','.'), 'vat_rate':vat})
    return dict(customer_code=data['customer_code'], contact_name=data['contact_name'],
                valid_until=data['valid_until'] or None, note=data['note'], lines=lines)


def _error(exc):
    if isinstance(exc,BusinessError):
        return exc.message,exc.code,exc.status
    loc = exc.errors()[0]['loc']
    labels = {'qty':'Số lượng','unit_price':'Đơn giá','unit':'Đơn vị','vat_rate':'VAT', 'product_code':'Mã hàng',
              'customer_code':'Khách hàng','contact_name':'Người liên hệ','valid_until':'Hạn báo giá','description':'Mô tả','lines':'Dòng báo giá'}
    text = labels.get(str(loc[-1]),'Dữ liệu') + ' chưa hợp lệ, hãy kiểm tra lại.'
    if len(loc)>1 and isinstance(loc[1],int):
        text = f'Dòng {loc[1]+1}: ' + text
    return text,'BAD_FORM',400


@router.get('/bao-gia')
def listing(request: Request,q: str='',status: str='',han: int=0,phan: int=0,nguoi: int=0,ky: str='',user: User=Depends(quote_user),session: Session=Depends(get_session)):
    rows = svc.search(session,user,q[:200],'DA_GUI' if han else (status or None))
    today = svc._today()
    if han:  # Ô "Sắp hết hạn": báo giá đã gửi, còn tối đa 2 ngày hoặc đã quá hạn.
        rows = [row for row in rows if row.valid_until <= today+timedelta(days=2)]
    # Lọc hiển thị theo người lập / khoảng ngày lập (giờ VN); chỉ đọc.
    vn = ZoneInfo('Asia/Ho_Chi_Minh')
    if nguoi:
        rows = [row for row in rows if row.created_by == nguoi]
    month = today.replace(day=1)
    periods = {'30': (today-timedelta(days=29), today), 'thang': (month, today),
               'thang-truoc': ((month-timedelta(days=1)).replace(day=1), month-timedelta(days=1))}
    if ky in periods:
        start,end = periods[ky]
        rows = [row for row in rows if start <= row.created_at.astimezone(vn).date() <= end]
    codes = {row.customer_code for row in rows}
    names = dict(session.execute(select(Customer.code,Customer.name).where(Customer.code.in_(codes))).all())
    phones = {}
    for code,phone in session.execute(select(CustomerPhone.customer_code,CustomerPhone.phone).where(CustomerPhone.customer_code.in_(codes))
                                      .order_by(CustomerPhone.is_contact,CustomerPhone.phone)):
        phones.setdefault(code,phone)
    makers = dict(session.execute(select(User.id,User.full_name).where(User.id.in_({row.created_by for row in rows}))).all())
    ids = [row.id for row in rows]
    orders = {qid:(oid,number) for qid,oid,number in session.execute(select(SalesOrder.quote_id,SalesOrder.id,SalesOrder.number)
              .where(SalesOrder.quote_id.in_(ids),SalesOrder.status!='DA_HUY'))} if ids else {}
    revised = dict(session.execute(select(Quote.id,Quote.number).where(Quote.id.in_({row.replaced_by_id for row in rows if row.replaced_by_id})))
                   .all())
    extra = dict(rows=rows,q=q,selected_status=status,soon=han,today=today,statuses=STATUSES,can_write=has_role(user,*svc.WRITERS),
                 expired=_expired,names=names,phones=phones,makers=makers,orders=orders,revised=revised,vn=vn,
                 can_order=has_role(user,*sales_orders.WRITERS),nguoi=nguoi,ky=ky)
    if phan:
        return render(request,'bao_gia_dong.jinja',**extra)
    # Số trên đầu trang: chỉ đọc, đếm trên toàn bộ báo giá (không theo ô tìm).
    counts = dict(session.execute(select(Quote.status,func.count()).group_by(Quote.status)).all())
    waiting = session.execute(select(func.count(),func.coalesce(func.sum(Quote.total),0)).where(Quote.status=='DA_GUI')).one()
    soon = session.scalar(select(func.count()).where(Quote.status=='DA_GUI',Quote.valid_until<=today+timedelta(days=2)))
    recent = dict(session.execute(select(Quote.status,func.count()).where(Quote.created_at>=func.now()-timedelta(days=30),
        Quote.status.in_(('CHAP_NHAN','TU_CHOI'))).group_by(Quote.status)).all())
    won = session.execute(select(func.count(),func.coalesce(func.sum(Quote.total),0)).where(Quote.status=='CHAP_NHAN',
        Quote.created_at>=func.date_trunc('month',func.timezone('Asia/Ho_Chi_Minh',func.now())).op('AT TIME ZONE')('Asia/Ho_Chi_Minh'))).one()
    decided = recent.get('CHAP_NHAN',0)+recent.get('TU_CHOI',0)
    stats = dict(counts=counts,total=sum(counts.values()),waiting=waiting[0],waiting_sum=waiting[1],soon=soon,won=won[0],won_sum=won[1],
                 rate=round(100*recent.get('CHAP_NHAN',0)/decided) if decided else None,accepted=recent.get('CHAP_NHAN',0),decided=decided)
    staff = session.execute(select(User.id,User.full_name).where(User.id.in_(select(Quote.created_by).distinct()))
                            .order_by(User.full_name)).all()
    return render(request,'bao_gia_ds.jinja',stats=stats,staff=staff,**extra)


@router.get('/bao-gia/tim-khach')
def pick_customer(request: Request,q: str='',user: User=Depends(quote_user),session: Session=Depends(get_session)):
    rows = [c for c in customers.search(session,q[:200]) if c.status=='DANG_DUNG']
    return render(request,'bao_gia_chon.jinja',kind='customer',rows=rows, customer_cards=picked.customer_cards(session,user,rows))


@router.get('/bao-gia/tim-hang')
def pick_product(request: Request,q: str='',customer_code: str='',user: User=Depends(quote_user),session: Session=Depends(get_session)):
    rows = products.search(session,q[:200],active_only=True,limit=20)
    return render(request,'bao_gia_chon.jinja',kind='product',rows=rows,last_prices=picked.last_prices(session,customer_code,rows))


@router.get('/bao-gia/san-pham')
def browse_products(request: Request,q: str='',nhom: str='',customer_code: str='',user: User=Depends(quote_user),session: Session=Depends(get_session)):
    """Cột "Tra giá sản phẩm" của màn tạo báo giá: chỉ đọc danh mục đang bán."""
    rows = products.search(session,q[:200],active_only=True,limit=40,group=nhom[:100])
    return render(request,'bao_gia_san_pham.jinja',rows=rows,last_prices=picked.last_prices(session,customer_code[:50],rows))


@router.get('/bao-gia/moi')
def new(request: Request,user: User=Depends(quote_user),session: Session=Depends(get_session)):
    svc.check_writer(user)
    data=None
    chosen=session.get(Customer,request.query_params.get('customer_code','')[:50]) if request.query_params.get('customer_code') else None
    if chosen and chosen.status=='DANG_DUNG':
        data={**_blank(),'customer_code':chosen.code}
    return _form(request,session,data)


@router.post('/bao-gia/moi')
def create(request: Request,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    svc.check_writer(user)
    raw=_raw(form)
    try:
        body=QuoteIn(**_body(raw))
        quote=svc.save_and_send(session,user,body) if form.get('then')=='send' else svc.create(session,user,body)
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _form(request,session,raw,error=message,code=code,status=status)
    return RedirectResponse(f'/bao-gia/{quote.id}?da_luu=1',status_code=303)


@router.get('/bao-gia/{quote_id}')
def view(request: Request,quote_id: int,user: User=Depends(quote_user),session: Session=Depends(get_session)):
    return _form(request,session,quote=svc.load(session,user,quote_id))


@router.post('/bao-gia/{quote_id}')
def update(request: Request,quote_id: int,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    svc.check_writer(user)
    quote=svc.load(session,user,quote_id)
    raw=_raw(form)
    try:
        body=QuoteUpdate(version=_version(form),**_body(raw))
        if form.get('then')=='send': svc.save_and_send(session,user,body,quote_id)
        else: svc.update_lines(session,user,quote_id,body)
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _form(request,session,raw,quote=svc.load(session,user,quote_id),error=message,code=code,status=status)
    return RedirectResponse(f'/bao-gia/{quote_id}?da_luu=1',status_code=303)


@router.post('/bao-gia/{quote_id}/{action}')
def action(request: Request,quote_id: int,action: str,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    svc.check_writer(user)
    try:
        version=_version(form)
        if action=='cancel':
            quote=svc.cancel(session,user,quote_id,str(form.get('reason','')),version)
        elif action in ('send','accept','reject','revise'):
            quote=getattr(svc,action)(session,user,quote_id,version)
        else:
            raise BusinessError('NOT_FOUND','Không tìm thấy thao tác báo giá.',status=404)
    except BusinessError as exc:
        return _form(request,session,quote=svc.load(session,user,quote_id),error=exc.message,code=exc.code,
                     status=exc.status,action_form=dict(form),action=action)
    return RedirectResponse(f'/bao-gia/{quote.id}?da_luu=1',status_code=303)


@router.get('/bao-gia/{quote_id}/in')
def print_quote(request: Request,quote_id: int,user: User=Depends(quote_user),session: Session=Depends(get_session)):
    from zoneinfo import ZoneInfo
    quote=svc.load(session,user,quote_id)
    return render(request,'bao_gia_in.jinja',q=quote,customer=session.get(Customer,quote.customer_code),company=get_settings(),
                  words=money_words(quote.total),quantity=_quantity,statuses=STATUSES,expired=_expired(quote),
                  issued=quote.created_at.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%d/%m/%Y'))
