"""Màn hình kho: gọi nghiệp vụ hiện có; tồn và thẻ kho chỉ đọc."""
from datetime import date
from decimal import Decimal
from urllib.parse import quote
import re

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import checked_form, render, web_user
from .. import products, stock_docs, stock_posting, stock_min_levels, technician_stock
from ..customers import like_pattern
from ..db import get_session
from ..errors import BusinessError
from ..inventory_catalogs import check_writer
from ..models import Product, StockBalance, StockDoc, StockLedger, StockMinLevel, Supplier, User, Warehouse
from ..schemas import StockDocCancel, StockDocIn, StockDocPatch, StockMinLevelIn
from ..security import has_role
from ..text import fold

router = APIRouter(include_in_schema=False)
KINDS = {"PN": "Nhập", "PX": "Xuất", "CK": "Chuyển", "KK": "Kiểm kê"}
STATES = {"NHAP": "Nháp", "DA_GHI": "Đã ghi", "DA_HUY": "Đã hủy"}


def _error(exc):
    if isinstance(exc, ValidationError):
        field = str(exc.errors()[0]['loc'][-1])
        labels = {'qty': 'Số lượng', 'unit_cost': 'Đơn giá', 'product_code': 'Mã hàng',
                  'doc_date': 'Ngày phiếu', 'lines': 'Dòng hàng', 'reason': 'Lý do', 'version': 'Phiên bản', 'min_qty': 'Mức tồn tối thiểu'}
        return labels.get(field, 'Dữ liệu') + ' chưa hợp lệ, hãy kiểm tra lại.'
    if exc.code == 'NEGATIVE_STOCK':
        d = exc.details
        return f"Không đủ tồn cho {d['product']}: tồn hiện có {Decimal(d['available']):.3f}, số cần {Decimal(d['requested']):.3f}."
    if exc.code == 'BAD_PRODUCT':
        return exc.message + ' ' + ', '.join(exc.details.get('invalid_codes', []))
    return exc.message


def _version(form):
    value = str(form.get('version', ''))
    if not value.isdigit() or int(value) < 1:
        raise BusinessError('BAD_VERSION', 'Thiếu phiên bản phiếu, hãy tải lại trang.')
    return int(value)


def _blank(kind='PN'):
    return dict(id=None, number='', kind=kind if kind in KINDS else 'PN', warehouse_code='',
                to_warehouse_code=None, supplier_code=None, customer_code=None,
                doc_date=date.today().isoformat(), reason='', note='', status='NHAP', version=0,
                lines=[dict(product_code='', qty='1', unit_cost='', serial='', note='')])


def _from_form(form):
    codes, qtys = form.getlist('product_code'), form.getlist('qty')
    lines = []
    # Giữ cả dòng rỗng để người dùng thấy lỗi, không tự bỏ hàng đã nhập.
    for i in range(max(len(codes), len(qtys))):
        row = {'product_code': codes[i] if i < len(codes) else '', 'qty': qtys[i] if i < len(qtys) else ''}
        for field, key in [('unit_cost', 'unit_cost'), ('serial', 'serial'), ('note', 'line_note')]:
            values = form.getlist(key)
            row[field] = values[i] if i < len(values) else ''
        lines.append(row)
    return dict(kind=str(form.get('kind', 'PN')), warehouse_code=str(form.get('warehouse_code', '')),
                to_warehouse_code=form.get('to_warehouse_code') or None,
                supplier_code=form.get('supplier_code') or None, customer_code=form.get('customer_code') or None,
                doc_date=str(form.get('doc_date', '')), reason=str(form.get('reason', '')),
                note=str(form.get('note', '')), lines=lines)


def _body(data):
    lines = []
    for i, row in enumerate(data['lines'], 1):
        cost = str(row['unit_cost']).strip()
        if cost and not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)', cost):
            raise BusinessError('BAD_UNIT_COST', f'Đơn giá dòng {i} phải là số nguyên đồng không âm.')
        lines.append({**row, 'qty': str(row['qty']).replace(',', '.'),
                      'unit_cost': int(cost.replace('.', '')) if cost else None,
                      'serial': row['serial'] or None})
    return StockDocIn(**{**data, 'lines': lines})


def _form(request, session, doc, error=None, status=200, action_reason='', saved=False):
    # Chỉ các bảng danh mục được đọc để gợi ý; không sửa tồn hoặc sổ ở tầng web.
    warehouses = list(session.scalars(select(Warehouse).order_by(Warehouse.code)))
    suppliers = list(session.scalars(select(Supplier).where(or_(Supplier.active.is_(True), Supplier.code == doc.get('supplier_code'))).order_by(Supplier.name)))
    selected_dest = session.get(Warehouse,doc.get('to_warehouse_code')) if doc.get('to_warehouse_code') else None
    return render(request, 'phieu_form.jinja', d=doc, kinds=KINDS, states=STATES, tech_destination=selected_dest.name if selected_dest and selected_dest.kind == 'THO' else '',
                  warehouses=warehouses, suppliers=suppliers, error=error, status=status,
                  action_reason=action_reason, saved=saved,
                  product_names={code: name for code, name in session.execute(
                      select(Product.code, Product.name).where(Product.code.in_([r['product_code'] for r in doc['lines']]))
                  )},
                  can_write=has_role(request.state.user, 'warehouse'))


