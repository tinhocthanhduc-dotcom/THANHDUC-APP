"""care.quanlythanhduc.com (TUẤN duyệt mẫu 19/11/2026): cùng app nhận theo tên miền, menu Care riêng,
Máy đã bán, Bảo hành sửa chữa, Nhận máy bảo hành điền sẵn phiếu cũ."""
from datetime import UTC, datetime, timedelta

from app import equipment
from app.models import Equipment, ServiceStep, ServiceTicket
from app.web.lists import _today
from app.schemas import EquipmentIn, ServiceTicketIn
from app.web import sites
from test_bh1_sales_warranty import deliver, machine  # noqa: F401  (fixture)
from test_receipt_cash import t3_people, ticket  # noqa: F401  (fixture)
from test_stock_reservations import order, people, purchase_people  # noqa: F401  (fixture)
from test_web import service_login

CARE = {'host': 'care.testserver', 'accept': 'text/html'}
PAGE = {'host': 'care.testserver', 'sec-fetch-dest': 'document'}


def test_site_from_host_only_for_known_labels():
    assert sites.key_of('care.quanlythanhduc.com') == 'care'
    assert sites.key_of('CARE.quanlythanhduc.com:443') == 'care'
    assert sites.key_of('care.localhost') == 'care'
    for host in ('quanlythanhduc.com', 'marketing.quanlythanhduc.com', 'testserver', 'care', '', 'carex.quanlythanhduc.com'):
        assert sites.key_of(host) is None


def test_care_menu_brand_and_main_menu_unchanged(client, db, t3_people):
    p = t3_people
    service_login(client, p['dispatcher'])
    r = client.get('/dich-vu', headers=CARE)
    assert r.status_code == 200
    assert 'Thành Đức Care' in r.text and 'site-care' in r.text
    for label in ('Phiếu sửa chữa', 'Máy ở xưởng', 'Quản lý thiết bị', 'Máy đã bán', 'Bảo hành sửa chữa', 'Bảng giá sửa', 'Sang trang quản lý đầy đủ'):
        assert label in r.text, label
    assert 'href="http://testserver/"' in r.text
    assert 'Thiết bị của khách' not in r.text and 'Tồn kho' not in r.text
    main = client.get('/dich-vu')
    # Gọn trang chính (TUẤN duyệt 19/11/2026): Thiết bị chỉ còn trên menu Care; khối Trang khác đã ẩn (TUẤN 19/11).
    assert main.status_code == 200 and 'Thành Đức Care' not in main.text and 'Thiết bị của khách' not in main.text
    assert 'site-care' not in main.text and 'Trang khác' not in main.text
    assert client.get('/thiet-bi').status_code == 200  # link cũ trên tên miền chính vẫn mở


