"""Đợt 1: bán hàng đi một mạch (báo giá đã đồng ý, bán tại quầy, kho nhắc tiền, thợ còn tiền chưa báo)."""
from datetime import datetime

from sqlalchemy import text

from app import my_work, quotes, service_sla, sales_orders as svc, sales_receipts
from app.models import SalesDelivery, SalesOrder, SalesReceipt
from app.schemas import QuoteIn, SalesOrderFromQuote, SalesReceiptReport
from test_counter_sales import draft
from test_my_work import DAY, Page, groups, report, ticket
from test_stock_reservations import people, purchase_people, stock
from test_web import service_login
from web_forms import key_for


def accepted_quote(db, p, who='sales'):
    q = quotes.create(db, p[who], QuoteIn(customer_code='KTD-000001', lines=[dict(description='Công', qty=1, unit_price=100)]))
    quotes.send(db, p[who], q.id, q.version)
    return quotes.accept(db, p[who], q.id, q.version)


def order_form(csrf, client, **changes):
    data = dict(csrf=csrf, form_key=key_for(client, '/don-ban/moi'), customer_code='KTD-000001', contact_name='Anh Hùng',
                delivery_address='', note='', product_code='TD-0001', description='Máy in', qty='2', unit='cái',
                unit_price='250.000', kind_0='HANG', warehouse_0='K58', vat_0='0', fulfillment='TAI_QUAY', payment_term='TRA_NGAY')
    data.update(changes)
    return data


def test_accepted_quote_stays_in_queue_until_active_order(db, people):
    p = people
    q = accepted_quote(db, p)
    other = accepted_quote(db, p, 'other_sales')
    rows = groups(db, p, 'sales')['quotes_accepted']['rows']
    assert [r['id'] for r in rows] == [q.id] and rows[0]['link'] == f'/don-ban/tu-bao-gia/{q.id}'
    assert rows[0]['overdue'] is False
    assert {r['id'] for r in groups(db, p)['quotes_accepted']['rows']} == {q.id, other.id}
    assert q.id not in {r['id'] for r in groups(db, p, 'sales')['quotes_open']['rows']}
    order = svc.create_from_quote(db, p['sales'], q.id, SalesOrderFromQuote(version=q.version))
    assert groups(db, p, 'sales')['quotes_accepted']['count'] == 0
    svc.cancel(db, p['sales'], order.id, 'Khách đổi ý', order.version)
    assert [r['id'] for r in groups(db, p, 'sales')['quotes_accepted']['rows']] == [q.id]


def test_accepted_quote_overdue_after_two_days_from_accept(db, people, monkeypatch):
    p = people
    monkeypatch.setattr(my_work, '_now', lambda: datetime.now(service_sla.TZ))
    fresh = accepted_quote(db, p)
    # Dữ liệu cũ không có nhật ký Khách đồng ý: lấy ngày tạo báo giá thay.
    old = quotes.create(db, p['sales'], QuoteIn(customer_code='KTD-000001', lines=[dict(description='Công', qty=1, unit_price=100)]))
    db.execute(text("update quotes set status='CHAP_NHAN', created_at=now()-interval '3 days' where id=:id"), dict(id=old.id))
    db.commit()
    rows = my_work.filtered(db, p['sales'], 'quotes_accepted', 1, DAY)['rows']
    assert [(r['id'], r['overdue']) for r in rows] == [(old.id, True), (fresh.id, False)]


def test_work_page_shows_create_order_button(client, db, people):
    p = people
    q = accepted_quote(db, p)
    service_login(client, p['sales'])
    page = client.get('/viec-cua-toi').text
    assert 'Báo giá đã đồng ý · chưa tạo đơn' in page and f'/don-ban/tu-bao-gia/{q.id}' in page and 'Tạo đơn' in page


