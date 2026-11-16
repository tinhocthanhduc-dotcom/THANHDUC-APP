"""Cổng chỉ đọc cho Growth OS (TUẤN 16/11/2026, Phần A): khóa riêng marketing_read, products, stock, margin-band.

Nghiệm thu: đọc được TD-0045 (giá bán, tồn khả dụng, nhóm biên); mọi đường khác bị từ chối; không lộ giá vốn/SĐT; mỗi lần đọc có nhật ký.
"""
from datetime import timedelta

from sqlalchemy import select

from app import ai_agent, marketing_api
from app.models import (AuditLog, Customer, Product, SalesOrder, StockBalance, StockMinLevel, StockReservation,
                        Warehouse)


def _setup(db, make_user):
    director = make_user('gd', 'director')
    db.add_all([Warehouse(code='58', name='Kho 58', kind='KHO'), Warehouse(code='145', name='Kho 145', kind='KHO'),
                Warehouse(code='CU', name='Kho cũ', kind='KHO', active=False),
                Warehouse(code='LOI', name='Hàng lỗi', kind='LOI'),
                Warehouse(code='T1', name='Kho thợ', kind='THO', holder_id=director.id)])
    db.add_all([Product(code='TD-0045', name='Hộp mực in 136X-W1360X', group_name='Hộp mực', unit='hộp', sale_price=200000),
                Product(code='TD-0046', name='Mực lỗ', sale_price=100000),
                Product(code='TD-0047', name='Chưa có giá vốn', sale_price=50000),
                Product(code='TD-0099', name='Hàng chạy thử', sale_price=1, is_test=True)])
    db.flush()
    db.add_all([StockBalance(warehouse_code='58', product_code='TD-0045', qty=10, value=1200000, avg_cost=120000),
                StockBalance(warehouse_code='145', product_code='TD-0045', qty=3, value=360000, avg_cost=120000),
                StockBalance(warehouse_code='CU', product_code='TD-0045', qty=50, value=6000000, avg_cost=120000),
                StockBalance(warehouse_code='T1', product_code='TD-0045', qty=4, value=480000, avg_cost=120000),
                StockBalance(warehouse_code='58', product_code='TD-0046', qty=1, value=150000, avg_cost=150000),
                StockMinLevel(warehouse_code='58', product_code='TD-0045', min_qty=5, updated_by=director.id),
                StockMinLevel(warehouse_code='T1', product_code='TD-0045', min_qty=9, updated_by=director.id)])
    db.add(Customer(code='KTD-0001', name='Khách A'))
    db.flush()
    order = SalesOrder(number='DB-26-00001', customer_code='KTD-0001', status='DA_CHOT', created_by=director.id)
    db.add(order)
    db.flush()
    db.add(StockReservation(order_id=order.id, order_line_id=1, warehouse_code='58', product_code='TD-0045', qty=2,
                            status='GIU', created_by=director.id))
    db.commit()
    raw = ai_agent.create_key(db, director, 'growth-os')
    return director, {'Authorization': 'Bearer ' + raw}


