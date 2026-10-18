"""M5: báo cáo/CSV chỉ đọc; quyền được kiểm lại theo từng tab ở lõi."""
import csv
import io
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import purchase_reports as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import User
from ..security import has_role
from . import render, web_user
from .dispatch import _csv_text
from .finance_reports import _period, _presets
from .quotes import _quantity

router=APIRouter()
TABS={'ncc':'Mua theo NCC','hang':'Theo mã hàng','tuoi-no':'Tuổi nợ NCC','lai-gop':'Lãi gộp'}


def report_user(user:User=Depends(web_user)):
    svc.check_reader(user)
    return user


def _tab(user,tab):
    if tab not in TABS:raise BusinessError('BAD_REPORT','Bảng báo cáo không hợp lệ.')
    svc.check_reader(user,financial=tab in ('tuoi-no','lai-gop'))
    return tab


def _as_of(request):
    try:return date.fromisoformat(request.query_params['as_of']) if request.query_params.get('as_of') else svc._today()
    except ValueError:raise BusinessError('BAD_DATE_RANGE','Ngày tính tuổi nợ chưa hợp lệ.')


def _data(session,user,tab,start,end,as_of):
    if tab=='ncc':return dict(rows=svc.purchases_by_supplier(session,user,start,end),totals=svc.purchase_totals(session,user,start,end))
    if tab=='hang':return dict(rows=svc.purchases_by_product(session,user,start,end),totals=svc.product_totals(session,user,start,end))
    if tab=='tuoi-no':return dict(rows=svc.supplier_aging(session,user,as_of),totals=svc.aging_totals(session,user,as_of))
    data=svc.gross_margin(session,user,start,end)
    return dict(rows=data['documents'],totals=data['totals'],products=data['products'],product_totals=data['product_totals'])


@router.get('/bao-cao-mua-hang')
def page(request:Request,user:User=Depends(report_user),session:Session=Depends(get_session)):
    tab=_tab(user,request.query_params.get('tab','ncc'))
    tabs={k:v for k,v in TABS.items() if has_role(user,'finance') or k in ('ncc','hang')}
    try:
        start,end=_period(request);as_of=_as_of(request)
        query=urlencode({'from':start.isoformat(),'to':end.isoformat(),'as_of':as_of.isoformat()})
        return render(request,'bao_cao_mua_hang.jinja',error=None,tab=tab,tabs=tabs,start=start.isoformat(),end=end.isoformat(),
            as_of=as_of.isoformat(),query=query,presets=_presets(start,end),buckets=svc.BUCKETS,quantity=_quantity,
            **_data(session,user,tab,start,end,as_of))
    except BusinessError as exc:
        return render(request,'bao_cao_mua_hang.jinja',error=exc.message,error_code=exc.code,status=exc.status,
            tab=tab,tabs=tabs,start=request.query_params.get('from',''),end=request.query_params.get('to',''),
            as_of=request.query_params.get('as_of',''),query='',presets=_presets(None,None))


def _csv_table(writer,tab,rows,totals):
    if tab=='ncc':
        writer.writerow(['Mã NCC','Nhà cung cấp','Nhận trước VAT','VAT nhận','Tổng nhận','Trả hàng','Mua ròng'])
        keys=('received_amount','received_vat','received_total','returned_total','net_total')
        for r in rows:writer.writerow([_csv_text(r['supplier_code']),_csv_text(r['name']),*[r[k] for k in keys]])
        writer.writerow(['Cộng','',*[totals[k] for k in keys]])
    elif tab=='hang':
        writer.writerow(['Mã hàng','Tên hàng','SL nhận','SL trả','Tiền chưa VAT ròng','Giá mua bình quân nhận'])
        for r in rows:writer.writerow([_csv_text(r['product_code']),_csv_text(r['name']),r['received_qty'],r['returned_qty'],r['net_amount'],r['avg_cost'] if r['avg_cost'] is not None else ''])
        writer.writerow(['Cộng','',totals['received_qty'],totals['returned_qty'],totals['net_amount'],''])
    elif tab=='tuoi-no':
        writer.writerow(['Mã NCC','Nhà cung cấp',*svc.BUCKETS.values(),'Đã trả trước','Còn nợ ròng'])
        for r in rows:writer.writerow([_csv_text(r['supplier_code']),_csv_text(r['name']),*[r[k] for k in svc.BUCKETS],r['advance'],r['remaining']])
        writer.writerow(['Cộng','',*[totals[k] for k in svc.BUCKETS],totals['advance'],totals['remaining']])
    elif tab=='lai-gop':
        writer.writerow(['Chứng từ','Ngày','Khách hàng','Doanh thu chưa VAT','Giá vốn','Lãi gộp','Tỷ lệ (%)'])
        for r in rows:writer.writerow([_csv_text(r['number']),r['day'].isoformat(),_csv_text(r['customer_name']),r['revenue'],r['cost'],r['profit'],r['rate'] if r['rate'] is not None else ''])
        writer.writerow(['Cộng','','',totals['revenue'],totals['cost'],totals['profit'],totals['rate'] if totals['rate'] is not None else ''])
    else:
        writer.writerow(['Mã hàng','Tên hàng','Doanh thu chưa VAT','Giá vốn','Lãi gộp','Tỷ lệ (%)'])
        for r in rows:writer.writerow([_csv_text(r['product_code']),_csv_text(r['name']),r['revenue'],r['cost'],r['profit'],r['rate'] if r['rate'] is not None else ''])
        writer.writerow(['Cộng','',totals['revenue'],totals['cost'],totals['profit'],totals['rate'] if totals['rate'] is not None else ''])


@router.get('/bao-cao-mua-hang/{table}.csv')
def download(request:Request,table:str,user:User=Depends(report_user),session:Session=Depends(get_session)):
    tab=_tab(user,'lai-gop' if table=='lai-gop-hang' else table)
    start,end=_period(request);as_of=_as_of(request)
    data=_data(session,user,tab,start,end,as_of)
    output=io.StringIO();writer=csv.writer(output)
    _csv_table(writer,table,data['products'] if table=='lai-gop-hang' else data['rows'],
        data['product_totals'] if table=='lai-gop-hang' else data['totals'])
    return Response(('\ufeff'+output.getvalue()).encode('utf-8'),media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition':f'attachment; filename="mua-hang-{table}-{start}-{end}.csv"','Cache-Control':'private, no-store'})
