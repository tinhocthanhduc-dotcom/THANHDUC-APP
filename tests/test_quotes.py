from datetime import date

import pytest
from sqlalchemy import select

from app import quotes as svc
from app.errors import BusinessError
from app.models import AuditLog, Customer, Product, Quote, User
from app.money_words import money_words
from app.schemas import QuoteIn, QuoteLineIn, QuoteUpdate
from app.security import issue_token


@pytest.fixture
def quote_people(db, make_user, monkeypatch):
    monkeypatch.setattr(svc, '_today', lambda: date(2026, 10, 17))
    db.add_all([
        Customer(code='KTD-000001', name='Công ty Minh Phát', search_text='cong ty minh phat'),
        Customer(code='KTD-000002', name='Ngừng dùng', status='NGUNG'),
        Product(code='TD-0001', name='Mực in', unit='hộp', sale_price=120000, search_text='td-0001 muc in'),
        Product(code='TD-0002', name='Cáp máy in', unit='sợi', sale_price=333, search_text='td-0002 cap may in'),
        Product(code='TD-0009', name='Ngừng bán', active=False),
    ])
    db.commit()
    return {role: make_user('bg.' + role, role) for role in ('sales','finance','technician','director')}


def body(**kw):
    data = dict(customer_code='KTD-000001', contact_name='Chị Lan', note='Giao tại văn phòng',
                lines=[dict(description='Công lắp đặt', qty='1.5', unit_price=333, vat_rate=10)])
    data.update(kw)
    return QuoteIn(**data)


def error(code, fn, *args):
    with pytest.raises(BusinessError) as exc:
        fn(*args)
    assert exc.value.code == code
    return exc.value


def test_quote_two_lines_number_totals_and_half_rounding(db, quote_people):
    q = svc.create(db, quote_people['sales'], body(lines=[
        dict(product_code='TD-0001', qty='2', unit_price=100, vat_rate=10),
        dict(description='Công lắp đặt', qty='1.5', unit_price=333, vat_rate=10),
    ]))
    assert q.number == 'BG-26-00001' and q.status == 'NHAP' and q.version == 1
    assert q.valid_until == date(2026, 10, 24)
    assert [(l.amount, l.vat_amount) for l in q.lines] == [(200,20),(500,50)]
    assert (q.subtotal,q.vat_total,q.total) == (700,70,770)
    assert svc.create(db, quote_people['sales'], body()).number == 'BG-26-00002'
    half_tax = svc.create(db, quote_people['sales'], body(lines=[dict(description='Phí nhỏ',qty='1',unit_price=5,vat_rate=10)]))
    assert half_tax.lines[0].vat_amount == 1 and half_tax.total == 6


def test_quote_default_price_description_unit_from_product(db, quote_people):
    q = svc.create(db, quote_people['sales'], body(lines=[dict(product_code='TD-0001',qty='2')]))
    assert q.lines[0].unit_price == 120000 and q.total == 240000
    assert q.lines[0].description == 'Mực in' and q.lines[0].unit == 'hộp'


def test_quote_sent_lines_locked_and_send_empty(db, quote_people):
    u = quote_people['sales']
    q = svc.create(db, u, body())
    svc.send(db, u, q.id, q.version)
    exc = error('PRICE_LOCKED', svc.update_lines, db, u, q.id, QuoteUpdate(version=q.version, lines=[]))
    assert exc.status == 409
    assert q.total == 550 and len(q.lines) == 1
    empty = svc.create(db, u, body(lines=[]))
    error('EMPTY_LINES', svc.send, db, u, empty.id, empty.version)
    assert svc.load(db,u,empty.id).status == 'NHAP'


def test_quote_cancel_reason_and_invalid_transition(db, quote_people):
    u = quote_people['sales']
    q = svc.create(db,u,body())
    error('REASON_REQUIRED',svc.cancel,db,u,q.id,'  ',q.version)
    svc.cancel(db,u,q.id,'Khách chưa có nhu cầu',q.version)
    exc = error('BAD_STATUS',svc.accept,db,u,q.id,q.version)
    assert exc.status == 409
    assert svc.load(db,u,q.id).cancel_reason == 'Khách chưa có nhu cầu'


def test_quote_revise_copies_prices_and_links_old(db, quote_people):
    u = quote_people['sales']
    q = svc.create(db,u,body(valid_until=date(2026,1,1)))
    svc.send(db,u,q.id,q.version)
    before = svc.snapshot(q)
    revised = svc.revise(db,u,q.id,q.version)
    assert revised.number == 'BG-26-00002' and revised.status == 'NHAP'
    assert svc.snapshot(revised)['lines'] == before['lines']
    assert revised.total == q.total == 550 and revised.contact_name == q.contact_name
    assert revised.valid_until == date(2026,10,24)
    assert q.status == 'DA_HUY' and q.replaced_by_id == revised.id
    assert q.cancel_reason == 'Thay bằng ' + revised.number
    assert len(svc.load(db,u,q.id).lines) == 1


def test_quote_stale_version_and_replace_all_lines(db, quote_people):
    u = quote_people['sales']
    q = svc.create(db,u,body())
    svc.update_lines(db,u,q.id,QuoteUpdate(version=1,lines=[dict(product_code='TD-0001',qty='1')]))
    assert q.version == 2 and len(q.lines) == 1 and q.lines[0].product_code == 'TD-0001'
    error('VERSION_CONFLICT',svc.send,db,u,q.id,1)
    error('VERSION_CONFLICT',svc.update_lines,db,u,q.id,QuoteUpdate(version=1,lines=[]))
    error('VERSION_REQUIRED',svc.send,db,u,q.id,None)
    assert q.total == 120000


