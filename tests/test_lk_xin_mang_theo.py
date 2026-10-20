"""Thợ xin hàng mang theo (yêu cầu linh kiện không gắn phiếu); kho cấp vào kho riêng của thợ."""
from sqlalchemy import select

from app import my_work, part_requests as svc
from app.models import AuditLog, PartRequest, StockDoc
from app.schemas import PartStockIn
from test_my_work import people, purchase_people  # noqa: F401  (fixtures)
from test_technician_stock import held, balance, error  # noqa: F401
from test_web import service_login


def ask(db, p, who='technician', qty=2, reason='Mang sẵn thay drum'):
    return svc.create_stock(db, p[who], PartStockIn(product_code='TD-0001', qty=qty, reason=reason))


def test_tao_va_cap_vao_kho_rieng_cua_tho(db, held):
    p, a, b = held
    r = ask(db, p)
    assert r.ticket_id is None and r.status == 'CHO_CAP' and r.number.startswith('YC-')
    assert r in svc.search(db, p['warehouse'])
    svc.supply(db, p['warehouse'], r.id, 'K58', r.version)
    assert r.status == 'DA_CAP' and r.to_warehouse_code == a.code
    doc = db.get(StockDoc, r.stock_doc_id)
    assert doc.kind == 'CK' and doc.status == 'DA_GHI' and 'mang theo' in doc.reason
    assert balance(db, 'K58') == 8 and balance(db, a.code) == 2
    assert db.scalar(select(AuditLog).where(AuditLog.action == 'part_request.supply')).after['ticket_id'] is None


def test_chi_tho_co_kho_rieng_va_ly_do(db, held, people):
    p, a, b = held
    for who in ('dispatcher', 'warehouse', 'sales'):
        error('FORBIDDEN', ask, db, p, who=who)
    error('REASON_REQUIRED', ask, db, p, reason='  ')
    error('BAD_PRODUCT', svc.create_stock, db, p['technician'], PartStockIn(product_code='BAD', qty=1, reason='x'))
    a.active = False
    db.commit()
    error('NO_TECH_WAREHOUSE', ask, db, p)
    assert db.query(PartRequest).count() == 0


def test_huy_het_hang_va_khong_du_ton(db, held):
    p, a, b = held
    r = ask(db, p, qty=11)
    error('NEGATIVE_STOCK', svc.supply, db, p['warehouse'], r.id, 'K58', r.version)
    assert r.status == 'CHO_CAP' and r.stock_doc_id is None and balance(db, a.code) == 0
    error('FORBIDDEN', svc.cancel, db, p['other_tech'], r.id, 'Không phải của tôi', r.version)
    svc.cancel(db, p['technician'], r.id, 'Không cần nữa', r.version)
    assert r.status == 'DA_HUY'
    r2 = ask(db, p)
    svc.out_of_stock(db, p['warehouse'], r2.id, 'Hết hàng chờ nhập', r2.version)
    assert r2.status == 'HET_HANG'


def test_viec_cua_toi_kho_va_danh_sach(client, db, held):
    p, a, b = held
    r = ask(db, p)
    rows = [row for g in my_work.collect(db, p['warehouse']) for row in g['rows'] if row.get('number') == r.number]
    assert rows and rows[0]['customer'] == 'Thợ xin mang theo'
    service_login(client, p['warehouse'])
    html = client.get('/yeu-cau-linh-kien').text
    assert r.number in html and 'Thợ xin mang theo' in html


def test_web_tho_xin_va_huy(client, db, held):
    p, a, b = held
    csrf = service_login(client, p['technician'])
    page = client.get('/hang-toi-giu').text
    assert 'href="/hang-toi-giu/xin-linh-kien"' in page and 'Yêu cầu linh kiện' in page
    form = client.get('/hang-toi-giu/xin-linh-kien').text
    assert 'data-source="/hang-toi-giu/tim-linh-kien"' in form and a.code in form
    assert 'data-part-pick="TD-0001"' in client.get('/hang-toi-giu/tim-linh-kien?q=may in').text
    r = client.post('/hang-toi-giu/xin-linh-kien', data=dict(csrf=csrf, product_code='TD-0001', qty='2', reason=''))
    assert r.status_code == 400 and 'lý do' in r.text and 'value="TD-0001"' in r.text
    r = client.post('/hang-toi-giu/xin-linh-kien', data=dict(csrf=csrf, product_code='TD-0001', qty='2', reason='Mang sẵn'), follow_redirects=False)
    assert r.status_code == 303
    row = db.scalar(select(PartRequest))
    page = client.get('/hang-toi-giu').text
    assert row.number in page and 'Chờ cấp' in page and f'/hang-toi-giu/xin-linh-kien/{row.id}/huy' in page
    r = client.post(f'/hang-toi-giu/xin-linh-kien/{row.id}/huy', data=dict(csrf=csrf, version=row.version), follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(PartRequest, row.id).status == 'DA_HUY'
    service_login(client, p['other_tech'])
    assert client.get('/hang-toi-giu').text.count(row.number) == 0
    service_login(client, p['dispatcher'])
    assert client.get('/hang-toi-giu/xin-linh-kien').status_code == 403
