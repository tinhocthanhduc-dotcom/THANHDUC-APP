from decimal import Decimal
import threading

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import stock_docs as svc
from app.errors import BusinessError
from app.models import AuditLog, DocCounter, Product, StockDoc, StockDocLine, Supplier, User, Warehouse
from app.schemas import StockDocIn, StockDocPatch


@pytest.fixture
def catalog(db):
    db.add_all([
        Warehouse(code='K58', name='Kho 58'),
        Warehouse(code='K145', name='Kho 145'),
        Warehouse(code='K999', name='Kho ngừng', active=False),
        Supplier(code='NCC-0001', name='Minh Phát'),
        Supplier(code='NCC-0002', name='Ngừng', active=False),
        Product(code='TD-0001', name='Mực HP'),
        Product(code='TD-0002', name='Drum'),
        Product(code='TD-0003', name='Ngừng bán', active=False),
    ])
    db.commit()


def draft(kind='PN', **kw):
    body = {'kind': kind, 'warehouse_code': 'K58', 'doc_date': '2026-10-17',
            'lines': [{'product_code': 'TD-0001', 'qty': '1.250'}]}
    if kind == 'PN':
        body['supplier_code'] = 'NCC-0001'
        body['lines'][0]['unit_cost'] = 125000
    if kind == 'CK':
        body['to_warehouse_code'] = 'K145'
    body.update(kw)
    return body


