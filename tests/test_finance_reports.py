from datetime import date, datetime

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app import sales_orders, cash_docs, cash_posting, finance_catalogs, finance_reports as svc, sales_receipts
from app.errors import BusinessError
from app.models import AuditLog, CashCategory, Customer, Fund, FundBalance
from app.schemas import CashCategoryIn
from test_cash import cash_people, opening, posted
from test_receipt_cash import t3_people, order, ticket, report

DAY = date(2026, 10, 10)
TODAY = date(2026, 10, 18)


@pytest.fixture
def t5_money(db, cash_people, monkeypatch):
    monkeypatch.setattr(svc, '_today', lambda: TODAY)
    p = cash_people
    finance_catalogs.create(db, p['finance'], CashCategory, CashCategoryIn(code='THU-B', name='Thu B', direction='THU'))
    opening(db, p, amount=5000000)
    a = posted(db, p['finance'], amount=1000000)
    b = posted(db, p['finance'], amount=500000, category_code='THU-B')
    posted(db, p['finance'], 'PC', amount=300000)
    posted(db, p['finance'], 'CQ', amount=200000, to_fund_code='TK-ACB')
    return p, a, b


@pytest.fixture
def t5_debts(db, t3_people, configured_finance, monkeypatch):
    monkeypatch.setattr(svc, '_today', lambda: TODAY)
    monkeypatch.setattr(cash_docs, 'today', lambda: TODAY)
    p = t3_people
    o = order(db, p)
    sales_orders.deliver(db,p['sales'],o.id,o.version)
    t = ticket(db, p)
    o.confirmed_at = datetime.fromisoformat('2026-10-08T00:30:00+07:00')
    sales_orders.deliveries(db,o.id)[0].delivered_at = o.confirmed_at
    t.priced_at = datetime.fromisoformat('2026-10-09T23:30:00+07:00')
    db.commit()
    for parent, amount in [(o, 400000), (t, 100000)]:
        receipt = report(db, p, parent, amount)
        sales_receipts.confirm(db, p['finance'], receipt.id, receipt.version)
    report(db, p, o, 200000)
    report(db, p, t, 200000)
    return p, o, t


def test_categories_exclude_transfers_opening_and_current_balances(db, t5_money):
    p, a, b = t5_money
    f = p['finance']
    assert svc.categories(db, f, 'THU') == [dict(code='THU-B', name='Thu B', documents=1, amount=500000),
                                          dict(code='THU-BH', name='Thu bán hàng', documents=1, amount=1000000)]
    assert svc.categories(db, f, 'CHI') == [dict(code='CHI-KHAC', name='Chi khác', documents=1, amount=300000)]
    assert svc.category_totals(db, f) == dict(income=1500000, expense=300000, net=1200000, income_documents=2, expense_documents=1)
    assert {r['code']: r['balance'] for r in svc.funds(db, f)} == {'QUY-TM':6000000, 'TK-ACB':200000}
    assert svc.fund_total(db, f) == 6200000
    # Quỹ chưa phát sinh và quỹ ngừng dùng vẫn hiện; không mất tiền khi ẩn danh mục.
    db.add(Fund(code='CHUA-DUNG', name='Quỹ chưa có giao dịch', kind='TIEN_MAT'))
    db.get(Fund, 'TK-ACB').active = False; db.commit()
    funds = svc.funds(db, f)
    assert funds[0]['balance'] == 0 and funds[-1]['active'] is False
    assert svc.fund_total(db, p['director']) == 6200000


