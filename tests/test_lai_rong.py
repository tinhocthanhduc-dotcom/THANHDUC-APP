"""Lãi ròng tháng (đợt 4, TUẤN duyệt 08/11/2026): tổng khớp lãi gộp, cờ danh mục chi, lương từ bảng lương, chi phí ngoài sổ, chỉ giám đốc."""
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app import cash_docs, cash_posting, employees, finance_catalogs, finance_mappings, profit_report as svc, purchase_reports
from app.errors import BusinessError
from app.models import CashCategory, PayrollLine, PayrollMonth, PlAdjustment, PlSetting
from app.schemas import CashCategoryIn, CashCategoryPatch, CashDocIn
from test_purchase_orders import purchase_people  # noqa: F401
from test_purchase_reports import DAY, clock, completed, received, report_people, sold  # noqa: F401
from test_supplier_debts import m3_people  # noqa: F401
from test_tk_tai_khoan import web_login

MONTH = date(2026, 10, 1)


def category(db, p, code, pl_kind='CHI_PHI', group=''):
    return finance_catalogs.create(db, p['finance'], CashCategory, CashCategoryIn(code=code, name=code, direction='CHI', pl_kind=pl_kind, pl_group=group))


def spend(db, p, code, amount, day=DAY):
    doc = cash_docs.create(db, p['director'], CashDocIn(kind='PC', doc_date=day, fund_code='QUY-TM', category_code=code, amount=amount, description='Chi'))
    cash_posting.post(db, p['director'], doc.id, doc.version)
    return doc


def frozen(monkeypatch):
    clock(monkeypatch, datetime(2026, 10, 18, 10, 0, tzinfo=svc.TZ))
    monkeypatch.setattr(svc, '_today', lambda: DAY)


def test_summary_matches_gross_margin_and_applies_category_flags(db, report_people, monkeypatch):
    p = report_people
    frozen(monkeypatch)
    received(db, p, qty=5, cost=100000)
    sold(db, p)  # doanh thu 250.000, vốn 100.000
    completed(db, p)  # vật tư 300.000 + công 200.000, vốn 100.000
    category(db, p, 'CHI-DIEN', 'CHI_PHI', 'Mặt bằng, điện nước')
    category(db, p, 'CHI-LUONG', 'KHONG_TINH')
    category(db, p, 'CHI-RUT', 'CHU_SO_HUU')
    spend(db, p, 'CHI-DIEN', 300000)
    spend(db, p, 'CHI-LUONG', 2000000)  # không tính: lương lấy từ bảng lương
    spend(db, p, 'CHI-MH', 700000)  # danh mục ghép Mua hàng mặc định Chi phí trong test (chưa qua migration dữ liệu) -> tính
    spend(db, p, 'CHI-RUT', 150000)
    svc.save_adjustment(db, p['director'], MONTH, 'Mặt bằng', 500000, 'Mặt bằng')
    s = svc.summary(db, p['director'], MONTH)
    gross = purchase_reports.gross_margin(db, p['director'], MONTH, date(2026, 10, 31))['totals']
    assert (s['revenue'], s['cost'], s['gross']) == (gross['revenue'], gross['cost'], gross['profit']) == (750000, 200000, 550000)
    assert s['deliveries'] == 250000 and s['services'] == 500000 and s['returns'] == 0
    assert s['payroll'] == dict(amount=0, status='CHUA_CO', source=None, people=0)
    assert s['opex']['amount'] == 1000000 and {r['code'] for r in s['opex']['rows']} == {'CHI-DIEN', 'CHI-MH'}
    assert s['owner']['amount'] == 150000 and s['adjustments']['amount'] == 500000 and s['damage'] == 0
    assert s['before_director'] == 550000 - 1000000 - 500000 == -950000
    assert s['director_salary'] == 45000000 and s['remaining'] == -45950000 and s['gap'] == 45950000
    assert s['cash']['expense'] == 3150000 and s['cash']['owner'] == 150000 and s['cash']['adjustments'] == 500000
    # Đổi cờ danh mục Mua hàng sang Không tính thì chi phí hoạt động giảm đúng 700.000.
    row = db.get(CashCategory, 'CHI-MH')
    finance_catalogs.update(db, p['finance'], CashCategory, 'CHI-MH', CashCategoryPatch(version=row.version, pl_kind='KHONG_TINH'))
    assert svc.summary(db, p['director'], MONTH)['opex']['amount'] == 300000
    # Tháng trước không có gì.
    assert s['previous']['revenue'] == 0 and s['previous']['before_director'] == 0


