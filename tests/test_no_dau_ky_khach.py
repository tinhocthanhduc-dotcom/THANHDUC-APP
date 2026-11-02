"""Nợ đầu kỳ khách hàng (TUẤN 02/11/2026): nợ cũ trước khi dùng app, Báo thu trừ dần như đơn bán."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import alerts, customer_opening, finance_reports, my_work, sales_orders, sales_receipts
from app.errors import BusinessError
from app.models import CashDoc, Customer, SalesReceipt
from app.schemas import SalesReceiptReport
from app.web.lists import _today
from test_web import csrf_of, service_login

CODE = 'KTD-000009'
URL = '/cong-no-khach-hang/dau-ky/' + CODE


@pytest.fixture
def p(db, make_user, configured_finance):
    db.add(Customer(code=CODE, name='Công ty Nợ Cũ', search_text='ktd-000009 cong ty no cu'))
    db.commit()
    people = {r: make_user('dk.' + r, r) for r in ('director', 'finance', 'sales')}
    people['thanh'] = make_user('dk.thanh', 'sales', 'finance')
    return people


def fail(code, fn, *args, **kw):
    with pytest.raises(BusinessError) as exc:
        fn(*args, **kw)
    assert exc.value.code == code
    return exc.value


def report(db, user, row, amount, method='TIEN_MAT'):
    db.refresh(row)
    return sales_receipts.report_for_opening(db, user, row.id, SalesReceiptReport(version=row.version, method=method, amount=amount))


def test_opening_debt_counts_everywhere_and_receipts_reduce_it(db, p):
    day = _today() - timedelta(days=40)
    fail('FORBIDDEN', customer_opening.set_opening, db, p['finance'], CODE, 1_000_000, day)
    row = customer_opening.set_opening(db, p['director'], CODE, 1_000_000, day, 'Sổ Finance cũ')
    assert row.number == 'NDK-' + CODE and row.amount == 1_000_000
    totals = finance_reports.debt_totals(db, p['director'])
    assert totals['remaining'] == 1_000_000
    assert sales_orders.credit_exposure(db, [CODE])[CODE]['actual_debt'] == 1_000_000
    found = alerts.collect(db, p['director'], today=_today())
    kinds = {a['kind'] for a in (found['items'] if isinstance(found, dict) else found)}
    assert {'KHACH_NO_QUA_HAN', 'KHACH_VUOT_HAN_MUC'} <= kinds

    fail('FORBIDDEN', report, db, p['sales'], row, 1)  # chỉ kế toán / giám đốc báo thu nợ cũ
    fail('OVER_PAYMENT', report, db, p['thanh'], row, 1_000_001)
    r1 = report(db, p['thanh'], row, 400_000)
    assert finance_reports.debt_totals(db, p['director'])['pending'] == 400_000
    pending = {g['key']: g for g in my_work.collect(db, p['director'], _today())}['receipts_pending']
    assert [x['number'] for x in pending['rows']] == [r1.number]
    fail('SELF_CONFIRM', sales_receipts.confirm, db, p['thanh'], r1.id, r1.version)
    fail('BELOW_COLLECTED', customer_opening.set_opening, db, p['director'], CODE, 399_999, day, '', row.version)
    sales_receipts.confirm(db, p['director'], r1.id, r1.version)
    db.refresh(r1)
    doc = db.get(CashDoc, r1.cash_doc_id)
    assert doc.kind == 'PT' and doc.amount == 400_000 and doc.customer_code == CODE and doc.status == 'DA_GHI'
    db.refresh(row)
    assert sales_receipts.balance(row) == dict(receivable=1_000_000, paid=400_000, pending=0, remaining=600_000)
    assert finance_reports.debt_totals(db, p['director'])['remaining'] == 600_000
    assert finance_reports.last_receipts(db, p['director'])[CODE][1] == 400_000

    # Hủy báo thu đã xác nhận: đảo phiếu thu, nợ quay lại.
    sales_receipts.void(db, p['director'], r1.id, 'Nhập nhầm', r1.version)
    assert finance_reports.debt_totals(db, p['director'])['remaining'] == 1_000_000
    fail('HAS_RECEIPTS', customer_opening.set_opening, db, p['director'], CODE, 0, day, '', row.version)

    # Thu đủ thì khách hết nợ.
    r2 = report(db, p['thanh'], row, 1_000_000, 'CHUYEN_KHOAN')
    sales_receipts.confirm(db, p['director'], r2.id, r2.version)
    assert finance_reports.debt_totals(db, p['director'])['remaining'] == 0


def test_cash_group_and_delete_without_receipts(db, p):
    row = customer_opening.set_opening(db, p['director'], CODE, 300_000, _today())
    fail('VERSION_CONFLICT', customer_opening.set_opening, db, p['director'], CODE, 200_000, _today(), '', 0)
    a, b = report(db, p['thanh'], row, 100_000), report(db, p['thanh'], row, 50_000)
    sales_receipts.confirm_cash_group(db, p['director'], [(a.id, a.version), (b.id, b.version)], 150_000, p['thanh'].id)
    assert finance_reports.debt_totals(db, p['director'])['remaining'] == 150_000

    other = Customer(code='KTD-000010', name='Khách khác', search_text='khach khac')
    db.add(other); db.commit()
    gone = customer_opening.set_opening(db, p['director'], other.code, 50_000, _today())
    assert customer_opening.set_opening(db, p['director'], other.code, 0, _today(), '', gone.version) is None
    assert customer_opening.get(db, other.code) is None
    fail('BAD_DATE', customer_opening.set_opening, db, p['director'], other.code, 1, _today() + timedelta(days=1))


def test_web_director_enters_finance_reports_director_confirms(client, db, p):
    service_login(client, p['sales'])
    assert client.get(URL).status_code == 403
    csrf = service_login(client, p['director'])
    page = client.get(URL).text
    assert 'Nhập nợ đầu kỳ' in page and 'Lưu nợ đầu kỳ' in page
    assert CODE in client.get('/cong-no-khach-hang/tim?q=no cu').text
    assert 'Nhập nợ đầu kỳ khách' in client.get('/cong-no-khach-hang').text
    r = client.post(URL, data=dict(csrf=csrf_of(page), amount='2.500.000', as_of=_today().isoformat(), note='Sổ cũ', version='0'))
    assert r.status_code == 200 and 'Đã lưu' in r.text and 'NDK-' + CODE in r.text
    listing = client.get('/cong-no-khach-hang').text
    assert f'href="{URL}#thu-tien"' in listing and 'nợ đầu kỳ' in listing
    assert f'href="{URL}"' in client.get('/khach-hang/' + CODE).text

    service_login(client, p['thanh'])
    page = client.get(URL).text
    assert 'Lưu nợ đầu kỳ' not in page and 'id="receipt-report"' in page
    row = customer_opening.get(db, CODE); db.refresh(row)
    r = client.post(URL + '/bao-thu', data=dict(csrf=csrf_of(page), version=str(row.version), method='TIEN_MAT', amount='1.000.000',
                                                received_at=_today().isoformat(), note=''))
    assert r.status_code == 200 and 'Không được tự xác nhận' in r.text
    receipt = db.scalar(select(SalesReceipt).where(SalesReceipt.opening_id == row.id))
    queue = client.get('/bao-thu').text
    assert f'href="{URL}#thu-tien"' in queue

    service_login(client, p['director'])
    page = client.get(URL).text
    r = client.post(f'/bao-thu/{receipt.id}/confirm', data=dict(csrf=csrf_of(page), version=str(receipt.version), origin='opening'))
    assert r.status_code == 200 and r.url.path == URL
    db.expire_all()
    assert db.get(SalesReceipt, receipt.id).status == 'DA_XAC_NHAN'
    assert finance_reports.debt_totals(db, p['director'])['remaining'] == 1_500_000
    report_page = client.get('/bao-cao/tai-chinh?khach=' + CODE).text
    assert 'Nợ đầu kỳ' in report_page


def test_import_script_dry_run_then_apply(db, p):
    from scripts import import_customer_opening as imp
    rows = [dict(MA_KHACH=CODE, SO_NO='1.200.000', NGAY='31/10/2026', GHI_CHU='Finance'),
            dict(MA_KHACH='KTD-999999', SO_NO='5', NGAY='', GHI_CHU=''),
            dict(MA_KHACH=CODE, SO_NO='abc', NGAY='', GHI_CHU='')]
    dry = imp.run(db, p['director'], rows, None, apply=False)
    assert [r[2] for r in dry] == ['TAO_MOI', 'LOI', 'LOI'] and customer_opening.get(db, CODE) is None
    imp.run(db, p['director'], rows[:1], None, apply=True)
    assert customer_opening.get(db, CODE).amount == 1_200_000
    assert [r[2] for r in imp.run(db, p['director'], rows[:1], None)] == ['DA_CO']
