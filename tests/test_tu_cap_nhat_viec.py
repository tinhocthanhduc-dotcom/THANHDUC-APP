"""Tự cập nhật việc mới: trang hàng việc mang dấu, /moi trả dấu mới khi có việc giao thêm (chỉ đọc)."""
import re

import pytest

from conftest import PASSWORD
from test_my_work import people, purchase_people, ticket
from test_technician_deliveries import setup, assigned
from test_web import service_login


def stamp_in(page):
    match = re.search(r'data-watch="([^"]+)" data-stamp="([0-9a-f]+)"', page.text)
    assert match, 'trang hàng việc phải mang dấu để tự kiểm việc mới'
    assert '/static/work-watch.js' in page.text
    return match.group(1), match.group(2)


def current(client, url):
    r = client.get(url)
    assert r.status_code == 200 and r.headers['cache-control'] == 'no-store'
    return r.json()['stamp']


def test_technician_today_changes_when_ticket_or_delivery_assigned(client, db, setup):
    p = setup
    service_login(client, p['technician'])
    url, first = stamp_in(client.get('/tho'))
    assert url == '/tho/moi' and current(client, url) == first
    # Việc của thợ khác không làm đổi dấu của mình.
    ticket(db, p, 'DA_GIAO', who='other_tech')
    assert current(client, url) == first
    mine = ticket(db, p, 'DA_GIAO')
    second = current(client, url)
    assert second != first and stamp_in(client.get('/tho'))[1] == second
    assigned(db, p)
    assert current(client, url) != second
    assert mine.number in client.get('/tho').text


def test_my_work_and_dispatch_stamp(client, db, people):
    p = people
    service_login(client, p['technician'])
    url, first = stamp_in(client.get('/viec-cua-toi'))
    assert url == '/viec-cua-toi/moi' and current(client, url) == first
    ticket(db, p, 'DA_GIAO')
    assert current(client, url) != first
    # Trang lọc một nhóm không tự kiểm (đang phân trang).
    assert 'data-watch=' not in client.get('/viec-cua-toi?nhom=service_assigned').text
    service_login(client, p['dispatcher'])
    url, first = stamp_in(client.get('/dieu-phoi'))
    assert url == '/dieu-phoi/moi' and current(client, url) == first
    ticket(db, p, 'MOI')
    assert current(client, url) != first


@pytest.mark.parametrize('role,url', [('sales', '/tho/moi'), ('warehouse', '/tho/moi'), ('technician', '/dieu-phoi/moi'), ('sales', '/dieu-phoi/moi')])
def test_stamp_endpoints_keep_page_permissions(client, people, role, url):
    service_login(client, people[role])
    assert client.get(url).status_code == 403


def test_stamp_requires_login(client):
    r = client.get('/viec-cua-toi/moi', follow_redirects=False)
    assert r.status_code in (302, 303) and '/dang-nhap' in r.headers['location']
