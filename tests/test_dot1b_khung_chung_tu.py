"""Đợt 1b: báo giá và đơn bán đã gửi/chốt dùng một khung chỉ đọc, tiến độ từng dòng, một màn giao hàng, thông báo có số chứng từ."""
from app import quotes, sales_orders as svc
from app.schemas import QuoteIn
from test_my_work import Page
from test_stock_reservations import order, people, purchase_people, stock
from test_web import service_login


def test_confirmed_order_is_read_only_with_progress_toast_and_shared_delivery(db, client, people):
    p = people
    stock(db, p, 10)
    o = order(db, p, 10)
    service_login(client, p['sales'])
    page = client.get(f'/don-ban/{o.id}?da_luu=1&xong=confirm')
    assert page.status_code == 200 and 'data-doc-view="order"' in page.text
    assert 'name="qty"' not in page.text and 'name="deliver_1"' not in page.text
    assert f'Đã chốt {o.number} · đã giữ hàng · chờ thủ kho K58 giao.' in page.text
    assert 'Đã giữ 10/10' in page.text and 'Bước tiếp · thủ kho K58' in page.text
    primary = Page(page.text).primary
    assert primary['href'] == f'/kho/giao/{o.id}' and primary['data-next-route'] == f'/kho/giao/{o.id}'
    # Kinh doanh mở được màn giao chung; nút quay lại về đơn.
    kho = client.get(f'/kho/giao/{o.id}')
    assert kho.status_code == 200 and 'name="deliver_1"' in kho.text and f'href="/don-ban/{o.id}"' in kho.text


def test_delivery_error_returns_to_shared_screen_and_progress_updates(db, client, people):
    p = people
    stock(db, p, 10)
    o = order(db, p, 10)
    csrf = service_login(client, p['sales'])
    data = dict(csrf=csrf, version=o.version, delivery_form='1', deliver_1='11', receiver_name='Chị Lan')
    wrong = client.post(f'/don-ban/{o.id}/deliver', data=data)
    assert wrong.status_code == 409 and 'name="deliver_1" value="11"' in wrong.text and 'Chị Lan' in wrong.text
    assert client.post(f'/don-ban/{o.id}/deliver', data={**data, 'deliver_1': '6'}, follow_redirects=False).status_code == 303
    db.expire_all()
    page = client.get(f'/don-ban/{o.id}?da_luu=1&xong=deliver')
    assert 'Giao 6/10' in page.text and 'Đã giữ 4/4' in page.text
    assert f'Đã giao một phần {o.number} · phần còn lại chờ thủ kho K58 giao.' in page.text


def test_saved_action_toast_and_finance_has_no_delivery_button(db, client, people):
    p = people
    stock(db, p, 10)
    o = order(db, p, 10)
    service_login(client, p['finance'])
    page = client.get(f'/don-ban/{o.id}')
    assert page.status_code == 200 and Page(page.text).primary is None
    service_login(client, p['sales'])
    assert f'Đã lưu thay đổi {o.number}.' in client.get(f'/don-ban/{o.id}?da_luu=1&xong=promise').text


def test_draft_order_keeps_edit_form(db, client, people):
    p = people
    o = order(db, p, 2, confirm=False)
    service_login(client, p['sales'])
    page = client.get(f'/don-ban/{o.id}?da_luu=1')
    assert 'data-doc-view' not in page.text and 'name="qty"' in page.text
    assert f'Đã lưu đơn nháp {o.number} · chưa chốt, chưa giữ hàng.' in page.text


def test_sent_and_accepted_quote_views(db, client, people):
    p = people
    q = quotes.create(db, p['sales'], QuoteIn(customer_code='KTD-000001', lines=[dict(description='Công', qty=1, unit_price=100)]))
    service_login(client, p['sales'])
    assert 'name="qty"' in client.get(f'/bao-gia/{q.id}').text
    q = quotes.send(db, p['sales'], q.id, q.version)
    page = client.get(f'/bao-gia/{q.id}?da_luu=1')
    assert 'data-doc-view="quote"' in page.text and 'name="qty"' not in page.text
    assert f'Đã gửi {q.number} · chờ khách trả lời' in page.text
    parsed = Page(page.text)
    assert parsed.primary['form'] == 'quote-accept' and 'quote-reject' in parsed.forms
    q = quotes.accept(db, p['sales'], q.id, q.version)
    page = client.get(f'/bao-gia/{q.id}?da_luu=1')
    assert f'Khách đồng ý {q.number} · bấm Tạo đơn bán để giữ hàng.' in page.text
    assert Page(page.text).primary['href'] == f'/don-ban/tu-bao-gia/{q.id}'
