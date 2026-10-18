"""Màn hình tiền D6: chỉ set_charges/receivables ghi/đọc nghiệp vụ tiền."""
import csv
import io
import re
from datetime import date
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import products, service_charges, service_tickets
from ..config import DEFAULT_VAT_RATE
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, Product, StockDoc, User, Warehouse
from ..schemas import ServiceCharges, ServiceLineOut
from ..security import has_role
from . import checked_form, money, render, web_user

router = APIRouter()
TZ = ZoneInfo('Asia/Ho_Chi_Minh')


def detail_context(session, user, ticket, shown=None):
    saved = dict(billing=ticket.billing or '', free_reason=ticket.free_reason or '', version=ticket.version,
                 lines=[ServiceLineOut.model_validate(line).model_dump(mode='json') for line in ticket.lines])
    for line in saved['lines']:
        line['unit_price'] = money(line['unit_price'])
    data = shown if shown is not None else saved
    warehouses = list(session.scalars(select(Warehouse).where(or_(Warehouse.active.is_(True),
                      Warehouse.code.in_([line.get('warehouse_code') for line in data['lines'] if line.get('warehouse_code')]))).order_by(Warehouse.code)))
    names = dict(session.execute(select(Product.code, Product.name).where(
                 Product.code.in_([line.get('product_code') for line in data['lines'] if line.get('product_code')]))).all())
    docs = list(session.execute(select(StockDoc.id, StockDoc.number).where(
                StockDoc.id.in_({line.stock_doc_id for line in ticket.lines if line.stock_doc_id is not None})).order_by(StockDoc.id)))
    return dict(charges=data, charge_names=names, charge_warehouses=warehouses, charge_docs=docs,
                can_charge=has_role(user, 'dispatcher') or ticket.assignee_id == user.id,
                charge_editable=ticket.status in service_tickets.OPEN,
                charge_unsaved=shown is not None,
                charge_subtotal=sum(line.amount for line in ticket.lines), charge_vat=sum(line.vat_amount for line in ticket.lines))


def _raw(form):
    fields = {key: form.getlist(key) for key in ('line_kind', 'line_description', 'line_product', 'line_qty', 'line_price')}
    lines = []
    for i, kind in enumerate(fields['line_kind']):
        def at(key):
            return str(fields[key][i]) if i < len(fields[key]) else ''
        lines.append(dict(kind=str(kind), description=at('line_description'), product_code=at('line_product') or None,
                          warehouse_code=str(form.get(f'warehouse_{i}', '')) or None, qty=at('line_qty'),
                          unit_price=at('line_price'), vat_rate=str(form.get(f'vat_{i}', str(DEFAULT_VAT_RATE)))))
    return dict(billing=str(form.get('billing', '')), free_reason=str(form.get('free_reason', '')),
                version=str(form.get('version', '')), lines=lines)


def _body(raw, version):
    if not raw['billing']:
        raise BusinessError('BILLING_REQUIRED', 'Chọn Có thu tiền hoặc Miễn phí trước khi lưu tiền.')
    lines = []
    for i, line in enumerate(raw['lines'], 1):
        price = str(line['unit_price']).strip()
        if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)', price):
            raise BusinessError('BAD_PRICE', f'Đơn giá dòng {i} chưa hợp lệ; nhập số nguyên đồng.')
        try:
            vat = int(line['vat_rate'])
        except ValueError:
            raise BusinessError('BAD_VAT', f'VAT dòng {i} phải là 0, 5, 8 hoặc 10%.')
        lines.append({**line, 'unit_price': int(price.replace('.', '')), 'vat_rate': vat,
                      'qty': str(line['qty']).replace(',', '.')})
    return ServiceCharges(billing=raw['billing'], free_reason=raw['free_reason'], version=version, lines=lines)