def test_care_menu_still_follows_roles(client, db, t3_people):
    service_login(client, t3_people['technician'])
    r = client.get('/thiet-bi', headers=CARE)
    assert r.status_code == 200 and 'Quản lý thiết bị' in r.text
    service_login(client, t3_people['finance'])
    r = client.get('/', headers=CARE, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/bang-gia?tab=goi'  # kế toán chỉ mở được Bảng giá trong menu Care
    r = client.get('/bang-gia', headers=CARE)
    assert 'Bảng giá sửa' in r.text and 'Phiếu sửa chữa' not in r.text and 'Máy đã bán' not in r.text


def test_outside_page_goes_to_main_domain_but_fetch_and_post_stay(client, db, t3_people):
    service_login(client, t3_people['dispatcher'])
    r = client.get('/khach-hang?q=minh', headers=PAGE, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == 'http://testserver/khach-hang?q=minh'
    r = client.get('/khach-hang', headers={**PAGE, 'x-forwarded-proto': 'https'}, follow_redirects=False)
    assert r.headers['location'] == 'https://testserver/khach-hang'
    # Ô tìm/fetch trong trang (không phải mở trang) không bị chuyển.
    r = client.get('/khach-hang', headers={'host': 'care.testserver', 'sec-fetch-dest': 'empty'}, follow_redirects=False)
    assert r.status_code == 200
    r = client.get('/dich-vu', headers=PAGE, follow_redirects=False)
    assert r.status_code == 200
    r = client.get('/', headers=PAGE, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/dich-vu'
    assert client.get('/dang-nhap', headers=PAGE, follow_redirects=False).status_code == 200
    assert client.get('/khach-hang', follow_redirects=False).status_code == 200  # tên miền chính giữ nguyên


def test_login_on_care_lands_on_care_home(client, db, t3_people):
    client.cookies.clear()
    r = client.get('/dang-nhap', headers=CARE)
    assert 'Thành Đức Care' in r.text
    from conftest import PASSWORD
    r = client.post('/dang-nhap', data={'username': t3_people['dispatcher'].username, 'password': PASSWORD, 'next': '/'},
                    headers={'host': 'care.testserver'}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/dich-vu'


def test_sold_machines_list_only_machines_we_sold(client, db, machine):
    p = machine
    o = order(db, p, 1)
    deliver(db, p, o, ['CARE-SOLD-1'])
    equipment.create(db, p['dispatcher'], EquipmentIn(serial='CARE-OWN-1', model_name='Máy khách tự mua', customer_code=o.customer_code))
    service_login(client, p['dispatcher'])
    r = client.get('/may-da-ban', headers=CARE)
    assert r.status_code == 200
    assert 'CARE-SOLD-1' in r.text and 'CARE-OWN-1' not in r.text
    assert 'Còn bảo hành' in r.text and o.number in r.text
    assert 'CARE-SOLD-1' in client.get('/may-da-ban?q=care-sold').text
    assert 'CARE-SOLD-1' not in client.get('/may-da-ban?q=khong-co').text
    assert 'CARE-SOLD-1' not in client.get('/may-da-ban?bh=het').text
    eq = db.get(Equipment, 'CARE-SOLD-1')
    eq.warranty_until = _today() + timedelta(days=10)
    db.commit()
    r = client.get('/may-da-ban?bh=sap')
    assert 'CARE-SOLD-1' in r.text and 'Còn 10 ngày' in r.text


def _done(db, p, days_ago, **changes):
    t = ticket(db, p, status='DA_DONG', **changes)
    db.add(ServiceStep(ticket_id=t.id, action='finish', from_status='DANG_LAM', to_status='HOAN_THANH', actor_id=p['dispatcher'].id,
                       at=datetime.now(UTC) - timedelta(days=days_ago)))
    db.commit()
    return t


def test_repair_warranty_tabs_and_take_back_button(client, db, t3_people):
    p = t3_people
    live = _done(db, p, 10, warranty_months=1, fix_done='Thay lô sấy', warranty_scope='lô sấy')
    expired = _done(db, p, 60, warranty_months=1, fix_done='Thay trục kéo')
    no_warranty = _done(db, p, 5, warranty_months=0, fix_done='Vệ sinh máy')
    back = _done(db, p, 20, warranty_months=3, fix_done='Thay đầu phun')
    from app import service_tickets
    child = service_tickets.create(db, p['dispatcher'], ServiceTicketIn(customer_code='KTD-000001', kind='BAO_HANH', symptom='Lại lỗi', warranty_of_id=back.id))
    service_login(client, p['dispatcher'])
    r = client.get('/bao-hanh-sua-chua', headers=CARE)
    assert r.status_code == 200
    assert live.number in r.text and 'Thay lô sấy' in r.text and 'BH: lô sấy' in r.text
    assert f'/dich-vu/moi?bao_hanh={live.id}' in r.text
    for t in (expired, no_warranty, back):
        assert t.number not in r.text
    r = client.get('/bao-hanh-sua-chua?tt=quay-lai')
    assert back.number in r.text and child.number in r.text and live.number not in r.text
    r = client.get('/bao-hanh-sua-chua?tt=het')
    assert expired.number in r.text and live.number not in r.text
    assert 'Thay lô sấy' in client.get('/bao-hanh-sua-chua?q=lo say').text
    # Nút Nhận máy bảo hành mở màn Nhận đơn có sẵn với phiếu gốc và loại Bảo hành.
    r = client.get(f'/dich-vu/moi?bao_hanh={live.id}')
    assert r.status_code == 200 and live.number in r.text
    assert 'name="warranty_of_id" value="%d"' % live.id in r.text
    # Phiếu chưa xong thì bỏ qua, mở form trống như thường.
    r = client.get(f'/dich-vu/moi?bao_hanh={child.id}')
    assert r.status_code == 200 and 'name="warranty_of_id" value=""' in r.text
    assert db.query(ServiceTicket).count() == 5  # chỉ xem, không tạo phiếu
