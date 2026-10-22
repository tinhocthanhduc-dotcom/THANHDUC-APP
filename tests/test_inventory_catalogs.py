import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import AuditLog


def test_duplicate_warehouse_code(client, login):
    h = login('kho', 'warehouse')
    data = {'code': 'K58', 'name': 'Kho 58'}
    assert client.post('/warehouses', json=data, headers=h).status_code == 201
    r = client.post('/warehouses', json=data, headers=h)
    assert r.status_code == 409 and r.json()['error'] == 'DUPLICATE_CODE'


def test_duplicate_supplier_tax(client, login):
    h = login('kho', 'warehouse')
    data = {'name': 'Minh Phát', 'tax_code': '0301234567'}
    assert client.post('/suppliers', json=data, headers=h).status_code == 201
    r = client.post('/suppliers', json={**data, 'tax_code': '301234567'}, headers=h)
    assert r.status_code == 409 and r.json()['error'] == 'DUPLICATE_TAX'


def test_supplier_codes_sequential(client, login):
    h = login('kho', 'warehouse')
    for expected in ('NCC-0001', 'NCC-0002'):
        r = client.post('/suppliers', json={'name': 'Minh Phát'}, headers=h)
        assert r.status_code == 201, r.text
        assert r.json()['code'] == expected


def test_supplier_search_without_accents(client, login):
    h = login('kho', 'warehouse')
    client.post('/suppliers', json={'name': 'Minh Phát', 'phones': ['903 123 456']}, headers=h)
    for q in ('minh phat', 'NCC-0001', '0903123456'):
        rows = client.get('/suppliers', params={'q': q}, headers=h).json()
        assert [x['name'] for x in rows] == ['Minh Phát']
    assert client.get('/suppliers?q=100%25', headers=h).json() == []


@pytest.mark.parametrize('path,data', [('/warehouses', {'code': 'K58', 'name': 'Kho 58'}), ('/suppliers', {'name': 'Minh Phát'})])
@pytest.mark.parametrize('role', ['sales', 'reception', 'dispatcher', 'technician', 'finance', 'admin'])
def test_other_roles_read_only(client, login, path, data, role):
    writer = login('kho', 'warehouse')
    row = client.post(path, json=data, headers=writer).json()
    reader = login('nguoidoc', role)
    expected = 200 if path == '/suppliers' and role == 'finance' else 403
    assert client.get(path, headers=reader).status_code == expected
    assert client.get(path + '/' + row['code'], headers=reader).status_code == expected
    assert client.post(path, json=data, headers=reader).status_code == 403
    assert client.patch(path + '/' + row['code'], json={'version': 1, 'name': 'Khác'}, headers=reader).status_code == 403


@pytest.mark.parametrize('path,data,entity', [('/warehouses', {'code': 'K58', 'name': 'Kho 58'}, 'warehouse'), ('/suppliers', {'name': 'Minh Phát'}, 'supplier')])
def test_version_audit_soft_disable_and_validation(client, login, db, path, data, entity):
    h = login('giamdoc', 'director')
    row = client.post(path, json=data, headers=h).json()
    url = path + '/' + row['code']
    assert client.patch(url, json={'name': 'Khác'}, headers=h).status_code == 422
    assert client.patch(url, json={'version': 1, 'name': None}, headers=h).status_code == 422
    r = client.patch(url, json={'version': 1, 'name': 'Tên mới', 'active': False}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()['version'] == 2 and r.json()['active'] is False
    stale = client.patch(url, json={'version': 1, 'address': 'Cũ'}, headers=h)
    assert stale.status_code == 409 and stale.json()['error'] == 'VERSION_CONFLICT'
    assert client.delete(url, headers=h).status_code == 405
    assert client.get(url, headers=h).json()['name'] == 'Tên mới'
    logs = list(db.scalars(select(AuditLog).where(AuditLog.entity == entity).order_by(AuditLog.id)))
    assert [r.action for r in logs] == [entity + '.create', entity + '.update']
    assert logs[0].before is None and logs[0].after['version'] == 1
    assert logs[1].before['name'] == data['name'] and logs[1].after['active'] is False


def test_supplier_tax_update_and_search_refresh(client, login):
    h = login('kho', 'warehouse')
    a = client.post('/suppliers', json={'name': 'A', 'tax_code': '0301234567'}, headers=h).json()
    b = client.post('/suppliers', json={'name': 'B'}, headers=h).json()
    r = client.patch('/suppliers/' + b['code'], json={'version': 1, 'tax_code': a['tax_code']}, headers=h)
    assert r.status_code == 409 and r.json()['error'] == 'DUPLICATE_TAX'
    r = client.patch('/suppliers/' + b['code'], json={'version': 1, 'name': 'Minh Phát', 'phones': ['903 123 456']}, headers=h)
    assert r.status_code == 200 and r.json()['phones'] == ['0903123456']
    assert len(client.get('/suppliers?q=minh%20phat', headers=h).json()) == 1


def test_concurrent_supplier_tax(client, login):
    h = login('kho', 'warehouse')
    results = []
    def go():
        with TestClient(app) as c:
            results.append(c.post('/suppliers', json={'name': 'Minh Phát', 'tax_code': '0301234567'}, headers=h).status_code)
    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [201, 409, 409, 409]