@router.get('/phieu-kho')
def listing(request: Request, loai: str = '', kho: str = '', trang_thai: str = '', tu: str = '', den: str = '',
            user: User = Depends(web_user), session: Session = Depends(get_session)):
    error = None
    rows = []
    try:
        start, end = date.fromisoformat(tu) if tu else None, date.fromisoformat(den) if den else None
        rows = stock_docs.search(session, loai or None, kho or None, trang_thai or None, start, end)
    except ValueError:
        error = 'Ngày lọc chưa hợp lệ.'
    return render(request, 'phieu_ds.jinja', rows=rows, kinds=KINDS, states=STATES,
                  warehouses=list(session.scalars(select(Warehouse).order_by(Warehouse.code))),
                  loai=loai, kho=kho, trang_thai=trang_thai, tu=tu, den=den, error=error,
                  can_write=has_role(user, 'warehouse'))


@router.get('/phieu-kho/tim-hang')
def product_picker(request: Request, q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    return render(request, 'phieu_chon_hang.jinja', rows=products.search(session, q[:200], limit=20))


@router.get('/phieu-kho/tim-kho-tho')
def pick_tech_warehouse(request: Request,q: str='',user: User=Depends(web_user),session: Session=Depends(get_session)):
    rows = [dict(code=w.code,name=w.name,holder=u.full_name) for w,u in session.execute(select(Warehouse,User).join(User,User.id==Warehouse.holder_id).where(
        Warehouse.kind=='THO',Warehouse.active.is_(True)).order_by(User.full_name,Warehouse.code))
        if fold(q[:200]) in fold(w.code+' '+w.name+' '+u.full_name+' '+u.username)][:20]
    return render(request,'phieu_chon_kho_tho.jinja',rows=rows)


@router.get('/hang-toi-giu')
def technician_held(request: Request,tho: int | None=None,user: User=Depends(web_user),session: Session=Depends(get_session)):
    data=technician_stock.held(session,user,tho)
    return render(request,'hang_toi_giu.jinja',**data,can_manage=has_role(user,'warehouse'))


@router.get('/hang-toi-giu/phieu/{doc_id}')
def technician_transfer(request: Request,doc_id: int,tho: int | None=None,user: User=Depends(web_user),session: Session=Depends(get_session)):
    data=technician_stock.held(session,user,tho)
    doc=stock_docs.load(session,doc_id)
    if not data['warehouse'] or doc.kind!='CK' or data['warehouse'].code not in (doc.warehouse_code,doc.to_warehouse_code):
        raise BusinessError('FORBIDDEN','Bạn chỉ được xem phiếu chuyển liên quan kho của mình.',status=403)
    return render(request,'hang_phieu_ck.jinja',d=doc,**data)


@router.get('/phieu-kho/moi')
def new(request: Request, loai: str = 'PN', user: User = Depends(web_user), session: Session = Depends(get_session)):
    data = _blank(loai)
    data['warehouse_code'] = request.query_params.get('kho_nguon','')
    data['to_warehouse_code'] = request.query_params.get('kho_dich') or None
    if loai == 'CK' and data['warehouse_code']:data['reason'] = 'Trả hàng từ kho thợ'
    return _form(request, session, data)


def _save_form(request, session, form, doc_id=None, posting=False):
    check_writer(request.state.user)
    data = _from_form(form)
    shown = {**(_blank() if doc_id is None else stock_docs.snapshot(stock_docs.load(session, doc_id))), **data,
             'version': form.get('version', '0')}
    try:
        body = _body(data)
        if doc_id is None:
            doc = stock_docs.create(session, request.state.user, body)
        else:
            doc = stock_docs.update(session, request.state.user, doc_id,
                                    StockDocPatch(version=_version(form), **body.model_dump()))
        # Sau khi lưu thành công, lỗi ghi sổ phải quay lại đúng phiếu và phiên bản mới.
        shown.update(id=doc.id, number=doc.number, version=doc.version)
        if posting:
            stock_posting.post(session, request.state.user, doc.id, doc.version)
    except (BusinessError, ValidationError) as exc:
        return _form(request, session, shown, error=_error(exc), status=getattr(exc, 'status', 400))
    return RedirectResponse(f'/phieu-kho/{doc.id}?da_luu=1', status_code=303)


@router.post('/phieu-kho/moi')
def create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    return _save_form(request, session, form, posting=form.get('action') == 'post')


@router.get('/phieu-kho/{doc_id}')
def view(request: Request, doc_id: int, da_luu: int = 0, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _form(request, session, stock_docs.snapshot(stock_docs.load(session, doc_id)), saved=bool(da_luu))


@router.post('/phieu-kho/{doc_id}')
def update(request: Request, doc_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    return _save_form(request, session, form, doc_id)


@router.post('/phieu-kho/{doc_id}/post')
def post(request: Request, doc_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    return _save_form(request, session, form, doc_id, posting=True)


def _cancel(request, session, form, doc_id, posted):
    check_writer(request.state.user)
    shown = stock_docs.snapshot(stock_docs.load(session, doc_id))
    shown['version'] = form.get('version', '')
    reason = str(form.get('reason', ''))
    try:
        version = _version(form)
        if posted:
            stock_posting.void(session, request.state.user, doc_id, reason, version)
        else:
            stock_docs.cancel(session, request.state.user, doc_id, StockDocCancel(reason=reason, version=version))
    except (BusinessError, ValidationError) as exc:
        return _form(request, session, shown, error=_error(exc), status=getattr(exc, 'status', 400), action_reason=reason)
    return RedirectResponse(f'/phieu-kho/{doc_id}?da_luu=1', status_code=303)


@router.post('/phieu-kho/{doc_id}/cancel')
def cancel(request: Request, doc_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    return _cancel(request, session, form, doc_id, False)


@router.post('/phieu-kho/{doc_id}/void')
def void(request: Request, doc_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    return _cancel(request, session, form, doc_id, True)


@router.get('/ton-kho')
def balances(request: Request, kho: str = '', q: str = '', phan: int = 0,
             canh_bao: bool = Query(default=False, alias='canh-bao'),
             user: User = Depends(web_user), session: Session = Depends(get_session)):
    if canh_bao:
        stmt = stock_min_levels.low_stock_query()
        warehouse_column = StockMinLevel.warehouse_code
    else:
        stmt = select(StockBalance.warehouse_code, StockBalance.product_code, StockBalance.qty,
                      StockBalance.value, StockBalance.avg_cost, Product.name, Warehouse.kind.label('warehouse_kind')).join(Warehouse,Warehouse.code==StockBalance.warehouse_code).join(
            Product, Product.code == StockBalance.product_code
        ).order_by(StockBalance.warehouse_code, StockBalance.product_code)
        warehouse_column = StockBalance.warehouse_code
    if kho:
        stmt = stmt.where(warehouse_column == kho)
    if fold(q):
        stmt = stmt.where(or_(Product.search_text.like(like_pattern(fold(q))), Product.code.ilike(like_pattern(q))))
    rows = list(session.execute(stmt).mappings())
    return render(request, 'ton_dong.jinja' if phan else 'ton_ds.jinja', rows=rows, q=q, kho=kho, canh_bao=canh_bao,
                  warehouses=list(session.scalars(select(Warehouse).where(Warehouse.kind=='KHO') if canh_bao else select(Warehouse).order_by(Warehouse.code))),total_value=sum(row['value'] for row in rows))


def _card(request, session, kho, ma, min_qty=None, version=None, error=None, status=200, saved=False):
    product = products.load(session, ma)
    warehouse = session.get(Warehouse, kho)
    if warehouse is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy kho.', status=404)
    rows = list(session.execute(select(StockLedger, StockDoc.number).join(StockDoc, StockDoc.id == StockLedger.doc_id)
                                .where(StockLedger.warehouse_code == kho, StockLedger.product_code == product.code)
                                .order_by(StockLedger.id)))
    level = session.get(StockMinLevel, (kho, product.code))
    return render(request, 'the_kho.jinja', rows=rows, product=product, warehouse=warehouse,
                  min_qty=min_qty if min_qty is not None else str(level.min_qty if level else 0),
                  level_version=version if version is not None else (level.version if level else 0),
                  can_write=has_role(request.state.user, 'warehouse') and warehouse.kind=='KHO', error=error, status=status, saved=saved)


@router.get('/the-kho/{kho}/{ma}')
def ledger(request: Request, kho: str, ma: str, da_luu: int = 0,
           user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _card(request, session, kho, ma, saved=bool(da_luu))


@router.post('/the-kho/{kho}/{ma}')
def set_minimum(request: Request, kho: str, ma: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    check_writer(request.state.user)
    raw_qty, version = str(form.get('min_qty', '')), form.get('version', '')
    try:
        body = StockMinLevelIn(min_qty=raw_qty.replace(',', '.'), version=version)
        stock_min_levels.set_level(session, request.state.user, kho, ma, body)
    except (BusinessError, ValidationError) as exc:
        return _card(request, session, kho, ma, min_qty=raw_qty, version=version,
                     error=_error(exc), status=getattr(exc, 'status', 400))
    return RedirectResponse(f'/the-kho/{quote(kho, safe="")}/{quote(ma, safe="")}?da_luu=1', status_code=303)
