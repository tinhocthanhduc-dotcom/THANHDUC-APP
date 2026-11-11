"""Bán dưới giá vốn (TUẤN duyệt mẫu 11/11/2026): nhắc, bắt ghi lý do khi chốt, giám đốc xem lại."""
from datetime import date, datetime

import pytest
from sqlalchemy import select

from app import alerts, below_cost, morning_report, quotes, sales_orders as orders
from app.errors import BusinessError
from app.models import AuditLog, Customer
from app.schemas import QuoteIn, SalesCounterSale, SalesOrderIn
from app.web import steps
from test_sales_orders import people, stocked  # noqa: F401
from test_web import service_login

# TD-0001 ở K58: giá vốn 100.000/hộp (fixture stocked).
LOW = dict(kind='HANG', product_code='TD-0001', warehouse_code='K58', qty='2', unit_price=90000, vat_rate=10)
OK = dict(kind='HANG', product_code='TD-0001', warehouse_code='K58', qty='1', unit_price=150000, vat_rate=10)


def fail(code, fn, *args, **kw):
    with pytest.raises(BusinessError) as exc:
        fn(*args, **kw)
    assert exc.value.code == code
    return exc.value


def make(db, p, *lines, **kw):
    return orders.create(db, p['sales'], SalesOrderIn(customer_code='KTD-000001', lines=list(lines or [LOW]), **kw))


def test_margins_hide_nothing_from_director_and_flag_below(db, stocked):
    row = make(db, stocked, LOW, OK)
    s = below_cost.summary(db, row)
    assert [l['line_no'] for l in s['below']] == [1]
    assert s['lines'][1]['margin'] == -20000 and s['lines'][2]['margin'] == 50000
    assert s['margin'] == 30000 and s['net'] == 330000


def test_sales_must_give_reason_director_need_not(db, stocked):
    p = stocked
    row = make(db, p)
    exc = fail(below_cost.REASON_REQUIRED, orders.confirm, db, p['sales'], row.id, row.version)
    assert 'Dòng 1 (TD-0001)' in exc.message and 'giá vốn' in exc.message and '100.000' not in exc.message
    db.rollback()
    row = orders.load(db, p['sales'], row.id)
    assert row.status == 'NHAP'
    orders.confirm(db, p['sales'], row.id, row.version, below_cost_reason='Khách quen, kèm hợp đồng mực')
    log = db.scalar(select(AuditLog).where(AuditLog.action == below_cost.ACTION, AuditLog.entity_id == str(row.id)))
    assert log.reason == 'Khách quen, kèm hợp đồng mực' and log.after['loss'] == 20000 and log.after['number'] == row.number
    other = make(db, p)
    orders.confirm(db, p['director'], other.id, other.version)
    assert db.scalar(select(AuditLog).where(AuditLog.action == below_cost.ACTION, AuditLog.entity_id == str(other.id))).reason == ''


def test_above_cost_and_no_cost_lines_need_nothing(db, stocked):
    p = stocked
    row = make(db, p, OK, dict(kind='KHAC', description='Công lắp', qty='1', unit='lần', unit_price=1, vat_rate=8))
    orders.confirm(db, p['sales'], row.id, row.version)
    assert db.scalar(select(AuditLog).where(AuditLog.action == below_cost.ACTION)) is None


def test_discount_counts_when_comparing(db, stocked):
    p = stocked
    # 2 × 105.000 = 210.000 chưa VAT, giảm 20.000 sau VAT → dưới 200.000 vốn.
    row = make(db, p, dict(LOW, unit_price=105000), discount_amount=20000)
    assert below_cost.summary(db, row)['below']
    fail(below_cost.REASON_REQUIRED, orders.confirm, db, p['sales'], row.id, row.version)


