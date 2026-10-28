"""B3: màn hình đơn bán; mọi thay đổi do lõi B2 thực hiện."""
import re
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import customers, products, quotes, sales_orders as svc, sales_receipts, stock_reservations
from ..config import get_settings
from ..config import DEFAULT_VAT_RATE
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, CustomerPhone, Product, Quote, ServiceTicket, SalesOrder, SalesDelivery, StockDoc, User, VAT_RATES, Warehouse
from ..money_words import money_words
from ..sales_warranty import parse_serials, for_order as serials_for_order
from ..schemas import SalesOrderFromQuote, SalesOrderIn, SalesOrderOut, SalesOrderUpdate, SalesOrderPromise, SalesDeliveryIn, SalesOrderOptions, SalesCounterSale, SalesOrderConfirm
from ..security import has_role
from . import doc_frame, checked_form, money, render, web_user
from .quotes import _quantity, _pricing_raw, _pricing_body
from .receipts import order_context, _report_body

from . import next_steps, picked, steps

router = APIRouter()
FULFILLMENTS = {'TAI_QUAY':'Tại quầy','KHACH_LAY_SAU':'Khách lấy sau','THO_GIAO':'Thợ giao','VAN_CHUYEN':'Gửi vận chuyển'}
PAYMENTS = {'TRA_NGAY':'Trả ngay','DAT_COC':'Đặt cọc','CONG_NO':'Công nợ'}

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
            'price_includes_vat':'Chế độ giá gồm VAT','discount_amount':'Giảm giá',
            'customer_code':'Khách hàng','fulfillment':'Cách nhận hàng','payment_term':'Thanh toán','due_days':'Số ngày nợ','deposit_amount':'Tiền cọc','carrier_name':'Đơn vị vận chuyển','tracking_code':'Mã vận đơn','contact_name':'Người liên hệ','delivery_address':'Địa chỉ giao',
            'description':'Mô tả','unit':'Đơn vị','product_code':'Mã hàng','lines':'Dòng đơn bán','warehouses':'Kho'}
    message=labels.get(str(loc[-1]),'Dữ liệu')+' chưa hợp lệ, hãy kiểm tra lại.'
    if len(loc)>1 and isinstance(loc[1],int) and loc[0]=='lines':
        message=f'Dòng {loc[1]+1}: '+message
    return message,'BAD_FORM',400


from .. import pending_liquidation


def _warehouses(session):
    return list(session.scalars(select(Warehouse).where(Warehouse.kind == 'KHO').order_by(Warehouse.code)))


def _active_order(session, quote_id):
    return session.execute(select(SalesOrder.id,SalesOrder.number).where(SalesOrder.quote_id==quote_id,
                           SalesOrder.status!='DA_HUY')).first()


