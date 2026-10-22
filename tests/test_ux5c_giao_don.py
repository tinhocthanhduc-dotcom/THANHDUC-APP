"""UX5c: thợ giao đơn theo bước; số lượng và tiền cần thu điền sẵn."""
import re

from app.models import SalesDelivery, SalesReceipt
from test_stock_reservations import people, purchase_people, stock, order  # noqa: F401
from test_technician_deliveries import setup, assigned, move  # noqa: F401
from test_web import d5_image, service_login
from web_forms import Forms


def deliver_form(client, o):
    page = client.get(f'/giao-don/{o.id}?return_to=tho')
    assert page.status_code == 200 and 'data-tho-delivery' in page.text
    form = next(f for f in Forms(page.text, page.url).forms if f['path'].endswith('/deliver'))['fields']
    # Bộ đọc form của test không biết radio nào đang chọn: lấy theo dấu checked trong HTML.
    for name in ('collect', 'method'):
        form[name] = re.search(rf'name="{name}" value="(\w+)" checked', page.text).group(1)
    return page, form


def test_prefilled_qty_and_amount_follow_held_stock(db, setup, client):
    p = setup; o = assigned(db, p); move(db, p, o, 2)  # đặt 4 × 200đ, thợ mới giữ 2
    service_login(client, p['technician'])
    page, form = deliver_form(client, o)
    # Số giao mặc định = số thợ đang giữ; tiền = 2 × 200 (không lấy cả đơn 800).
    assert form['qty_1'] == '2' and form['amount'] == '400' and form['collect'] == 'YES' and form['method'] == 'TIEN_MAT'
    assert 'data-gross="200.0"' in page.text and 'class="tho-tabs"' not in page.text and 'class="tho-back" href="/tho"' in page.text
    assert re.search(r'data-swipe data-tone="ok"><button[^>]*form="giao-xong"', page.text)
    # Không giao được nằm trong khung riêng, lý do là chip.
    # TUẤN 2026-10-23: chỉ còn 3 lựa chọn Khách hẹn lại / Sai địa chỉ giao / Hủy đơn.
    assert 'id="viec-khac"' in page.text and re.findall(r'name="reason" value="(\w+)"', page.text) == ['HEN_LAI', 'SAI_DIA_CHI', 'DE_NGHI_HUY']


def test_full_delivery_collects_remaining_balance(db, setup, client):
    p = setup; o = assigned(db, p); move(db, p, o, 4)
    service_login(client, p['technician'])
    page, form = deliver_form(client, o)
    assert form['qty_1'] == '4' and form['amount'] == '800'
    r = client.post(f'/giao-don/{o.id}/deliver', data=form, files={'photos': ('g.png', d5_image(), 'image/png')}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/tho'
    receipt = db.query(SalesReceipt).one()
    assert receipt.amount == 800 and receipt.method == 'TIEN_MAT' and receipt.order_id == o.id
    assert db.query(SalesDelivery).count() == 1


def test_no_collection_ignores_prefilled_amount(db, setup, client):
    p = setup; o = assigned(db, p); move(db, p, o, 4)
    service_login(client, p['technician'])
    page, form = deliver_form(client, o)
    form.update(collect='NO')
    r = client.post(f'/giao-don/{o.id}/deliver', data=form, files={'photos': ('g.png', d5_image(), 'image/png')}, follow_redirects=False)
    assert r.status_code == 303 and db.query(SalesReceipt).count() == 0 and db.query(SalesDelivery).count() == 1


def test_error_keeps_typed_amount_and_money_list_links_to_technician_screens(db, setup, client):
    p = setup; o = assigned(db, p); move(db, p, o, 2)
    service_login(client, p['technician'])
    page, form = deliver_form(client, o)
    form.update(qty_1='3', amount='350')
    r = client.post(f'/giao-don/{o.id}/deliver', data=form, files={'photos': ('g.png', d5_image(), 'image/png')})
    assert r.status_code == 409 and 'data-tho-delivery' in r.text and 'value="350"' in r.text and 'data-manual' in r.text
    page, form = deliver_form(client, o)
    form.update(amount='300')
    assert client.post(f'/giao-don/{o.id}/deliver', data=form, files={'photos': ('g.png', d5_image(), 'image/png')}, follow_redirects=False).status_code == 303
    money = client.get('/tien-toi-giu').text
    assert f'href="/giao-don/{o.id}?return_to=tho"' in money and 'tho-money-row' in money and '300' in money


def test_photo_is_big_camera_button(db, setup, client):
    p = setup; o = assigned(db, p); move(db, p, o, 2)
    service_login(client, p['technician'])
    page, _ = deliver_form(client, o)
    # Ô chọn ảnh phủ kín nút lớn để bấm trên điện thoại mở thẳng máy ảnh; vẫn bắt buộc.
    assert re.search(r'<label class="btn photo-capture delivery-capture">[^<]*<input type="file" name="photos" accept="image/\*" capture="environment" multiple required data-delivery-camera>', page.text)
    assert 'data-photo-thumbs' in page.text
