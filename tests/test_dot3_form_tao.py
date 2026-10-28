"""Đợt 3 · khuôn 5: các form tạo mới (đơn mua, phiếu kho, thu chi, thêm khách, đầu trang nhận đơn dịch vụ)."""
import re

from sqlalchemy import select

from app.models import Customer, PurchaseOrder, StockBalance, StockDoc
from test_cash import cash_people
from test_purchase_orders import purchase_people
from test_web import csrf_of, customer_form, service_login, stock_catalog, web_login


def purchase_form(csrf, **changes):
    data = dict(csrf=csrf, supplier_code='NCC-0001', order_date='2026-10-18', expected_date='2026-10-25', note='',
                product_code='TD-0001', qty='2', unit_cost='120.000', warehouse_0='K58', vat_0='8')
    data.update(changes)
    return data


def test_purchase_compose_search_save_and_order_in_one_click(client, db, purchase_people):
    service_login(client, purchase_people['warehouse'])
    page = client.get('/don-mua/moi').text
    csrf = csrf_of(page)
    assert 'data-po-primary>Lưu và đặt hàng</button>' in page and 'Chỉ lưu nháp' in page
    found = client.get('/don-mua/san-pham?q=may in').text
    assert 'data-po-add' in found and 'data-code="TD-0001"' in found and 'Chưa mua lần nào' in found
    r = client.post('/don-mua/moi', data=purchase_form(csrf, then='order'), follow_redirects=False)
    assert r.status_code == 303 and 'da_dat=1' in r.headers['location']
    order = db.scalar(select(PurchaseOrder))
    assert order.status == 'DA_DAT' and order.total == 259200
    assert 'Đã đặt hàng' in client.get(r.headers['location']).text
    # Chỉ lưu nháp: vẫn là nháp.
    client.post('/don-mua/moi', data=purchase_form(csrf, then=''))
    db.expire_all()
    assert sorted(o.status for o in db.scalars(select(PurchaseOrder))) == ['DA_DAT', 'NHAP']


def test_purchase_errors_point_at_the_field_and_keep_input(client, db, purchase_people):
    service_login(client, purchase_people['warehouse'])
    csrf = csrf_of(client.get('/don-mua/moi').text)
    r = client.post('/don-mua/moi', data=purchase_form(csrf, supplier_code='NCC-KHONG', then='order'))
    assert r.status_code >= 400 and 'Chưa lưu được.' in r.text
    assert re.search(r'po-supplier[^"]*field-invalid', r.text) and 'value="120.000"' in r.text
    r = client.post('/don-mua/moi', data=purchase_form(csrf, product_code='TD-KHONG'))
    assert r.status_code >= 400 and 'pol-bad' in r.text
    assert db.scalar(select(PurchaseOrder)) is None


def test_stock_doc_compose_pickers_and_field_errors(client, db, web_login, stock_catalog):
    db.add(StockBalance(warehouse_code='K58', product_code='TD-0001', qty=7, value=0, avg_cost=0))
    db.commit()
    csrf = web_login('d3.kho', 'warehouse')
    page = client.get('/phieu-kho/moi').text
    # Bắt đầu bằng ô tìm hàng, không có dòng rỗng (dòng duy nhất là khuôn trong <template>).
    assert page.count('data-stock-line ') == 1 and 'data-sk-q' in page and 'data-sk-primary' in page
    # Nhập nhanh như đơn bán: hàng phím tắt, Enter ở Số lượng/Đơn giá là "tiếp".
    assert 'data-sk-keys' in page and 'Enter thêm' in page
    assert re.search(r'<input name="qty"[^>]*enterkeyhint="next"', page) and re.search(r'<input name="unit_cost"[^>]*enterkeyhint="next"', page)
    assert '<select name="supplier_code"' not in page and 'data-source="/phieu-kho/tim-ncc"' in page
    found = client.get('/phieu-kho/san-pham?q=muc&warehouse=K58').text
    assert 'data-sk-add' in found and 'Kho K58: còn 7' in found
    assert 'data-pick-party="NCC-0001"' in client.get('/phieu-kho/tim-ncc').text
    db.add(Customer(code='KTD-000001', name='Khách A', search_text='ktd-000001 khach a'))
    db.commit()
    assert 'data-pick-party="KTD-000001"' in client.get('/phieu-kho/tim-khach?q=khach').text
    data = dict(csrf=csrf, kind='PN', warehouse_code='K58', doc_date='2026-10-17', supplier_code='NCC-KHONG',
                reason='Nhập', note='', product_code=['TD-0001'], qty=['2'], unit_cost=['120.000'], serial=[''], line_note=[''])
    r = client.post('/phieu-kho/moi', data=data)
    assert r.status_code >= 400 and 'Chưa lưu được.' in r.text and re.search(r'sk-party field-invalid', r.text)
    r = client.post('/phieu-kho/moi', data={**data, 'supplier_code': 'NCC-0001', 'unit_cost': ['12,5']})
    assert r.status_code >= 400 and 'skl pol-bad' in r.text
    assert db.scalar(select(StockDoc)) is None
    r = client.post('/phieu-kho/moi', data={**data, 'supplier_code': 'NCC-0001', 'action': 'post'}, follow_redirects=False)
    assert r.status_code == 303 and db.scalar(select(StockDoc)).status == 'DA_GHI'