def create(client, h, body=None):
    r = client.post('/stock-docs', json=body or draft(), headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_pn_sequential_numbers(client, login, catalog):
    h = login('kho', 'warehouse')
    first, second = create(client, h), create(client, h)
    assert first['number'] == 'PN-26-00001'
    assert second['number'] == 'PN-26-00002'
    assert first['status'] == 'NHAP' and first['posted_at'] is None and first['posted_by'] is None
    assert first['version'] == 1 and first['lines'][0]['line_no'] == 1
    assert Decimal(first['lines'][0]['qty']) == Decimal('1.250')
    assert client.get('/stock-docs/' + str(first['id']), headers=h).json() == first


@pytest.mark.parametrize('kind,dest', [('CK', None), ('CK', 'K58'), ('PN', 'K145'), ('PX', 'K145'), ('KK', 'K145')])
def test_bad_transfer(client, login, catalog, kind, dest):
    h = login('kho', 'warehouse')
    r = client.post('/stock-docs', json=draft(kind, to_warehouse_code=dest), headers=h)
    assert r.status_code == 400 and r.json()['error'] == 'BAD_TRANSFER'


@pytest.mark.parametrize('kind,qty', [('PN', 0), ('PN', -1), ('PX', 0), ('CK', 0), ('KK', -1)])
def test_invalid_qty(client, login, catalog, kind, qty):
    h = login('kho', 'warehouse')
    body = draft(kind)
    body['lines'][0]['qty'] = qty
    assert client.post('/stock-docs', json=body, headers=h).status_code in (400, 422)


@pytest.mark.parametrize('cost', [None, 0, 250000])
def test_kk_zero_qty_with_optional_cost(client, login, catalog, cost):
    h = login('kho', 'warehouse')
    body = draft('KK')
    body['lines'][0]['qty'] = 0
    if cost is not None:
        body['lines'][0]['unit_cost'] = cost
    doc = create(client, h, body)
    assert Decimal(doc['lines'][0]['qty']) == 0
    assert doc['lines'][0]['unit_cost'] == cost


@pytest.mark.parametrize('kind,cost', [('PN', None), ('PX', 100), ('CK', 100), ('PN', -1), ('KK', -1), ('PN', 0.5), ('KK', 1.5), ('PN', True)])
def test_invalid_unit_cost(client, login, catalog, kind, cost):
    h = login('kho', 'warehouse')
    body = draft(kind)
    body['lines'][0]['unit_cost'] = cost
    assert client.post('/stock-docs', json=body, headers=h).status_code in (400, 422)


def test_pn_cost_required_and_kk_null_allowed(client, login, catalog):
    h = login('kho', 'warehouse')
    body = draft()
    body['lines'][0].pop('unit_cost')
    r = client.post('/stock-docs', json=body, headers=h)
    assert r.status_code == 400 and r.json()['error'] == 'BAD_UNIT_COST'
    body = draft('KK')
    body['lines'][0]['unit_cost'] = None
    assert create(client, h, body)['lines'][0]['unit_cost'] is None


def test_bad_products_returns_all_codes(client, login, catalog):
    h = login('kho', 'warehouse')
    body = draft(lines=[{'product_code': code, 'qty': 1, 'unit_cost': 0} for code in ['TD-0003', 'TD-9999', 'Mực HP']])
    r = client.post('/stock-docs', json=body, headers=h)
    assert r.status_code == 400 and r.json()['error'] == 'BAD_PRODUCT'
    assert set(r.json()['details']['invalid_codes']) == {'TD-0003', 'TD-9999', 'Mực HP'}


@pytest.mark.parametrize('kind,extra,error', [
    ('PN', {'warehouse_code': 'K999'}, 'BAD_WAREHOUSE'),
    ('PN', {'warehouse_code': 'K404'}, 'BAD_WAREHOUSE'),
    ('CK', {'to_warehouse_code': 'K999'}, 'BAD_WAREHOUSE'),
    ('CK', {'to_warehouse_code': 'K404'}, 'BAD_WAREHOUSE'),
    ('PN', {'supplier_code': None}, 'BAD_SUPPLIER'),
    ('PN', {'supplier_code': 'NCC-0002'}, 'BAD_SUPPLIER'),
    ('PN', {'supplier_code': 'NCC-9999'}, 'BAD_SUPPLIER'),
    ('PN', {'customer_code': 'KTD-000001'}, 'BAD_CUSTOMER'),
    ('PX', {'customer_code': 'KTD-999999'}, 'BAD_CUSTOMER'),
])
def test_invalid_catalogs(client, login, catalog, kind, extra, error):
    h = login('kho', 'warehouse')
    r = client.post('/stock-docs', json=draft(kind, **extra), headers=h)
    assert r.status_code == 400 and r.json()['error'] == error


def test_patch_replaces_all_lines_and_audits(client, login, catalog, db):
    h = login('kho', 'warehouse')
    doc = create(client, h)
    url = '/stock-docs/' + str(doc['id'])
    lines = [{'product_code': 'TD-0002', 'qty': '2.500', 'unit_cost': 0, 'serial': 'SN-01', 'note': 'Hàng mới'},
             {'product_code': 'TD-0001', 'qty': 3, 'unit_cost': 100}]
    r = client.patch(url, json={'version': 1, 'warehouse_code': 'K145', 'note': 'Đổi kho', 'lines': lines}, headers=h)
    assert r.status_code == 200, r.text
    changed = r.json()
    assert changed['version'] == 2 and changed['warehouse_code'] == 'K145'
    assert [x['line_no'] for x in changed['lines']] == [1, 2]
    assert [x['product_code'] for x in changed['lines']] == ['TD-0002', 'TD-0001']
    assert changed['lines'][0]['serial'] == 'SN-01'
    assert len(list(db.scalars(select(StockDocLine).where(StockDocLine.doc_id == doc['id'])))) == 2
    r = client.patch(url, json={'version': 1, 'note': 'Cũ'}, headers=h)
    assert r.status_code == 409 and r.json()['error'] == 'VERSION_CONFLICT'
    assert client.patch(url, json={'note': 'Thiếu version'}, headers=h).status_code == 422
    logs = list(db.scalars(select(AuditLog).where(AuditLog.entity == 'stock_doc').order_by(AuditLog.id)))
    assert [r.action for r in logs] == ['stock_doc.create', 'stock_doc.update']
    assert logs[0].before is None and logs[0].after['number'] == doc['number']
    assert logs[1].before['lines'][0]['product_code'] == 'TD-0001'
    assert logs[1].after['lines'][0]['serial'] == 'SN-01'


@pytest.mark.parametrize('action', ['patch', 'cancel'])
def test_posted_doc_cannot_change(client, login, catalog, db, action):
    h = login('kho', 'warehouse')
    doc = create(client, h)
    row = db.get(StockDoc, doc['id'])
    row.status = 'DA_GHI'
    db.commit()
    url = '/stock-docs/' + str(doc['id'])
    r = client.patch(url, json={'version': 1, 'note': 'Đổi'}, headers=h) if action == 'patch' else client.post(url + '/cancel', json={'reason': 'Sai'}, headers=h)
    assert r.status_code == 409 and r.json()['error'] == 'DOC_POSTED'


def test_cancel_draft_requires_reason_then_locks_doc(client, login, catalog, db):
    h = login('kho', 'warehouse')
    doc = create(client, h)
    url = '/stock-docs/' + str(doc['id'])
    for body in ({}, {'reason': ''}, {'reason': '   '}):
        assert client.post(url + '/cancel', json=body, headers=h).status_code == 422
    r = client.post(url + '/cancel', json={'reason': 'Nhập sai nhà cung cấp', 'version': 1}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()['status'] == 'DA_HUY' and r.json()['void_reason'] == 'Nhập sai nhà cung cấp'
    assert r.json()['version'] == 2 and r.json()['lines'] == doc['lines']
    assert client.patch(url, json={'version': 2, 'note': 'x'}, headers=h).json()['error'] == 'DOC_VOID'
    assert client.post(url + '/cancel', json={'reason': 'Lại'}, headers=h).json()['error'] == 'DOC_VOID'
    logs = list(db.scalars(select(AuditLog).where(AuditLog.entity == 'stock_doc').order_by(AuditLog.id)))
    assert [r.action for r in logs] == ['stock_doc.create', 'stock_doc.cancel']
    assert logs[-1].reason == 'Nhập sai nhà cung cấp'
    assert logs[-1].before['status'] == 'NHAP' and logs[-1].after['status'] == 'DA_HUY'


def test_failed_create_does_not_consume_number(client, login, catalog, db):
    h = login('kho', 'warehouse')
    first = create(client, h)
    invalid = draft()
    invalid['lines'][0]['product_code'] = 'TD-9999'
    assert client.post('/stock-docs', json=invalid, headers=h).status_code == 400
    assert create(client, h)['number'] == 'PN-26-00002'
    assert db.get(DocCounter, ('PN', 2026)).last == 2
    assert first['number'] == 'PN-26-00001'


def test_failure_after_number_allocation_rolls_back(client, login, catalog, db, monkeypatch):
    h = login('kho', 'warehouse')
    original = svc.audit.record
    def fail(*args, **kwargs):
        raise RuntimeError('simulate database/audit failure after number allocation')
    monkeypatch.setattr(svc.audit, 'record', fail)
    with pytest.raises(RuntimeError):
        client.post('/stock-docs', json=draft(), headers=h)
    monkeypatch.setattr(svc.audit, 'record', original)
    assert create(client, h)['number'] == 'PN-26-00001'
    assert len(list(db.scalars(select(StockDoc)))) == 1


@pytest.mark.parametrize('role', ['sales', 'reception', 'dispatcher', 'technician', 'finance', 'admin'])
def test_roles_read_only(client, login, catalog, role):
    h = login('kho', 'warehouse')
    doc = create(client, h)
    h = login('reader', role)
    url = '/stock-docs/' + str(doc['id'])
    assert client.get('/stock-docs', headers=h).status_code == 200
    assert client.get(url, headers=h).status_code == 200
    assert client.post('/stock-docs', json=draft(), headers=h).status_code == 403
    assert client.patch(url, json={'version': 1, 'note': 'Sai'}, headers=h).status_code == 403
    assert client.post(url + '/cancel', json={'reason': 'Sai'}, headers=h).status_code == 403


def test_director_and_list_filters(client, login, catalog):
    h = login('director', 'director')
    first = create(client, h, draft(doc_date='2026-01-01'))
    create(client, h, draft('PX', doc_date='2026-02-01', warehouse_code='K145'))
    last = create(client, h, draft('KK', doc_date='2026-03-01'))
    client.post('/stock-docs/' + str(last['id']) + '/cancel', json={'reason': 'Sai'}, headers=h)
    for params, expected in [({'kind': 'PN'}, [first['id']]), ({'warehouse': 'K58', 'status': 'DA_HUY'}, [last['id']]),
                             ({'from': '2026-01-01', 'to': '2026-01-01'}, [first['id']]),
                             ({'from': '2026-04-01'}, [])]:
        r = client.get('/stock-docs', params=params, headers=h)
        assert r.status_code == 200 and [x['id'] for x in r.json()] == expected
    assert client.get('/stock-docs?kind=XX', headers=h).status_code == 422
    assert client.get('/stock-docs?from=bad-date', headers=h).status_code == 422


def test_empty_lines_null_fields_and_forbidden_endpoints(client, login, catalog):
    h = login('kho', 'warehouse')
    assert client.post('/stock-docs', json=draft(lines=[]), headers=h).status_code == 422
    doc = create(client, h)
    url = '/stock-docs/' + str(doc['id'])
    for data in ({'lines': []}, {'lines': None}, {'warehouse_code': None}, {'kind': None}, {'doc_date': None}):
        assert client.patch(url, json={'version': 1, **data}, headers=h).status_code == 422
    for field in ('status', 'number', 'created_by', 'posted_by', 'posted_at', 'void_reason'):
        assert client.patch(url, json={'version': 1, field: 'x'}, headers=h).status_code == 422
    assert client.delete(url, headers=h).status_code == 405
    assert client.post(url + '/post', headers=h).status_code == 404
    assert client.post(url + '/void', headers=h).status_code == 404
    assert client.get('/stock-docs/999999', headers=h).status_code == 404
    assert client.get('/stock-docs').status_code == 401


def test_invalid_patch_keeps_header_lines_number_and_audit(client, login, catalog, db):
    h = login('kho', 'warehouse')
    doc = create(client, h)
    url = '/stock-docs/' + str(doc['id'])
    data = {'version': 1, 'note': 'Không được lưu', 'doc_date': '2026-12-31',
            'lines': [{'product_code': 'TD-9999', 'qty': 1, 'unit_cost': 0}]}
    assert client.patch(url, json=data, headers=h).status_code == 400
    assert client.get(url, headers=h).json() == doc
    assert len(list(db.scalars(select(AuditLog).where(AuditLog.entity == 'stock_doc')))) == 1
    assert db.get(DocCounter, ('PN', 2027)) is None


def test_header_changes_lock_number_but_allow_same_year_date(client, login, catalog, db):
    h = login('kho', 'warehouse')
    doc = create(client, h)
    assert doc['number'] == 'PN-26-00001'
    url = '/stock-docs/' + str(doc['id'])
    for changes in ({'kind': 'KK'}, {'doc_date': '2027-01-01'}):
        r = client.patch(url, json={'version': 1, 'note': 'Không lưu', **changes}, headers=h)
        assert r.status_code == 409 and r.json()['error'] == 'NUMBER_LOCKED'
        assert r.json()['message'] == 'Đổi loại phiếu hoặc sang năm khác thì hủy nháp và lập phiếu mới.'
        assert client.get(url, headers=h).json() == doc
    assert db.get(DocCounter, ('PN', 2027)) is None
    assert db.get(DocCounter, ('KK', 2026)) is None
    assert len(list(db.scalars(select(AuditLog).where(AuditLog.entity == 'stock_doc')))) == 1
    r = client.patch(url, json={'version': 1, 'doc_date': '2026-12-31'}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()['doc_date'] == '2026-12-31'
    assert r.json()['number'] == 'PN-26-00001' and r.json()['version'] == 2
    assert db.get(DocCounter, ('PN', 2026)).last == 1


def test_concurrent_updates_only_one_succeeds(client, login, catalog, engine, monkeypatch):
    h = login('kho', 'warehouse')
    doc = create(client, h)
    barrier = threading.Barrier(2)
    original = svc._validate
    def validate(*args):
        original(*args)
        barrier.wait(timeout=10)
    monkeypatch.setattr(svc, '_validate', validate)
    results = []
    def go(note):
        with Session(engine, expire_on_commit=False) as session:
            user = session.get(User, doc['created_by'])
            try:
                svc.update(session, user, doc['id'], StockDocPatch(version=1, note=note))
                results.append('OK')
            except BusinessError as exc:
                results.append(exc.code)
    threads = [threading.Thread(target=go, args=(x,)) for x in ('A', 'B')]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert sorted(results) == ['OK', 'VERSION_CONFLICT']
    with Session(engine) as session:
        assert session.get(StockDoc, doc['id']).version == 2
        logs = list(session.scalars(select(AuditLog).where(AuditLog.action == 'stock_doc.update')))
        assert len(logs) == 1


def test_database_status_check(catalog, db, make_user):
    user = make_user('kho', 'warehouse')
    doc = svc.create(db, user, StockDocIn(**draft()))
    doc.status = 'INVALID'
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_failed_line_replacement_rolls_back_everything(client, login, catalog, db, monkeypatch):
    h = login('kho', 'warehouse')
    doc = create(client, h)
    url = '/stock-docs/' + str(doc['id'])
    original = svc.audit.record
    def fail(*args, **kwargs):
        raise RuntimeError('audit failed after flushing new header and lines')
    monkeypatch.setattr(svc.audit, 'record', fail)
    with pytest.raises(RuntimeError):
        client.patch(url, json={'version': 1, 'note': 'Không lưu', 'doc_date': '2026-12-31',
                               'lines': [{'product_code': 'TD-0002', 'qty': 3, 'unit_cost': 999}]}, headers=h)
    monkeypatch.setattr(svc.audit, 'record', original)
    assert client.get(url, headers=h).json() == doc
    assert db.get(DocCounter, ('PN', 2027)) is None
    assert len(list(db.scalars(select(AuditLog).where(AuditLog.entity == 'stock_doc')))) == 1
