"""TUẤN 04/11/2026: ở màn thợ bấm link trong chuông thông báo không được nhảy sang giao diện văn phòng."""
from app import notifications
from test_stock_reservations import people, purchase_people, stock, order  # noqa: F401
from test_technician_deliveries import setup, assigned  # noqa: F401
from test_my_work import ticket
from test_web import service_login


def links(db, user, **kw):
    return {row['id']: row['link'] for row in notifications.items(db, user, **kw)}


def roles(db, user, *names):
    user.roles = list(names); db.commit()


def test_pure_technician_delivery_link_stays_in_tho_shell(db, setup, client):
    p = setup; o = assigned(db, p)
    assert links(db, p['technician'])[f'technician_deliveries:{o.id}'] == f'/giao-don/{o.id}?return_to=tho'
    service_login(client, p['technician'])
    page = client.get('/tho').text
    assert 'data-notify="/ca-nhan/thong-bao?man=tho"' in page
    data = client.get('/ca-nhan/thong-bao?man=tho').json()
    link = next(i['link'] for i in data['items'] if i['id'] == f'technician_deliveries:{o.id}')
    assert 'class="tho-app"' in client.get(link).text


def test_mixed_role_technician_keeps_office_links_outside_tho(db, setup, client):
    p = setup; o = assigned(db, p); roles(db, p['technician'], 'technician', 'warehouse')
    key = f'technician_deliveries:{o.id}'
    assert links(db, p['technician'])[key] == f'/giao-don/{o.id}'
    assert links(db, p['technician'], tho=True)[key] == f'/giao-don/{o.id}?return_to=tho'
    service_login(client, p['technician'])
    assert 'data-notify="/ca-nhan/thong-bao?man=tho"' in client.get('/tho').text
    office = client.get('/ca-nhan/thong-bao').json()['items']
    tho = client.get('/ca-nhan/thong-bao?man=tho').json()['items']
    assert next(i['link'] for i in office if i['id'] == key) == f'/giao-don/{o.id}'
    link = next(i['link'] for i in tho if i['id'] == key)
    assert 'class="tho-app"' in client.get(link).text


def test_tho_mode_opens_own_ticket_on_tho_screen_only(db, setup):
    p = setup
    mine = ticket(db, p, 'DA_GIAO'); other = ticket(db, p, 'DA_GIAO', who='other_tech')
    roles(db, p['technician'], 'technician', 'director')
    office = links(db, p['technician'])
    assert office[f'service_assigned:{mine.id}'] == f'/dich-vu/{mine.id}'
    tho = links(db, p['technician'], tho=True)
    assert tho[f'service_assigned:{mine.id}'] == f'/tho/phieu/{mine.id}'
    # Phiếu của thợ khác: /tho/phieu sẽ báo không phải phiếu của mình nên giữ link văn phòng.
    assert tho[f'service_assigned:{other.id}'] == f'/dich-vu/{other.id}'
