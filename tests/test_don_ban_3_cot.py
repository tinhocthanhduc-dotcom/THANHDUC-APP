"""Màn tạo đơn bán 3 cột: cột tra giá chỉ đọc danh mục + tồn; form vẫn gửi đúng các ô cũ."""
import re

from test_sales_orders import people, stocked
from test_web import service_login


def test_browse_shows_price_and_stock_of_chosen_warehouse(client,db,stocked):
    service_login(client,stocked['sales'])
    html=client.get('/don-ban/san-pham?q=muc&warehouse=K58').text
    assert 'data-browse-add' in html and 'data-code="TD-0001"' in html and 'data-price="120000"' in html
    assert 'Còn 5 ở kho K58' in html and 'data-available="5' in html
    assert 'TD-0002' not in html
    assert 'Hết hàng ở kho K145' in client.get('/don-ban/san-pham?q=muc&warehouse=K145').text
    assert 'Còn' not in client.get('/don-ban/san-pham?q=muc').text


def test_new_order_form_has_three_columns_and_keeps_fields(client,db,stocked):
    service_login(client,stocked['sales'])
    html=client.get('/don-ban/moi').text
    for part in ('data-browse','qf-main','qf-side','data-order-wh-all','data-order-vat-all','data-promise-days',
                 'name="customer_code"','name="contact_name"','name="delivery_address"','name="promised_date"','name="note"',
                 'name="fulfillment"','name="payment_term"','id="order-line-template"'):
        assert part in html
    assert '<select' not in html
    # Ô tìm của cột tra giá không có name, không lẫn vào dữ liệu gửi lên.
    assert re.search(r'<input type="search" data-browse-q[^>]*>',html) and 'name="q"' not in html


def test_product_search_matches_words_in_any_order_and_ranks_exact_code_first(db):
    from app import products
    from app.models import Product
    from app.text import fold
    for code,name in [('TD-0107','Mực HP 107A'),('TD-0731','Máy in HP M107a'),('TD-1070','Giấy in 1070'),('TD-0010','Mực HP 10A')]:
        db.add(Product(code=code,name=name,unit='cái',sale_price=1,search_text=fold(f'{code} {name}')))
    db.commit()
    assert [p.code for p in products.search(db,'107 hp')]==['TD-0107','TD-0731']
    # Mã trùng đúng → mã bắt đầu bằng chữ gõ → từ trong tên bắt đầu bằng chữ gõ → chỉ chứa ở giữa.
    assert [p.code for p in products.search(db,'td-0107')][0]=='TD-0107'
    assert [p.code for p in products.search(db,'107')]==['TD-0107','TD-1070','TD-0731']
    assert [p.code for p in products.search(db,'muc hp')]==['TD-0010','TD-0107']


def test_new_order_form_has_keyboard_entry_hints(client,db,stocked):
    service_login(client,stocked['sales'])
    html=client.get('/don-ban/moi').text
    assert 'data-browse-keys' in html and 'Enter thêm' in html
    assert 'name="qty"' in html and 'enterkeyhint="next"' in html
