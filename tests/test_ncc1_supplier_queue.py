"""NCC1: a read-only queue backed by the existing supplier debt balance."""
from datetime import datetime, time, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import event

from app import my_work, purchase_receipts, supplier_debts
from app.config import Settings, get_settings
from app.models import AuditLog, CashDoc, FundLedger, StockDoc, StockLedger, Supplier
from test_purchase_orders import purchase_people, DAY
from test_purchase_returns import returned
from test_supplier_debts import m3_people, received, pay
from test_web import service_login

KEY = 'supplier_debts_due'


@pytest.fixture
def people(db, m3_people, monkeypatch):
    monkeypatch.setattr(my_work, '_now', lambda: datetime.combine(DAY, time(10), my_work.service_sla.TZ))
    monkeypatch.setattr(get_settings(), 'supplier_debt_alert_days', 30)
    return m3_people


def group(db, p, role='finance', page=1):
    return my_work.filtered(db, p[role], KEY, page)


def supplier(db, code, name=None):
    db.add(Supplier(code=code, name=name or code))
    db.commit()
    return code


def opening(db, p, code, days, amount=100000):
    supplier_debts.set_opening(db, p['director'], code, amount, DAY - timedelta(days=days), 'Nợ mẫu', 0)


@pytest.mark.parametrize('role', ['finance', 'director'])
def test_remaining_debt_card_links_straight_to_payment(client, db, people, role):
    p = people
    received(db, p)
    pay(db, p, 400000)
    g = group(db, p, role)
    assert g['title'] == 'Nợ NCC cần trả' and g['list_link'] == '/cong-no-ncc'
    assert g['count'] == 1
    assert g['rows'] == [dict(id=1, number='NCC-0001', status='CON_NO', customer='Minh Phát',
        amount=600000, overdue=False, oldest_debt_date=DAY, link='/cong-no-ncc/NCC-0001#tra-tien', kind='supplier_debt')]
    csrf = service_login(client, p[role])
    assert csrf
    r = client.get('/viec-cua-toi')
    assert r.status_code == 200
    assert 'data-work-group="supplier_debts_due"' in r.text
    assert '600.000 đồng' in r.text and 'Còn nợ' in r.text and 'Trả tiền ›' in r.text
    assert 'href="/cong-no-ncc/NCC-0001#tra-tien"' in r.text
    assert 'NCC còn nợ, cũ nhất lên đầu' in r.text
    target = client.get(g['rows'][0]['link'])
    assert target.status_code == 200 and 'id="tra-tien"' in target.text


@pytest.mark.parametrize('scenario', ['paid', 'voided', 'credit'])
def test_zero_or_negative_balance_and_voided_receipts_are_absent(db, people, scenario):
    p = people
    _, receipt = received(db, p)
    if scenario == 'paid':
        pay(db, p, 1000000)
    else:
        if scenario == 'credit':
            pay(db, p, 400000)
        purchase_receipts.void(db, p['warehouse'], receipt.id, 'Hủy nhận hàng', receipt.version)
    assert supplier_debts.balance(db, p['finance'], 'NCC-0001')['remaining'] <= 0
    assert group(db, p)['count'] == 0 and group(db, p)['rows'] == []


def test_purchase_return_reduces_exactly_the_existing_balance(db, people):
    p = people
    order, receipt = received(db, p)
    returned(db, p, order, receipt, qty='0.25')
    expected = supplier_debts.balance(db, p['finance'], 'NCC-0001')
    assert expected['remaining'] == 750000
    assert group(db, p)['rows'][0]['amount'] == expected['remaining']


def test_receipt_dates_oldest_posted_only_threshold_and_code_tie_break(db, people, monkeypatch):
    p = people
    _, old = received(db, p)
    old.receipt_date = DAY - timedelta(days=40)
    db.commit()
    code = supplier(db, 'NCC-0002')
    _, recent = received(db, p, code)
    recent.receipt_date = DAY - timedelta(days=5)
    db.commit()
    code3 = supplier(db, 'NCC-0003')
    opening(db, p, code3, 40)
    rows = group(db, p)['rows']
    assert [(r['number'], r['overdue']) for r in rows] == [
        ('NCC-0001', True), ('NCC-0003', True), ('NCC-0002', False)]
    assert [r['id'] for r in rows] == [1, 2, 3]
    monkeypatch.setattr(get_settings(), 'supplier_debt_alert_days', 60)
    assert not any(r['overdue'] for r in group(db, p)['rows'])
    # A voided old receipt must not determine the supplier's age.
    purchase_receipts.void(db, p['warehouse'], old.id, 'Hủy nhận cũ', old.version)
    _, newer = received(db, p)
    newer.receipt_date = DAY - timedelta(days=2)
    db.commit()
    monkeypatch.setattr(get_settings(), 'supplier_debt_alert_days', 30)
    assert [(r['number'], r['overdue']) for r in group(db, p)['rows']] == [
        ('NCC-0003', True), ('NCC-0002', False), ('NCC-0001', False)]