def test_reversal_is_on_reversal_day_not_original_date(db, t5_money, monkeypatch):
    p, a, b = t5_money
    monkeypatch.setattr(cash_docs, 'today', lambda: date(2026,10,11))
    cash_posting.void(db, p['finance'], b.id, 'Hoàn tiền', b.version)
    f = p['finance']
    assert svc.category_totals(db, f, DAY, DAY)['income'] == 1500000
    assert svc.category_totals(db, f, date(2026,10,11), date(2026,10,11))['income'] == -500000
    assert svc.category_totals(db, f)['income'] == 1000000
    assert svc.daily(db, f, DAY, date(2026,10,11)) == [
        dict(day=DAY, income=1500000, expense=300000, net=1200000, closing_balance=6200000),
        dict(day=date(2026,10,11), income=-500000, expense=0, net=-500000, closing_balance=5700000)]
    # Một phiếu được đếm một lần trong cả khoảng, kể cả có cả dòng gốc và đảo.
    assert svc.categories(db, f, 'THU')[0]['documents'] == 1


def test_daily_includes_empty_days_opening_and_carry_forward(db, t5_money):
    p, a, b = t5_money
    rows = svc.daily(db, p['finance'], date(2026,10,1), TODAY)
    assert len(rows) == 18
    assert rows[0] == dict(day=date(2026,10,1), income=0, expense=0, net=0, closing_balance=5000000)
    assert rows[1]['closing_balance'] == 5000000
    assert rows[-1]['closing_balance'] == svc.fund_total(db, p['finance']) == 6200000
    assert svc.daily(db, p['finance'], date(2026,10,12), TODAY)[0]['closing_balance'] == 6200000
    assert svc.daily(db, p['finance'], date(2000,1,1), date(2000,1,1))[0]['closing_balance'] == 0
    # PC đảo ghi chi âm, không biến thành thu.
    pc = posted(db, p['finance'], 'PC', amount=100000, doc_date=TODAY)
    cash_posting.void(db, p['finance'], pc.id, 'Chi nhầm', pc.version)
    assert svc.category_totals(db, p['finance'], TODAY, TODAY)['expense'] == 0


def test_vietnam_dates_are_entry_dates_not_utc_posting_stamps(db, t5_money):
    p, a, b = t5_money
    a.posted_at = datetime.fromisoformat('2026-10-10T00:30:00+07:00'); db.commit()
    db.execute(text("SET LOCAL TIME ZONE 'America/New_York'"))
    assert svc.category_totals(db, p['finance'], date(2026,10,9), date(2026,10,9))['income'] == 0
    assert svc.daily(db, p['finance'], DAY, DAY)[0]['income'] == 1500000


def test_combined_customer_debts_pending_not_deducted_paid_customer_disappears(db, t5_debts):
    p, o, t = t5_debts
    assert svc.customers(db, p['finance']) == [dict(customer_code='KTD-000001', name='Minh Phát', documents=2,
        receivable=1500000, paid=500000, pending=400000, remaining=1000000)]
    assert svc.debt_totals(db, p['director']) == dict(receivable=1500000, paid=500000, pending=400000, remaining=1000000)
    rows = svc.customer_docs(db, p['finance'], o.customer_code)
    assert [(r['kind'], r['number'], r['day'], r['days'], r['remaining']) for r in rows] == [
        ('DB', o.number, date(2026,10,8), 10, 600000), ('DV', t.number, date(2026,10,9), 9, 400000)]
    # Xác nhận cả phần đang chờ và báo thu phần còn lại.
    for parent, more in [(o, 400000), (t, 200000)]:
        receipt = report(db, p, parent, more)
        sales_receipts.confirm(db, p['finance'], receipt.id, receipt.version)
    for receipt in sales_receipts.list_pending(db, p['finance']):
        sales_receipts.confirm(db, p['finance'], receipt.id, receipt.version)
    assert svc.customers(db, p['finance']) == []
    assert svc.customer_docs(db, p['finance'], o.customer_code) == []
    assert svc.debt_totals(db, p['finance'])['remaining'] == 0


