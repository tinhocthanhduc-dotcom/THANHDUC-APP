"""Sửa báo thu đã xác nhận một bước (TUẤN duyệt mẫu 12/11/2026)."""
from datetime import date

from app import cash_posting, day_closings, receipt_corrections as rc, sales_orders, sales_receipts as svc
from app.errors import BusinessError
from app.models import AuditLog, CashDoc, Customer, FundLedger, SalesOrder, SalesReceipt
from app.schemas import SalesOrderIn
from test_receipt_cash import fails, order, report, t3_people, ticket  # noqa: F401  (fixture)
from test_web import service_login


def _confirmed(db, p, parent, amount=600000, method='TIEN_MAT'):
    r = report(db, p, parent, amount, method)
    svc.confirm(db, p['finance'], r.id, r.version)
    db.expire_all()
    return db.get(SalesReceipt, r.id)


def _fix(db, user, r, **kw):
    args = dict(kind='DB', target_id=r.order_id, amount=r.amount, received_at=r.received_at, method=r.method, reason='Nhầm đơn')
    args.update(kw)
    return rc.correct(db, user, r.id, r.version, **args)


def _paid(db, model, ident):
    db.expire_all()
    return svc.balance(db.get(model, ident))['paid']


def test_move_to_other_order_and_lower_amount(db, t3_people, configured_finance):
    p = t3_people; a = order(db, p); b = order(db, p)
    r = _confirmed(db, p, a, 600000)
    old_pt = r.cash_doc_id
    for who in ('sales', 'finance'):
        fails('FORBIDDEN', _fix, db, p[who], r, target_id=b.id)
    fails('NO_CHANGE', _fix, db, p['director'], r)
    fails('REASON_REQUIRED', _fix, db, p['director'], r, target_id=b.id, reason='  ')
    fails('BAD_DATE', _fix, db, p['director'], r, received_at=date(2099, 1, 1))
    new = _fix(db, p['director'], r, target_id=b.id, amount=450000)
    db.expire_all(); old = db.get(SalesReceipt, r.id); new = db.get(SalesReceipt, new.id)
    assert old.status == 'DA_HUY' and old.replaced_by_id == new.id and rc.is_correction(old)
    assert f'{a.number} → {b.number}' in old.reason and '600.000 → 450.000' in old.reason and 'Nhầm đơn' in old.reason
    assert (new.status, new.order_id, new.amount, new.method, new.received_at, new.reported_by, new.decided_by) == \
        ('DA_XAC_NHAN', b.id, 450000, 'TIEN_MAT', old.received_at, p['sales'].id, p['director'].id)
    assert db.get(CashDoc, old_pt).status == 'DA_HUY'
    pt = db.get(CashDoc, new.cash_doc_id)
    assert pt.status == 'DA_GHI' and pt.amount == 450000 and pt.doc_date == old.received_at and pt.source_id == str(new.id)
    assert cash_posting.balance(db, p['finance'], 'QUY-TM') == 450000
    assert _paid(db, SalesOrder, a.id) == 0 and _paid(db, SalesOrder, b.id) == 450000
    assert db.query(AuditLog).filter(AuditLog.action == 'sales_receipt.correct', AuditLog.entity_id == str(old.id)).count() == 1
    fails('BAD_STATUS', _fix, db, p['director'], old, amount=1)


