"""Đợt 2b: Khách hàng, Hàng hóa, Thiết bị, Nhân sự, Linh kiện thu hồi dùng khuôn danh sách (đủ mọi dòng, 25 dòng/trang)."""
from datetime import date, timedelta

from app import employees
from app.models import Customer, Equipment, Product, StockBalance, Warehouse
from test_dot2_danh_sach import tab
from test_ns1_employees import body, p, salary
from test_tk_tai_khoan import web_login


def customers(db, n):
    db.add_all([Customer(code=f'KTD-{i:06d}', name=f'Khách số {i}', kind='CONG_TY' if i % 2 else 'CA_NHAN',
                         search_text=f'ktd-{i:06d} khach so {i}') for i in range(1, n + 1)])
    db.commit()


def test_customer_list_shows_every_customer_25_per_page_with_tabs(db, client, make_user):
    customers(db, 60)
    db.get(Customer, 'KTD-000001').status = 'NGUNG'
    db.commit()
    web_login(client, make_user('d2b.sales', 'sales'))
    page = client.get('/khach-hang').text
    assert page.count('data-customer-row=') == 25 and 'Hiện <b>1–25</b> / 59 khách' in page
    assert tab(page, 'Đang dùng') == 59 and tab(page, 'Ngừng giao dịch') == 1 and tab(page, 'Tất cả') == 60
    assert 'Còn nợ · hạn mức' not in page and 'Đang còn nợ' not in page
    last = client.get('/khach-hang?trang=3').text
    assert last.count('data-customer-row=') == 9
    companies = client.get('/khach-hang?loai=CONG_TY').text
    assert 'data-customer-row="KTD-000003"' in companies and 'data-customer-row="KTD-000002"' not in companies
    stopped = client.get('/khach-hang?trang_thai=NGUNG').text
    assert 'data-customer-row="KTD-000001"' in stopped and stopped.count('data-customer-row=') == 1
    # Ô tìm trực tiếp ở form khác vẫn trả phần dòng gọn.
    assert 'Khách số 42' in client.get('/khach-hang?q=khach+so+42&phan=1').text


def test_customer_debt_column_only_for_finance(db, client, make_user):
    customers(db, 2)
    web_login(client, make_user('d2b.finance', 'finance'))
    page = client.get('/khach-hang').text
    assert 'Còn nợ · hạn mức' in page and 'Đang còn nợ' in page and 'Còn nợ <b>0</b> đ' in page
    assert client.get('/khach-hang?no=1').text.count('data-customer-row=') == 0


def test_product_list_no_cap_stock_states_and_stopped_tab(db, client, make_user):
    db.add_all([Warehouse(code='K58', name='Kho 58')] + [Product(code=f'TD-{i:04d}', name=f'Hàng {i}', search_text=f'td-{i:04d} hang {i}',
                active=i != 70) for i in range(1, 71)])
    db.commit()
    db.add_all([StockBalance(warehouse_code='K58', product_code='TD-0001', qty=0, value=0, avg_cost=0),
                StockBalance(warehouse_code='K58', product_code='TD-0002', qty=5, value=0, avg_cost=0)])
    db.commit()
    web_login(client, make_user('d2b.kho', 'warehouse'))
    page = client.get('/hang-hoa').text
    assert page.count('data-product-row=') == 25 and 'Hiện <b>1–25</b> / 69 mã hàng' in page
    assert tab(page, 'Đang bán') == 69 and tab(page, 'Ngừng bán') == 1
    assert client.get('/hang-hoa?trang=3').text.count('data-product-row=') == 19
    empty = client.get('/hang-hoa?ton=het').text
    assert 'data-product-row="TD-0001"' in empty and 'data-product-row="TD-0002"' not in empty and 'lt-alert' in empty
    assert 'data-product-row="TD-0070"' in client.get('/hang-hoa?tt=ngung').text
    assert 'data-product-row="TD-0070"' in client.get('/hang-hoa?tat_ca=1&q=hang+70').text
    assert 'Hàng 7' in client.get('/hang-hoa?q=hang+7&phan=1').text


def test_device_list_no_cap_and_warranty_soon(db, client, make_user):
    db.add(Product(code='TD-0001', name='Máy in', search_text='td-0001 may in'))
    db.commit()
    today, user = date.today(), make_user('d2b.dp', 'dispatcher')
    db.add_all([Equipment(serial=f'SN{i:03d}', product_code='TD-0001', search_text=f'sn{i:03d}', created_by=user.id,
                          warranty_until=today + timedelta(days=10 if i == 1 else 400)) for i in range(1, 56)])
    db.commit()
    web_login(client, user)
    page = client.get('/thiet-bi').text
    assert page.count('data-device-row=') == 25 and 'Hiện <b>1–25</b> / 55 thiết bị' in page
    soon = client.get('/thiet-bi?bh=sap').text
    assert soon.count('data-device-row=') == 1 and 'lt-alert' in soon and 'Còn 10 ngày' in soon
    assert client.get('/thiet-bi?bh=con').text.count('data-device-row=') == 25
    assert client.get('/thiet-bi?o=CHUA_GAN&trang=3').text.count('data-device-row=') == 5


def test_employee_list_tabs_gaps_and_salary_only_director(db, client, p):
    e = employees.create(db, p['admin'], p['technician'].id, body())
    employees.create(db, p['admin'], p['sales'].id, {**body(), 'position': 'Kinh doanh', 'id_number': '079091000001', 'end_date': '2026-06-01'})
    employees.add_salary(db, p['director'], e.id, salary())
    web_login(client, p['director'])
    page = client.get('/nhan-su').text
    assert tab(page, 'Đang làm') == 1 and tab(page, 'Đã nghỉ') == 1 and 'Lương hiện tại' in page
    assert f'data-employee-row="{e.id}"' in page and 'Thiếu CCCD' in page and 'lt-alert' in page
    left = client.get('/nhan-su?status=left').text
    assert f'data-employee-row="{e.id}"' not in left and left.count('data-employee-row=') == 1
    assert client.get('/nhan-su?thieu=1').text.count('data-employee-row=') == 1
    web_login(client, p['admin'])
    page = client.get('/nhan-su').text
    assert 'Lương hiện tại' not in page and 'Thiếu lương' not in page and 'Thiếu CCCD' in page