def test_quote_inactive_customer_product_and_failed_creation_does_not_spend_number(db, quote_people):
    u = quote_people['sales']
    error('BAD_CUSTOMER',svc.create,db,u,body(customer_code='KTD-000002'))
    error('BAD_PRODUCT',svc.create,db,u,body(lines=[dict(product_code='TD-0009',qty='1')]))
    error('BAD_PRODUCT',svc.create,db,u,body(lines=[dict(product_code='TD-9999',qty='1')]))
    error('PRICE_REQUIRED',svc.create,db,u,body(lines=[dict(description='Tự do',qty='1')]))
    error('AMOUNT_TOO_LARGE',svc.create,db,u,body(lines=[dict(description='Quá lớn',qty='99999999999.999',unit_price=9_223_372_036_854_775_807)]))
    assert db.query(Quote).count() == 0
    assert svc.create(db,u,body()).number == 'BG-26-00001'


def test_quote_role_permissions_api_and_search(client, db, quote_people):
    sales, finance, tech = (quote_people[r] for r in ('sales','finance','technician'))
    q = svc.create(db,sales,body())
    assert svc.search(db,finance,'minh phat')[0].id == q.id
    assert svc.search(db,finance,'bg-26')[0].id == q.id
    assert svc.search(db,finance,status='DA_GUI') == []
    error('FORBIDDEN',svc.create,db,finance,body())
    error('FORBIDDEN',svc.load,db,tech,q.id)
    h = lambda u: {'Authorization':'Bearer ' + issue_token(u)}
    assert client.post('/quotes',headers=h(tech),json=body().model_dump(mode='json')).status_code == 403
    assert client.post('/quotes',headers=h(finance),json=body().model_dump(mode='json')).status_code == 403
    assert client.get(f'/quotes/{q.id}',headers=h(finance)).status_code == 200
    response = client.post(f'/quotes/{q.id}/send',headers=h(sales),json={'version':q.version})
    assert response.status_code == 200 and response.json()['status'] == 'DA_GUI'
    assert client.post(f'/quotes/{q.id}/accept',headers=h(sales),json={'version':response.json()['version']}).json()['status'] == 'CHAP_NHAN'


def test_quote_audit_for_every_operation(db, quote_people):
    u = quote_people['director']
    q = svc.create(db,u,body())
    svc.update_lines(db,u,q.id,QuoteUpdate(version=q.version,lines=body().lines))
    svc.send(db,u,q.id,q.version); svc.accept(db,u,q.id,q.version)
    q2 = svc.create(db,u,body()); svc.send(db,u,q2.id,q2.version); svc.reject(db,u,q2.id,q2.version)
    q3 = svc.create(db,u,body()); svc.cancel(db,u,q3.id,'Khách hủy',q3.version)
    q4 = svc.create(db,u,body()); svc.send(db,u,q4.id,q4.version); svc.revise(db,u,q4.id,q4.version)
    logs = list(db.scalars(select(AuditLog).where(AuditLog.entity=='quote').order_by(AuditLog.id)))
    assert len(logs) == 13
    assert {l.action for l in logs} == {'quote.create','quote.update_lines','quote.send','quote.accept','quote.reject','quote.cancel','quote.revise'}
    for log in logs:
        assert log.after['number'].startswith('BG-26-') and 'lines' in log.after
        if log.action == 'quote.create':
            assert log.before is None
        else:
            assert log.after['version'] > log.before['version']


def test_quote_concurrent_updates_and_numbers(db, engine, quote_people):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    u = quote_people['sales']
    q = svc.create(db,u,body())
    quote_id, user_id = q.id, u.id
    barrier = Barrier(2)
    def update(qty):
        with Session(engine,expire_on_commit=False) as s:
            user = s.get(User,user_id)
            loaded = svc.load(s,user,quote_id)
            version = loaded.version
            barrier.wait(timeout=10)
            try:
                svc.update_lines(s,user,quote_id,QuoteUpdate(version=version,lines=[dict(description='Công',qty=qty,unit_price=100)]))
                return 'OK'
            except BusinessError as exc:
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update,['2','3'])) == ['OK','VERSION_CONFLICT']
    db.expire_all()
    assert svc.load(db,u,quote_id).version == 2
    barrier = Barrier(2)
    def create(_):
        with Session(engine,expire_on_commit=False) as s:
            user = s.get(User,user_id)
            barrier.wait(timeout=10)
            return svc.create(s,user,body()).number
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create,[1,2])) == ['BG-26-00002','BG-26-00003']


@pytest.mark.parametrize('value,expected',[
    (0,'Không đồng'), (15,'Mười lăm đồng'), (21,'Hai mươi mốt đồng'),
    (105,'Một trăm lẻ năm đồng'), (1005,'Một nghìn không trăm lẻ năm đồng'),
    (1000001,'Một triệu không trăm lẻ một đồng'),
    (550,'Năm trăm năm mươi đồng'),
])
def test_quote_money_words(value, expected):
    assert money_words(value) == expected