def test_by_group_and_by_customer_sum_to_summary(db, report_people, monkeypatch):
    p = report_people
    frozen(monkeypatch)
    received(db, p, qty=5, cost=100000)
    sold(db, p)
    completed(db, p)
    s = svc.summary(db, p['director'], MONTH)
    g = svc.by_group(db, p['director'], MONTH)
    assert g['totals']['profit'] == s['gross'] == 550000
    assert sum(r['revenue'] for r in g['rows']) == g['totals']['revenue'] and sum(r['cost'] for r in g['rows']) == g['totals']['cost']
    fee = next(r for r in g['rows'] if r['group'] == svc.SERVICE_FEE_GROUP)
    assert (fee['revenue'], fee['cost']) == (200000, 0)
    goods = next(r for r in g['rows'] if r['group'] == 'Chưa xếp nhóm')
    assert (goods['revenue'], goods['cost'], goods['products']) == (550000, 200000, 1)
    c = svc.by_customer(db, p['director'], MONTH)
    assert c['count'] == 1 and c['top'][0]['profit'] == 550000 and c['top'][0]['documents'] == 2 and not c['losses']
    assert c['totals']['profit'] == s['gross']
    months = svc.twelve_months(db, p['director'], MONTH)
    assert len(months['rows']) == 12 and months['rows'][-1]['month'] == MONTH and months['rows'][-1]['gross'] == 550000
    assert months['rows'][0]['month'] == date(2025, 11, 1) and months['rows'][-1]['remaining'] == 550000 - 45000000


def test_payroll_from_closed_month_else_estimate(db, report_people, monkeypatch):
    p = report_people
    frozen(monkeypatch)
    e = employees.create(db, p['director'], p['technician'].id, dict(position='Khác', start_date='2026-01-01'))
    sv = employees.add_salary(db, p['director'], e.id, dict(effective_from='2026-01-01', base_salary=6000000, meal_allowance=0, fuel_allowance=0,
                                                              phone_allowance=0, other_allowance=0, standard_days=26, reason='Lương gốc'))
    sep = date(2026, 9, 1)
    db.add(PayrollMonth(month=sep, approved_by=p['director'].id, approved_at=datetime(2026, 10, 2, tzinfo=svc.TZ), total_net=6500000))
    db.add(PayrollLine(month=sep, employee_id=e.id, user_id=p['technician'].id, salary_version_id=sv.id, base_salary=6000000, standard_days=Decimal(26),
                       cong=Decimal(26), base_pay=6000000, allowances=0, commission=500000, bonus=0, advance=0, social_insurance=0, income_tax=0,
                       other_deduction=0, gross=6500000, net=6500000))
    db.commit()
    assert svc.payroll_cost(db, sep) == dict(amount=6500000, status='DA_CHOT', source=sep, people=1)
    estimate = svc.payroll_cost(db, MONTH)
    assert estimate == dict(amount=6500000, status='UOC', source=sep, people=1)
    assert svc.payroll_cost(db, date(2026, 8, 1)) == dict(amount=0, status='CHUA_CO', source=None, people=0)
    s = svc.summary(db, p['director'], MONTH)
    assert s['payroll']['status'] == 'UOC' and s['before_director'] == -6500000