def _form(request,session,data=None,order=None,error=None,code='',status=200,action_form=None,action='',receipt_form=None,receipt_decision=None,failed_receipt_id=None,failed_receipt_action='',refund_form=None,customer_code=''):
    if data is None:
        data=SalesOrderOut.model_validate(order).model_dump(mode='json') if order else dict(
            customer_code=customer_code,contact_name='',delivery_address='',note='',promised_date='',version=0,lines=[],
            fulfillment='TAI_QUAY',payment_term='TRA_NGAY',due_days=30,deposit_amount='',price_includes_vat=False,discount_amount=0)
        for line in data['lines']:
            line['unit_price']=money(line['unit_price'])
        if data.get('deposit_amount') not in (None,''):
            data['deposit_amount']=money(data['deposit_amount'])
    customer=session.get(Customer,data['customer_code']) if data['customer_code'] else None
    card = picked.customer_cards(session, request.state.user, [customer]).get(customer.code) if customer else None
    if card and not data.get('contact_name', '').strip(): data['contact_name'] = card['contact']
    if card and not data.get('delivery_address', '').strip() and data.get('fulfillment') in ('VAN_CHUYEN', 'THO_GIAO'): data['delivery_address'] = card['address']

    codes={l.get('product_code') for l in data['lines'] if l.get('product_code')}
    shipments=svc.deliveries(session,order.id) if order else []
    ids={l.stock_doc_id for g in shipments for l in g.lines if l.stock_doc_id}
    for g in shipments:ids.update(r.stock_doc_id for r in pending_liquidation.delivery_rows(session,g.id))
    source=quotes.load(session,request.state.user,order.quote_id) if order and order.quote_id and has_role(request.state.user,'sales','finance') else None
    receipt_ctx=order_context(session,request.state.user,order,receipt_form,receipt_decision,failed_receipt_id,failed_receipt_action)
    deposit_remaining=0
    if order and order.payment_term=='DAT_COC' and receipt_ctx['collection']:
        balance=receipt_ctx['collection']['balance']
        deposit_remaining=max(0,min(order.deposit_amount,svc.effective_total(order))-balance['paid']-balance['pending'])
        if deposit_remaining>0:
            receipt_ctx['receipt_report_label']='Báo thu cọc'
            if receipt_form is None:
                receipt_ctx['receipt_data']['amount']=money(min(deposit_remaining,receipt_ctx['available']))
    warehouses=_warehouses(session)
    default_warehouse=next((w.code for w in warehouses if w.code==get_settings().sales_default_warehouse and w.active), '')
    stock_values=stock_reservations.quantities(session,[(l.get('warehouse_code'),l.get('product_code')) for l in data['lines'] if l.get('product_code') and l.get('warehouse_code')])
    from .technician_deliveries import context as delivery_context
    tech_delivery=delivery_context(session,request.state.user,order) if order and order.fulfillment=='THO_GIAO' else None
    from .sales_returns import order_context as return_context
    # Cột "Tra giá · tồn kho" của màn tạo/sửa đơn nháp: chỉ đọc danh mục, tồn ở kho mặc định.
    live=has_role(request.state.user,'sales') and (order is None or order.status=='NHAP')
    browse=products.search(session,'',active_only=True,limit=40) if live else []
    trip_id=str(data.get('service_ticket_id') or '')
    trip_ticket=session.get(ServiceTicket,int(trip_id)) if not order and trip_id.isdigit() else (session.get(ServiceTicket,order.service_ticket_id) if order and order.service_ticket_id else None)
    viewing=order is not None and order.status!='NHAP'
    reservations=stock_reservations.for_order(session,order) if order else {}
    delivery_state=svc.remaining_lines(session,order) if order else {}
    saved=request.query_params.get('da_luu')=='1' and not error
    frame={}
    if order is not None:
        frame=dict(toast_message=doc_frame.order_toast(order,receipt_ctx.get('collection'),receipt_ctx['available'],request.query_params.get('xong','')) if saved else '')
        if viewing:
            frame.update(doc_next=doc_frame.order_next(order,receipt_ctx.get('collection'),receipt_ctx['available']),
                         progress=doc_frame.line_progress(order,reservations,delivery_state))
    # Cảnh báo công nợ trên đơn nháp đã lưu (nút Chốt đơn): không chặn, chỉ nhắc.
    credit_warning=svc.credit_warning(session,customer,order.total,exclude_order_id=order.id) if order is not None and order.status=='NHAP' and order.payment_term=='CONG_NO' else ''
    return render(request,'don_ban_xem.jinja' if viewing else 'don_ban_form.jinja',**frame,trip_ticket=trip_ticket,credit_warning=credit_warning,workflow=steps.order(order),primary_checks=steps.order_checks(session,request.state.user,data,stock_values,customer) if order is None or order.status=='NHAP' else [],receipt_error=receipt_form is not None,returns_ctx=return_context(session,request.state.user,order,refund_form) if order else None,tech_delivery=tech_delivery,fulfillments=FULFILLMENTS,payments=PAYMENTS,is_director=has_role(request.state.user,'director'),
                  order_summary=svc.summaries(session,[order]).get(order.id) if order else None,deliveries=shipments,delivery_state=delivery_state,
                  can_void_delivery=has_role(request.state.user,'director'),
                  can_close=bool(order and (has_role(request.state.user,'director') or (has_role(request.state.user,'sales') and order.created_by==request.state.user.id))),o=order,d=data,statuses=STATUSES,vat_rates=VAT_RATES,
                  line_errors=picked.line_errors(data['lines'],error,code,stock_values=stock_values if order is None or order.status=='NHAP' else None), customer_card=card, customer_name=customer.name if customer else '',source=source,
                  reservations=reservations,
                  stock_values=stock_values,
                  can_keep=bool(order and stock_reservations.can_keep(request.state.user,order)),can_release=has_role(request.state.user,'warehouse'),
                  warranty_months=dict(session.execute(select(Product.code,Product.warranty_months).where(Product.code.in_(codes))).all()),
                  sale_serials=serials_for_order(session,order.id) if order else [],
                  product_names=dict(session.execute(select(Product.code,Product.name).where(Product.code.in_(codes))).all()),
                  next_step=next_steps.order(request.state.user,order,receipt_ctx['available'],deposit_remaining),stock_numbers=dict(session.execute(select(StockDoc.id,StockDoc.number).where(StockDoc.id.in_(ids))).all()),
                  liquidation_warehouses=pending_liquidation.choices(session,codes|{p.code for p in browse}),warehouses=warehouses,
                  browse=browse,groups=products.groups(session) if browse else [],browse_prices=picked.last_prices(session,data['customer_code'],browse) if browse else {},browse_stock=stock_reservations.picker(session,browse,default_warehouse),default_warehouse=default_warehouse,can_write=has_role(request.state.user,'sales'),
                  can_deliver=has_role(request.state.user,'sales','warehouse') and not (order and order.delivery_assignee_id),editable=order is None or order.status=='NHAP',
                  error=error,error_code=code,status=status,action_form=action_form or {},failed_action=action,
                  saved=saved,unsaved=bool(error),**receipt_ctx)


