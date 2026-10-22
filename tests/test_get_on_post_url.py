"""Mở bằng GET một địa chỉ chỉ nhận POST (tải lại trang lỗi, đăng nhập lại sau hết phiên) thì về trang của form, không ra JSON 405."""
from app.web import COOKIE
from test_my_work import people, purchase_people  # noqa: F401
from test_web import service_login

HTML = {'accept': 'text/html,application/xhtml+xml'}


def test_get_on_deliver_url_goes_back_to_delivery_page(client, people):
    service_login(client, people['technician'])
    r = client.get('/giao-don/5/deliver', headers=HTML, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/giao-don/5?return_to=tho'
    r = client.get('/tho/phieu/7/nhap', headers=HTML | {'referer': 'http://testserver/tho/phieu/7'}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/tho/phieu/7'
    # Trang khác máy chủ không được dùng làm đích.
    r = client.get('/tho/phieu/7/nhap', headers=HTML | {'referer': 'https://evil.example/x'}, follow_redirects=False)
    assert r.headers['location'] == '/tho/phieu/7'
    # Gọi API (không xin HTML) vẫn nhận 405 như cũ.
    assert client.get('/giao-don/5/deliver', headers={'accept': 'application/json'}).status_code == 405


def test_session_expired_on_submit_returns_to_form_page_after_login(client, people):
    service_login(client, people['technician'])
    client.cookies.delete(COOKIE)
    r = client.post('/giao-don/5/deliver', data=dict(csrf='x'), headers={'referer': 'http://testserver/giao-don/5?return_to=tho'}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/dang-nhap?next=%2Fgiao-don%2F5%3Freturn_to%3Dtho'