def tc_form(csrf, **changes):
    data = {'csrf': csrf, 'kind': 'PT', 'doc_date': '2026-10-10', 'fund_code': 'QUY-TM', 'category_code': 'THU-BH',
            'amount': '1.000.000', 'partner_name': 'An', 'description': 'Thu thử', 'action': 'save'}
    data.update(changes)
    return data


def test_cash_form_one_column_party_toggle_and_field_errors(client, cash_people):
    csrf = service_login(client, cash_people['finance'])
    page = client.get('/thu-chi/moi').text
    assert 'data-tc-party' in page and 'data-tc-day="-1"' in page and 'data-tc-primary>Ghi sổ</button>' in page
    # Chỉ một ô đối tượng mở: khách hàng mặc định cho phiếu thu, ô NCC bị khóa.
    assert re.search(r'name="supplier_code"[^>]*disabled', page) and not re.search(r'name="customer_code"[^>]*disabled', page)
    assert re.search(r'value="khac"\s+checked', client.get('/thu-chi/moi?loai=PC').text)
    r = client.post('/thu-chi/moi', data=tc_form(csrf, category_code='KHONG-CO'))
    assert r.status_code == 400 and 'Chưa lưu được.' in r.text and 'tc-pick field-invalid' in r.text
    r = client.post('/thu-chi/moi', data=tc_form(csrf, amount='12,5'))
    assert r.status_code == 400 and 'tc-amount field-invalid' in r.text and 'id="loi-amount"' in r.text
    r = client.post('/thu-chi/moi', data=tc_form(csrf), follow_redirects=False)
    assert r.status_code == 303
    saved = client.get(r.headers['location']).text
    assert 'THU-BH · Thu bán hàng' in saved and 'data-picked="1"' in saved


def test_customer_form_cards_and_duplicate_under_phone(client, web_login):
    csrf = web_login('d3.sales', 'sales')
    page = client.get('/khach-hang/moi', params={'quay_ve': '/don-ban/moi'}).text
    assert 'Khách là ai' in page and 'Địa chỉ và liên hệ' in page and 'Lưu và quay lại đơn' in page
    assert 'Đang tạo cho Đơn bán' in page and 'data-kh-credit' not in page
    client.post('/khach-hang/moi', data=customer_form(csrf))
    r = client.post('/khach-hang/moi', data=customer_form(csrf, name='Khách trùng', quay_ve='/don-ban/moi'))
    assert r.status_code == 409 and 'Chưa lưu được.' in r.text
    assert re.search(r'<label class=" field-invalid">Số điện thoại', r.text)
    assert 'href="/don-ban/moi?customer_code=KTD-000001"' in r.text and 'Mở khách cũ' in r.text
    web_login('d3.finance', 'finance')
    page = client.get('/khach-hang/moi').text
    assert 'data-kh-credit' in page and 'Không cho nợ' in page and '>Lưu khách</button>' in page


def test_service_intake_head_matches_quote(client, web_login):
    web_login('d3.dp', 'dispatcher')
    page = client.get('/dich-vu/moi').text
    assert 'class="page-head"' in page and 'Dịch vụ · phiếu mới' in page and 'steps-bar' in page
    assert 'Khách hàng (tìm và chọn)' in page and 'Có thể tìm bằng tên, mã khách hàng hoặc số điện thoại.' in page


def test_purchase_compose_has_keyboard_entry_like_sales(client, db, purchase_people):
    service_login(client, purchase_people['warehouse'])
    page = client.get('/don-mua/moi').text
    assert 'data-po-keys' in page and 'Enter thêm' in page
    assert re.search(r'<input name="qty"[^>]*enterkeyhint="next"', page) and re.search(r'<input name="unit_cost"[^>]*enterkeyhint="next"', page)


def test_quote_compose_has_keyboard_entry_like_sales(client, web_login, stock_catalog):
    web_login('d3.kd', 'sales')
    page = client.get('/bao-gia/moi').text
    assert 'data-browse-keys' in page and 'Enter thêm' in page
    assert re.search(r'<input name="qty"[^>]*enterkeyhint="next"', page) and re.search(r'<input name="unit_price"[^>]*enterkeyhint="next"', page)
