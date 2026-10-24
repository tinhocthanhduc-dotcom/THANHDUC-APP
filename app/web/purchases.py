"""M1: web gọi cùng lõi đơn mua với API, POST dùng checked_form."""
import re
from decimal import Decimal, InvalidOperation

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import purchase_returns, supplier_debts, inventory_catalogs, products, purchase_orders as svc, purchase_receipts as receipts, quotes
from ..config import DEFAULT_VAT_RATE, get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import Product, PurchaseOrder, PurchaseOrderLine, PurchaseReceipt, StockBalance, StockDoc, Supplier, User, Warehouse, VAT_RATES
from ..money_words import money_words
from ..schemas import PurchaseOrderIn, PurchaseOrderUpdate
from ..security import has_role
from . import checked_form, money, nav, render, web_user
from .quotes import _quantity

router = APIRouter()
STATUSES = {'NHAP':'Nháp', 'DA_DAT':'Đã đặt', 'NHAN_MOT_PHAN':'Nhận một phần', 'DA_NHAN':'Đã nhận', 'DA_HUY':'Đã hủy'}
TONES = {'NHAP':'nhap', 'DA_DAT':'lam', 'NHAN_MOT_PHAN':'cho', 'DA_NHAN':'xong', 'DA_HUY':'huy'}
OPEN = ('DA_DAT', 'NHAN_MOT_PHAN')


def purchase_user(user: User = Depends(web_user)):
    svc.check_reader(user)
    return user


def _stock(session, codes):
    """Tồn hiện có theo mã hàng và kho thường (chỉ để hiện trên form, không giữ hàng)."""
    rows = session.execute(select(StockBalance.product_code, StockBalance.warehouse_code, StockBalance.qty)
        .join(Warehouse, Warehouse.code == StockBalance.warehouse_code)
        .where(StockBalance.product_code.in_(codes), Warehouse.kind == 'KHO')).all() if codes else []
    stock = {}
    for code, warehouse, qty in rows:
        stock.setdefault(code, {})[warehouse] = _quantity(qty)
    return stock


def incoming(session, pairs):
    """Đợt 4: hàng đã đặt NCC chưa nhận, theo (kho, mã). Chỉ đọc."""
    if not pairs:
        return {}
    left = func.sum(PurchaseOrderLine.qty - PurchaseOrderLine.received_qty)
    rows = session.execute(select(PurchaseOrderLine.warehouse_code, PurchaseOrderLine.product_code, left).join(PurchaseOrder)
                           .where(PurchaseOrder.status.in_(OPEN), PurchaseOrderLine.product_code.in_({c for _, c in pairs}))
                           .group_by(PurchaseOrderLine.warehouse_code, PurchaseOrderLine.product_code))
    return {(w, c): q for w, c, q in rows if q and q > 0}


def last_suppliers(session, codes):
    """Đợt 4: NCC của lần đặt gần nhất từng mã (đơn đã đặt, không tính nháp/hủy). Chỉ đọc."""
    if not codes:
        return {}
    ranked = select(PurchaseOrderLine.product_code, PurchaseOrder.supplier_code, func.row_number().over(
        partition_by=PurchaseOrderLine.product_code,
        order_by=(PurchaseOrder.ordered_at.desc().nulls_last(), PurchaseOrder.id.desc())).label('n')).join(PurchaseOrder).where(
        PurchaseOrderLine.product_code.in_(set(codes)), PurchaseOrder.status.not_in(('NHAP', 'DA_HUY'))).subquery()
    rows = session.execute(select(ranked.c.product_code, Supplier.code, Supplier.name).join(Supplier, Supplier.code == ranked.c.supplier_code)
                           .where(ranked.c.n == 1))
    return {code: dict(code=sup, name=name) for code, sup, name in rows}


