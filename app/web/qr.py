"""Trang QR chuyển khoản VietQR cho đơn bán, phiếu dịch vụ, báo giá và QR tĩnh công ty (đợt 1, 08/11/2026).

Chỉ đọc: vẽ QR theo tài khoản ngân hàng đang ghép "Thu chuyển khoản". Không ghi sổ, không lập báo thu.
Số tiền mặc định bằng số còn phải thu; người xem chỉ được hạ (thu một phần), không được nâng.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import quotes, sales_orders, sales_receipts, service_tickets, vietqr
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, User
from ..security import has_role
from . import render, web_user
from .money_input import parse_money

router = APIRouter()


def _order(session, user, order_id):
    """Người xem đơn theo quyền đơn bán; thợ được giao đơn cũng mở được QR của đơn đó."""
    try:
        order = sales_orders.load(session, user, order_id)
    except BusinessError as exc:
        if exc.code != 'FORBIDDEN' or not has_role(user, 'technician'):
            raise
        order = sales_orders._load(session, order_id)
        if order.delivery_assignee_id != user.id:
            raise
    if order.status == 'NHAP' or order.status == 'DA_HUY':
        raise BusinessError('BAD_STATUS', 'Đơn chưa chốt hoặc đã hủy, chưa có số phải thu.', status=409)
    due = sales_receipts.balance(order)['available']
    back = f'/don-ban/{order.id}' if has_role(user, *sales_orders.VIEWERS) else f'/tho/giao-don/{order.id}'
    return dict(kind='Đơn bán', number=order.number, customer_code=order.customer_code, due=due, cap=due, back=back,
                receipt_hint='Khách chuyển xong, lập Báo thu · Chuyển khoản trên đơn như hiện nay.')


def _ticket(session, user, ticket_id):
    t = service_tickets.load(session, user, ticket_id)
    if t.status in ('HOAN_THANH', 'DA_DONG') and t.billing == 'THU_TIEN' and (t.receivable or 0) > 0:
        bal = sales_receipts.balance(t)
        due = max(0, bal['remaining'] - bal['pending'])
        cap = due
    else:
        # Phiếu chưa báo giá xong: chưa có số phải thu, thợ tự gõ số tiền khách đồng ý.
        due, cap = (t.total or 0), None
    technician = has_role(user, 'technician') and not has_role(user, *service_tickets.CREATORS)
    back = f'/tho/phieu/{t.id}' if technician else f'/dich-vu/{t.id}'
    return dict(kind='Phiếu dịch vụ', number=t.number, customer_code=t.customer_code, due=due, cap=cap, back=back,
                receipt_hint='Khách chuyển xong, bấm Báo thu · Chuyển khoản trên phiếu như hiện nay.')


def _quote(session, user, quote_id):
    q = quotes.load(session, user, quote_id)
    return dict(kind='Báo giá', number=q.number, customer_code=q.customer_code, due=q.total, cap=q.total, back=f'/bao-gia/{q.id}',
                receipt_hint='Khách chuyển theo báo giá thì lập đơn bán từ báo giá rồi báo thu trên đơn.')


LOADERS = {'don-ban': _order, 'dich-vu': _ticket, 'bao-gia': _quote}


def _amount(request, doc):
    raw = request.query_params.get('so', '')
    if not raw.strip():
        return doc['due'] if doc['due'] >= vietqr.MIN_AMOUNT else None, ''
    try:
        value = parse_money(raw, 'Số tiền', allow_zero=False)
    except BusinessError as exc:
        return (doc['due'] if doc['due'] >= vietqr.MIN_AMOUNT else None), exc.message
    if value < vietqr.MIN_AMOUNT:
        return (doc['due'] if doc['due'] >= vietqr.MIN_AMOUNT else None), f'Số tiền tối thiểu {vietqr.MIN_AMOUNT:,} đồng.'.replace(',', '.')
    if doc['cap'] is not None and value > doc['cap']:
        return doc['cap'], f'Chỉ được hạ số tiền (thu một phần), không được quá số còn phải thu {doc["cap"]:,} đồng.'.replace(',', '.')
    return value, ''


def _load(kind, session, user, ident):
    loader = LOADERS.get(kind)
    if loader is None:
        raise BusinessError('NOT_FOUND', 'Không có loại chứng từ này.', status=404)
    return loader(session, user, ident)


@router.get('/qr/quy')
def static_page(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    account = vietqr.company_account(session)
    qr = dict(account, svg=vietqr.svg(vietqr.payload(account['bank_bin'], account['account_no'], None, '')), amount=None,
              content='', line=vietqr.bank_line(account)) if account else None
    return render(request, 'qr_chuyen_khoan.jinja', qr=qr, doc=None, amount=None, error='', back='/quy' if has_role(user, 'finance') else '/',
                  can_setup=has_role(user, 'finance'), title='QR chuyển khoản công ty')


@router.get('/qr/{kind}/{ident}.svg')
def image(kind: str, ident: int, request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    doc = _load(kind, session, user, ident)
    amount, _ = _amount(request, doc)
    qr = vietqr.for_document(session, doc['number'], amount)
    if qr is None:
        raise BusinessError('QR_NOT_READY', 'Chưa cấu hình tài khoản ngân hàng để in QR.', status=404)
    return Response(qr['svg'], media_type='image/svg+xml', headers={'Cache-Control': 'no-store'})


@router.get('/qr/{kind}/{ident}')
def page(kind: str, ident: int, request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    doc = _load(kind, session, user, ident)
    amount, error = _amount(request, doc)
    qr = vietqr.for_document(session, doc['number'], amount) if amount is not None else None
    if qr is None and amount is None and vietqr.company_account(session) is not None:
        error = error or 'Chứng từ này không còn số phải thu; gõ số tiền khách muốn chuyển nếu cần.'
    customer = session.get(Customer, doc['customer_code'])
    return render(request, 'qr_chuyen_khoan.jinja', qr=qr, doc=doc, amount=amount, error=error, back=doc['back'],
                  customer_name=customer.name if customer else '', can_setup=has_role(user, 'finance'),
                  title=f"QR chuyển khoản · {doc['number']}", kind=kind, ident=ident)
