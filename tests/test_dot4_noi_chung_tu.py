"""Đợt 4 · nối chứng từ: trang tổng quan khách, Tồn kho Sắp hết → Lập đơn mua điền sẵn."""
import re
from datetime import date

from app import purchase_orders
from app.models import Customer, Quote, SalesOrder, ServiceTicket, StockMinLevel
from app.schemas import PurchaseOrderIn
from test_purchase_orders import purchase_people
from test_web import service_login, web_login


def customer_with_docs(db, user):
    db.add(Customer(code='KTD-000001', name='Công ty Phú Gia', kind='CONG_TY', search_text='ktd-000001 cong ty phu gia',
                    address='12 Cộng Hòa', allow_debt=True, credit_limit=10_000_000))
    db.flush()
    db.add_all([Quote(number='BG-26-00001', customer_code='KTD-000001', created_by=user.id, status='DA_GUI', subtotal=2_950_000, total=2_950_000,
                      valid_until=date(2099, 1, 1)),
                SalesOrder(number='DB-26-00001', customer_code='KTD-000001', created_by=user.id, status='DA_CHOT',
                           subtotal=500_000, total=500_000, receivable=500_000),
                ServiceTicket(number='DV-26-00001', customer_code='KTD-000001', created_by=user.id, status='MOI', symptom='Kẹt giấy')])
    db.commit()


def test_customer_overview_for_sales(client, db, web_login, make_user):
    web_login('d4.sales', 'sales')
    customer_with_docs(db, make_user('d4.owner', 'sales'))
    page = client.get('/khach-hang/KTD-000001').text
    # Nút tạo nhanh theo quyền: bán hàng không vào được Dịch vụ nên không có nút đó.
    assert 'href="/bao-gia/moi?customer_code=KTD-000001"' in page and 'href="/don-ban/moi?customer_code=KTD-000001"' in page
    assert '/dich-vu/moi?customer_code' not in page and 'DV-26-00001' not in page
    assert 'BG-26-00001' in page and 'Chờ khách trả lời' in page and 'DB-26-00001' in page
    assert 'href="/khach-hang/KTD-000001/sua"' in page and 'Sửa thông tin' in page
    # Công nợ chỉ kế toán, giám đốc thấy.
    assert 'id="cong-no"' not in page and 'Còn nợ' not in page
    tab = client.get('/khach-hang/KTD-000001?tab=bao-gia').text
    assert 'BG-26-00001' in tab and 'DB-26-00001' not in tab
    # Form sửa vẫn ở /sua và gửi về đúng địa chỉ cũ.
    edit = client.get('/khach-hang/KTD-000001/sua').text
    assert 'action="/khach-hang/KTD-000001"' in edit and 'name="name" value="Công ty Phú Gia"' in edit


def test_customer_overview_debt_for_finance_and_services_for_dispatcher(client, db, web_login, make_user):
    web_login('d4.finance', 'finance')
    customer_with_docs(db, make_user('d4.owner', 'sales'))
    page = client.get('/khach-hang/KTD-000001').text
    assert 'id="cong-no"' in page and '500.000 đ' in page and 'Hạn mức' in page and '/bao-cao/tai-chinh?khach=KTD-000001' in page
    assert '/dich-vu/moi?customer_code' not in page
    web_login('d4.dp', 'dispatcher')
    page = client.get('/khach-hang/KTD-000001').text
    assert 'href="/dich-vu/moi?customer_code=KTD-000001"' in page and 'DV-26-00001' in page and 'Giao thợ' in page
    assert 'BG-26-00001' not in page and 'DB-26-00001' not in page and 'id="cong-no"' not in page


def test_customer_overview_forbidden_for_technician(client, db, web_login, make_user):
    customer_with_docs(db, make_user('d4.owner', 'sales'))
    web_login('d4.tech', 'technician')
    assert client.get('/khach-hang/KTD-000001').status_code == 403
    assert client.get('/khach-hang/KTD-000001/sua').status_code == 403


def test_low_stock_incoming_need_and_prefilled_purchase(client, db, purchase_people):
    people = purchase_people
    order = purchase_orders.create(db, people['warehouse'], PurchaseOrderIn(supplier_code='NCC-0001', order_date=date.today(),
                                   lines=[dict(product_code='TD-0001', warehouse_code='K58', qty='2', unit_cost=100000)]))
    purchase_orders.order(db, people['warehouse'], order.id, order.version)
    db.add_all([StockMinLevel(warehouse_code='K58', product_code='TD-0001', min_qty=5, version=1, updated_by=people['warehouse'].id),
                StockMinLevel(warehouse_code='K58', product_code='TD-0002', min_qty=3, version=1, updated_by=people['warehouse'].id)])
    db.commit()
    service_login(client, people['warehouse'])
    page = client.get('/ton-kho?canh-bao=1').text
    assert '<dt>Đang về</dt><dd>2</dd>' in page and '<dt>Nên đặt</dt><dd>3</dd>' in page
    assert 'NCC lần trước: NCC-0001 · Minh Phát' in page and 'Chưa mua lần nào' in page
    assert 'href="/don-mua/moi?product_code=TD-0001&amp;kho=K58&amp;qty=3&amp;tu=sap-het"' in page and 'data-ls-bar' in page
    form = client.get('/don-mua/moi?product_code=TD-0001&kho=K58&qty=3&product_code=TD-0002&kho=K58&qty=3&tu=sap-het').text
    assert 'Điền sẵn từ Tồn kho › Sắp hết: 2 dòng' in form
    assert form.count('name="product_code" value="TD-0001"') == 1 and 'name="product_code" value="TD-0002"' in form
    # Hai mã khác NCC lần trước (TD-0002 chưa mua) nên không tự chọn NCC.
    assert 'name="supplier_code" value=""' in form
    one = client.get('/don-mua/moi?product_code=TD-0001&kho=K58&qty=3&tu=sap-het').text
    assert 'name="supplier_code" value="NCC-0001"' in one and re.search(r'name="qty"[^>]*value="3"', one)
    # Kế toán xem được Sắp hết? Không: chỉ kho; giám đốc thấy nút Lập đơn mua.
    service_login(client, people['director'])
    assert 'data-ls-bar' in client.get('/ton-kho?canh-bao=1').text


def test_work_queue_low_stock_opens_filtered_list(client, db, purchase_people):
    people = purchase_people
    db.add(StockMinLevel(warehouse_code='K58', product_code='TD-0001', min_qty=5, version=1, updated_by=people['warehouse'].id))
    db.commit()
    service_login(client, people['warehouse'])
    page = client.get('/viec-cua-toi').text
    assert 'href="/ton-kho?canh-bao=1&amp;kho=K58&amp;q=TD-0001"' in page and 'Lập đơn mua ›' in page