def test_new_counter_order_one_click_sells_and_reports(client, db, people):
    p = people
    stock(db, p, 5)
    csrf = service_login(client, p['sales'])
    page = client.get('/don-ban/moi').text
    primary = Page(page).primary
    assert primary['value'] == 'counter' and 'Chỉ chốt, giao và thu sau' in page and 'name="counter_method"' in page
    r = client.post('/don-ban/moi', data=order_form(csrf, client, then='counter', counter_method='CHUYEN_KHOAN'), follow_redirects=False)
    assert r.status_code == 303
    order = db.query(SalesOrder).one()
    db.refresh(order)
    receipt = db.query(SalesReceipt).one()
    assert order.status == 'DA_GIAO' and db.query(SalesDelivery).count() == 1
    assert receipt.amount == order.total == 500000 and receipt.method == 'CHUYEN_KHOAN' and receipt.status == 'CHO_XAC_NHAN'


def test_counter_failure_keeps_draft_with_reason(client, db, people):
    p = people  # không nhập tồn: chốt giữ hàng thất bại
    csrf = service_login(client, p['sales'])
    r = client.post('/don-ban/moi', data=order_form(csrf, client, then='counter'), follow_redirects=False)
    order = db.query(SalesOrder).one()
    assert r.status_code != 303 and order.status == 'NHAP'
    assert f'Đã lưu đơn nháp {order.number} nhưng chưa bán tại quầy được' in r.text
    assert db.query(SalesReceipt).count() == db.query(SalesDelivery).count() == 0


def test_existing_draft_counter_and_other_terms_keep_confirm(client, db, people):
    p = people
    stock(db, p, 5)
    o = draft(db, p)
    csrf = service_login(client, p['sales'])
    page = client.get(f'/don-ban/{o.id}').text
    assert Page(page).primary['value'] == 'counter' and '>Bán tại quầy<' in page
    form = order_form(csrf, client, then='counter', version=str(o.version), qty='1')
    form['form_key'] = key_for(client, f'/don-ban/{o.id}')
    assert client.post(f'/don-ban/{o.id}', data=form, follow_redirects=False).status_code == 303
    db.refresh(o)
    assert o.status == 'DA_GIAO' and o.total == 250000 and db.query(SalesReceipt).one().amount == 250000
    later = draft(db, p, payment_term='CONG_NO')
    page = client.get(f'/don-ban/{later.id}').text
    primary = Page(page).primary
    assert primary['value'] == 'confirm' and '>Lưu và chốt<' in page
    assert 'data-counter-sale hidden' in page


def test_warehouse_sees_pay_due_reminder_but_can_deliver(client, db, people):
    p = people
    stock(db, p, 5)
    o = draft(db, p)
    svc.confirm(db, p['sales'], o.id, o.version)
    service_login(client, p['warehouse'])
    page = client.get(f'/kho/giao/{o.id}').text
    assert 'data-pay-due' in page and '500.000 đ' in page
    sales_receipts.report(db, p['sales'], o.id, SalesReceiptReport(version=o.version, amount=200000, method='TIEN_MAT', received_at=DAY))
    assert '300.000 đ' in client.get(f'/kho/giao/{o.id}').text
    credit = draft(db, p, payment_term='CONG_NO')
    db.execute(text("update customers set allow_debt=true, credit_limit=10000000 where code='KTD-000001'"))
    db.commit()
    svc.confirm(db, p['sales'], credit.id, credit.version)
    assert 'data-pay-due' not in client.get(f'/kho/giao/{credit.id}').text


def test_technician_home_lists_money_not_reported(client, db, people):
    p = people
    done = ticket(db, p, 'DA_DONG')
    ticket(db, p, 'DA_DONG', 'other_tech')
    ticket(db, p, 'DA_DONG', free=True)
    service_login(client, p['technician'])
    page = client.get('/tho').text
    assert 'Còn tiền chưa báo · 1 phiếu' in page and f'data-unreported="{done.id}"' in page
    assert f'/tho/phieu/{done.id}#thu-tien' in page and 'Còn 500.000 đ' in page
    report(db, p, done, 500000)
    assert 'data-unreported' not in client.get('/tho').text