def _where(exc, data):
    """Lỗi nằm ở ô nào: trả (ô đầu đơn, {chỉ số dòng: lời nhắn}) để hiện ngay dưới ô đó."""
    fields, lines = {}, {}
    if isinstance(exc, BusinessError):
        if exc.code == 'BAD_SUPPLIER': fields['supplier_code'] = exc.message
        elif exc.code == 'EMPTY_ORDER': fields['lines'] = exc.message
        elif exc.code in ('BAD_PRODUCT', 'BAD_WAREHOUSE'):
            key = 'product_code' if exc.code == 'BAD_PRODUCT' else 'warehouse_code'
            bad = set(exc.details.get('invalid_codes', []))
            for i, line in enumerate(data['lines']):
                if line.get(key) in bad: lines[i] = exc.message
        elif 'line' in exc.details: lines[exc.details['line']] = exc.message
    elif isinstance(exc, ValidationError):
        for e in exc.errors():
            loc = e['loc']
            if loc and loc[0] == 'lines' and len(loc) > 1 and isinstance(loc[1], int): lines.setdefault(loc[1], _error(exc)[0])
            elif loc: fields.setdefault(str(loc[0]), _error(exc)[0])
    return fields, lines


def _form(request, session, row=None, data=None, error=None, code='', status=200, reason='', receiving=False, receive_data=None, receipt_action='', failed_receipt=None, where=({}, {}), prefill_note=''):
    if data is None:
        data = svc.snapshot(row) if row else dict(supplier_code='', order_date=quotes._today().isoformat(),
                            expected_date='', note='', version=0, lines=[])
        for line in data['lines']:
            line['unit_cost'] = money(line['unit_cost'])
    supplier = session.get(Supplier, data.get('supplier_code')) if data.get('supplier_code') else None
    codes = {line.get('product_code') for line in data['lines']}
    history=receipts.list_by_order(session,request.state.user,row.id) if row else []
    returns=purchase_returns.list_by_order(session,request.state.user,row.id) if row else []
    returned_qty={}
    receipt_line_orders={l.id:l.order_line_id for r in history for l in r.lines}
    for t in returns:
        if t.status=='DA_GHI':
            for l in t.lines:
                ident=receipt_line_orders[l.receipt_line_id]
                returned_qty[ident]=returned_qty.get(ident,0)+l.qty
    products=session.execute(select(Product.code,Product.name,Product.unit).where(Product.code.in_(codes))).all()
    composing = row is None or row.status == 'NHAP'
    kho = [w for w in session.scalars(select(Warehouse).where(Warehouse.kind == 'KHO').order_by(Warehouse.code)) if w.active]
    picked = {line.get('warehouse_code') for line in data['lines'] if line.get('warehouse_code')}
    default_warehouse = picked.pop() if len(picked) == 1 else ('*' if picked else (kho[0].code if kho else ''))
    rates = {str(line.get('vat_rate')) for line in data['lines']}
    default_vat = rates.pop() if len(rates) == 1 else ('*' if rates else str(DEFAULT_VAT_RATE))
    if receive_data is None and row:
        receive_data=dict(receipt_date=quotes._today().isoformat(),version=row.version,quantities={str(l.id):_quantity(l.qty-l.received_qty) for l in row.lines if l.received_qty<l.qty})
    return render(request, 'don_mua_form.jinja', o=row, d=data, statuses=STATUSES, vat_rates=VAT_RATES,
        supplier_debt=supplier_debts.order_balance(session,request.state.user,row.id) if row and has_role(request.state.user,'finance') else None,
        returns=returns,returned_qty=returned_qty,history=history,stock_numbers=dict(session.execute(select(StockDoc.id,StockDoc.number).where(StockDoc.id.in_([r.stock_doc_id for r in history+returns]))).all()),
        receiving=receiving,receive_data=receive_data or {},quantity=_quantity,receipt_action=receipt_action,failed_receipt=failed_receipt,
        payable=receipts.payable_by_order(session,request.state.user,row.id) if row else 0,
        supplier_name=supplier.name if supplier else '',
        product_names={code:name for code,name,_ in products},product_units={code:unit for code,_,unit in products},
        warehouses=list(session.scalars(select(Warehouse).where(Warehouse.kind == 'KHO').order_by(Warehouse.code))),
        can_write=has_role(request.state.user, *svc.WRITERS), editable=row is None or row.status=='NHAP',
        error=error, error_code=code, status=status, reason=reason, saved=request.query_params.get('da_luu')=='1' and not error, placed=request.query_params.get('da_dat')=='1',
        supplier=supplier, prefill_note=prefill_note, stock=_stock(session, codes) if composing else {}, kho=kho, default_warehouse=default_warehouse, default_vat=default_vat,
        last_costs={c: svc.suggest_cost(session, request.state.user, c) for c in codes if c} if composing else {},
        field_errors=where[0], line_errors=where[1])


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
            raise BusinessError('BAD_PRICE',f'Đơn giá dòng {n} phải là số nguyên đồng, không âm.',{'line':n-1})
        try:vat=int(line['vat_rate'])
        except ValueError:raise BusinessError('BAD_VAT',f'VAT dòng {n} phải là 0, 5, 8 hoặc 10%.',{'line':n-1})
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
def listing(request: Request, q: str='', status: str='', supplier_code: str='', tre: str='',
            from_date: str=Query(default='', alias='from'), to_date: str=Query(default='', alias='to'), trang: str='', so: str='',
            user: User=Depends(purchase_user), session: Session=Depends(get_session)):
    from . import paging
    q, status = q[:200], status if status in (*STATUSES, 'CHO_NHAN') else ''
    error = None
    try:
        start, end = (date.fromisoformat(v) if v else None for v in (from_date, to_date))
    except ValueError:
        start = end = None; from_date = to_date = ''
        error = 'Ngày lọc chưa hợp lệ.'
    today = datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date()
    late = lambda r: r.status in OPEN and r.expected_date is not None and r.expected_date < today
    # Lấy đủ theo ô tìm, NCC, ngày; tab đếm trên tập này rồi mới lọc trạng thái và cắt trang.
    found = svc.search(session, user, q, None, supplier_code or None, start, end, cap=False)
    counts = {code: sum(1 for r in found if r.status == code) for code in STATUSES}
    rows = [r for r in found if (not status or (r.status in OPEN if status == 'CHO_NHAN' else r.status == status)) and (not tre or late(r))]
    query = dict(q=q, status=status, supplier_code=supplier_code, tre=tre, **{'from': from_date, 'to': to_date})
    page, shown = paging.slice_rows(rows, trang, so, query)
    link = paging.linker('/don-mua', query)
    received = dict(session.execute(select(PurchaseReceipt.order_id, func.sum(PurchaseReceipt.total))
        .where(PurchaseReceipt.order_id.in_([r.id for r in shown]), PurchaseReceipt.status == 'DA_GHI')
        .group_by(PurchaseReceipt.order_id)).all()) if shown else {}
    codes = {l.product_code for r in shown for l in r.lines}
    suppliers = list(session.scalars(select(Supplier).order_by(Supplier.code)))
    # Ô số đếm toàn bộ đơn (không theo bộ lọc), bấm là lọc.
    month_start = today.replace(day=1)
    alls = list(session.scalars(select(PurchaseOrder).where(PurchaseOrder.status != 'DA_HUY')))
    waiting = [r for r in alls if r.status in OPEN]
    month = [r for r in alls if r.status != 'NHAP' and r.order_date >= month_start]
    late_n = sum(1 for r in alls if late(r))
    tiles = [
        dict(label='Chờ nhận hàng', n=len(waiting), sub=money(sum(r.total for r in waiting)) + ' đ', href='/don-mua?status=CHO_NHAN', on=status == 'CHO_NHAN' and not tre, tone='cho' if waiting else ''),
        dict(label='Nhận quá hẹn', n=late_n, sub='Gọi NCC hỏi ngày giao' if late_n else 'Không có đơn trễ', href='/don-mua?tre=1', on=bool(tre), tone='huy' if late_n else ''),
        dict(label='Nháp chưa đặt', n=sum(1 for r in alls if r.status == 'NHAP'), sub='Đặt để NCC giao', href='/don-mua?status=NHAP', on=status == 'NHAP' and not tre),
        dict(label='Đặt tháng này', n=len(month), sub=money(sum(r.total for r in month)) + ' đ', href=f'/don-mua?from={month_start.isoformat()}', on=False, tone='xong'),
    ]
    tabs = [dict(label='Tất cả', n=len(found), href=link(status=''), on=not status)] + [
        dict(label=label, n=counts[code], href=link(status=code), on=status == code) for code, label in STATUSES.items()]
    if status == 'CHO_NHAN':
        tabs.insert(1, dict(label='Chờ nhận', n=counts['DA_DAT'] + counts['NHAN_MOT_PHAN'], href=link(status='CHO_NHAN'), on=True))
    names = dict(session.execute(select(Supplier.code, Supplier.name)).all())
    chips = [dict(label=label, href=link(**{key: ''})) for key, label in (
        ('supplier_code', 'NCC: ' + names.get(supplier_code, supplier_code)), ('tre', 'Nhận quá hẹn')) if query.get(key)]
    if from_date or to_date:
        chips.append(dict(label='Ngày đặt', href=link(**{'from': '', 'to': ''})))
    return render(request, 'don_mua_ds.jinja', rows=shown, page=page, tiles=tiles, tabs=tabs, chips=chips, link=link, q=q,
        selected_status=status, statuses=STATUSES, supplier_code=supplier_code, suppliers=suppliers, supplier_names=names,
        received=received, late=late, tre=tre, from_date=from_date, to_date=to_date, error=error, total_sum=sum(r.total for r in rows),
        product_names=dict(session.execute(select(Product.code, Product.name).where(Product.code.in_(codes))).all()) if codes else {},
        can_write=has_role(user, *svc.WRITERS), can_debts=nav.allowed(user, '/cong-no-ncc'))