def test_unpaid_opening_date_precedes_receipt_date(db, people):
    p = people
    opening(db, p, 'NCC-0001', 120)
    assert group(db, p)['rows'][0]['overdue'] is True
    _, recent = received(db, p)
    recent.receipt_date = DAY - timedelta(days=5)
    db.commit()
    assert group(db, p)['rows'][0]['overdue'] is True
    assert group(db, p)['rows'][0]['oldest_debt_date'] == DAY - timedelta(days=120)
    assert group(db, p)['rows'][0]['amount'] == 1100000


@pytest.mark.parametrize('days,overdue', [(30, False), (31, True)])
def test_threshold_is_strictly_older(db, people, days, overdue):
    opening(db, people, 'NCC-0001', days)
    assert group(db, people)['rows'][0]['overdue'] is overdue


@pytest.mark.parametrize('role', ['warehouse', 'sales', 'technician'])
def test_unauthorized_roles_cannot_see_or_select_group(client, db, people, role):
    p = people
    received(db, p)
    assert KEY not in {g['key'] for g in my_work.collect(db, p[role])}
    service_login(client, p[role])
    page = client.get('/viec-cua-toi')
    assert page.status_code == 200 and 'Nợ NCC cần trả' not in page.text
    assert client.get('/viec-cua-toi?nhom=' + KEY).status_code == 403
    assert client.get('/cong-no-ncc').status_code == 403


def test_twenty_six_suppliers_count_and_page_two_has_one(client, db, people):
    p = people
    for i in range(1, 27):
        code = 'NCC-' + str(i).zfill(4)
        if i > 1:
            supplier(db, code)
        opening(db, p, code, 40)
    first = group(db, p)
    last = group(db, p, page=2)
    assert first['count'] == last['count'] == 26
    assert first['page_size'] == last['page_size'] == 25
    assert len(first['rows']) == 25 and len(last['rows']) == 1
    assert last['rows'][0]['id'] == 26 and last['rows'][0]['number'] == 'NCC-0026'
    assert {r['number'] for r in first['rows']}.isdisjoint(r['number'] for r in last['rows'])
    service_login(client, p['finance'])
    first_html = client.get('/viec-cua-toi?nhom=' + KEY)
    assert first_html.status_code == 200 and first_html.text.count('class="work-item') == 25
    assert 'Trang sau' in first_html.text
    last_html = client.get('/viec-cua-toi?nhom=' + KEY + '&trang=2')
    assert last_html.status_code == 200 and last_html.text.count('class="work-item') == 1
    assert 'NCC-0026' in last_html.text and 'Trang trước' in last_html.text and 'Trang sau' not in last_html.text


def test_queue_queries_are_read_only_with_outstanding_debt(db, people):
    p = people
    received(db, p)
    before = [db.query(m).count() for m in (AuditLog, CashDoc, FundLedger, StockDoc, StockLedger)]
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    db.get(Supplier, 'NCC-0001').name = 'Unsaved'
    event.listen(db.get_bind(), 'before_cursor_execute', capture)
    try:
        assert group(db, p)['count'] == 1
        my_work.collect(db, p['finance'])
    finally:
        event.remove(db.get_bind(), 'before_cursor_execute', capture)
    assert statements and all(s.lstrip().upper().startswith('SELECT') for s in statements)
    db.rollback()
    assert db.get(Supplier, 'NCC-0001').name == 'Minh Phát'
    assert [db.query(m).count() for m in (AuditLog, CashDoc, FundLedger, StockDoc, StockLedger)] == before


def test_alert_setting_defaults_and_bounds(monkeypatch):
    monkeypatch.delenv('TD_SUPPLIER_DEBT_ALERT_DAYS', raising=False)
    assert Settings(_env_file=None).supplier_debt_alert_days == 30
    for value in (0, 365):
        assert Settings(_env_file=None, supplier_debt_alert_days=value).supplier_debt_alert_days == value
    for value in (-1, 366):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, supplier_debt_alert_days=value)