def test_over_payment_blocked_and_nothing_written(db, t3_people, configured_finance):
    p = t3_people; a = order(db, p); b = order(db, p)
    r = _confirmed(db, p, a, 600000)
    _confirmed(db, p, b, 700000)
    counts = (db.query(SalesReceipt).count(), db.query(CashDoc).count(), db.query(FundLedger).count(), db.query(AuditLog).count())
    fails('OVER_PAYMENT', _fix, db, p['director'], r, target_id=b.id)  # b chỉ còn 300.000
    fails('OVER_PAYMENT', _fix, db, p['director'], r, amount=1000001)
    db.expire_all()
    assert db.get(SalesReceipt, r.id).status == 'DA_XAC_NHAN'
    assert counts == (db.query(SalesReceipt).count(), db.query(CashDoc).count(), db.query(FundLedger).count(), db.query(AuditLog).count())
    assert cash_posting.balance(db, p['finance'], 'QUY-TM') == 1300000
    # cùng đơn, tăng tới đúng số phải thu được
    new = _fix(db, p['director'], r, amount=1000000, reason='Ghi nhầm số tiền')
    assert _paid(db, SalesOrder, a.id) == 1000000 and db.get(SalesReceipt, new.id).order_id == a.id


def test_closed_day_not_unlocked_posts_today(db, t3_people, configured_finance, monkeypatch):
    p = t3_people; a = order(db, p)
    r = _confirmed(db, p, a, 600000)
    old_pt = db.get(CashDoc, r.cash_doc_id)
    monkeypatch.setattr('app.cash_docs.today', lambda: date(2026, 10, 25))
    day_closings.close(db, p['finance'], date(2026, 10, 18), 0)
    # Hủy thường vẫn bị chặn vì ngày gốc đã chốt (giữ nguyên luật cũ)
    fails('DAY_CLOSED', svc.void, db, p['director'], r.id, 'Thử hủy', r.version)
    new = _fix(db, p['director'], r, method='CHUYEN_KHOAN', amount=550000, reason='Nhầm hình thức')
    db.expire_all()
    assert day_closings.is_closed(db, date(2026, 10, 18))
    assert db.get(CashDoc, old_pt.id).status == 'DA_HUY'
    rev = db.query(FundLedger).filter(FundLedger.doc_id == old_pt.id, FundLedger.is_reversal.is_(True)).one()
    assert rev.entry_date == date(2026, 10, 25) and rev.amount == -600000
    new = db.get(SalesReceipt, new.id); pt = db.get(CashDoc, new.cash_doc_id)
    assert new.received_at == date(2026, 10, 18) and pt.doc_date == date(2026, 10, 25) and pt.fund_code == 'TK-ACB'
    assert 'đã chốt sổ' in pt.description
    assert cash_posting.balance(db, p['finance'], 'QUY-TM') == 0 and cash_posting.balance(db, p['finance'], 'TK-ACB') == 550000
    # hôm nay đã chốt thì chặn
    day_closings.close(db, p['finance'], date(2026, 10, 25), 0)
    fails('DAY_CLOSED', _fix, db, p['director'], new, amount=500000)


def test_move_to_other_customer_ticket(db, t3_people, configured_finance):
    p = t3_people; a = order(db, p); t = ticket(db, p)
    r = _confirmed(db, p, a, 500000)
    db.add(Customer(code='KTD-000002', name='Khách B', search_text='khach b')); db.commit()
    o2 = sales_orders.create(db, p['sales'], SalesOrderIn(customer_code='KTD-000002', lines=[dict(kind='KHAC', description='Đơn B', qty=1, unit_price=800000, vat_rate=0)]))
    o2 = sales_orders.confirm(db, p['sales'], o2.id, o2.version)
    new = _fix(db, p['director'], r, target_id=o2.id, reason='Nhầm khách')
    db.expire_all()
    assert _paid(db, SalesOrder, a.id) == 0 and _paid(db, SalesOrder, o2.id) == 500000
    assert db.get(CashDoc, db.get(SalesReceipt, new.id).cash_doc_id).customer_code == 'KTD-000002'
    new2 = _fix(db, p['director'], db.get(SalesReceipt, new.id), kind='DV', target_id=t.id, reason='Nhầm đơn')
    db.expire_all()
    assert db.get(SalesReceipt, new2.id).ticket_id == t.id and _paid(db, SalesOrder, o2.id) == 0
    assert db.get(CashDoc, db.get(SalesReceipt, new2.id).cash_doc_id).category_code == 'THU-DV'