@router.get('/don-mua/tim-ncc')
def pick_supplier(request: Request, q: str='', user: User=Depends(purchase_user), session: Session=Depends(get_session)):
    return render(request,'don_mua_chon.jinja',kind='supplier',rows=[s for s in inventory_catalogs.search(session,Supplier,q[:200]) if s.active],costs={})


@router.get('/don-mua/tim-hang')
def pick_product(request: Request, q: str='', user: User=Depends(purchase_user), session: Session=Depends(get_session)):
    rows=products.search(session,q[:200],active_only=True,limit=20)
    return render(request,'don_mua_chon.jinja',kind='product',rows=rows,costs={p.code:svc.suggest_cost(session,user,p.code) for p in rows},stock=_stock(session,[p.code for p in rows]))


@router.get('/don-mua/san-pham')
def browse_products(request: Request, q: str='', user: User=Depends(purchase_user), session: Session=Depends(get_session)):
    """Ô "+ Tìm hàng để thêm": hàng đang bán, giá nhập lần trước, tồn từng kho."""
    rows=products.search(session,q[:200],active_only=True,limit=20)
    return render(request,'don_mua_san_pham.jinja',rows=rows,costs={p.code:svc.suggest_cost(session,user,p.code) for p in rows},stock=_stock(session,[p.code for p in rows]))