def test_adjustments_crud_copy_and_director_salary(db, report_people):
    p = report_people
    d = p['director']
    row = svc.save_adjustment(db, d, MONTH, 'Mặt bằng', 7000000, 'Mặt bằng', 'Trả 6 tháng')
    svc.save_adjustment(db, d, MONTH, 'Khấu hao xe', 1500000, 'Khấu hao')
    assert svc.adjustments(db, MONTH)['amount'] == 8500000
    svc.save_adjustment(db, d, MONTH, 'Mặt bằng mới', 6000000, 'Mặt bằng', ident=row.id, version=row.version)
    db.expire_all()
    assert db.get(PlAdjustment, row.id).amount == 6000000 and db.get(PlAdjustment, row.id).version == 2
    with pytest.raises(BusinessError) as exc:
        svc.save_adjustment(db, d, MONTH, 'Sai', 1000, ident=row.id, version=1)
    assert exc.value.code == 'VERSION_CONFLICT'
    for bad, code in ((dict(name='', amount=1000), 'NAME_REQUIRED'), (dict(name='X', amount=0), 'BAD_AMOUNT')):
        with pytest.raises(BusinessError) as exc:
            svc.save_adjustment(db, d, MONTH, bad['name'], bad['amount'])
        assert exc.value.code == code
    # Chép sang tháng 11: phải trống mới chép; chép xong 2 khoản.
    nov = date(2026, 11, 1)
    copies = svc.copy_adjustments(db, d, nov)
    assert len(copies) == 2 and svc.adjustments(db, nov)['amount'] == 7500000
    with pytest.raises(BusinessError) as exc:
        svc.copy_adjustments(db, d, nov)
    assert exc.value.code == 'DA_CO'
    with pytest.raises(BusinessError) as exc:
        svc.copy_adjustments(db, d, date(2026, 5, 1))
    assert exc.value.code == 'KHONG_CO'
    svc.delete_adjustment(db, d, copies[0].id, copies[0].version)
    assert len(svc.adjustments(db, nov)['rows']) == 1
    # Lương giám đốc mục tiêu: mặc định 45 triệu, giám đốc sửa được, kế toán bị chặn.
    assert svc.director_salary(db) == 45000000
    svc.set_director_salary(db, d, 30000000, 1)
    assert svc.director_salary(db) == 30000000 and db.get(PlSetting, svc.DIRECTOR_SALARY_KEY).version == 2
    for fn, args in ((svc.summary, (MONTH,)), (svc.by_group, (MONTH,)), (svc.by_customer, (MONTH,)), (svc.twelve_months, (MONTH,)),
                     (svc.save_adjustment, (MONTH, 'X', 1000)), (svc.delete_adjustment, (1, 1)), (svc.copy_adjustments, (MONTH,)), (svc.set_director_salary, (1, 1))):
        with pytest.raises(BusinessError) as exc:
            fn(db, p['finance'], *args)
        assert exc.value.code == 'FORBIDDEN'