def test_nghiem_thu_doc_td0045(client, db, make_user):
    _, h = _setup(db, make_user)
    r = client.get('/marketing/v1/products', headers=h)
    assert r.status_code == 200, r.text
    items = {p['code']: p for p in r.json()['items']}
    assert 'TD-0099' not in items  # hàng chạy thử không ra ngoài
    p = items['TD-0045']
    assert p['name'] == 'Hộp mực in 136X-W1360X' and p['group'] == 'Hộp mực' and p['unit'] == 'hộp'
    assert p['sale_price'] == 200000 and p['price_includes_vat'] is False and p['sale_price_with_vat'] == 216000
    assert p['active'] is True and p['data_version'] == 1 and p['updated_at']
    assert not {'avg_cost', 'cost', 'value'} & set(p)

    r = client.get('/marketing/v1/stock?codes=TD-0045,td-0046,TD-9999,TD-0099', headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    s = {i['code']: i for i in body['items']}
    # 58: 10 − 2 giữ = 8; 145: 3. Không tính kho ngừng dùng, kho lỗi, kho thợ.
    assert s['TD-0045']['available'] == 11 and s['TD-0045']['min_level'] == 5
    assert {w['warehouse'] for w in s['TD-0045']['by_warehouse']} == {'58', '145'}
    assert s['TD-0046']['available'] == 1
    assert body['not_found'] == ['TD-9999', 'TD-0099']
    assert 'value' not in str(body) and 'avg_cost' not in str(body)

    r = client.get('/marketing/v1/products/TD-0045/margin-band', headers=h)
    assert r.status_code == 200 and r.json()['band'] == 'CAO'  # (200k − 120k)/200k = 40%
    assert set(r.json()) == {'code', 'band', 'reason', 'as_of'}
    assert client.get('/marketing/v1/products/TD-0046/margin-band', headers=h).json()['band'] == 'AM'
    none = client.get('/marketing/v1/products/td-0047/margin-band', headers=h).json()
    assert none['band'] is None and none['reason'] == 'CHUA_CO_GIA_VON'
    assert client.get('/marketing/v1/products/TD-0099/margin-band', headers=h).status_code == 404


def test_nguong_nhom_bien():
    band = marketing_api.band_for
    assert band(100, 70) == 'CAO' and band(100, 71) == 'VUA' and band(100, 85) == 'VUA'
    assert band(100, 86) == 'THAP' and band(100, 100) == 'THAP' and band(100, 101) == 'AM'
    assert band(100, None) is None and band(0, 50) is None


def test_moi_lan_doc_ghi_nhat_ky(client, db, make_user):
    _, h = _setup(db, make_user)
    client.get('/marketing/v1/products', headers=h)
    client.get('/marketing/v1/stock?codes=TD-0045', headers=h)
    client.get('/marketing/v1/products/TD-0045/margin-band', headers=h)
    logs = list(db.scalars(select(AuditLog).where(AuditLog.action == 'ai.marketing_read').order_by(AuditLog.id)))
    assert [log.after['path'] for log in logs] == ['/products', '/stock', '/products/TD-0045/margin-band']
    assert logs[1].after['params'] == {'codes': ['TD-0045'], 'count': 1}
    # Nhật ký hiện ở Trợ lý AI › Nhật ký
    assert any(r['action'] == 'ai.marketing_read' for r in ai_agent.listing(db, 'nhat-ky', 0, 10))


def test_tu_choi_ngoai_pham_vi(client, db, make_user):
    director, h = _setup(db, make_user)
    # Không khóa, khóa sai, khóa đi qua Cloudflare
    assert client.get('/marketing/v1/products').status_code == 401
    assert client.get('/marketing/v1/products', headers={'Authorization': 'Bearer tdai_sai'}).status_code == 401
    assert client.get('/marketing/v1/products', headers={**h, 'cf-connecting-ip': '1.2.3.4'}).status_code == 403
    # Khóa marketing không mở được đường nào khác
    for path in ('/customers', '/customers/KTD-0001', '/products', '/sales-orders', '/quotes', '/stock-docs',
                 '/ai/xem', '/ai/viec', '/n8n/viec-cho', '/tong-quan', '/khach-hang'):
        r = client.get(path, headers=h, follow_redirects=False)
        assert r.status_code in (401, 403, 404, 303, 307), (path, r.status_code)
        assert 'KTD-0001' not in r.text and 'Khách A' not in r.text, path
    # Chỉ đọc: không có POST/PUT/DELETE
    for method in ('post', 'put', 'patch', 'delete'):
        assert getattr(client, method)('/marketing/v1/products', headers=h).status_code == 405
    # Khóa AI khác không đọc được cổng marketing
    other = ai_agent.create_key(db, director, 'n8n')
    assert client.get('/marketing/v1/products', headers={'Authorization': 'Bearer ' + other}).status_code == 403
    # Tạm dừng / thu hồi
    ai_agent.set_paused(db, director, True, 'growth-os')
    assert client.get('/marketing/v1/products', headers=h).status_code == 423
    ai_agent.revoke_key(db, director, 'growth-os')
    assert client.get('/marketing/v1/products', headers=h).status_code == 401


def test_doc_theo_moc_va_phan_trang(client, db, make_user):
    _, h = _setup(db, make_user)
    first = client.get('/marketing/v1/products?limit=2', headers=h).json()
    assert len(first['items']) == 2 and first['next_cursor']
    rest = client.get('/marketing/v1/products', headers=h, params={'limit': 2, 'cursor': first['next_cursor']}).json()
    codes = [p['code'] for p in first['items'] + rest['items']]
    assert sorted(codes) == ['TD-0045', 'TD-0046', 'TD-0047'] and rest['next_cursor'] is None
    # Đổi giá một mã: đọc theo mốc chỉ ra mã đó, data_version tăng
    p = db.get(Product, 'TD-0046')
    mark = p.updated_at
    p.sale_price = 110000
    db.commit()
    db.refresh(p)
    since = (p.updated_at - timedelta(microseconds=1)).isoformat()
    assert p.updated_at > mark
    got = client.get('/marketing/v1/products', headers=h, params={'updated_since': since}).json()['items']
    assert [i['code'] for i in got] == ['TD-0046'] and got[0]['sale_price'] == 110000 and got[0]['data_version'] == 2
    assert client.get('/marketing/v1/products?updated_since=hom-qua', headers=h).status_code == 422
    assert client.get('/marketing/v1/stock', headers=h).status_code == 422
    many = ','.join(f'TD-{i:04d}' for i in range(201))
    assert client.get('/marketing/v1/stock', headers=h, params={'codes': many}).status_code == 422
