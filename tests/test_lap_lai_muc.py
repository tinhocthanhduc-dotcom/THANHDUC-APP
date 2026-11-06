"""Lặp lại phiếu Mực định kỳ lần trước (TUẤN duyệt 06/11/2026): chỉ điền sẵn form, không ghi gì khi xem."""
import json
import re
from html import unescape

from app import service_repeat, service_tickets as svc
from app.models import PartRequest, ServiceTicket
from test_muc_nhan_don import form
from test_my_work import people, purchase_people, NOW  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_web import service_login


def _repeat(html):
    m = re.search(r'data-sv-repeat-use="([^"]+)"', html)
    return json.loads(unescape(m.group(1))) if m else None


def test_customer_without_ink_ticket_shows_no_box(client, db, held):  # noqa: F811
    p, a, b = held
    service_login(client, p['dispatcher'])
    assert 'data-sv-repeat' not in client.get('/dich-vu/chon-khach?customer_code=KTD-000001').text
    assert service_repeat.last_ink(db, '') == []


def test_last_ink_ticket_prefills_codes_qty_price_and_skips_cancelled(client, db, held):  # noqa: F811
    p, a, b = held
    csrf = service_login(client, p['dispatcher'])
    r = client.post('/dich-vu/moi', data=form(csrf, ink_qty=['3'], ink_price='520.000', symptom='Giao mực tầng 2'), follow_redirects=False)
    assert r.status_code == 303, r.text
    first = db.query(ServiceTicket).one()
    html = client.get('/dich-vu/chon-khach?customer_code=KTD-000001').text
    assert 'Mực định kỳ lần trước' in html and first.number in html
    data = _repeat(html)
    assert data['number'] == first.number and data['price'] == '520.000' and data['note'] == 'Giao mực tầng 2'
    assert data['lines'] == [dict(code='TD-0001', name=data['lines'][0]['name'], qty='3')]
    # Trang nhận đơn mở sẵn khách cũng có khung; xem không tạo thêm gì.
    assert 'data-sv-repeat-use' in client.get('/dich-vu/moi?customer_code=KTD-000001').text
    assert db.query(ServiceTicket).count() == 1 and db.query(PartRequest).count() == 1
    # Phiếu mới hơn bị hủy thì vẫn lấy phiếu cũ.
    r = client.post('/dich-vu/moi', data=form(csrf, ink_qty=['9']), follow_redirects=False)
    newer = db.query(ServiceTicket).order_by(ServiceTicket.id.desc()).first()
    svc.transition(db, p['dispatcher'], newer.id, 'DA_HUY', newer.version, note='Khách gọi nhầm')
    db.refresh(newer)
    assert newer.status == 'DA_HUY'
    assert _repeat(client.get('/dich-vu/chon-khach?customer_code=KTD-000001').text)['number'] == first.number


def test_inactive_product_is_dropped(client, db, held):  # noqa: F811
    from app.models import Product
    p, a, b = held
    csrf = service_login(client, p['dispatcher'])
    assert client.post('/dich-vu/moi', data=form(csrf), follow_redirects=False).status_code == 303
    db.get(Product, 'TD-0001').active = False
    db.commit()
    rows = service_repeat.last_ink(db, 'KTD-000001')
    assert rows[0]['lines'] == [] and rows[0]['dropped'] == ['TD-0001']


def test_technician_does_not_see_box(client, db, held):  # noqa: F811
    p, a, b = held
    csrf = service_login(client, p['dispatcher'])
    assert client.post('/dich-vu/moi', data=form(csrf), follow_redirects=False).status_code == 303
    service_login(client, p['technician'])
    r = client.get('/dich-vu/chon-khach?customer_code=KTD-000001')
    assert 'data-sv-repeat' not in r.text