def _raw(form):
    fields={key:form.getlist(key) for key in ('product_code','description','qty','unit','unit_price')}
    lines=[]
    for i in range(max((len(v) for v in fields.values()),default=0)):
        line={key:str(values[i]) if i<len(values) else '' for key,values in fields.items()}
        line['counter_serials']=str(form.getlist('counter_serials')[i]) if i<len(form.getlist('counter_serials')) else ''
        line.update(kind=str(form.get(f'kind_{i}','HANG')),warehouse_code=str(form.get(f'warehouse_{i}','')),
                    vat_rate=str(form.get(f'vat_{i}',str(DEFAULT_VAT_RATE))))
        lines.append(line)
    return dict(customer_code=str(form.get('customer_code','')),contact_name=str(form.get('contact_name','')),
                delivery_address=str(form.get('delivery_address','')),note=str(form.get('note','')),promised_date=str(form.get('promised_date','')),
                version=str(form.get('version','')),lines=lines,fulfillment=str(form.get('fulfillment','TAI_QUAY')),
                payment_term=str(form.get('payment_term','TRA_NGAY')),due_days=str(form.get('due_days','30')),deposit_amount=str(form.get('deposit_amount','')),**_pricing_raw(form))


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
        if line.get('kind','HANG')=='HANG' and line['product_code'].strip() and not re.fullmatch(r'[0-9]+',line['qty'].strip()):
            raise BusinessError('BAD_QTY',f'Số lượng dòng {i} phải là số nguyên.')
        lines.append({**{k:v for k,v in line.items() if k!='counter_serials'},'qty':line['qty'].replace(',','.'),'unit_price':int(price.replace('.','')) if price else None,'vat_rate':vat})
    return dict(contact_name=data['contact_name'],delivery_address=data['delivery_address'],note=data['note'],lines=lines,
                promised_date=data.get('promised_date') or None,fulfillment=data.get('fulfillment','TAI_QUAY'),
                payment_term=data.get('payment_term','TRA_NGAY'),due_days=data.get('due_days','30'),
                deposit_amount=_deposit(data.get('deposit_amount','')),**_pricing_body(data))


def _deposit(value):
    value=str(value or '').strip()
    if not value:return None
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)',value) or len(value.replace('.',''))>19:
        raise BusinessError('BAD_DEPOSIT','Tiền cọc phải là số nguyên đồng, có thể dùng dấu chấm hàng nghìn.')
    return int(value.replace('.',''))


def warehouse_user(user: User=Depends(web_user)):
    if not has_role(user,*svc.DELIVERERS):
        raise BusinessError('FORBIDDEN','Chỉ kho, kinh doanh hoặc giám đốc được giao hàng.',status=403)
    return user