@router.get('/don-mua/moi')
def new(request: Request, user: User=Depends(purchase_user), session: Session=Depends(get_session)):
    svc.check_writer(user)
    # Đợt 4: điền sẵn một hoặc nhiều dòng (product_code, qty, kho lặp theo thứ tự), ví dụ từ Tồn kho › Sắp hết.
    params = request.query_params
    codes, qtys, khos = params.getlist('product_code')[:50], params.getlist('qty'), params.getlist('kho')
    active_kho = {w.code for w in session.scalars(select(Warehouse).where(Warehouse.kind == 'KHO')) if w.active}
    lines, seen = [], set()
    for i, code in enumerate(codes):
        product = session.get(Product, code) if code else None
        if product is None or not product.active or code in seen:
            continue
        seen.add(code)
        qty = re.sub(r'\D', '', qtys[i] if i < len(qtys) else '')[:6].lstrip('0') or '1'
        kho = khos[i] if i < len(khos) and khos[i] in active_kho else ''
        lines.append(dict(product_code=code, warehouse_code=kho, qty=qty, unit_cost=money(svc.suggest_cost(session, user, code) or 0), vat_rate=DEFAULT_VAT_RATE))
    data, note = None, ''
    if lines:
        found = last_suppliers(session, [l['product_code'] for l in lines])
        same = {found[l['product_code']]['code'] if l['product_code'] in found else '' for l in lines}
        supplier = same.pop() if len(same) == 1 else ''
        data = dict(supplier_code=supplier, order_date=quotes._today().isoformat(), expected_date='', note='', version=0, lines=lines)
        if params.get('tu') == 'sap-het':
            note = (f'Điền sẵn từ Tồn kho › Sắp hết: {len(lines)} dòng, số lượng = Nên đặt, giá = lần mua trước'
                    + (', NCC = NCC lần trước của các dòng' if supplier else '') + '. Kiểm tra rồi bấm Lưu và đặt hàng.')
    # Đợt 7: mở từ trang một NCC thì điền sẵn NCC (chỉ NCC đang dùng).
    chosen = params.get('supplier_code', '')
    if chosen and (data is None or not data['supplier_code']):
        sup = session.get(Supplier, chosen)
        if sup is not None and sup.active:
            data = data or dict(order_date=quotes._today().isoformat(), expected_date='', note='', version=0, lines=[])
            data['supplier_code'] = sup.code
    return _form(request,session,data=data,prefill_note=note)


