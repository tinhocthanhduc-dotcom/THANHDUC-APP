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
    # Ô ảnh chuẩn (TUẤN 24/10): nút Chụp ảnh mở thẳng máy ảnh, 1 ảnh to vừa màn hình, vẫn bắt buộc.
    assert re.search(r'<label class="btn photo-capture">[^<]*<input type="file" name="photos" accept="image/jpeg,image/png,image/webp" capture="environment"\s+required data-photo-pick', page.text)
    assert 'photo-slot photo-wide' in page.text
    assert 'data-photo-picked' in page.text


def test_giao_xong_khong_gui_anh_goc_luc_dang_nen():
    """TUẤN 13/11 "Giao xong chậm": thanh vuốt ở ngoài form (form="giao-xong") trước đây không bị khóa lúc nén ảnh,
    vuốt nhanh là gửi ảnh gốc vài MB. Nay khóa mọi nút của form (form.elements), chỉ mở lại nút do bước nén khóa."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / 'app' / 'web' / 'static' / 'ui.js').read_text()
    assert "input.form.elements" in js and "b.dataset.photoBusy = '1'" in js and "delete b.dataset.photoBusy" in js
    assert "'Đang nén ảnh, chờ 1–2 giây rồi vuốt lại'" in js


def test_anh_jpeg_lon_giai_nen_nho_van_ra_1280():
    import io
    from PIL import Image
    from app.attachments import _prepare
    src = io.BytesIO(); Image.new('RGB', (4000, 3000), (200, 120, 40)).save(src, 'JPEG', quality=90)
    data, ctype, ext, w, h = _prepare(src.getvalue())
    assert (ctype, ext, w, h) == ('image/jpeg', 'jpg', 1280, 960)
    src = io.BytesIO(); Image.new('RGB', (3000, 4000), (200, 120, 40)).save(src, 'JPEG', quality=90)
    assert _prepare(src.getvalue())[3:] == (960, 1280)
