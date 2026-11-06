"""Báo thu theo khách (TUẤN 06/11/2026): một số tiền tổng chia vào chứng từ khách còn chưa trả, cũ nhất trước.
Mỗi phần là một báo thu chờ xác nhận như báo thu trên từng đơn; dư so với tổng nợ thì chặn, không ghi ứng trước."""
import pytest
from sqlalchemy import func, select

from app import finance_reports, sales_orders, sales_receipts
from app.errors import BusinessError
from app.models import SalesReceipt
from app.schemas import CustomerReceiptReport, SalesOrderIn
from test_cong_no_khach import setup
from test_stock_reservations import people, purchase_people
from test_web import csrf_of, service_login

CODE = 'KTD-000001'
URL = '/cong-no-khach-hang/bao-thu/' + CODE


def more(db, p, amount):
    row = sales_orders.create(db, p['sales'], SalesOrderIn(customer_code=CODE, payment_term='TRA_NGAY',
                              lines=[dict(kind='KHAC', description='Công sửa', qty=1, unit_price=amount, vat_rate=0)]))
    row = sales_orders.confirm(db, p['sales'], row.id, row.version)
    sales_orders.deliver(db, p['warehouse'], row.id, row.version)
    db.refresh(row)
    return row


def receipts(db):
    return db.scalar(select(func.count()).select_from(SalesReceipt))


def body(amount, **kw):
    return CustomerReceiptReport(method=kw.pop('method', 'TIEN_MAT'), amount=amount, **kw)


def test_splits_oldest_first_one_pending_receipt_per_doc(db, people, configured_finance):
    p = people
    o1, _ = setup(db, p)  # KTD-000001: o1 phải thu 100, đã thu 30, chờ 20 → khách còn chưa trả 50
    o3 = more(db, p, 40)
    before = finance_reports.debt_totals(db, p['finance'])
    docs = sales_receipts.customer_unpaid_docs(db, CODE)
    assert [(d['id'], d['unpaid']) for d in docs] == [(o1.id, 50), (o3.id, 40)]
    rows = sales_receipts.report_for_customer(db, p['director'], CODE, body(70, method='CHUYEN_KHOAN', note='Khách đưa'))
    assert [(r.order_id, r.amount, r.status, r.method) for r in rows] == [(o1.id, 50, 'CHO_XAC_NHAN', 'CHUYEN_KHOAN'),
                                                                          (o3.id, 20, 'CHO_XAC_NHAN', 'CHUYEN_KHOAN')]
    assert all(r.note.startswith('Khách đưa · Báo thu theo khách: tổng 70 đ chia 2 chứng từ') for r in rows)
    after = finance_reports.debt_totals(db, p['finance'])
    # Chưa xác nhận thì sổ nợ không đổi, chỉ tăng phần chờ xác nhận đúng 70.
    assert after['remaining'] == before['remaining'] and after['pending'] == before['pending'] + 70
    assert [(d['id'], d['unpaid']) for d in sales_receipts.customer_unpaid_docs(db, CODE)] == [(o3.id, 20)]
    # Kế toán xác nhận từng báo thu như cũ.
    for r in rows:
        db.refresh(r)
        sales_receipts.confirm(db, p['finance'], r.id, r.version)
    assert finance_reports.debt_totals(db, p['finance'])['remaining'] == before['remaining'] - 70


def test_exact_total_clears_customer_and_overpay_is_blocked_without_writes(db, people, configured_finance):
    p = people
    setup(db, p)
    more(db, p, 40)
    n = receipts(db)
    with pytest.raises(BusinessError) as e:
        sales_receipts.report_for_customer(db, p['director'], CODE, body(91))
    assert e.value.code == 'OVER_PAYMENT' and 'dư 1 đ so với tổng nợ 90 đ' in e.value.message and receipts(db) == n
    with pytest.raises(BusinessError) as e:
        sales_receipts.report_for_customer(db, p['director'], CODE, body(10, expected_total=89))
    assert e.value.code == 'DEBT_CHANGED' and receipts(db) == n
    rows = sales_receipts.report_for_customer(db, p['director'], CODE, body(90, expected_total=90))
    assert sum(r.amount for r in rows) == 90 and sales_receipts.customer_unpaid_docs(db, CODE) == []
    with pytest.raises(BusinessError) as e:
        sales_receipts.report_for_customer(db, p['director'], CODE, body(1))
    assert e.value.code == 'NOTHING_DUE'


def test_doc_permissions_unchanged(db, people, configured_finance):
    p = people
    setup(db, p)
    n = receipts(db)
    with pytest.raises(BusinessError) as e:  # kế toán không có quyền kinh doanh thì không báo thu đơn bán được, như cũ
        sales_receipts.report_for_customer(db, p['finance'], CODE, body(10))
    assert e.value.status == 403 and receipts(db) == n


def test_web_form_previews_and_reports(client, db, people, configured_finance):
    p = people
    o1, _ = setup(db, p)
    o3 = more(db, p, 40)
    service_login(client, p['director'])
    page = client.get(URL).text
    assert 'Báo thu theo khách' in page and 'data-customer-pay-form' in page and 'name="expected_total" value="90"' in page
    assert page.index(f'href="/don-ban/{o1.id}#thu-tien"') < page.index(f'href="/don-ban/{o3.id}#thu-tien"')
    over = client.post(URL, data=dict(csrf=csrf_of(page), amount='1.000', method='TIEN_MAT', received_at='', note='', expected_total='90'))
    assert over.status_code == 409 and 'Khách đưa dư 910 đ so với tổng nợ 90 đ' in over.text and 'data-error-code="OVER_PAYMENT"' in over.text
    page = client.get(URL).text
    r = client.post(URL, data=dict(csrf=csrf_of(page), amount='60', method='TIEN_MAT', received_at='', note='', expected_total='90'),
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].endswith('?da_bao=2&tong=60')
    done = client.get(r.headers['location']).text
    assert 'data-pay-done' in done and 'name="expected_total" value="30"' in done
    for role in ('sales', 'technician'):
        service_login(client, p[role])
        assert client.get(URL).status_code == 403
