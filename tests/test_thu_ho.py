"""Thu hộ theo khách trên màn thợ (TUẤN duyệt mẫu 12/11/2026): thợ báo thu công nợ khách dù đơn/phiếu không phải của mình,
trừ chứng từ cũ nhất trước, chờ kế toán xác nhận như mọi báo thu."""
from datetime import date

from app import customer_opening, khach_tra_sau as kts, sales_orders, sales_receipts as svc
from app.models import SalesReceipt
from app.schemas import CustomerReceiptReport, SalesReceiptReport
from test_receipt_cash import fails, order, t3_people, ticket  # noqa: F401  (fixture)
from test_web import service_login

CODE = 'KTD-000001'


def debts(db, p):
    """Nợ đầu kỳ 200.000 (01/09), phiếu DV của điều phối 500.000 (18/10), đơn bán 1.000.000 giao hôm nay."""
    customer_opening.set_opening(db, p['director'], CODE, 200000, date(2026, 9, 1), 'Nợ cũ')
    t = ticket(db, p, assignee_id=p['dispatcher'].id, status='DA_DONG')
    o = order(db, p)
    sales_orders.deliver(db, p['director'], o.id, o.version)
    db.refresh(o)
    return t, o


def body(amount, **kw):
    return CustomerReceiptReport(method=kw.pop('method', 'TIEN_MAT'), amount=amount, **kw)


def test_technician_collects_any_doc_oldest_first_and_waits_for_finance(db, t3_people, configured_finance):
    p = t3_people; tech = p['technician']
    t, o = debts(db, p)
    [row] = kts.customers(db, tech)
    assert (row['code'], row['docs'], row['due'], row['mine']) == (CODE, 3, 1700000, False)
    assert kts.customers(db, tech, q='minh phat')[0]['code'] == CODE and kts.customers(db, tech, q='khong co') == []
    fails('FORBIDDEN', kts.customers, db, p['sales'])
    # không qua Thu hộ thì quyền từng chứng từ giữ như cũ
    fails('FORBIDDEN', svc.report_for_customer, db, tech, CODE, body(100))
    fails('FORBIDDEN', svc.report_for_ticket, db, tech, t.id, SalesReceiptReport(version=t.version, amount=100, method='TIEN_MAT'))
    fails('FORBIDDEN', svc.report_for_customer, db, p['sales'], CODE, body(100), thu_ho=True)
    n = db.query(SalesReceipt).count()
    fails('OVER_PAYMENT', svc.report_for_customer, db, tech, CODE, body(1700001), thu_ho=True)
    fails('DEBT_CHANGED', svc.report_for_customer, db, tech, CODE, body(100, expected_total=1), thu_ho=True)
    assert db.query(SalesReceipt).count() == n
    rows = svc.report_for_customer(db, tech, CODE, body(900000), thu_ho=True)
    assert [(r.opening_id is not None, r.ticket_id, r.order_id, r.amount) for r in rows] == [
        (True, None, None, 200000), (False, t.id, None, 500000), (False, None, o.id, 200000)]
    assert all(r.status == 'CHO_XAC_NHAN' and r.reported_by == tech.id and svc.is_collected(r.note) for r in rows)
    assert rows[0].note == 'Thu hộ theo khách: tổng 900.000 đ chia 3 chứng từ'
    assert kts.customers(db, tech)[0]['due'] == 800000
    held = svc.held_by(db, tech)
    assert held['total'] == 900000 and all(r['thu_ho'] for r in held['rows'])
    # thợ không tự xác nhận; kế toán xác nhận thì nợ giảm
    fails('FORBIDDEN', svc.confirm, db, tech, rows[1].id, rows[1].version)
    for r in rows:
        db.refresh(r)
        svc.confirm(db, p['finance'], r.id, r.version)
    db.refresh(t)
    assert svc.balance(t)['remaining'] == 0
    assert kts.customers(db, tech)[0]['due'] == 800000


def test_permit_cannot_be_faked(db, t3_people, configured_finance):
    p = t3_people
    t, _ = debts(db, p)
    fails('FORBIDDEN', svc.check_ticket_reporter, p['technician'], t, True)
    fails('FORBIDDEN', svc.check_opening_reporter, p['technician'], True)


def test_web_flow(client, db, t3_people, configured_finance):
    p = t3_people; tech = p['technician']
    debts(db, p)
    csrf = service_login(client, tech)
    page = client.get('/tien-toi-giu/khach-tra-sau', params={'tab': 'khach'})
    assert page.status_code == 200 and f'data-collect-customer="{CODE}"' in page.text and 'data-kts-tab="khach"' in page.text
    part = client.get('/tien-toi-giu/khach-tra-sau', params={'tab': 'khach', 'q': 'minh', 'phan': '1'})
    assert f'data-collect-customer="{CODE}"' in part.text and 'kts-page' not in part.text
    form = client.get(f'/tien-toi-giu/thu-ho/{CODE}')
    assert form.status_code == 200 and 'value="1.700.000"' in form.text and 'data-swipe' in form.text and form.text.count('data-unpaid=') == 3
    data = dict(csrf=csrf, amount='1.800.000', method='TIEN_MAT', received_at='', expected_total='1700000')
    bad = client.post(f'/tien-toi-giu/thu-ho/{CODE}', data=data)
    assert bad.status_code == 409 and 'data-error-code="OVER_PAYMENT"' in bad.text and 'value="1.800.000"' in bad.text
    ok = client.post(f'/tien-toi-giu/thu-ho/{CODE}', data=dict(data, amount='700.000'), follow_redirects=False)
    assert ok.status_code == 303 and ok.headers['location'].startswith('/tien-toi-giu?thu_ho=')
    done = client.get(ok.headers['location'])
    assert 'data-collect-done' in done.text and '700.000' in done.text and 'Minh Phát' in done.text and 'data-held-collect' in done.text
    assert db.query(SalesReceipt).filter(SalesReceipt.reported_by == tech.id).count() == 2
    service_login(client, p['finance'])
    assert 'data-receipt-collect' in client.get('/bao-thu', params={'nguoi': tech.id}).text
    assert client.get('/tien-toi-giu/khach-tra-sau', params={'tab': 'khach'}).status_code == 403
    assert client.get(f'/tien-toi-giu/thu-ho/{CODE}').status_code == 403
