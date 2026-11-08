"""Trang Lãi ròng tháng (đợt 4, 08/11/2026): chỉ giám đốc; chỉ ghi bảng chi phí ngoài sổ và lương giám đốc mục tiêu."""
import csv
import io
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from .. import profit_report as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import User
from . import checked_form, render, web_user
from .dispatch import _csv_text
from .money_input import parse_money

router = APIRouter()
TABS = (('tong-hop', 'Tổng hợp'), ('theo-loai', 'Theo loại'), ('theo-khach', 'Theo khách'), ('12-thang', '12 tháng'), ('ngoai-so', 'Chi phí ngoài sổ'))


def report_user(user: User = Depends(web_user)):
    svc.check_reader(user)
    return user


def _month(request):
    return svc.month_of(request.query_params.get('thang', ''))


def _url(month, tab='tong-hop', **extra):
    query = dict(thang=month.strftime('%Y-%m'), tab=tab, **{k: v for k, v in extra.items() if v})
    return '/bao-cao/lai-rong?' + urlencode(query)


def _version(form):
    try:
        version = int(str(form.get('version', '')))
        if version < 1:
            raise ValueError
        return version
    except (ValueError, TypeError):
        raise BusinessError('VERSION_REQUIRED', 'Phải gửi phiên bản, hãy tải lại trang.')


@router.get('/bao-cao/lai-rong')
def page(request: Request, user: User = Depends(report_user), session: Session = Depends(get_session)):
    tab = request.query_params.get('tab', 'tong-hop')
    if tab not in dict(TABS):
        tab = 'tong-hop'
    try:
        month = _month(request)
        ctx = dict(month=month, tab=tab, tabs=TABS, prev=svc.prev_month(month), next=svc.next_month(month), url=_url,
                   summary=svc.summary(session, user, month), error=request.query_params.get('loi', ''), error_code='',
                   saved=request.query_params.get('da_luu') == '1', edit_id=request.query_params.get('sua', ''))
        if tab == 'theo-loai':
            ctx['groups'] = svc.by_group(session, user, month)
        elif tab == 'theo-khach':
            ctx['customers'] = svc.by_customer(session, user, month)
        elif tab == '12-thang':
            ctx['months'] = svc.twelve_months(session, user, month)
        elif tab == 'ngoai-so':
            from ..models import PlSetting
            ctx['setting'] = session.get(PlSetting, svc.DIRECTOR_SALARY_KEY)
        return render(request, 'bao_cao_lai_rong.jinja', title=f'Lãi ròng tháng {month:%m/%Y}', **ctx)
    except BusinessError as exc:
        month = svc.month_of(None)
        return render(request, 'bao_cao_lai_rong.jinja', title='Lãi ròng tháng', month=month, tab='tong-hop', tabs=TABS, prev=svc.prev_month(month),
                      next=svc.next_month(month), url=_url, summary=None, error=exc.message, error_code=exc.code, status=exc.status, saved=False, edit_id='')


@router.get('/bao-cao/lai-rong/tong-hop.csv')
def download(request: Request, user: User = Depends(report_user), session: Session = Depends(get_session)):
    month = _month(request)
    s = svc.summary(session, user, month)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['Dòng', f'Tháng {month:%m/%Y}', f'Tháng {s["previous"]["month"]:%m/%Y}'])
    p = s['previous']
    rows = [('Doanh thu thuần (chưa VAT)', s['revenue'], p['revenue']), ('Giá vốn', s['cost'], p['cost']), ('Lãi gộp', s['gross'], p['gross']),
            ('Lương và hoa hồng', s['payroll']['amount'], p['payroll']['amount']), ('Chi phí hoạt động', s['opex']['amount'], p['opex']['amount']),
            ('Hàng hỏng', s['damage'], p['damage']), ('Chi phí ngoài sổ', s['adjustments']['amount'], p['adjustments']['amount']),
            ('Lãi ròng trước lương giám đốc', s['before_director'], p['before_director']),
            ('Lương giám đốc mục tiêu', s['director_salary'], s['director_salary']), ('Còn lại', s['remaining'], s['previous_remaining'])]
    for name, a, b in rows:
        w.writerow([_csv_text(name), a, b])
    w.writerow([])
    w.writerow(['Chi phí hoạt động theo danh mục', 'Số phiếu', 'Số tiền'])
    for r in s['opex']['rows']:
        w.writerow([_csv_text(f"{r['code']} · {r['name']}"), r['documents'], r['amount']])
    return Response(('﻿' + out.getvalue()).encode('utf-8'), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="lai-rong-{month:%Y-%m}.csv"', 'Cache-Control': 'private, no-store'})


def _back(month, error=None, **extra):
    if error is not None:
        extra['loi'] = error
    else:
        extra['da_luu'] = '1'
    return RedirectResponse(_url(month, 'ngoai-so', **extra) + '#ngoai-so', status_code=303)


@router.post('/bao-cao/lai-rong/ngoai-so')
def save_adjustment(request: Request, form=Depends(checked_form), user: User = Depends(report_user), session: Session = Depends(get_session)):
    month = svc.month_of(str(form.get('thang', '')))
    try:
        ident = int(form['id']) if str(form.get('id', '')).strip() else None
        amount = parse_money(form.get('amount', ''), 'Số tiền', allow_zero=False)
        svc.save_adjustment(session, user, month, form.get('name', ''), amount, form.get('pl_group', ''), form.get('note', ''),
                            ident=ident, version=_version(form) if ident else None)
    except BusinessError as exc:
        return _back(month, exc.message)
    return _back(month)


@router.post('/bao-cao/lai-rong/ngoai-so/{ident}/xoa')
def delete_adjustment(ident: int, request: Request, form=Depends(checked_form), user: User = Depends(report_user), session: Session = Depends(get_session)):
    month = svc.month_of(str(form.get('thang', '')))
    try:
        svc.delete_adjustment(session, user, ident, _version(form))
    except BusinessError as exc:
        return _back(month, exc.message)
    return _back(month)


@router.post('/bao-cao/lai-rong/ngoai-so/chep')
def copy_adjustments(request: Request, form=Depends(checked_form), user: User = Depends(report_user), session: Session = Depends(get_session)):
    month = svc.month_of(str(form.get('thang', '')))
    try:
        svc.copy_adjustments(session, user, month)
    except BusinessError as exc:
        return _back(month, exc.message)
    return _back(month)


@router.post('/bao-cao/lai-rong/luong-giam-doc')
def director_salary(request: Request, form=Depends(checked_form), user: User = Depends(report_user), session: Session = Depends(get_session)):
    month = svc.month_of(str(form.get('thang', '')))
    try:
        svc.set_director_salary(session, user, parse_money(form.get('amount', ''), 'Lương giám đốc', allow_zero=True), _version(form))
    except BusinessError as exc:
        return _back(month, exc.message)
    return _back(month)
