"""NCC2: FIFO aging shares the exact posted reductions used by supplier balances."""
from datetime import timedelta

import pytest
from sqlalchemy import event, select

from app import (my_work, pending_dispositions, purchase_orders, purchase_receipts,
                 purchase_returns, supplier_debts)
from app.models import AuditLog, CashDoc, FundLedger, StockDoc, StockLedger
from test_cxl1_pending_items import defect, manual
from test_ncc1_supplier_queue import people, group, opening, supplier
from test_purchase_orders import purchase_people, body, DAY
from test_supplier_debts import m3_people, pay
from test_purchase_returns import returned
from test_web import service_login


def receipt(db, p, days, amount, code='NCC-0001'):
    order = purchase_orders.create(db, p['warehouse'], body(supplier_code=code, lines=[
        dict(product_code='TD-0001', warehouse_code='K58', qty=1, unit_cost=amount, vat_rate=0)]))
    purchase_orders.order(db, p['warehouse'], order.id, order.version)
    row = purchase_receipts.receive(db, p['warehouse'], order.id, order.version, DAY,
        [dict(order_line_id=order.lines[0].id, qty=1)])[0]
    # Age the fixture after the valid receiving workflow.
    row.receipt_date = DAY - timedelta(days=days)
    db.commit()
    return order, row


@pytest.fixture
def two_receipts(db, people):
    p = people
    old_order, old = receipt(db, p, 60, 1000000)
    new_order, new = receipt(db, p, 5, 500000)
    return p, old_order, old, new_order, new


def assert_debt(db, p, amount, days, overdue):
    g = group(db, p)
    assert g['count'] == len(g['rows']) == 1
    row = g['rows'][0]
    assert row['amount'] == amount == supplier_debts.balance(db, p['finance'], 'NCC-0001')['remaining']
    assert row['oldest_debt_date'] == DAY - timedelta(days=days)
    assert row['overdue'] is overdue
    return row


@pytest.mark.parametrize('paid,amount,days,overdue', [
    (1000000, 500000, 5, False), (600000, 900000, 60, True)])
@pytest.mark.parametrize('role', ['finance', 'director'])
def test_old_receipt_settled_or_partially_paid_and_card_date(client, db, two_receipts, paid, amount, days, overdue, role):
    p, _, _, _, _ = two_receipts
    pay(db, p, paid)
    row = assert_debt(db, p, amount, days, overdue)
    service_login(client, p[role])
    page = client.get('/viec-cua-toi?nhom=supplier_debts_due')
    assert page.status_code == 200
    assert 'Nợ từ ' + row['oldest_debt_date'].strftime('%d/%m/%Y') in page.text
    assert ('class="work-item work-overdue"' in page.text) is overdue
    assert 'href="/cong-no-ncc/NCC-0001#tra-tien"' in page.text and 'Trả tiền ›' in page.text


def test_purchase_return_clears_old_debt_and_void_restores_its_age(db, two_receipts):
    p, order, old, _, _ = two_receipts
    row = returned(db, p, order, old)
    assert_debt(db, p, 500000, 5, False)
    purchase_returns.void(db, p['warehouse'], row.id, 'Hủy trả hàng mẫu', row.version)
    assert_debt(db, p, 1500000, 60, True)


def test_opening_remains_first_until_fully_paid(db, people):
    p = people
    opening(db, p, 'NCC-0001', 40, 1000000)
    assert_debt(db, p, 1000000, 40, True)
    receipt(db, p, 3, 500000)
    pay(db, p, 600000)
    assert_debt(db, p, 900000, 40, True)
    pay(db, p, 400000)
    assert_debt(db, p, 500000, 3, False)


def test_opening_priority_is_not_minimum_calendar_date(db, people):
    p = people
    opening(db, p, 'NCC-0001', 3, 100000)
    receipt(db, p, 60, 500000)
    assert_debt(db, p, 600000, 3, False)
    pay(db, p, 100000)
    assert_debt(db, p, 500000, 60, True)


def test_same_date_receipts_break_ties_by_id_and_strict_prefix_sum(db, people):
    p = people
    _, first = receipt(db, p, 5, 1000000)
    _, second = receipt(db, p, 5, 500000)
    assert first.id < second.id
    pay(db, p, 999999)
    def oldest_id():
        dates = my_work._supplier_debt_dates(supplier_debts._deductions().subquery())
        return db.scalar(select(dates.c.oldest_debt_id).where(dates.c.supplier_code == 'NCC-0001'))
    assert [oldest_id() for _ in range(3)] == [first.id] * 3
    pay(db, p, 1)
    assert [oldest_id() for _ in range(3)] == [second.id] * 3
    assert_debt(db, p, 500000, 5, False)


