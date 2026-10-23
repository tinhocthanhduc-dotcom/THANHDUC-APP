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
