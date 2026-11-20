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
from . import money, render, web_user
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
    if preset == 'week':
        return today - timedelta(days=today.weekday()), today
    if preset == 'year':
        return today.replace(month=1, day=1), today.replace(month=12, day=31)
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
            ('Tuần này', 'week', start == today - timedelta(days=today.weekday()) and end == today and today.weekday() > 0),
            ('Tháng này', 'month', start == today.replace(day=1) and end == today.replace(day=monthrange(today.year, today.month)[1])),
            ('Tháng trước', 'last_month', start == last.replace(day=1) and end == last),
            ('Năm nay', 'year', start == today.replace(month=1, day=1) and end == today.replace(month=12, day=31))]


def _short(value):
    """158.034.298 -> '158,0 triệu' cho câu tóm tắt; dưới 1 triệu giữ số đủ."""
    value = int(value or 0)
    if abs(value) >= 1_000_000_000:
        return f'{value / 1_000_000_000:,.2f} tỷ'.replace('.', ',')
    if abs(value) >= 1_000_000:
        return f'{value / 1_000_000:,.1f} triệu'.replace('.', ',')
    return money(value) + ' đ'


def _change(now, before):
    """% thay đổi so với kỳ trước; None khi kỳ trước bằng 0."""
    if not before:
        return None
    return round((now - before) * 100 / abs(before))


def _ranked(rows, total, limit=6):
    """Danh mục xếp lớn đến nhỏ, có %; quá limit thì gom phần còn lại vào một dòng Khác."""
    rows = sorted(rows, key=lambda r: -r['amount'])
    out = [dict(r, pct=(r['amount'] * 100 / total) if total else 0) for r in rows]
    if len(out) > limit:
        rest = out[limit - 1:]
        amount = sum(r['amount'] for r in rest)
        out = out[:limit - 1] + [dict(code='', name=f'Khác ({len(rest)} danh mục)', documents=sum(r['documents'] for r in rest),
                                      amount=amount, pct=(amount * 100 / total) if total else 0)]
    return out


def _chart(daily, opening):
    """Dữ liệu vẽ SVG dòng tiền (vẽ ở server, không thư viện): theo ngày, kỳ dài hơn 62 ngày thì gộp theo tháng."""
    if len(daily) > 62:
        months = {}
        for r in daily:
            key = r['day'].replace(day=1)
            m = months.setdefault(key, dict(day=key, income=0, expense=0, closing_balance=r['closing_balance']))
            m['income'] += r['income']; m['expense'] += r['expense']; m['closing_balance'] = r['closing_balance']
        buckets, fmt = list(months.values()), '%m/%Y'
    else:
        buckets, fmt = daily, '%d/%m'
    if not buckets:
        return None
    W, H, X0, TOP, MID, BOT = 1000, 230, 64, 16, 118, 200
    peak = max([1] + [b['income'] for b in buckets] + [b['expense'] for b in buckets])
    balances = [opening] + [b['closing_balance'] for b in buckets]
    lo, hi = min(balances + [0]), max(balances + [1])
    step = (W - X0 - 10) / len(buckets)
    bw = max(2, min(14, step * 0.36))
    bars, points, labels = [], [], []
    every = max(1, round(len(buckets) / 12))
    for i, b in enumerate(buckets):
        x = X0 + i * step + step / 2
        up = (MID - TOP) * b['income'] / peak
        down = (BOT - MID) * b['expense'] / peak
        if b['income']:
            bars.append(dict(x=round(x - bw, 1), y=round(MID - up, 1), w=round(bw, 1), h=round(max(up, 1), 1), tone='in',
                             tip=f"{b['day'].strftime(fmt)}: vào {money(b['income'])}"))
        if b['expense']:
            bars.append(dict(x=round(x, 1), y=MID, w=round(bw, 1), h=round(max(down, 1), 1), tone='out',
                             tip=f"{b['day'].strftime(fmt)}: ra {money(b['expense'])}"))
        y = BOT - (BOT - TOP) * (b['closing_balance'] - lo) / ((hi - lo) or 1)
        points.append(f'{x:.1f},{y:.1f}')
        if i % every == 0 or i == len(buckets) - 1:
            labels.append(dict(x=round(x, 1), text=b['day'].strftime(fmt)))
    last_x, last_y = points[-1].split(',')
    return dict(w=W, h=H, x0=X0, top=TOP, mid=MID, bot=BOT, bars=bars, points=' '.join(points), labels=labels,
                peak=_short(peak).replace(' triệu', 'tr').replace(' đ', ''), by_month=fmt == '%m/%Y',
                last=dict(x=float(last_x), y=float(last_y), text=money(buckets[-1]['closing_balance'])))


