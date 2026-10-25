"""SY1 (báo cáo đồng bộ 25/10/2026): F1 nút hủy TK của thợ, F3 trang đích Quản trị, F4 máy ngoài danh mục khi nhận đơn, F9 link nguồn nhận mua."""
from sqlalchemy import select
from app import stock_sources, technician_returns as svc
from app.models import Equipment, StockDoc
from test_web import web_login, d1_catalog, service_login, PASSWORD  # noqa: F401
from test_ux1d_bao_hu import held, transfer, split, people, purchase_people, ticket  # noqa: F401
from test_lg1_stock_sources import make_case  # noqa: F401


def test_f3_admin_only_login_lands_on_accounts_page(client, make_user):
    make_user('qt.admin', 'admin')
    r = client.post('/dang-nhap', data={'username': 'qt.admin', 'password': PASSWORD}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/tai-khoan'
    assert client.get('/tai-khoan').status_code == 200
    # Có vai nghiệp vụ thì vẫn vào trang đích theo vai đó, không đổi.
    make_user('kt.admin', 'finance', 'admin')
    r = client.post('/dang-nhap', data={'username': 'kt.admin', 'password': PASSWORD}, follow_redirects=False)
    assert r.headers['location'] == '/tong-quan'


def test_f4_intake_lists_outside_catalog_machine(client, web_login, d1_catalog, db):
    web_login('le.tan', 'reception')
    db.add(Equipment(serial='NGOAI-9', model_name='Canon LBP 2900', customer_code='KTD-000001', active=True, search_text='canon lbp 2900', created_by=1))
    db.add(Equipment(serial='TRONG-1', product_code='TD-0001', customer_code='KTD-000001', active=True, search_text='may in', created_by=1))
    db.commit()
    page = client.get('/dich-vu/moi?customer_code=KTD-000001').text
    assert 'NGOAI-9 · Canon LBP 2900' in page and 'TRONG-1 · Máy in' in page
    # Khách khác không thấy máy này.
    assert 'NGOAI-9' not in client.get('/dich-vu/moi?customer_code=KTD-000002').text


def test_f1_owner_sees_cancel_form_without_receive_form(client, db, held):
    p, own, _ = held; transfer(db, p, qty=2); row = svc.create(db, p['technician'], split())[0]
    service_login(client, p['technician'])
    page = client.get(f'/tho-tra-hang/{row.id}').text
    assert 'id="return-cancel"' in page and 'id="return-receive"' not in page
    # Thợ khác: 403 ngay ở trang, không thấy gì.
    service_login(client, p['other_tech'])
    assert client.get(f'/tho-tra-hang/{row.id}').status_code == 403
    # Kho vẫn có cả nhận và hủy.
    service_login(client, p['warehouse'])
    page = client.get(f'/tho-tra-hang/{row.id}').text
    assert 'id="return-receive"' in page and 'id="return-cancel"' in page
    # Hủy xong thì form biến mất, hàng vẫn ở kho thợ.
    csrf = service_login(client, p['technician'])
    r = client.post(f'/tho-tra-hang/{row.id}/huy', data=dict(csrf=csrf, version=row.version, reason='Giữ lại kiểm tra'), follow_redirects=False)
    assert r.status_code == 303
    page = client.get(f'/tho-tra-hang/{row.id}').text
    assert 'id="return-cancel"' not in page and 'Lý do hủy: Giữ lại kiểm tra' in page


def test_f9_purchase_receipt_source_opens_order_view_even_when_fully_received(client, db, people):
    p = people
    doc = make_case(db, p, "purchase")
    source = stock_sources.describe(db, doc)
    assert source['href'].endswith('#lan-nhan') and '/nhan#' not in source['href']
    order_url = source['href'].split('#')[0]
    # Đơn đã nhận đủ: bản xem vẫn mở được với kho, giám đốc và kế toán; trang nhận hàng cũ trả 409.
    for who in ('warehouse', 'director', 'finance'):
        service_login(client, p[who])
        assert client.get(order_url).status_code == 200, who
        assert 'id="lan-nhan"' in client.get(order_url).text
    service_login(client, p['warehouse'])
    assert client.get(order_url + '/nhan').status_code == 409
