"""Sửa báo thu đã xác nhận một bước (TUẤN duyệt mẫu 12/11/2026). Chỉ giám đốc. Lõi: app.receipt_corrections."""
import re
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import cash_docs, customers as customer_svc, day_closings, finance_mappings
from .. import receipt_corrections as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import CashDoc, Customer, Fund, SalesReceipt, User
from ..security import has_role
from . import checked_form, render, web_user

router = APIRouter()

REASONS = ('Ghi nhầm số tiền', 'Nhầm đơn / phiếu', 'Nhầm khách', 'Nhầm ngày thu', 'Nhầm hình thức', 'KHAC')
REASON_LABELS = {'Ghi nhầm số tiền': 'Nhầm số tiền', 'Nhầm đơn / phiếu': 'Nhầm đơn', 'Nhầm khách': 'Nhầm khách',
                 'Nhầm ngày thu': 'Nhầm ngày', 'Nhầm hình thức': 'Nhầm TM/CK', 'KHAC': 'Khác'}
KIND_LABELS = {'DB': 'Đơn bán', 'DV': 'Phiếu dịch vụ', 'DK': 'Nợ đầu kỳ'}


def _director(user):
    if not has_role(user, 'director'):
        raise BusinessError('FORBIDDEN', 'Chỉ giám đốc được sửa báo thu đã xác nhận.', status=403)


def _receipt(session, receipt_id):
    row = session.get(SalesReceipt, receipt_id)
    if row is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy báo thu.', status=404)
    return row


def source_url(kind, ident, customer_code):
    if kind == 'DB':
        return f'/don-ban/{ident}'
    if kind == 'DV':
        return f'/dich-vu/{ident}'
    return '/cong-no-khach-hang/dau-ky/' + quote(customer_code, safe='')


def _fund_name(session, key):
    try:
        code = finance_mappings.require(session, key)
    except BusinessError:
        return ''
    fund = session.get(Fund, code)
    return fund.name if fund else code


def _people(session, receipt):
    return dict(session.execute(select(User.id, User.full_name)).all())


def _page(request, session, user, receipt, khach='', values=None, error=None, status=200, done=False):
    _director(user)
    info = svc.parent_info(session, receipt)
    code = khach or info['customer_code']
    customer = session.get(Customer, code)
    if customer is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy khách hàng.', status=404)
    old_customer = session.get(Customer, info['customer_code'])
    options = svc.options(session, receipt, code) if receipt.status == 'DA_XAC_NHAN' else []
    old_doc = session.get(CashDoc, receipt.cash_doc_id) if receipt.cash_doc_id else None
    values = values or {}
    default_target = f"{info['kind']}:{info['id']}" if code == info['customer_code'] else (f"{options[0]['kind']}:{options[0]['id']}" if options else '')
    reporter = session.get(User, receipt.reported_by)
    return render(request, 'sua_bao_thu.jinja', r=receipt, info=info, customer=customer, old_customer=old_customer,
                  options=options, old_doc=old_doc, reporter=reporter, can=svc.can_correct(user, receipt),
                  replaced_by=session.get(SalesReceipt, receipt.replaced_by_id) if receipt.replaced_by_id else None,
                  replaces=session.scalar(select(SalesReceipt).where(SalesReceipt.replaced_by_id == receipt.id)), done=done,
                  is_correction=svc.is_correction, people=_people(session, receipt),
                  old_day_closed=bool(old_doc) and day_closings.is_closed(session, old_doc.doc_date), today=cash_docs.today(),
                  funds={'TIEN_MAT': _fund_name(session, 'THU_TIEN_MAT'), 'CHUYEN_KHOAN': _fund_name(session, 'THU_CHUYEN_KHOAN')},
                  v=dict(target=values.get('target', default_target), amount=values.get('amount', receipt.amount),
                         received_at=values.get('received_at', receipt.received_at.isoformat()), method=values.get('method', receipt.method),
                         reason_choice=values.get('reason_choice', ''), reason_other=values.get('reason_other', ''),
                         version=values.get('version', receipt.version)),
                  reasons=REASONS, reason_labels=REASON_LABELS, kind_labels=KIND_LABELS, source_url=source_url,
                  error=error.message if error else None, error_code=error.code if error else '', status=status)


@router.get('/sua-bao-thu/{receipt_id}')
def page(request: Request, receipt_id: int, khach: str = '', xong: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    _director(user)
    return _page(request, session, user, _receipt(session, receipt_id), khach=khach[:64], done=xong == '1')


@router.get('/sua-bao-thu/{receipt_id}/tim-khach')
def find_customer(request: Request, receipt_id: int, q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    _director(user)
    rows = list(session.scalars(select(Customer).where(*customer_svc.matches(q[:200])).order_by(Customer.name).limit(20))) if q.strip() else []
    return render(request, 'sua_bao_thu_khach.jinja', rows=rows, receipt_id=receipt_id)


def _parse(form):
    target = str(form.get('target', ''))
    m = re.fullmatch(r'(DB|DV|DK):([0-9]{1,18})', target)
    if not m:
        raise BusinessError('BAD_TARGET', 'Chọn đơn, phiếu hoặc nợ đầu kỳ để gán báo thu.')
    amount = str(form.get('amount', '')).strip()
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)', amount) or len(amount.replace('.', '')) > 18:
        raise BusinessError('BAD_AMOUNT', 'Số tiền đúng phải là số nguyên đồng.')
    try:
        received = date.fromisoformat(str(form.get('received_at', '')))
    except ValueError:
        raise BusinessError('BAD_DATE', 'Ngày thu chưa hợp lệ.') from None
    choice = str(form.get('reason_choice', '')).strip()
    other = str(form.get('reason_other', '')).strip()[:500]
    if choice not in REASONS:
        choice = 'KHAC'
    reason = other if choice == 'KHAC' else (choice + (': ' + other if other else ''))
    version = str(form.get('version', ''))
    if not version.isdigit() or len(version) > 9:
        raise BusinessError('VERSION_REQUIRED', 'Phải gửi phiên bản báo thu, hãy tải lại.')
    return dict(kind=m.group(1), target_id=int(m.group(2)), amount=int(amount.replace('.', '')), received_at=received,
                method=str(form.get('method', '')), reason=reason), int(version)


@router.post('/sua-bao-thu/{receipt_id}')
def save(request: Request, receipt_id: int, background: BackgroundTasks, form=Depends(checked_form),
         user: User = Depends(web_user), session: Session = Depends(get_session)):
    _director(user)
    receipt = _receipt(session, receipt_id)
    try:
        args, version = _parse(form)
        new = svc.correct(session, user, receipt_id, version, **args)
    except BusinessError as exc:
        session.rollback()
        receipt = _receipt(session, receipt_id)
        values = {k: str(form.get(k, '')) for k in ('target', 'amount', 'received_at', 'method', 'reason_choice', 'reason_other', 'version')}
        return _page(request, session, user, receipt, khach=str(form.get('khach', ''))[:64], values=values, error=exc, status=exc.status)
    background.add_task(svc.notify, receipt.id)
    return RedirectResponse(f'/sua-bao-thu/{new.id}?xong=1', status_code=303)
