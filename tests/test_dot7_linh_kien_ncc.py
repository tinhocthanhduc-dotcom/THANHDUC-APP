"""Đợt 7: Yêu cầu linh kiện và Nhà cung cấp theo khuôn 3, trang tổng quan một NCC. Chỉ giao diện."""
from datetime import date

from app import part_requests as svc, purchase_orders
from app.models import Supplier
from app.schemas import PartRequestIn, PurchaseOrderIn
from test_my_work import people, purchase_people, ticket
from test_technician_stock import held
from test_web import service_login


def ask(db, p, t, qty=2):
    return svc.create(db, p['technician'], t.id, PartRequestIn(version=t.version, product_code='TD-0001', qty=qty, reason='Thiếu drum'))


def test_queue_tiles_stock_buttons_filters_and_empty(client, db, held):
    p, a, b = held
    service_login(client, p['warehouse'])
    empty = client.get('/yeu-cau-linh-kien').text
    assert 'Không có yêu cầu nào đang chờ cấp' in empty and 'href="/yeu-cau-linh-kien?status=DA_CAP"' in empty
    r = ask(db, p, ticket(db, p, 'DANG_LAM'))
    page = client.get('/yeu-cau-linh-kien').text
    assert r.number in page and 'Cấp được ngay' in page and 'Thiếu hàng' in page and 'Đã cấp hôm nay' in page
    # Nút cấp chỉ ở kho đủ hàng (K58 tồn 10); tồn từng kho tô xanh/đỏ.
    assert 'value="K58" class="btn-ok">Cấp từ K58' in page and 'ps-ok' in page and 'Báo hết hàng' in page
    assert r.number in client.get('/yeu-cau-linh-kien?ton=du').text
    assert r.number not in client.get('/yeu-cau-linh-kien?ton=thieu').text
    assert r.number in client.get('/yeu-cau-linh-kien?q=may in').text
    nothing = client.get('/yeu-cau-linh-kien?q=khongcogi').text
    assert r.number not in nothing and 'Xóa lọc' in nothing
    assert r.number in client.get('/yeu-cau-linh-kien?loai=phieu').text
    assert r.number not in client.get('/yeu-cau-linh-kien?loai=mang-theo').text
    assert r.number in client.get('/yeu-cau-linh-kien?status=tat-ca').text


def test_queue_supply_message_names_ck(client, db, held):
    p, a, b = held
    r = ask(db, p, ticket(db, p, 'DANG_LAM'))
    csrf = service_login(client, p['warehouse'])
    page = client.post(f'/yeu-cau-linh-kien/{r.id}/cap', data=dict(csrf=csrf, version=r.version, warehouse_code='K58')).text
    assert f'Đã cấp {r.number} từ K58' in page and 'CK-26-' in page
    done = client.get('/yeu-cau-linh-kien?status=DA_CAP').text
    assert r.number in done and 'Đã cấp từ K58' in done and '/phieu-kho/' in done


def order(db, p, **kw):
    o = purchase_orders.create(db, p['warehouse'], PurchaseOrderIn(supplier_code='NCC-0001', order_date=date.today(),
                               lines=[dict(product_code='TD-0001', warehouse_code='K58', qty='2', unit_cost=100000)], **kw))
    return o


def test_supplier_list_overview_and_edit(client, db, purchase_people):
    p = purchase_people
    db.add(Supplier(code='NCC-0002', name='Hưng Phát', search_text='ncc-0002 hung phat', active=False))
    db.commit()
    o = order(db, p)
    purchase_orders.order(db, p['warehouse'], o.id, o.version)
    order(db, p)  # nháp
    service_login(client, p['warehouse'])
    page = client.get('/nha-cung-cap').text
    assert 'Có hàng đang về' in page and '1 đơn' in page and 'Ngừng sử dụng' in page and 'Còn nợ' not in page
    assert 'NCC-0001' in page and 'NCC-0002' not in page and 'NCC-0002' in client.get('/nha-cung-cap?tt=ngung').text
    assert 'NCC-0001' in client.get('/nha-cung-cap?ve=1').text
    view = client.get('/nha-cung-cap/NCC-0001').text
    assert o.number in view and 'Nhận hàng' in view and 'Kiểm tra rồi Đặt hàng' in view
    assert 'href="/don-mua/moi?supplier_code=NCC-0001"' in view and 'href="/nha-cung-cap/NCC-0001/sua"' in view and 'id="cong-no"' not in view
    form = client.get('/don-mua/moi?supplier_code=NCC-0001').text
    assert 'name="supplier_code" value="NCC-0001"' in form
    edit = client.get('/nha-cung-cap/NCC-0001/sua').text
    assert 'name="name" value="Minh Phát"' in edit and 'action="/nha-cung-cap/NCC-0001"' in edit
    service_login(client, p['finance'])
    page = client.get('/nha-cung-cap').text
    assert 'Đang còn nợ' in page and 'Còn nợ' in page
    view = client.get('/nha-cung-cap/NCC-0001').text
    assert 'id="cong-no"' in view and '/cong-no-ncc/NCC-0001' in view and 'href="/don-mua/moi?supplier_code' not in view
    service_login(client, p['sales'])
    assert client.get('/nha-cung-cap/NCC-0001').status_code == 403
    assert client.get('/nha-cung-cap/NCC-0001/sua').status_code == 403
