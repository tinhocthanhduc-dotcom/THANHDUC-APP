"""Đợt 5 (TUẤN duyệt mẫu 18/11/2026): Khách đồng ý → tạo đơn một chạm; trang tạo đơn điền sẵn kho đủ hàng,
giao nhận + thanh toán như đơn trước, hẹn hôm nay. Không tự chốt."""
import re

from app import quotes, sales_orders
from app.models import Customer, Quote, SalesOrder
from app.schemas import QuoteIn, SalesOrderIn
from test_stock_reservations import people, purchase_people, stock  # noqa: F401
from test_web import service_login
from web_forms import key_for


def _sent(db, p, qty=2):
    q = quotes.create(db, p['sales'], QuoteIn(customer_code='KTD-000001', lines=[dict(product_code='TD-0001', description='Máy in', qty=qty, unit_price=300)]))
    return quotes.send(db, p['sales'], q.id, q.version)


def test_accept_then_order_page_prefilled(client, db, people):
    p = people
    stock(db, p, qty=7)
    c = db.get(Customer, 'KTD-000001'); c.due_days = 45; db.commit()
    prev = sales_orders.create(db, p['sales'], SalesOrderIn(customer_code='KTD-000001', fulfillment='THO_GIAO', payment_term='CONG_NO',
                               lines=[dict(kind='KHAC', description='Công', qty=1, unit_price=100, vat_rate=0)]))
    q = _sent(db, p)
    csrf = service_login(client, p['sales'])
    page = client.get(f'/bao-gia/{q.id}').text
    assert 'name="then" value="order"' in page and 'Khách đồng ý → tạo đơn' in page and 'Chỉ ghi khách đồng ý' in page
    r = client.post(f'/bao-gia/{q.id}/accept', data=dict(csrf=csrf, form_key=key_for(client, f'/bao-gia/{q.id}'), version=str(q.version), then='order'),
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == f'/don-ban/tu-bao-gia/{q.id}?dong_y=1'
    db.expire_all()
    assert db.get(Quote, q.id).status == 'CHAP_NHAN'
    assert db.query(SalesOrder).filter(SalesOrder.quote_id == q.id).count() == 0  # chỉ mở trang, chưa tạo đơn
    page = client.get(r.headers['location']).text
    assert 'data-oq-prefill' in page and f'Đã ghi khách đồng ý {q.number}' in page and prev.number in page
    assert re.search(r'name="warehouse_1" value="K58" checked', page) and 'data-oq-auto' in page
    assert re.search(r'name="fulfillment" value="THO_GIAO" checked', page)
    assert re.search(r'name="payment_term" value="CONG_NO" checked', page) and 'name="due_days" inputmode="numeric" value="45"' in page
    assert re.search(r'name="promised_date" value="\d{4}-\d{2}-\d{2}"', page)


def test_accept_only_stays_on_quote_and_no_stock_leaves_warehouse_empty(client, db, people):
    p = people
    stock(db, p, qty=1)
    q = _sent(db, p, qty=5)
    csrf = service_login(client, p['sales'])
    r = client.post(f'/bao-gia/{q.id}/accept', data=dict(csrf=csrf, form_key=key_for(client, f'/bao-gia/{q.id}'), version=str(q.version)),
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == f'/bao-gia/{q.id}?da_luu=1'
    page = client.get(f'/don-ban/tu-bao-gia/{q.id}').text
    assert 'data-oq-auto' not in page and re.search(r'name="warehouse_1" value="" checked', page)
    assert re.search(r'name="fulfillment" value="TAI_QUAY" checked', page) and re.search(r'name="payment_term" value="TRA_NGAY" checked', page)
    assert 'như đơn trước' not in page
