"""Thợ có thêm vai khác (ví dụ vừa thợ vừa bán hàng) vẫn mở được màn thợ trên điện thoại."""
from test_my_work import people, purchase_people, ticket  # noqa: F401
from test_web import service_login


def test_multi_role_technician_gets_technician_shell(client, db, people):
    p = people; tech = p['technician']; tech.roles = ['technician', 'sales']; db.commit()
    t = ticket(db, p, 'DA_HEN'); service_login(client, tech)
    page = client.get('/tho')
    assert page.status_code == 200 and 'class="tho-app"' in page.text and 'class="tho-tabs"' in page.text
    assert 'data-full-app' in page.text and t.number in page.text
    assert 'class="tho-app"' in client.get(f'/tho/phieu/{t.id}').text
    # Trang của vai khác vẫn là khung đầy đủ, menu có lối vào Hôm nay.
    other = client.get('/don-ban').text
    assert 'class="tho-app"' not in other and 'href="/tho"' in other


def test_pure_technician_unchanged_and_others_no_shell(client, db, people):
    p = people; service_login(client, p['technician'])
    page = client.get('/tho').text
    assert 'class="tho-app"' in page and 'data-full-app' not in page
    service_login(client, p['dispatcher'])
    assert client.get('/tho').status_code == 403