def test_failure_rolls_back_everything(db, t3_people, configured_finance, monkeypatch):
    p = t3_people; a = order(db, p); b = order(db, p)
    r = _confirmed(db, p, a, 600000)
    counts = (db.query(SalesReceipt).count(), db.query(CashDoc).count(), db.query(AuditLog).count())
    def boom(*a, **k):
        raise BusinessError('TEST_FAILURE', 'Lỗi thử')
    monkeypatch.setattr(svc, '_post_cash', boom)
    fails('TEST_FAILURE', _fix, db, p['director'], r, target_id=b.id)
    db.expire_all()
    assert db.get(SalesReceipt, r.id).status == 'DA_XAC_NHAN' and db.get(SalesReceipt, r.id).replaced_by_id is None
    assert counts == (db.query(SalesReceipt).count(), db.query(CashDoc).count(), db.query(AuditLog).count())
    assert cash_posting.balance(db, p['finance'], 'QUY-TM') == 600000


def test_notice_text(db, t3_people, configured_finance):
    p = t3_people; a = order(db, p); b = order(db, p)
    r = _confirmed(db, p, a, 600000)
    new = _fix(db, p['director'], r, target_id=b.id, amount=450000, reason='Nhầm đơn: khách trả đơn kia')
    db.expire_all()
    text = rc.notice_text(db, db.get(SalesReceipt, r.id), db.get(SalesReceipt, new.id))
    assert text.startswith(f'Sửa báo thu {r.number} → {new.number}')
    assert '600.000 → 450.000' in text and 'Lý do: Nhầm đơn: khách trả đơn kia' in text and f'/bao-thu/{new.id}' in text


def test_web_flow(client, db, t3_people, configured_finance):
    p = t3_people; a = order(db, p); b = order(db, p)
    r = _confirmed(db, p, a, 600000)
    csrf = service_login(client, p['finance'])
    assert f'data-receipt-correct="{r.id}"' not in client.get(f'/bao-thu/{r.id}').text
    assert client.get(f'/sua-bao-thu/{r.id}').status_code == 403
    csrf = service_login(client, p['director'])
    assert f'data-receipt-correct="{r.id}"' in client.get(f'/bao-thu/{r.id}').text
    page = client.get(f'/sua-bao-thu/{r.id}')
    assert page.status_code == 200 and 'data-sbt-form' in page.text and f'value="DB:{b.id}"' in page.text
    assert 'Minh Phát' in client.get(f'/sua-bao-thu/{r.id}/tim-khach', params={'q': 'minh'}).text
    form = dict(csrf=csrf, version=r.version, khach='KTD-000001', target=f'DB:{b.id}', amount='1.200.000',
                received_at=r.received_at.isoformat(), method='TIEN_MAT', reason_choice='Nhầm đơn / phiếu')
    bad = client.post(f'/sua-bao-thu/{r.id}', data=form)
    assert bad.status_code == 409 and 'data-error-code="OVER_PAYMENT"' in bad.text and 'value="1.200.000"' in bad.text
    ok = client.post(f'/sua-bao-thu/{r.id}', data=dict(form, amount='450.000'), follow_redirects=False)
    assert ok.status_code == 303
    db.expire_all(); old = db.get(SalesReceipt, r.id); new = db.get(SalesReceipt, old.replaced_by_id)
    assert ok.headers['location'] == f'/sua-bao-thu/{new.id}?xong=1'
    assert new.amount == 450000 and new.order_id == b.id and old.reason.endswith('Nhầm đơn / phiếu')
    done = client.get(ok.headers['location'])
    assert 'data-sbt-done' in done.text and old.number in done.text
    assert 'data-sbt-replaced' in client.get(f'/sua-bao-thu/{old.id}').text
    assert 'Đã sửa →' in client.get(f'/bao-thu/{old.id}').text