def test_web_page_tabs_forms_and_director_only(client, db, report_people, monkeypatch):
    p = report_people
    frozen(monkeypatch)
    received(db, p, qty=5, cost=100000)
    sold(db, p)
    category(db, p, 'CHI-DIEN', 'CHI_PHI', 'Mặt bằng, điện nước')
    spend(db, p, 'CHI-DIEN', 300000)
    web_login(client, p['finance'])
    assert client.get('/bao-cao/lai-rong?thang=2026-10').status_code == 403
    assert 'Lãi ròng' not in client.get('/bao-cao/tai-chinh').text
    csrf = web_login(client, p['director'])
    page = client.get('/bao-cao/lai-rong?thang=2026-10')
    assert page.status_code == 200, page.text
    html = page.text
    assert 'data-pl-table' in html and 'Tháng 10/2026' in html and '>150.000<' in html and 'data-payroll-status="CHUA_CO"' in html
    assert 'Chưa · thiếu 45.150.000' in html and 'Chi phí ngoài sổ: 0 khoản' in html and 'data-todo-box' in html and 'href="/bao-cao/lai-rong"' in html
    assert 'Lãi ròng' in client.get('/bao-cao/tai-chinh').text  # có trong menu Báo cáo của giám đốc
    assert 'data-group-table' in client.get('/bao-cao/lai-rong?thang=2026-10&tab=theo-loai').text
    assert 'data-customer-table' in client.get('/bao-cao/lai-rong?thang=2026-10&tab=theo-khach').text
    assert 'data-pl-bars' in client.get('/bao-cao/lai-rong?thang=2026-10&tab=12-thang').text
    assert 'Tháng chưa hợp lệ' in client.get('/bao-cao/lai-rong?thang=abc').text
    # Thêm khoản ngoài sổ qua form, sửa lương GĐ, chép, xóa.
    r = client.post('/bao-cao/lai-rong/ngoai-so', data=dict(csrf=csrf, thang='2026-10', name='Mặt bằng', pl_group='Mặt bằng', amount='7.000.000', note=''), follow_redirects=False)
    assert r.status_code == 303 and 'tab=ngoai-so' in r.headers['location'] and 'da_luu=1' in r.headers['location']
    html = client.get('/bao-cao/lai-rong?thang=2026-10&tab=ngoai-so').text
    assert 'data-adjust-table' in html and '7.000.000' in html and 'data-director-form' in html
    adj = db.scalar(select(PlAdjustment))
    r = client.post('/bao-cao/lai-rong/ngoai-so', data=dict(csrf=csrf, thang='2026-10', id=str(adj.id), version='1', name='Mặt bằng', pl_group='', amount='0'), follow_redirects=False)
    assert 'loi=' in r.headers['location']
    assert 'data-error-code' in client.get(r.headers['location']).text
    r = client.post('/bao-cao/lai-rong/luong-giam-doc', data=dict(csrf=csrf, thang='2026-10', version='1', amount='40.000.000'), follow_redirects=False)
    assert r.status_code == 303 and svc.director_salary(db) == 40000000
    summary = client.get('/bao-cao/lai-rong?thang=2026-10').text
    assert 'Còn lại sau lương GĐ 40 tr' in summary and 'Chưa · thiếu 47.150.000' in summary
    r = client.post('/bao-cao/lai-rong/ngoai-so/chep', data=dict(csrf=csrf, thang='2026-11'), follow_redirects=False)
    assert r.status_code == 303 and svc.adjustments(db, date(2026, 11, 1))['amount'] == 7000000
    r = client.post(f'/bao-cao/lai-rong/ngoai-so/{adj.id}/xoa', data=dict(csrf=csrf, thang='2026-10', version='1'), follow_redirects=False)
    assert r.status_code == 303 and 'da_luu=1' in r.headers['location'], r.headers['location']
    assert db.scalar(select(func.count()).select_from(PlAdjustment).where(PlAdjustment.month == MONTH)) == 0
    csv_text = client.get('/bao-cao/lai-rong/tong-hop.csv?thang=2026-10').text
    assert csv_text.startswith('﻿') and 'Lãi ròng trước lương giám đốc' in csv_text and 'CHI-DIEN' in csv_text
    # Kế toán không gửi được form.
    csrf = web_login(client, p['finance'])
    assert client.post('/bao-cao/lai-rong/ngoai-so', data=dict(csrf=csrf, thang='2026-10', name='X', amount='1.000'), follow_redirects=False).status_code == 403


def test_cash_category_form_shows_pl_flag_only_for_expense(client, db, report_people):
    p = report_people
    csrf = web_login(client, p['finance'])
    page = client.get('/danh-muc-thu-chi/moi').text
    assert 'name="pl_kind"' in page and 'name="pl_group"' in page
    r = client.post('/danh-muc-thu-chi/moi', data=dict(csrf=csrf, version='0', code='CHI-VC', name='Vận chuyển', direction='CHI', pl_kind='CHI_PHI',
                                                      pl_group='Vận chuyển', note='', active='1'), follow_redirects=False)
    assert r.status_code == 303, r.text
    row = db.get(CashCategory, 'CHI-VC')
    assert (row.pl_kind, row.pl_group) == ('CHI_PHI', 'Vận chuyển')
    r = client.post('/danh-muc-thu-chi/moi', data=dict(csrf=csrf, version='0', code='THU-KHAC', name='Thu khác', direction='THU', pl_kind='CHI_PHI', note='', active='1'), follow_redirects=False)
    assert r.status_code == 303 and db.get(CashCategory, 'THU-KHAC').pl_kind == 'KHONG_TINH'
    assert 'Lãi ròng: Chi phí (Vận chuyển)' in client.get('/danh-muc-thu-chi').text
