"""T5: báo cáo/CSV tài chính, không có thao tác ghi."""
import csv
import io
from calendar import monthrange
from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import finance_reports as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import User
from . import render, web_user
from .dispatch import _csv_text

router = APIRouter()


def report_user(user: User = Depends(web_user)):
    svc.check_reader(user)
    return user


def _period(request):
    today = svc._today()
    preset = request.query_params.get('period', '')
    if preset == 'today':
        return today, today
    if preset == 'month':
        return svc.date_range()
    if preset == 'last_month':
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    try:
        start = date.fromisoformat(request.query_params['from']) if request.query_params.get('from') else None
        end = date.fromisoformat(request.query_params['to']) if request.query_params.get('to') else None
    except ValueError:
        raise BusinessError('BAD_DATE_RANGE', 'Khoảng ngày chưa hợp lệ, hãy nhập ngày theo lịch.')
    return svc.date_range(start, end)


def _presets(start, end):
    today = svc._today()
    last = today.replace(day=1) - timedelta(days=1)
    return [('Hôm nay', 'today', start == today and end == today),
            ('Tháng này', 'month', start == today.replace(day=1) and end == today.replace(day=monthrange(today.year, today.month)[1])),
            ('Tháng trước', 'last_month', start == last.replace(day=1) and end == last)]


@router.get('/bao-cao/tai-chinh')
def page(request: Request, user: User = Depends(report_user), session: Session = Depends(get_session)):
    try:
        start, end = _period(request)
        customer = request.query_params.get('khach', '')
        return render(request, 'bao_cao_tai_chinh.jinja', error=None, start=start.isoformat(), end=end.isoformat(),
            presets=_presets(start, end), query=urlencode({'from': start.isoformat(), 'to': end.isoformat()}),
            funds=svc.funds(session, user), fund_total=svc.fund_total(session, user),
            income=svc.categories(session, user, 'THU', start, end), expense=svc.categories(session, user, 'CHI', start, end),
            totals=svc.category_totals(session, user, start, end), daily=svc.daily(session, user, start, end),
            customers=svc.customers(session, user), debts=svc.debt_totals(session, user), customer=customer,
            customer_query=urlencode({'khach': customer}), detail=svc.customer_docs(session, user, customer) if customer else [],
            as_of=svc._today().isoformat())
    except BusinessError as exc:
        return render(request, 'bao_cao_tai_chinh.jinja', error=exc.message, error_code=exc.code, status=exc.status,
            start=request.query_params.get('from', ''), end=request.query_params.get('to', ''), presets=_presets(None, None))


@router.get('/bao-cao/tai-chinh/{table}.csv')
def download(request: Request, table: str, user: User = Depends(report_user), session: Session = Depends(get_session)):
    start, end = _period(request)
    output = io.StringIO()
    writer = csv.writer(output)
    if table == 'quy':
        writer.writerow(['Mã quỹ', 'Quỹ', 'Số dư hiện tại'])
        for r in svc.funds(session, user):
            writer.writerow([_csv_text(r['code']), _csv_text(r['name']), r['balance']])
        writer.writerow(['Cộng', '', svc.fund_total(session, user)])
    elif table in ('thu', 'chi'):
        writer.writerow(['Mã danh mục', 'Danh mục', 'Số phiếu', 'Số tiền'])
        for r in svc.categories(session, user, 'THU' if table == 'thu' else 'CHI', start, end):
            writer.writerow([_csv_text(r['code']), _csv_text(r['name']), r['documents'], r['amount']])
        r = svc.category_totals(session, user, start, end)
        writer.writerow(['Cộng', '', r['income_documents' if table == 'thu' else 'expense_documents'], r['income' if table == 'thu' else 'expense']])
    elif table == 'dong-tien':
        writer.writerow(['Ngày', 'Thu', 'Chi', 'Chênh lệch', 'Số dư cuối ngày'])
        rows = svc.daily(session, user, start, end)
        for r in rows:
            writer.writerow([r['day'].isoformat(), r['income'], r['expense'], r['net'], r['closing_balance']])
        r = svc.category_totals(session, user, start, end)
        writer.writerow(['Cộng / Cuối kỳ', r['income'], r['expense'], r['net'], rows[-1]['closing_balance']])
    elif table == 'cong-no':
        writer.writerow(['Mã khách', 'Khách hàng', 'Phải thu', 'Đã thu', 'Chờ xác nhận', 'Còn nợ'])
        for r in svc.customers(session, user):
            writer.writerow([_csv_text(r['customer_code']), _csv_text(r['name']), r['receivable'], r['paid'], r['pending'], r['remaining']])
        r = svc.debt_totals(session, user)
        writer.writerow(['Cộng', '', r['receivable'], r['paid'], r['pending'], r['remaining']])
    elif table == 'chung-tu':
        writer.writerow(['Chứng từ', 'Ngày chốt', 'Số ngày', 'Phải thu', 'Đã thu', 'Chờ xác nhận', 'Còn nợ'])
        for r in svc.customer_docs(session, user, request.query_params.get('khach', '')):
            writer.writerow([_csv_text(r['number']), r['day'].isoformat() if r['day'] else '', r['days'],
                             r['receivable'], r['paid'], r['pending'], r['remaining']])
    else:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy bảng báo cáo.', status=404)
    return Response(('\ufeff' + output.getvalue()).encode('utf-8'), media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="tai-chinh-{table}-{start}-{end}.csv"', 'Cache-Control': 'private, no-store'})
