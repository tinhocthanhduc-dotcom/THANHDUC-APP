"""Thu chung cả chỗ (TUẤN duyệt mẫu 13/11/2026): nhiều máy cùng chỗ, khách trả một lần.

Lõi: sales_receipts.report_for_family — mỗi phiếu một báo thu đúng số của phiếu, thiếu thì trừ phiếu gốc trước, chờ kế toán xác nhận.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import equipment, sales_receipts, service_tickets, vietqr
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, Equipment, Product, User
from ..security import has_role
from . import checked_form, render, web_user
from .money_input import parse_money

router = APIRouter()
METHODS = {'TIEN_MAT': 'Tiền mặt', 'CHUYEN_KHOAN': 'Chuyển khoản'}


def _base(request, user):
    return '/tho/phieu' if request.url.path.startswith('/tho/') and has_role(user, 'technician') else '/dich-vu'


def _page(request, session, user, ticket_id, data=None, error=None, status=200):
    t = service_tickets.load(session, user, ticket_id)
    rows = sales_receipts.family_due(session, user, t)
    if len(rows) < 2 and not any(r['due'] for r in rows):
        raise BusinessError('NOTHING_DUE', 'Phiếu này không có máy cùng chỗ để thu chung.', status=409)
    serials = [r['t'].serial for r in rows if r['t'].serial]
    from sqlalchemy import select
    names = dict(session.execute(select(Equipment.serial, equipment.name_column()).outerjoin(Product, Product.code == Equipment.product_code)
                                 .where(Equipment.serial.in_(serials))).all()) if serials else {}
    total = sum(r['due'] for r in rows)
    base = _base(request, user)
    data = data or dict(mode='FULL', method='TIEN_MAT', amount='')
    qr = f'/qr/thu-chung/{t.id}' if total and vietqr.company_account(session) else ''
    customer = session.get(Customer, t.customer_code)
    return render(request, 'thu_chung.jinja', t=t, rows=rows, names=names, total=total, data=data, methods=METHODS, base=base,
                  qr_url=qr, customer=customer, error=error.message if error else None, error_code=error.code if error else '', status=status)


@router.get('/tho/phieu/{ticket_id}/thu-chung')
@router.get('/dich-vu/{ticket_id}/thu-chung')
def form(request: Request, ticket_id: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _page(request, session, user, ticket_id)


@router.post('/tho/phieu/{ticket_id}/thu-chung')
@router.post('/dich-vu/{ticket_id}/thu-chung')
def report(request: Request, ticket_id: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    data = {key: str(form.get(key, '')) for key in ('mode', 'method', 'amount', 'note')}
    try:
        t = service_tickets.load(session, user, ticket_id)
        total = sum(r['due'] for r in sales_receipts.family_due(session, user, t))
        if data['method'] not in METHODS:
            raise BusinessError('BAD_METHOD', 'Chọn cách thu: Tiền mặt hoặc Chuyển khoản.')
        if data['mode'] == 'PARTIAL':
            amount = parse_money(data['amount'], 'Số tiền khách đưa', allow_zero=False)
            if amount >= total:
                raise BusinessError('BAD_PARTIAL_AMOUNT', 'Thu một phần phải nhỏ hơn tổng chưa thu; khách trả đủ thì chọn Thu đủ.')
        elif data['mode'] == 'FULL':
            amount = total
        else:
            raise BusinessError('BAD_MODE', 'Chọn Thu đủ hoặc Thu một phần.')
        receipts = sales_receipts.report_for_family(session, user, ticket_id, amount, data['method'], data['note'])
    except BusinessError as exc:
        session.rollback()
        if exc.code in ('FORBIDDEN', 'NOT_FOUND'):
            raise
        return _page(request, session, user, ticket_id, data=data, error=exc, status=exc.status)
    return RedirectResponse(f'{_base(request, user)}/{ticket_id}?da_luu=1&thu_chung={len(receipts)}', status_code=303)