def test_counter_sale_needs_reason_too(db, stocked):
    p = stocked
    row = make(db, p, fulfillment='TAI_QUAY', payment_term='TRA_NGAY')
    body = SalesCounterSale(version=row.version, method='TIEN_MAT', amount=row.total)
    fail(below_cost.REASON_REQUIRED, orders.counter_sale, db, p['sales'], row.id, body)
    db.rollback()
    row = orders.load(db, p['sales'], row.id)
    orders.counter_sale(db, p['sales'], row.id, body.model_copy(update={'version': row.version, 'below_cost_reason': 'Xả hàng cận date'}))
    assert orders.load(db, p['sales'], row.id).status == 'DA_GIAO'


def test_live_checks_hide_cost_from_sales_and_show_margin_to_director(db, stocked):
    p = stocked
    row = make(db, p, LOW, OK)
    data = orders.snapshot(row)
    stock = {('K58', 'TD-0001'): dict(available=5)}
    customer = db.get(Customer, 'KTD-000001')
    sales = {c['key']: c for c in steps.order_checks(db, p['sales'], data, stock, customer)}
    assert not sales['below-cost-1']['ok'] and sales['below-cost-1']['blocking'] and 'ghi lý do' in sales['below-cost-1']['label']
    assert 'margin' not in sales and '20.000' not in sales['below-cost-1']['label']
    data['below_cost_reason'] = 'Khách quen'
    assert {c['key']: c for c in steps.order_checks(db, p['sales'], data, stock, customer)}['below-cost-1']['ok']
    boss = {c['key']: c for c in steps.order_checks(db, p['director'], orders.snapshot(row), stock, customer)}
    assert boss['below-cost-1']['ok'] and 'lỗ 20.000' in boss['below-cost-1']['label']
    assert '+ 30.000' in boss['margin']['label'] and not boss['margin']['blocking']


def test_web_confirm_form_asks_reason_and_shows_margin_only_to_director(client, db, stocked):
    p = stocked
    row = make(db, p)
    service_login(client, p['sales'])
    html = client.get(f'/don-ban/{row.id}').text
    assert 'name="below_cost_reason"' in html and 'Dưới giá vốn' in html and 'Lãi gộp' not in html and '200.000' not in html
    service_login(client, p['director'])
    html = client.get(f'/don-ban/{row.id}').text
    assert 'Lãi gộp' in html and 'lỗ 20.000' in html


def test_quote_view_warns(client, db, stocked):
    p = stocked
    q = quotes.create(db, p['sales'], QuoteIn(customer_code='KTD-000001', lines=[dict(product_code='TD-0001', description='Mực', qty='1', unit='hộp', unit_price=80000, vat_rate=10)]))
    assert below_cost.summary(db, q)['below'][0]['margin'] == -20000
    service_login(client, p['sales'])
    html = client.get(f'/bao-gia/{q.id}').text
    assert 'Dưới giá vốn' in html and 'Lãi gộp' not in html


def test_director_sees_it_in_alerts_and_morning_report(db, stocked, monkeypatch):
    p = stocked
    row = make(db, p)
    orders.confirm(db, p['sales'], row.id, row.version, below_cost_reason='Khách quen')
    today = datetime.now(below_cost.TZ).date()
    rows = below_cost.recent(db, today, today)
    assert rows[0]['number'] == row.number and rows[0]['loss'] == 20000 and rows[0]['reason'] == 'Khách quen'
    found = alerts.collect(db, p['director'], today=today)
    assert found.counts['DUOI_GIA_VON'] == 1 and any(a['kind'] == 'DUOI_GIA_VON' and a['link'] == f'/don-ban/{row.id}' for a in found)
    assert 'DUOI_GIA_VON' not in alerts.collect(db, p['finance'], today=today).counts
    d = dict(morning_report.build(db, p['director'], today))
    d['below_cost'] = rows
    text = morning_report.render_text(d)
    assert 'ĐƠN DƯỚI GIÁ VỐN: 1 đơn' in text and row.number in text and 'Khách quen' in text