def warehouse_delivery(request,session,user,order_id,form=None,error=None,code='',status=200):
    warehouse_user(user)
    order=svc.load(session,user,order_id)
    remaining=svc.remaining_lines(session,order)
    customer=session.get(Customer,order.customer_code)
    data=dict(form) if form is not None else dict(version=order.version,receiver_name=order.contact_name,
        note='',carrier_name='',tracking_code='',**{'deliver_'+str(n):_quantity(v['remaining']) for n,v in remaining.items()})
    from .technician_deliveries import context as delivery_context
    tech_delivery=delivery_context(session,user,order) if order.fulfillment=='THO_GIAO' else None
    # Trả ngay chỉ nhắc kho số khách còn phải trả (đã báo thu thì tính là đã trả); không chặn giao.
    money=svc.summaries(session,[order])[order.id]
    pay_due=max(0,svc.effective_total(order)-money['paid']-money['pending']) if order.payment_term=='TRA_NGAY' else 0
    # Thủ kho quay về Việc của tôi; kinh doanh mở từ đơn thì quay về đơn.
    # Thủ kho giao xong thì in phiếu; kinh doanh giao xong quay về đơn để báo thu (in phiếu ở đầu đơn).
    is_kho=has_role(user,'warehouse')
    back='/viec-cua-toi' if is_kho else f'/don-ban/{order.id}'
    return render(request,'kho_giao.jinja',o=order,customer=customer,tech_delivery=tech_delivery,pay_due=pay_due,back=back,print_after=is_kho,
        phone=' · '.join(p.phone for p in sorted(customer.phones,key=lambda p:(not p.is_contact,p.phone))),
        remaining=remaining,reservations=stock_reservations.for_order(session,order),
        stock_values=stock_reservations.quantities(session,[(l.warehouse_code,l.product_code) for l in order.lines if l.kind=='HANG']),
        warranty_months=dict(session.execute(select(Product.code,Product.warranty_months).where(Product.code.in_([l.product_code for l in order.lines]))).all()),fulfillments=FULFILLMENTS,d=data,error=error,error_code=code,status=status)


@router.get('/kho/giao/{order_id}')
def warehouse_view(request:Request,order_id:int,user:User=Depends(warehouse_user),session:Session=Depends(get_session)):
    return warehouse_delivery(request,session,user,order_id)