def _summary(session, user, start, end):
    totals = svc.category_totals(session, user, start, end)
    prev_start, prev_end = svc.previous_range(start, end)
    prev = svc.category_totals(session, user, prev_start, prev_end)
    income = _ranked(svc.categories(session, user, 'THU', start, end), totals['income'])
    expense = _ranked(svc.categories(session, user, 'CHI', start, end), totals['expense'])
    daily = svc.daily(session, user, start, end)
    opening = svc.opening_balance(session, user, start)
    today = svc._today()
    upto = min(end, today)
    closing = next((r['closing_balance'] for r in reversed(daily) if r['day'] <= upto), opening)
    same_month = start.day == 1 and start.month == end.month and start.year == end.year
    label = (f'Tháng {start.month}' + (' đến hôm nay' if start <= today < end else '')) if same_month and end.day >= 28 else (
        f'Ngày {start:%d/%m}' if start == end else f'Từ {start:%d/%m} đến {end:%d/%m}')
    prev_label = f'tháng {prev_start.month}/{prev_start.year}' if prev_start.day == 1 and prev_end.day >= 28 and prev_start.month == prev_end.month else f'{prev_start:%d/%m} – {prev_end:%d/%m}'
    ratio = round(totals['expense'] * 100 / totals['income']) if totals['income'] else None
    return dict(totals=totals, prev=prev, prev_label=prev_label, label=label, income=income, expense=expense,
                daily=[r for r in daily if r['income'] or r['expense']], chart=_chart(daily, opening), opening=opening, closing=closing,
                ratio=ratio, short=_short, change=dict(income=_change(totals['income'], prev['income']),
                expense=_change(totals['expense'], prev['expense']), net=_change(totals['net'], prev['net'])),
                biggest=expense[0] if expense and expense[0]['code'] else None)


@router.get('/bao-cao/tai-chinh')
def page(request: Request, user: User = Depends(report_user), session: Session = Depends(get_session)):
    try:
        start, end = _period(request)
        customer = request.query_params.get('khach', '')
        return render(request, 'bao_cao_tai_chinh.jinja', error=None, start=start.isoformat(), end=end.isoformat(),
            presets=_presets(start, end), query=urlencode({'from': start.isoformat(), 'to': end.isoformat()}),
            funds=svc.funds(session, user), fund_total=svc.fund_total(session, user),
            income=svc.categories(session, user, 'THU', start, end), expense=svc.categories(session, user, 'CHI', start, end),
            pending_return_difference=svc.pending_return_difference(session,user,start,end),damage_cost=svc.damage_cost(session,user,start,end),totals=svc.category_totals(session, user, start, end), daily=svc.daily(session, user, start, end),
            customers=svc.customers(session, user), debts=svc.debt_totals(session, user), customer=customer, s=_summary(session, user, start, end),
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
        writer.writerow(['Chứng từ', 'Ngày phát sinh nợ', 'Số ngày', 'Phải thu', 'Đã thu', 'Chờ xác nhận', 'Còn nợ'])
        for r in svc.customer_docs(session, user, request.query_params.get('khach', '')):
            writer.writerow([_csv_text(r['number']), r['day'].isoformat() if r['day'] else '', r['days'],
                             r['receivable'], r['paid'], r['pending'], r['remaining']])
    else:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy bảng báo cáo.', status=404)
    return Response(('\ufeff' + output.getvalue()).encode('utf-8'), media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="tai-chinh-{table}-{start}-{end}.csv"', 'Cache-Control': 'private, no-store'})