@router.post('/don-mua/moi')
def create(request: Request, form=Depends(checked_form), session: Session=Depends(get_session)):
    user=request.state.user;svc.check_writer(user);data=_raw(form)
    try:row=svc.create(session,user,PurchaseOrderIn(**_body(data)))
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _form(request,session,data=data,error=message,code=code,status=status,where=_where(exc,data))
    return _then_order(request,session,user,row,form)


def _then_order(request,session,user,row,form):
    """Nút "Lưu và đặt hàng": lưu xong đặt luôn. Đặt không được thì nháp vẫn giữ, báo lý do trên trang đơn."""
    if form.get('then')=='order':
        try:svc.order(session,user,row.id,row.version)
        except BusinessError as exc:
            return _form(request,session,row=svc.load(session,user,row.id),error='Đã lưu nháp nhưng chưa đặt hàng được: '+exc.message,code=exc.code,status=exc.status)
        return RedirectResponse(f'/don-mua/{row.id}?da_luu=1&da_dat=1',status_code=303)
    return RedirectResponse(f'/don-mua/{row.id}?da_luu=1',status_code=303)


@router.get('/don-mua/{order_id}')
def view(request: Request, order_id: int, user: User=Depends(purchase_user), session: Session=Depends(get_session)):
    return _form(request,session,row=svc.load(session,user,order_id))


@router.post('/don-mua/{order_id}')
def update(request: Request, order_id: int, form=Depends(checked_form), session: Session=Depends(get_session)):
    user=request.state.user;svc.check_writer(user);data=_raw(form)
    try:row=svc.update(session,user,order_id,PurchaseOrderUpdate(version=_version(form),**_body(data)))
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _form(request,session,row=svc.load(session,user,order_id),data=data,error=message,code=code,status=status,where=_where(exc,data))
    return _then_order(request,session,user,row,form)


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