@router.get('/don-ban')
def listing(request: Request,q: str='',status: str='',phan: int=0,
            from_date: str=Query(default='',alias='from'),to_date: str=Query(default='',alias='to'),holding: bool=False,fulfillment: str='',overdue: bool=False,
            nguoi: str='',user: User=Depends(order_user),session: Session=Depends(get_session)):
    error=code=None
    http_status=200
    try:
        try:
            start,end=(date.fromisoformat(v) if v else None for v in (from_date,to_date))
        except ValueError:
            raise BusinessError('BAD_DATE_RANGE','Khoảng ngày chưa hợp lệ, hãy nhập ngày theo lịch.')
        rows=svc.search(session,user,q[:200],status or None,from_date=start,to_date=end,holding=holding,fulfillment=fulfillment or None,overdue=overdue)
    except BusinessError as exc:
        rows=[]
        error,code,http_status=_error(exc)
    nguoi = int(nguoi) if nguoi.isdigit() else 0  # ô "Mọi người lập" gửi nguoi= rỗng
    if nguoi:  # Lọc hiển thị theo người lập; chỉ đọc.
        rows=[r for r in rows if r.created_by==nguoi]
    codes={r.customer_code for r in rows}
    names=dict(session.execute(select(Customer.code,Customer.name).where(Customer.code.in_(codes))).all())
    phones={}
    for ccode,phone in session.execute(select(CustomerPhone.customer_code,CustomerPhone.phone).where(CustomerPhone.customer_code.in_(codes))
                                       .order_by(CustomerPhone.is_contact,CustomerPhone.phone)):
        phones.setdefault(ccode,phone)
    makers=dict(session.execute(select(User.id,User.full_name).where(User.id.in_({r.created_by for r in rows}))).all())
    quote_numbers=dict(session.execute(select(Quote.id,Quote.number).where(Quote.id.in_({r.quote_id for r in rows if r.quote_id})))
                       .all())
    extra=dict(phones=phones,makers=makers,quote_numbers=quote_numbers,vn=ZoneInfo('Asia/Ho_Chi_Minh'),nguoi=nguoi)
    if not phan:
        # Ô số liệu đầu trang: chỉ đếm, không tính nợ (nợ đã có ở Đơn cần thu).
        counts=dict(session.execute(select(SalesOrder.status,func.count()).group_by(SalesOrder.status)).all())
        waiting=session.execute(select(func.count(),func.coalesce(func.sum(SalesOrder.total),0))
                                .where(SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN')))).one()
        late=session.scalar(select(func.count()).where(SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN')),
                                                       SalesOrder.promised_date<svc._today()))
        month=session.execute(select(func.count(),func.coalesce(func.sum(SalesOrder.total),0)).where(SalesOrder.status!='DA_HUY',
            SalesOrder.created_at>=func.date_trunc('month',func.timezone('Asia/Ho_Chi_Minh',func.now())).op('AT TIME ZONE')('Asia/Ho_Chi_Minh'))).one()
        staff=session.execute(select(User.id,User.full_name).where(User.id.in_(select(SalesOrder.created_by).distinct()))
                              .order_by(User.full_name)).all()
        extra.update(stats=dict(counts=counts,total=sum(counts.values()),waiting=waiting[0],waiting_sum=waiting[1],late=late,
                                month=month[0],month_sum=month[1]),staff=staff)
    return render(request,'don_ban_dong.jinja' if phan else 'don_ban_ds.jinja',rows=rows,names=names,q=q,**extra,
                  selected_status=status,statuses=STATUSES,fulfillments=FULFILLMENTS,selected_fulfillment=fulfillment,overdue=overdue,
                  summaries=svc.summaries(session,rows),from_date=from_date,to_date=to_date,holding=holding,today=svc._today(),
                  can_write=has_role(user,'sales'),can_receivables=has_role(user,'sales','finance'),error=error,error_code=code,status=http_status)


@router.get('/don-ban/tim-khach')
def pick_customer(request: Request,q: str='',user: User=Depends(order_user),session: Session=Depends(get_session)):
    rows = [c for c in customers.search(session,q[:200]) if c.status=='DANG_DUNG']
    return render(request,'don_ban_chon.jinja',kind='customer',rows=rows, customer_cards=picked.customer_cards(session,user,rows))


@router.get('/don-ban/tim-hang')
def pick_product(request: Request,q: str='',customer_code: str='',warehouse: str='',user: User=Depends(order_user),session: Session=Depends(get_session)):
    rows=products.search(session,q[:200],active_only=True,limit=20)
    return render(request,'don_ban_chon.jinja',kind='product',rows=rows,liquidation_warehouses=pending_liquidation.choices(session,[p.code for p in rows]),last_prices=picked.last_prices(session,customer_code,rows),warehouse=warehouse,availability=stock_reservations.picker(session,rows,warehouse))


@router.get('/don-ban/san-pham')
def browse_products(request: Request,q: str='',nhom: str='',customer_code: str='',warehouse: str='',user: User=Depends(order_user),session: Session=Depends(get_session)):
    """Cột "Tra giá · tồn kho" của màn tạo đơn bán: chỉ đọc danh mục đang bán và tồn khả dụng."""
    rows=products.search(session,q[:200],active_only=True,limit=40,group=nhom[:100])
    warehouse=warehouse[:50]
    return render(request,'don_ban_san_pham.jinja',rows=rows,last_prices=picked.last_prices(session,customer_code[:50],rows),
                  availability=stock_reservations.picker(session,rows,warehouse),warehouse=warehouse,liquidation_warehouses=pending_liquidation.choices(session,[p.code for p in rows]))


@router.get('/don-ban/dieu-kien')
def conditions(request: Request,user: User=Depends(order_user),session: Session=Depends(get_session)):
    _writer(user)
    data=_raw(request.query_params)
    data['credit_override_reason']=str(request.query_params.get('credit_override_reason',''))
    customer=session.get(Customer,data['customer_code']) if data['customer_code'] else None
    stock=stock_reservations.quantities(session,[(l.get('warehouse_code'),l.get('product_code')) for l in data['lines'] if l.get('product_code') and l.get('warehouse_code')])
    return render(request,'dieu_kien.jinja',primary_checks=steps.order_checks(session,user,data,stock,customer))


@router.get('/don-ban/moi')
def new(request: Request,user: User=Depends(order_user),session: Session=Depends(get_session)):
    _writer(user)
    chosen=session.get(Customer,request.query_params.get('customer_code','')[:50]) if request.query_params.get('customer_code') else None
    ticket_id=request.query_params.get('service_ticket','')
    ticket=session.get(ServiceTicket,int(ticket_id)) if ticket_id.isdigit() and len(ticket_id)<12 else None
    if ticket is not None and ticket.status in ('MOI','DA_GIAO','DA_NHAN','DA_HEN','DANG_LAM','CHO_LINH_KIEN','TAM_DUNG'):
        # Kèm hàng bán từ phiếu dịch vụ: khách lấy theo phiếu, mặc định thợ giao, đơn nối sẵn với phiếu.
        data=dict(customer_code=ticket.customer_code,contact_name='',delivery_address='',note='',promised_date='',version=0,lines=[],
            fulfillment='THO_GIAO',payment_term='TRA_NGAY',due_days=30,deposit_amount='',service_ticket_id=str(ticket.id))
        return _form(request,session,data)
    return _form(request,session,customer_code=chosen.code if chosen and chosen.status=='DANG_DUNG' else '')


@router.post('/don-ban/moi')
def create(request: Request,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    _writer(user)
    raw=_raw(form)
    raw['credit_override_reason']=str(form.get('credit_override_reason',''))
    trip=str(form.get('service_ticket_id','')).strip()
    try:
        body=_body(raw)
        body['delivery_address']=body['delivery_address'] or None
        body=SalesOrderIn(customer_code=raw['customer_code'],service_ticket_id=trip or None,**body)
        order=svc.save_and_confirm(session,user,body,credit_override_reason=SalesOrderConfirm(version=1,credit_override_reason=str(form.get('credit_override_reason',''))).credit_override_reason) if form.get('then')=='confirm' else svc.create(session,user,body)
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        raw['service_ticket_id']=trip
        return _form(request,session,raw,error=message,code=code,status=status)
    if form.get('then')=='counter':
        return _counter_after_save(request,session,user,order,form)
    return RedirectResponse(f'/don-ban/{order.id}?da_luu=1',status_code=303)


def _counter_after_save(request,session,user,order,form):
    """Đơn đã lưu nháp; Bán tại quầy = lõi counter_sale với số tiền bằng tổng đơn vừa lưu."""
    try:
        values=form.getlist('counter_serials')
        body=SalesCounterSale(version=order.version,method=str(form.get('counter_method','TIEN_MAT')),amount=order.total,
            delivery_lines=[dict(order_line_no=l.line_no,qty=l.qty,serials=parse_serials(values[i] if i<len(values) else '')) for i,l in enumerate(order.lines)])
        svc.counter_sale(session,user,order.id,body)
    except (BusinessError,ValidationError) as exc:
        # Giữ đơn nháp và nói rõ vì sao chưa bán được, như màn Tạo đơn từ báo giá.
        message,code,status=_error(exc)
        return _form(request,session,order=svc.load(session,user,order.id),error=f'Đã lưu đơn nháp {order.number} nhưng chưa bán tại quầy được: {message}',
                     code=code,status=status,action_form=dict(form),action='counter-sale')
    return RedirectResponse(f'/don-ban/{order.id}?da_luu=1',status_code=303)


def _quote_form(request,session,quote,data=None,error=None,code='',status=200):
    customer=session.get(Customer,quote.customer_code)
    data=data if data is not None else dict(version=quote.version,contact_name=quote.contact_name,
        delivery_address=customer.address,note=quote.note,promised_date='',warehouses={},
        fulfillment='TAI_QUAY',payment_term='TRA_NGAY',due_days='30',deposit_amount='')
    warehouses=_warehouses(session)
    codes={l.product_code for l in quote.lines if l.product_code}
    active=[w.code for w in warehouses if w.active]
    stock=stock_reservations.quantities(session,[(w,c) for w in active for c in codes])
    # Tồn khả dụng từng kho cho mỗi dòng hàng, để chọn kho ngay trên trang.
    stock_by_line={l.line_no:{w:float(stock.get((w,l.product_code),{}).get('available',0)) for w in active} for l in quote.lines if l.product_code}
    card=picked.customer_cards(session,request.state.user,[customer]).get(customer.code) if customer else None
    return render(request,'don_ban_tu_bao_gia.jinja',q=quote,d=data,customer=customer,customer_card=card,quantity=_quantity,credit_warning=svc.credit_warning(session,customer,quote.total),
                  liquidation_warehouses=pending_liquidation.choices(session,list(codes)),warehouses=warehouses,linked=_active_order(session,quote.id),
                  stock_by_line=stock_by_line,goods_count=len(codes),fulfillments=FULFILLMENTS,payments=PAYMENTS,is_director=has_role(request.state.user,'director'),
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
             warehouses={int(k[10:]):str(v) for k,v in form.items() if re.fullmatch(r'warehouse_[0-9]+',k)},
             **{k:str(form[k]) for k in svc.TERM_FIELDS if k in form},credit_override_reason=str(form.get('credit_override_reason','')))
    try:
        # Trường để trống thì dùng mặc định của đơn (Tại quầy, Trả ngay, 30 ngày, không cọc).
        terms={k:raw[k] for k in svc.TERM_FIELDS if raw.get(k,'').strip()}
        if 'deposit_amount' in terms:terms['deposit_amount']=_deposit(terms['deposit_amount'])
        body=SalesOrderFromQuote(**{k:v for k,v in raw.items() if k not in svc.TERM_FIELDS and k not in ('credit_override_reason','version','promised_date')},**terms,
                                 **{'version':_version(form),'promised_date':raw['promised_date'] or None})
        order=svc.create_from_quote(session,user,quote_id,body)
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _quote_form(request,session,quotes.load(session,user,quote_id),raw,error=message,code=code,status=status)
    if form.get('then')=='confirm':
        try:
            reason=SalesOrderConfirm(version=order.version,credit_override_reason=raw['credit_override_reason']).credit_override_reason
            svc.confirm(session,user,order.id,order.version,reason)
        except (BusinessError,ValidationError) as exc:
            # Đơn nháp đã tạo; báo rõ vì sao chưa chốt để sửa kho/số lượng rồi chốt trên đơn.
            message,code,status=_error(exc)
            return _form(request,session,order=svc.load(session,user,order.id),error=f'Đã tạo đơn nháp {order.number} nhưng chưa chốt được: {message}',
                         code=code,status=status,action_form=dict(form),action='confirm')
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
    raw['credit_override_reason']=str(form.get('credit_override_reason',''))
    raw['customer_code']=order.customer_code  # B2 không cho đổi khách của đơn đã lập.
    try:
        body=SalesOrderUpdate(version=_version(form),**_body(raw))
        if form.get('then')=='confirm': svc.save_and_confirm(session,user,body,order_id,SalesOrderConfirm(version=body.version,credit_override_reason=str(form.get('credit_override_reason',''))).credit_override_reason)
        else: svc.update_lines(session,user,order_id,body)
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _form(request,session,raw,order=svc.load(session,user,order_id),error=message,code=code,status=status)
    if form.get('then')=='counter':
        return _counter_after_save(request,session,user,svc.load(session,user,order_id),form)
    return RedirectResponse(f'/don-ban/{order_id}?da_luu=1',status_code=303)


@router.post('/don-ban/{order_id}/{action}')
def action(request: Request,order_id: int,action: str,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user
    warehouse_return=action=='deliver' and form.get('return_to')=='kho' and has_role(user,*svc.DELIVERERS)
    # Đợt 1b: chỉ còn một màn giao hàng (/kho/giao), nên lỗi của form giao luôn quay lại màn đó, giữ số vừa nhập.
    delivery_retry=action=='deliver' and 'delivery_form' in form and has_role(user,*svc.DELIVERERS)
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
        elif action=='options':
            values={k:str(form[k]) for k in svc.TERM_FIELDS if k in form}
            if 'deposit_amount' in values:values['deposit_amount']=_deposit(values['deposit_amount'])
            svc.change_options(session,user,order_id,SalesOrderOptions(version=version,**values))
        elif action=='counter-sale':
            order=svc.load(session,user,order_id)
            raw={k:str(form.get(k,'')) for k in ('version','method','amount','received_at','note')}
            body=SalesCounterSale(**_report_body(form,raw).model_dump(),delivery_lines=[dict(order_line_no=l.line_no,qty=l.qty,serials=parse_serials(form.get('serials_'+str(l.line_no),''))) for l in order.lines])
            svc.counter_sale(session,user,order_id,body)
        elif action=='confirm':
            body=SalesOrderConfirm(version=version,credit_override_reason=str(form.get('credit_override_reason','')))
            svc.confirm(session,user,order_id,version,body.credit_override_reason)
        elif action=='deliver':
            rows=[dict(order_line_no=int(k[8:]),qty=str(v).replace(',','.'),serials=parse_serials(form.get('serials_'+k[8:],''))) for k,v in form.items() if re.fullmatch(r'deliver_[0-9]+',k)]
            body=SalesDeliveryIn(version=version,lines=rows if rows or 'delivery_form' in form else None,
                receiver_name=str(form.get('receiver_name','')),note=str(form.get('note','')),
                carrier_name=str(form.get('carrier_name','')),tracking_code=str(form.get('tracking_code','')))
            svc.deliver(session,user,order_id,version,body.lines,body.receiver_name,body.note,body.carrier_name,body.tracking_code)
        else:
            raise BusinessError('NOT_FOUND','Không tìm thấy thao tác đơn bán.',status=404)
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        if warehouse_return or delivery_retry:return warehouse_delivery(request,session,user,order_id,form,message,code,status)
        return _form(request,session,order=svc.load(session,user,order_id),error=message,code=code,status=status,
                     action_form=dict(form),action=action)
    if warehouse_return and has_role(user,'warehouse'):
        return RedirectResponse(f'/don-ban/{order_id}/in-giao?return_to=kho',status_code=303)
    return RedirectResponse(f'/don-ban/{order_id}?da_luu=1&xong={action}',status_code=303)


@router.get('/don-ban/{order_id}/in-giao')
def latest_print(request:Request,order_id: int,user: User=Depends(order_user),session: Session=Depends(get_session)):
    order=svc.load(session,user,order_id)
    rows=[g for g in svc.deliveries(session,order.id) if g.status=='DA_GIAO']
    if not rows:
        raise BusinessError('BAD_STATUS','Đơn chưa có lần giao để in.',status=409)
    return RedirectResponse(f'/don-ban/{order_id}/giao/{rows[-1].id}/in'+('?return_to=kho' if request.query_params.get('return_to')=='kho' and has_role(user,'warehouse') else ''),status_code=303)


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
    discount=svc.delivery_discount(session,order,gh)
    shipments=[g for g in svc.deliveries(session,order.id) if g.status=='DA_GIAO' or g.id==gh.id]
    serials={}
    for row,_,line_no in serials_for_order(session,order.id):
        if row.delivery_id==gh.id: serials[line_no]=(serials[line_no]+', ' if line_no in serials else '')+row.serial
    deliverer=session.get(User,gh.delivered_by)
    warehouse_return=request.query_params.get('return_to')=='kho' and has_role(user,'warehouse')
    return render(request,'don_ban_in.jinja',warehouse_return=warehouse_return,o=order,gh=gh,lines=lines,customer=session.get(Customer,order.customer_code),
                  back='/viec-cua-toi' if warehouse_return else f'/don-ban/{order.id}',
                  delivery_discount=discount,entered_total=(gh.total if order.price_includes_vat else gh.subtotal)+discount,
                  company=get_settings(),quantity=_quantity,words=money_words(gh.total),statuses=STATUSES,
                  delivery_index=next((i for i,g in enumerate(shipments,1) if g.id==gh.id),1),delivery_count=len(shipments),
                  quote_number=session.get(Quote,order.quote_id).number if order.quote_id else '',deliverer=deliverer.full_name if deliverer else '',
                  serials=serials,balance=sales_receipts.balance(order) if gh.status=='DA_GIAO' else None,
                  fulfillment=FULFILLMENTS.get(order.fulfillment,order.fulfillment),payment=PAYMENTS.get(order.payment_term,order.payment_term),
                  watermark='ĐÃ HỦY' if gh.status!='DA_GIAO' else '',
                  issued=gh.delivered_at.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%d/%m/%Y'))


@router.get('/don-ban/giao/{delivery_id}')
def delivery_order(delivery_id: int,user: User=Depends(order_user),session: Session=Depends(get_session)):
    gh=session.get(SalesDelivery,delivery_id)
    if gh is None:
        raise BusinessError('NOT_FOUND','Không tìm thấy lần giao.',status=404)
    svc.load(session,user,gh.order_id)
    return RedirectResponse(f'/don-ban/{gh.order_id}',status_code=303)
