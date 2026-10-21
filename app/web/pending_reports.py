"""CXL3: financial readers only, HTML and CSV share the same report cells."""
import csv
import io
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import finance_reports, pending_reports as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import User
from . import render
from .dispatch import _csv_text
from .finance_reports import _period, _presets, report_user

router = APIRouter()


def period(request):
    if any(request.query_params.get(k) for k in ('period', 'from', 'to')):
        return _period(request)
    end = finance_reports._today()
    start = end.replace(day=1)
    for _ in range(5):
        start = (start - timedelta(days=1)).replace(day=1)
    return start, end


@router.get('/bao-cao/cho-xu-ly')
def page(request: Request, user: User = Depends(report_user), session: Session = Depends(get_session)):
    try:
        start, end = period(request)
        return render(request, 'bao_cao_cho_xu_ly.jinja', error=None, tz=finance_reports.TZ,
            start=start.isoformat(), end=end.isoformat(), presets=_presets(start, end),
            query=urlencode({'from': start.isoformat(), 'to': end.isoformat()}),
            aging=svc.aging(session, user), monthly=svc.monthly(session, user, start, end),
            suppliers=svc.supplier_summary(session, user, start, end), buckets=svc.BUCKETS,
            hold_buckets=svc.HOLD_BUCKETS, month_columns=svc.MONTH_COLUMNS, supplier_columns=svc.SUPPLIER_COLUMNS)
    except BusinessError as exc:
        return render(request, 'bao_cao_cho_xu_ly.jinja', status=exc.status, error=exc.message,
            error_code=exc.code, start=request.query_params.get('from', ''), end=request.query_params.get('to', ''),
            presets=_presets(None, None))


@router.get('/bao-cao/cho-xu-ly/{table}.csv')
def download(request: Request, table: str, user: User = Depends(report_user), session: Session = Depends(get_session)):
    start, end = period(request)
    output = io.StringIO()
    writer = csv.writer(output)
    if table == 'tuoi-hang':
        writer.writerow(['Vị trí', 'NCC', 'Nhóm', 'Tuổi', 'Số hồ sơ', 'Số lượng', 'Giá trị'])
        data = svc.aging(session, user)
        for location, rows, buckets in (('Tại kho', data['rows'], svc.BUCKETS), ('Đang gửi NCC', data['holding'], svc.HOLD_BUCKETS)):
            for row in rows:
                for key, label, _ in buckets:
                    cell = row['cells'][key]
                    writer.writerow([location, _csv_text(row.get('supplier_name', '')), row['label'], label,
                                     cell['count'], cell['qty'], cell['value']])
        cell = data['total']
        writer.writerow(['Tổng', '', '', '', cell['count'], cell['qty'], cell['value']])
    elif table == 'thang':
        writer.writerow(['Tháng', *[label for _, label in svc.MONTH_COLUMNS]])
        data = svc.monthly(session, user, start, end)
        for row in data['rows']:
            writer.writerow([row['month'].strftime('%m/%Y'), *[row[key] for key, _ in svc.MONTH_COLUMNS]])
        writer.writerow(['Tổng', *[data['total'][key] for key, _ in svc.MONTH_COLUMNS]])
    elif table == 'ncc':
        writer.writerow(['Mã NCC', 'NCC', *[label for _, label in svc.SUPPLIER_COLUMNS]])
        for row in svc.supplier_summary(session, user, start, end):
            stamp = row['oldest_sent_at']
            values = {**row, 'oldest_sent_at': stamp.astimezone(finance_reports.TZ).strftime('%d/%m/%Y %H:%M') if stamp else ''}
            writer.writerow([_csv_text(row['supplier_code'] or ''), _csv_text(row['supplier_name'] or 'Chưa có NCC'),
                             *[values[key] for key, _ in svc.SUPPLIER_COLUMNS]])
    else:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy bảng báo cáo.', status=404)
    return Response(('\ufeff' + output.getvalue()).encode('utf-8'), media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="cho-xu-ly-{table}-{start}-{end}.csv"', 'Cache-Control': 'private, no-store'})
