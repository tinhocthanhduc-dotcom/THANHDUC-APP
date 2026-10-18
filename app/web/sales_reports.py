"""B5: trang báo cáo và CSV, chỉ gọi các truy vấn đọc."""
import csv
import io
from calendar import monthrange
from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import sales_reports as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import User
from . import render, web_user
from .dispatch import _csv_text

router = APIRouter()


def report_user(user: User=Depends(web_user)):
    svc.check_reader(user)
    return user


def _period(request):
    today=svc._today()
    preset=request.query_params.get('period','')
    if preset=='today':
        return today,today
    if preset=='month':
        return svc.date_range()
    if preset=='last_month':
        end=today.replace(day=1)-timedelta(days=1)
        return end.replace(day=1),end
    try:
        start=date.fromisoformat(request.query_params['from']) if request.query_params.get('from') else None
        end=date.fromisoformat(request.query_params['to']) if request.query_params.get('to') else None
    except ValueError:
        raise BusinessError('BAD_DATE_RANGE','Khoảng ngày chưa hợp lệ, hãy nhập ngày theo lịch.')
    return svc.date_range(start,end)


def _links(start,end):
    return urlencode({'from':start.isoformat(),'to':end.isoformat()})


@router.get('/bao-cao/ban-hang')
def page(request: Request,user: User=Depends(report_user),session: Session=Depends(get_session)):
    try:
        start,end=_period(request)
        bucket=request.query_params.get('nhom','')
        detail=svc.aging_orders(session,user,bucket) if bucket else []
        today=svc._today()
        last=today.replace(day=1)-timedelta(days=1)
        return render(request,'bao_cao_ban_hang.jinja',start=start.isoformat(),end=end.isoformat(),
            daily=svc.daily(session,user,start,end),totals=svc.totals(session,user,start,end),
            employees=svc.by_employee(session,user,start,end),customers=svc.by_customer(session,user,start,end),
            aging=svc.aging(session,user),conversion=svc.quote_conversion(session,user,start,end),
            detail=detail,bucket=bucket,buckets=svc.BUCKETS,query=_links(start,end),as_of=today.isoformat(),error=None,
            presets=[('Hôm nay','today',start==today and end==today),
                     ('Tháng này','month',start==today.replace(day=1) and end==today.replace(day=monthrange(today.year,today.month)[1])),
                     ('Tháng trước','last_month',start==last.replace(day=1) and end==last)])
    except BusinessError as exc:
        return render(request,'bao_cao_ban_hang.jinja',start=request.query_params.get('from',''),end=request.query_params.get('to',''),
                      presets=[('Hôm nay','today',False),('Tháng này','month',False),('Tháng trước','last_month',False)],
                      error=exc.message,error_code=exc.code,status=exc.status)


@router.get('/bao-cao/ban-hang/{table}.csv')
def download(request: Request,table: str,user: User=Depends(report_user),session: Session=Depends(get_session)):
    start,end=_period(request)
    output=io.StringIO()
    writer=csv.writer(output)
    if table=='ngay':
        writer.writerow(['Ngày','Số đơn','Trước VAT','VAT','Tổng'])
        rows=svc.daily(session,user,start,end)
        for r in rows:
            writer.writerow([r['day'].isoformat(),r['orders'],r['subtotal'],r['vat_total'],r['total']])
        r=svc.totals(session,user,start,end)
        writer.writerow(['Cộng',r['orders'],r['subtotal'],r['vat_total'],r['total']])
    elif table in ('nhan-vien','khach-hang'):
        writer.writerow(['Mã nhân viên' if table=='nhan-vien' else 'Mã khách','Nhân viên' if table=='nhan-vien' else 'Khách hàng',
                         'Số đơn','Trước VAT','VAT','Tổng'])
        rows=svc.by_employee(session,user,start,end) if table=='nhan-vien' else svc.by_customer(session,user,start,end)
        for r in rows:
            writer.writerow([_csv_text(r.get('user_id',r.get('customer_code'))),_csv_text(r['name']),r['orders'],r['subtotal'],r['vat_total'],r['total']])
    elif table=='tuoi-no':
        writer.writerow(['Nhóm tuổi nợ','Số đơn','Còn nợ','Chờ xác nhận'])
        for r in svc.aging(session,user):
            writer.writerow([r['label'],r['orders'],r['remaining'],r['pending']])
    elif table=='don-no':
        writer.writerow(['Số đơn','Khách hàng','Nhân viên','Ngày chốt','Số ngày','Phải thu','Đã thu','Chờ xác nhận','Còn nợ'])
        for r in svc.aging_orders(session,user,request.query_params.get('nhom','')):
            writer.writerow([_csv_text(r['number']),_csv_text(r['customer_name']),_csv_text(r['employee_name']),
                r['confirmed_at'].astimezone(svc.TZ).date().isoformat(),r['days'],r['receivable'],r['paid'],r['pending'],r['remaining']])
    elif table=='bao-gia':
        writer.writerow(['Số báo giá','Có đơn chưa hủy','Tỷ lệ (%)'])
        r=svc.quote_conversion(session,user,start,end)
        writer.writerow([r['quotes'],r['converted'],f"{r['rate']:.2f}"])
    else:
        raise BusinessError('NOT_FOUND','Không tìm thấy bảng báo cáo.',status=404)
    return Response(('\ufeff'+output.getvalue()).encode('utf-8'),media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition':f'attachment; filename="ban-hang-{table}-{start}-{end}.csv"','Cache-Control':'private, no-store'})