def test_debts_sort_exclude_ineligible_docs_and_nonconfirmed_receipts(db, t5_debts):
    p, o, t = t5_debts
    db.add(Customer(code='KTD-000002', name='Khách nợ lớn')); db.commit()
    other = order(db, p); other.customer_code='KTD-000002'; other.receivable=2000000; db.commit()
    receipt = report(db, p, other, 500000)
    sales_receipts.reject(db, p['finance'], receipt.id, 'Nhầm', receipt.version)
    assert [r['remaining'] for r in svc.customers(db, p['finance'])] == [2000000, 1000000]
    assert svc.customers(db, p['finance'])[0]['pending'] == 0
    other.status = 'DA_HUY'; t.status = 'DANG_LAM'; db.commit()
    assert svc.customers(db, p['finance'])[0]['remaining'] == 600000
    o.status = 'NHAP'; db.commit()
    assert svc.customers(db, p['finance']) == []


def test_all_queries_read_only_grouped_no_orm_loads_no_autoflush(db, engine, t5_money):
    p, a, b = t5_money
    before = db.query(AuditLog).count()
    loaded, sql = [], []
    def capture(conn, cursor, statement, parameters, context, executemany):
        sql.append(statement)
    with Session(engine) as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        event.listen(session, 'loaded_as_persistent', lambda s, obj: loaded.append(type(obj)))
        event.listen(engine, 'before_cursor_execute', capture)
        try:
            svc.funds(session,p['finance']); svc.fund_total(session,p['finance']); svc.categories(session,p['finance'],'THU')
            svc.category_totals(session,p['finance']); svc.daily(session,p['finance']); svc.customers(session,p['finance'])
            svc.debt_totals(session,p['finance']); svc.customer_docs(session,p['finance'],'KTD-000001')
        finally:
            event.remove(engine,'before_cursor_execute',capture)
    assert not loaded and len(sql)==8 and all(s.lstrip().upper().startswith(('SELECT','WITH')) for s in sql)
    assert sum('GROUP BY' in s for s in sql)>=5
    fund=db.get(Fund,'QUY-TM');fund.name='Chưa lưu'
    assert svc.funds(db,p['finance'])[0]['name'] != 'Chưa lưu'
    assert db.is_modified(fund)
    db.rollback()
    assert db.query(AuditLog).count()==before


def test_exact_integer_sums_above_javascript_precision(db, cash_people, monkeypatch):
    monkeypatch.setattr(svc,'_today',lambda:TODAY)
    huge=9007199254740993
    for _ in range(2):posted(db,cash_people['finance'],amount=huge)
    assert svc.category_totals(db,cash_people['finance'])['income']==huge*2
    assert svc.daily(db,cash_people['finance'],DAY,DAY)[0]['closing_balance']==huge*2
    assert isinstance(svc.fund_total(db,cash_people['finance']),int)


def test_forbidden_roles_and_invalid_range(db, cash_people):
    user=cash_people['sales']
    for fn,args in [(svc.funds,()),(svc.fund_total,()),(svc.categories,('THU',)),(svc.category_totals,()),
                    (svc.daily,()),(svc.customers,()),(svc.debt_totals,()),(svc.customer_docs,('KTD-000001',))]:
        with pytest.raises(BusinessError) as exc:fn(db,user,*args)
        assert exc.value.status==403
    with pytest.raises(BusinessError) as exc:svc.daily(db,cash_people['finance'],TODAY,DAY)
    assert exc.value.code=='BAD_DATE_RANGE'
    with pytest.raises(BusinessError) as exc:svc.categories(db,cash_people['finance'],'CQ')
    assert exc.value.code=='BAD_DIRECTION'


def test_voided_confirmed_receipt_restores_customer_debt(db, t5_debts):
    from app.models import SalesReceipt
    p, o, t = t5_debts
    receipt=db.scalar(select(SalesReceipt).where(SalesReceipt.order_id==o.id,SalesReceipt.status=='DA_XAC_NHAN'))
    sales_receipts.void(db,p['director'],receipt.id,'Hoàn lại tiền',receipt.version)
    assert svc.customers(db,p['finance'])[0]['remaining']==1400000
    assert svc.customers(db,p['finance'])[0]['paid']==100000
