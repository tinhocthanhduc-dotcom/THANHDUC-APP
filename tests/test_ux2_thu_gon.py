"""UX2 preserves POST contracts; summaries and defaults are display-only."""
import re
from datetime import datetime, UTC, timedelta

from sqlalchemy import event

from app import quotes, sales_orders
from app.models import Customer, CustomerContact, CustomerPhone, Equipment, CustomerSite, Warehouse
from app.schemas import QuoteIn
from app.web.picked import last_prices
from test_sales_orders import people, stocked, body, GOOD
from test_web import web_login, service_people, service_login, b1_form


def contact(db):
    customer=db.get(Customer,'KTD-000001')
    customer.address='58 Mã Lò'
    customer.contacts=[CustomerContact(name='Chị Hạnh',phones=['0901234567'])]
    customer.phones=[CustomerPhone(phone='0901234567',is_contact=True)]
    db.commit()


def test_quote_customer_card_defaults_permissions_and_locked(client,db,stocked):
    contact(db)
    q=quotes.create(db,stocked['sales'],QuoteIn(customer_code='KTD-000001',lines=[dict(description='Lắp đặt',qty=1,unit_price=500)]))
    service_login(client,stocked['sales'])
    html=client.get(f'/bao-gia/{q.id}').text
    assert 'data-customer-card' in html and 'KTD-000001 · Minh Phát' in html
    assert '0901234567 · 58 Mã Lò' in html and 'data-picked-edit>Sửa' in html
    assert 'name="customer_code" value="KTD-000001"' in html
    assert 'name="contact_name" value="Chị Hạnh"' in html and 'Còn nợ' not in html
    assert 'data-line-summary' in html and 'data-line-detail' in html
    service_login(client,stocked['finance'])
    assert 'Còn nợ' in client.get(f'/bao-gia/{q.id}').text
    quotes.send(db,stocked['sales'],q.id,q.version)
    service_login(client,stocked['sales'])
    assert 'data-picked-edit' not in client.get(f'/bao-gia/{q.id}').text


def test_order_customer_fixed_and_preserves_contact(client,db,stocked):
    contact(db)
    order=sales_orders.create(db,stocked['sales'],body(contact_name='Anh Tuấn',fulfillment='THO_GIAO'))
    service_login(client,stocked['sales'])
    html=client.get(f'/don-ban/{order.id}').text
    customer_block=html.split('<section data-picked',1)[1].split('</section>',1)[0]
    assert 'Cố định sau khi tạo đơn' in customer_block and 'data-picked-edit' not in customer_block
    assert 'name="contact_name" value="Anh Tuấn"' in html
    assert '58 Mã Lò</textarea>' in html
    assert 'data-line-summary' in html and 'data-line-detail' in html


def test_new_order_warehouse_default_active_only(client,db,stocked,monkeypatch):
    from app.config import get_settings
    service_login(client,stocked['sales'])
    db.add(Warehouse(code='58',name='Kho mặc định'));db.commit()
    html=client.get('/don-ban/moi').text
    assert 'value="58" checked data-order-warehouse' in html
    monkeypatch.setattr(get_settings(),'sales_default_warehouse','MISSING')
    html=client.get('/don-ban/moi').text
    assert 'value="58" checked data-order-warehouse' not in html
    assert 'value="" checked data-order-warehouse' in html
    monkeypatch.setattr(get_settings(),'sales_default_warehouse','K00')
    assert 'value="K00" checked data-order-warehouse' not in client.get('/don-ban/moi').text


def test_service_single_machine_and_site_defaults_and_none(client,db,service_people):
    contact(db)
    csrf=service_login(client,service_people['dispatcher'])
    assert 'data-phone="0901234567"' in client.get('/dich-vu/tim-khach?q=minh').text
    html=client.get('/dich-vu/chon-khach?customer_code=KTD-000001').text
    site=db.query(CustomerSite).filter_by(customer_code='KTD-000001').one()
    assert f'<option value="{site.id}" data-address="{site.address}" selected>' in html
    assert '<option value="AB123" selected>' in html
    db.add(Equipment(serial='SECOND',product_code='TD-0001',customer_code='KTD-000001',created_by=service_people['dispatcher'].id))
    db.add(CustomerSite(customer_code='KTD-000001',name='Chi nhánh',created_by=service_people['dispatcher'].id));db.commit()
    html=client.get('/dich-vu/chon-khach?customer_code=KTD-000001').text
    assert '<option value="" selected>— Không chọn máy —' in html and re.search(r'<option value="" data-address="[^"]*" selected>Địa chỉ chính', html)
    response=client.post('/dich-vu/moi',data=dict(csrf=csrf,customer_code='KTD-000001',site_id='',serial='',kind='SUA_TAI_CHO',priority='THUONG',symptom='Máy lỗi'))
    assert response.status_code==400
    assert 'KTD-000001 · Minh Phát' in response.text and '0901234567 · 58 Mã Lò' in response.text
    assert '<option value="" selected>— Không chọn máy —' in response.text