def test_voided_receipt_does_not_consume_the_fifo_prefix(db, two_receipts):
    p, _, old, _, _ = two_receipts
    purchase_receipts.void(db, p['warehouse'], old.id, 'Hủy nhận cũ', old.version)
    assert_debt(db, p, 500000, 5, False)


def test_zero_opening_and_zero_receipt_do_not_become_oldest_debt(db, people):
    p = people
    opening(db, p, 'NCC-0001', 120, 0)
    receipt(db, p, 90, 0)
    receipt(db, p, 5, 500000)
    assert_debt(db, p, 500000, 5, False)


def test_voided_payment_reopens_oldest_unpaid_charge(db, two_receipts):
    p, _, _, _, _ = two_receipts
    payment = pay(db, p, 1000000)
    assert_debt(db, p, 500000, 5, False)
    supplier_debts.void(db, p['finance'], payment.id, 'Nhầm lần trả', payment.version)
    assert_debt(db, p, 1500000, 60, True)


def pending_return(db, p, amount, qty='1'):
    defect(db)
    item = manual(db, p, qty)
    return pending_dispositions.create(db, p['warehouse'], item.id, 'TRA_NCC', item.qty_left, item.version,
        supplier_code='NCC-0001', amount=amount, vat_rate=0, reason='Trả NCC hàng lỗi giảm nợ')


def test_cxl_credit_only_counts_after_posting_and_void_restores_age(db, two_receipts):
    p, _, _, _, _ = two_receipts
    credit = pending_return(db, p, 1000000)
    assert credit.status == 'CHO_DUYET'
    assert_debt(db, p, 1500000, 60, True)
    pending_dispositions.approve(db, p['finance'], credit.id, credit.version)
    assert_debt(db, p, 500000, 5, False)
    pending_dispositions.void(db, p['finance'], credit.id, credit.version, 'Nhầm hồ sơ giảm nợ')
    assert_debt(db, p, 1500000, 60, True)


def test_all_three_reductions_share_balance_and_fifo_totals(db, two_receipts):
    p, order, old, _, _ = two_receipts
    pay(db, p, 200000)
    returned(db, p, order, old, qty='0.3')
    credit = pending_return(db, p, 500000, qty='0.5')
    pending_dispositions.approve(db, p['finance'], credit.id, credit.version)
    assert supplier_debts.balance(db, p['finance'], 'NCC-0001') == dict(
        opening=0, received=1500000, paid=200000, returned=800000, remaining=500000)
    deductions = supplier_debts._deductions().subquery()
    assert dict(db.execute(select(deductions).where(deductions.c.supplier_code == 'NCC-0001')).mappings().one()) == dict(
        supplier_code='NCC-0001', paid=200000, returned=800000, amount=1000000)
    assert_debt(db, p, 500000, 5, False)


def test_other_supplier_credits_never_clear_this_supplier_debt(db, two_receipts):
    p, _, _, _, _ = two_receipts
    code = supplier(db, 'NCC-0002')
    opening(db, p, code, 100, 1000000)
    pay(db, p, 1000000, supplier_code=code)
    assert_debt(db, p, 1500000, 60, True)


def test_sorting_uses_unpaid_dates_after_cleared_old_receipts(db, two_receipts):
    p, _, _, _, _ = two_receipts
    pay(db, p, 1000000)
    for code, days in [('NCC-0002', 40), ('NCC-0003', 10), ('NCC-0004', 5)]:
        supplier(db, code)
        opening(db, p, code, days)
    assert [(r['number'], r['overdue']) for r in group(db, p)['rows']] == [
        ('NCC-0002', True), ('NCC-0003', False), ('NCC-0001', False), ('NCC-0004', False)]


def test_fifo_is_one_set_query_and_never_writes(db, two_receipts):
    p, _, _, _, _ = two_receipts
    pay(db, p, 1000000)
    before = [db.query(m).count() for m in (AuditLog, CashDoc, FundLedger, StockDoc, StockLedger)]
    sql = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        sql.append(statement)
    event.listen(db.get_bind(), 'before_cursor_execute', capture)
    try:
        assert group(db, p)['rows'][0]['oldest_debt_date'] == DAY - timedelta(days=5)
    finally:
        event.remove(db.get_bind(), 'before_cursor_execute', capture)
    assert len(sql) == 1  # TD2: tổng đếm bằng count() over() ngay trong câu lấy dòng.
    assert all(s.lstrip().upper().startswith('SELECT') for s in sql)
    assert 'sum(' in sql[-1].lower() and ' over (' in sql[-1].lower()
    assert [db.query(m).count() for m in (AuditLog, CashDoc, FundLedger, StockDoc, StockLedger)] == before