@router.get('/dich-vu/tim-vat-tu')
def pick_material(request: Request, q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    return render(request, 'dich_vu_chon_vat_tu.jinja', rows=products.search(session, q[:200], active_only=True, limit=20))


@router.post('/dich-vu/{ticket_id}/tien/luu')
def save(request: Request, ticket_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    from .services import _detail, _version
    service_tickets.load(session, request.state.user, ticket_id)
    raw = _raw(form)
    try:
        service_charges.set_charges(session, request.state.user, ticket_id, _body(raw, _version(form)))
    except (BusinessError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            labels = {'qty': 'Số lượng', 'unit_price': 'Đơn giá', 'vat_rate': 'VAT', 'description': 'Mô tả',
                      'product_code': 'Mã hàng', 'warehouse_code': 'Kho', 'billing': 'Cách thu', 'lines': 'Dòng tiền'}
            location = exc.errors()[0]['loc']
            error = labels.get(str(location[-1]), 'Dữ liệu') + ' chưa hợp lệ, hãy kiểm tra lại.'
            if len(location) > 1 and isinstance(location[1], int):
                error = f'Dòng {location[1]+1}: ' + error
            code, status = 'BAD_FORM', 400
        else:
            error, code, status = exc.message, exc.code, exc.status
        return _detail(request, session, request.state.user, ticket_id, error=error, error_code=code,
                       status=status, charge_shown=raw)
    return RedirectResponse(f'/dich-vu/{ticket_id}?da_luu=1#tien', status_code=303)


def _viewer(user: User = Depends(web_user)):
    if not has_role(user, *service_charges.VIEWERS):
        raise BusinessError('FORBIDDEN', 'Chỉ điều phối, kế toán hoặc giám đốc xem danh sách cần thu.', status=403)
    return user


def _dates(request):
    start_raw, end_raw = request.query_params.get('from', ''), request.query_params.get('to', '')
    try:
        start = date.fromisoformat(start_raw) if start_raw else None
        end = date.fromisoformat(end_raw) if end_raw else None
        if (start and end and start > end) or end == date.max:
            raise ValueError
    except ValueError:
        raise BusinessError('BAD_DATE_RANGE', 'Khoảng ngày chưa hợp lệ; hãy kiểm tra ngày bắt đầu và ngày kết thúc.')
    return start, end


def _receivables(session, user, start, end):
    rows = service_charges.receivables(session, user, start, end)
    names = dict(session.execute(select(Customer.code, Customer.name).where(Customer.code.in_({t.customer_code for t in rows}))).all())
    return rows, names, sum(t.remaining for t in rows)


@router.get('/dich-vu/can-thu')
def receivables_page(request: Request, user: User = Depends(_viewer), session: Session = Depends(get_session)):
    try:
        start, end = _dates(request)
    except BusinessError as exc:
        return render(request, 'dich_vu_can_thu.jinja', rows=[], names={}, total=0,
                      start=request.query_params.get('from', ''), end=request.query_params.get('to', ''), error=exc.message, status=exc.status)
    rows, names, total = _receivables(session, user, start, end)
    return render(request, 'dich_vu_can_thu.jinja', rows=rows, names=names, total=total,
                  start=start or '', end=end or '', query=urlencode({'from': start or '', 'to': end or ''}), error=None)


@router.get('/dich-vu/can-thu.csv')
def receivables_csv(request: Request, user: User = Depends(_viewer), session: Session = Depends(get_session)):
    start, end = _dates(request)
    rows, names, total = _receivables(session, user, start, end)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Số phiếu', 'Khách hàng', 'Ngày hoàn thành', 'Số phải thu', 'Đã thu', 'Chờ xác nhận', 'Còn nợ'])
    for ticket in rows:
        name = names.get(ticket.customer_code, ticket.customer_code)
        if name.lstrip().startswith(('=', '+', '-', '@')):
            name = "'" + name
        writer.writerow([ticket.number, name, ticket.priced_at.astimezone(TZ).strftime('%d/%m/%Y %H:%M'), ticket.receivable, ticket.paid, ticket.pending, ticket.remaining])
    writer.writerow(['Tổng cộng', '', '', '', '', '', total])
    return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="dich-vu-can-thu.csv"', 'Cache-Control': 'private, no-store'})
