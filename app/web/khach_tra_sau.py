"""Khách trả sau trên màn thợ (TUẤN duyệt mẫu 12/11/2026). Lõi danh sách: app.khach_tra_sau; báo thu: sales_receipts.report_for_ticket."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .. import khach_tra_sau as svc, sales_receipts, vietqr
from ..db import get_session
from ..errors import BusinessError
from ..models import SalesReceipt, ServiceTicket, User
from . import checked_form, render, web_user
from .receipts import _report_body

router = APIRouter()
METHODS = {'TIEN_MAT': 'Tiền mặt', 'CHUYEN_KHOAN': 'Chuyển khoản'}
KINDS = {'SUA_TAI_CHO': 'Sửa tại chỗ', 'MANG_VE': 'Mang về xưởng', 'BAO_HANH': 'Bảo hành', 'MUC_DINH_KY': 'Mực định kỳ'}


@router.get('/tien-toi-giu/khach-tra-sau')
def pick(request: Request, q: str = '', phan: str = '', tab: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    """tab=khach: Thu hộ theo khách (TUẤN duyệt mẫu 12/11/2026); mặc định: phiếu của tôi."""
    if tab == 'khach':
        rows = svc.customers(session, user, q)
        if phan:
            return render(request, 'thu_ho_dong.jinja', rows=rows, q=q)
        return render(request, 'khach_tra_sau_ds.jinja', tab='khach', rows=rows, q=q, days=svc.DAYS, kinds=KINDS,
                      mine_count=len(svc.tickets(session, user)))
    rows = svc.tickets(session, user, q)
    if phan:
        return render(request, 'khach_tra_sau_dong.jinja', rows=rows, q=q, kinds=KINDS)
    return render(request, 'khach_tra_sau_ds.jinja', tab='phieu', rows=rows, q=q, days=svc.DAYS, kinds=KINDS, mine_count=len(rows) if not q else None)


def _form(request, session, user, ticket_id, data=None, error=None, status=200):
    row = svc.one(session, user, ticket_id)
    ticket = session.get(ServiceTicket, ticket_id)
    from .lists import _today
    data = data or dict(amount=f'{row["due"]:,}'.replace(',', '.'), method='TIEN_MAT', received_at=_today().isoformat(), version=ticket.version)
    qr = f'/qr/dich-vu/{ticket_id}' if vietqr.company_account(session) else ''
    return render(request, 'khach_tra_sau_bao_thu.jinja', row=row, data=data, methods=METHODS, kinds=KINDS, qr_url=qr, today=_today(),
                  error=error.message if error else None, error_code=error.code if error else '', status=status)


@router.get('/tien-toi-giu/khach-tra-sau/{ticket_id}')
def form(request: Request, ticket_id: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _form(request, session, user, ticket_id)


@router.post('/tien-toi-giu/khach-tra-sau/{ticket_id}')
def report(request: Request, ticket_id: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    raw = {key: str(form.get(key, '')) for key in ('version', 'method', 'amount', 'received_at')}
    raw['note'] = 'Khách trả sau'
    try:
        svc.one(session, user, ticket_id)
        if raw['received_at']:
            date.fromisoformat(raw['received_at'])
        receipt = sales_receipts.report_for_ticket(session, user, ticket_id, _report_body(form, raw))
    except (BusinessError, ValidationError, ValueError) as exc:
        session.rollback()
        if isinstance(exc, BusinessError) and exc.code in ('FORBIDDEN', 'NOT_ELIGIBLE'):
            raise
        error = exc if isinstance(exc, BusinessError) else BusinessError('BAD_FORM', 'Số tiền, cách thu hoặc ngày thu chưa hợp lệ.')
        return _form(request, session, user, ticket_id, data=raw, error=error, status=error.status)
    return RedirectResponse(f'/tien-toi-giu?da_bao={receipt.id}', status_code=303)


def reported(session, user, receipt_id):
    """Dòng báo vừa lập để hiện thông báo trên Tiền tôi giữ (chỉ của chính người báo)."""
    row = session.get(SalesReceipt, receipt_id) if receipt_id else None
    if row is None or row.reported_by != user.id or row.ticket_id is None:
        return None
    ticket = session.get(ServiceTicket, row.ticket_id)
    return dict(amount=row.amount, number=ticket.number, method=METHODS[row.method])


# ---- Thu hộ theo khách (TUẤN duyệt mẫu 12/11/2026) ----

def _collect_form(request, session, user, code, data=None, error=None, status=200):
    from .lists import _today
    row = svc.customer(session, user, code)
    docs = sales_receipts.customer_unpaid_docs(session, code)
    data = data or dict(amount=f'{row["due"]:,}'.replace(',', '.'), method='TIEN_MAT', received_at=_today().isoformat())
    return render(request, 'thu_ho_bao_thu.jinja', row=row, docs=docs, total=sum(d['unpaid'] for d in docs), data=data,
                  methods=METHODS, today=_today(), error=error.message if error else None, error_code=error.code if error else '', status=status)


@router.get('/tien-toi-giu/thu-ho/{code}')
def collect_form(request: Request, code: str, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _collect_form(request, session, user, code)


@router.post('/tien-toi-giu/thu-ho/{code}')
def collect(request: Request, code: str, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    from ..schemas import CustomerReceiptReport
    from .customer_debts import _parse_amount
    raw = {key: str(form.get(key, '')) for key in ('method', 'amount', 'received_at', 'expected_total')}
    try:
        svc.check(user)
        amount = _parse_amount(raw['amount'])
        received = date.fromisoformat(raw['received_at']) if raw['received_at'] else None
        expected = int(raw['expected_total']) if raw['expected_total'].isdigit() else None
        body = CustomerReceiptReport(method=raw['method'], amount=amount, received_at=received, expected_total=expected)
        receipts = sales_receipts.report_for_customer(session, user, code, body, thu_ho=True)
    except (BusinessError, ValidationError, ValueError) as exc:
        session.rollback()
        if isinstance(exc, BusinessError) and exc.code in ('FORBIDDEN', 'NOT_FOUND', 'NOTHING_DUE'):
            raise
        error = exc if isinstance(exc, BusinessError) else BusinessError('BAD_FORM', 'Số tiền, cách thu hoặc ngày thu chưa hợp lệ.')
        return _collect_form(request, session, user, code, data=raw, error=error, status=error.status)
    return RedirectResponse('/tien-toi-giu?thu_ho=' + '.'.join(str(r.id) for r in receipts), status_code=303)


def collected(session, user, raw):
    """Thông báo vừa thu hộ trên Tiền tôi giữ: chỉ đọc lại báo thu của chính người báo."""
    ids = [int(x) for x in (raw or '').split('.') if x.isdigit()][:50]
    if not ids:
        return None
    rows = [r for r in (session.get(SalesReceipt, i) for i in ids) if r is not None and r.reported_by == user.id
            and sales_receipts.is_collected(r.note)]
    if not rows:
        return None
    name = svc.customer_name(session, rows[0])
    return dict(amount=sum(r.amount for r in rows), n=len(rows), customer=name, method=METHODS[rows[0].method])
