"""Thông báo theo vị trí: chuông trên mọi trang, /ca-nhan/thong-bao chỉ trả việc của đúng người (chỉ đọc)."""
from app import my_work, payroll
from test_my_work import people, purchase_people, ticket
from test_ns5_payroll import p, money
from test_tk_tai_khoan import web_login
from test_web import service_login


def notes(client):
    r = client.get('/ca-nhan/thong-bao')
    assert r.status_code == 200 and r.headers['cache-control'] == 'no-store'
    return {item['id']: item for item in r.json()['items']}


def test_assigned_ticket_notifies_only_that_technician(client, db, people):
    pp = people
    service_login(client, pp['technician'])
    page = client.get('/tho')
    assert 'data-notify="/ca-nhan/thong-bao"' in page.text and '/static/notify.js' in page.text
    assert not [k for k in notes(client) if k.startswith('service_assigned:')]
    row = ticket(db, pp, 'DA_GIAO')
    got = notes(client)
    item = got[f'service_assigned:{row.id}']
    assert item['title'] == 'Mới giao · chờ nhận' and row.number in item['text'] and item['link'] == f'/tho/phieu/{row.id}'
    service_login(client, pp['other_tech'])
    assert f'service_assigned:{row.id}' not in notes(client)
    # Điều phối thấy phiếu mới chưa giao thợ.
    fresh = ticket(db, pp, 'MOI')
    service_login(client, pp['dispatcher'])
    assert f'service_unassigned:{fresh.id}' in notes(client)
    assert 'data-notify=' in client.get('/dieu-phoi').text


def test_advance_goes_to_director_then_finance_then_back_to_employee(client, db, p, money, monkeypatch):
    monkeypatch.setattr(my_work, '_now', lambda: p['clock']['at'])
    row = payroll.request_advance(db, p['technician'], 500000, 'Gia đình')
    web_login(client, p['director']);assert notes(client)[f'advance_approve:{row.id}']['title'] == 'Tạm ứng chờ duyệt'
    web_login(client, p['finance']);assert f'advance_approve:{row.id}' not in notes(client)
    web_login(client, p['sales']);assert not [k for k in notes(client) if k.startswith('advance')]
    payroll.decide_advance(db, p['director'], row.id, row.version)
    web_login(client, p['director']);assert f'advance_approve:{row.id}' not in notes(client)
    web_login(client, p['finance']);assert f'advance_pay:{row.id}' in notes(client)
    web_login(client, p['technician']);assert notes(client)[f'advance_da_duyet:{row.id}']['title'] == 'Tạm ứng đã được duyệt'


def test_notices_need_login(client):
    r = client.get('/ca-nhan/thong-bao', follow_redirects=False)
    assert r.status_code in (302, 303) and '/dang-nhap' in r.headers['location']


def test_read_marks_badge_and_hides_seen_results(client, db, p, money, monkeypatch):
    monkeypatch.setattr(my_work, '_now', lambda: p['clock']['at'])
    row = payroll.request_advance(db, p['technician'], 500000, 'Gia đình')
    csrf = web_login(client, p['director'])
    got = client.get('/ca-nhan/thong-bao').json()
    item = next(i for i in got['items'] if i['id'] == f'advance_approve:{row.id}')
    assert item['read'] is False and got['unread'] >= 1
    assert client.post('/ca-nhan/thong-bao/da-xem', data={'id': item['id']}).status_code == 403
    assert client.post('/ca-nhan/thong-bao/da-xem', data={'csrf': csrf, 'id': item['id']}).status_code == 200
    got = client.get('/ca-nhan/thong-bao').json()
    # Việc cần làm vẫn hiện (đã xem) tới khi duyệt xong; badge không đếm nữa.
    assert notes(client)[item['id']]['read'] is True and got['unread'] == 0
    # Đã xem của giám đốc không ảnh hưởng người khác.
    payroll.decide_advance(db, p['director'], row.id, row.version)
    csrf = web_login(client, p['technician'])
    done = f'advance_da_duyet:{row.id}'
    assert notes(client)[done]['read'] is False
    client.post('/ca-nhan/thong-bao/da-xem', data={'csrf': csrf, 'all': '1'})
    assert done not in notes(client)  # thông báo kết quả: xem rồi thì ẩn