def test_last_price_only_confirmed_non_cancelled_one_query(client,db,stocked):
    user=stocked['sales']
    first=sales_orders.create(db,user,body(lines=[GOOD|dict(qty=1,unit_price=450000)]))
    sales_orders.confirm(db,user,first.id,first.version)
    first.confirmed_at=datetime(2026,9,21,tzinfo=UTC);db.commit()
    sales_orders.create(db,user,body(lines=[GOOD|dict(qty=1,unit_price=300000)]))
    cancelled=sales_orders.create(db,user,body(lines=[GOOD|dict(qty=1,unit_price=200000)]))
    sales_orders.confirm(db,user,cancelled.id,cancelled.version)
    sales_orders.cancel(db,user,cancelled.id,'Khách đổi ý',cancelled.version)
    service_login(client,user)
    for path in ['/bao-gia/tim-hang','/don-ban/tim-hang']:
        assert 'data-last-price="450000"' in client.get(path+'?q=TD-0001&customer_code=KTD-000001').text
        assert 'data-last-price' not in client.get(path+'?q=TD-0001&customer_code=KTD-000009').text
    latest=sales_orders.create(db,user,body(lines=[GOOD|dict(qty=1,unit_price=470000)]))
    sales_orders.confirm(db,user,latest.id,latest.version)
    assert 'data-last-price="470000"' in client.get('/don-ban/tim-hang?q=TD-0001&customer_code=KTD-000001').text
    from app.models import Product
    products=[db.get(Product,'TD-0001'),db.get(Product,'TD-0002')]
    statements=[]
    def collect(*args): statements.append(args[2])
    event.listen(db.bind,'before_cursor_execute',collect)
    try: result=last_prices(db,'KTD-000001',products)
    finally: event.remove(db.bind,'before_cursor_execute',collect)
    assert len(statements)==1 and result['TD-0001']['price']==470000


def test_error_lines_open_only_invalid_and_keep_entered_contact(client,db,stocked):
    contact(db)
    csrf=service_login(client,stocked['sales'])
    response=client.post('/bao-gia/moi',data=b1_form(csrf,contact_name='Anh nhập trước',product_code=['',''],description=['Đúng','Sai'],qty=['1','0'],unit=['lần','lần'],unit_price=['100','200'],vat_0='8',vat_1='8'))
    assert response.status_code==400
    html=response.text
    classes=re.findall(r'<fieldset class="card quote-line ([^"]+)"',html)
    assert classes[:2]==['line-collapsed','line-open line-error']
    assert 'name="contact_name" value="Anh nhập trước"' in html


def test_pick_customer_metadata_respects_finance_permission(client,db,stocked):
    contact(db)
    for role in ['sales','finance']:
        service_login(client,stocked[role])
        for path in ['/bao-gia/tim-khach','/don-ban/tim-khach']:
            html=client.get(path+'?q=minh').text
            assert 'data-phone="0901234567"' in html and 'data-address="58 Mã Lò"' in html
            assert 'data-contact="Chị Hạnh"' in html
            assert ('data-debt=' in html)==(role=='finance')


def test_delivered_order_does_not_mark_consumed_stock_as_line_error(client,db,stocked):
    order=sales_orders.create(db,stocked['sales'],body(lines=[GOOD|dict(qty=5)]))
    sales_orders.confirm(db,stocked['sales'],order.id,order.version)
    sales_orders.deliver(db,stocked['warehouse'],order.id,order.version)
    service_login(client,stocked['sales'])
    html=client.get(f'/don-ban/{order.id}').text
    # Đợt 1b: đơn đã giao mở khung chỉ đọc; dòng hàng không bị đánh lỗi, tiến độ ghi đã giao đủ.
    assert 'data-doc-view="order"' in html and 'Đã giao 5/5' in html and 'line-error' not in html


def test_bad_product_marks_only_affected_line():
    from app.web.picked import line_errors
    assert line_errors([
        dict(product_code=None,description='Công sửa',qty='1',unit_price=500),
        dict(product_code='TD-0999',description='',qty='1',unit_price=500),
    ], 'Mã hàng không tồn tại: TD-0999', 'BAD_PRODUCT')=={1}
