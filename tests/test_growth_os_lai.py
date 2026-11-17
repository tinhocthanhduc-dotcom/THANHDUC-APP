"""Growth OS Phần D: lãi đóng góp theo đơn (TUẤN 17/11/2026). Khóa riêng có giá vốn, chỉ POST /marketing/v1/contribution."""
from sqlalchemy import select

from app import ai_agent, sales_orders, sales_returns
from app.models import AuditLog, ServiceExpense
from app.schemas import SalesOrderIn, SalesReturnIn
from test_sales_orders import people, stocked  # noqa: F401

GOOD = dict(kind='HANG', product_code='TD-0001', warehouse_code='K58', qty='3', unit_price=150000, vat_rate=8)


def _delivered(db, p):
    o = sales_orders.create(db, p['sales'], SalesOrderIn(customer_code='KTD-000001', lines=[GOOD]))
    o = sales_orders.confirm(db, p['sales'], o.id, o.version)
    return sales_orders.deliver(db, p['warehouse'], o.id, o.version)


def test_lai_dong_gop_gia_von_luc_ban(client, db, stocked):
    p = stocked
    h = {'Authorization': 'Bearer ' + ai_agent.create_key(db, p['director'], 'growth-os-lai')}
    o = _delivered(db, p)
    gh = sales_orders.deliveries(db, o.id)[0]
    gh_id = gh['id'] if isinstance(gh, dict) else gh.id
    db.add(ServiceExpense(order_id=o.id, delivery_id=gh_id, technician_id=p['warehouse'].id, kind='GUI_HANG',
                          description='Gửi xe', amount=30000, cost=30000, status='DA_DUYET'))
    db.commit()
    waiting = sales_orders.create(db, p['sales'], SalesOrderIn(customer_code='KTD-000001', lines=[GOOD]))
    r = client.post('/marketing/v1/contribution', headers=h, json=dict(order_ids=[o.id, waiting.id, 999999], marketing_cost=50000))
    assert r.status_code == 200, r.text
    body = r.json()
    row = {x['order_id']: x for x in body['items']}
    a = row[o.id]
    # 3 hộp × 150.000 chưa VAT; giá vốn lúc xuất 100.000/hộp
    assert (a['net_revenue'], a['cogs_at_sale'], a['fees'], a['contribution_before_marketing']) == (450000, 300000, 30000, 120000)
    assert 'PHI_SAN' in a['missing'] and 'GIA_VON' not in a['missing']
    assert row[waiting.id]['net_revenue'] == 0 and 'CHUA_GIAO' in row[waiting.id]['missing']
    assert body['not_found'] == [999999] and body['calc_version']
    assert body['totals']['contribution'] == 120000 - 50000
    assert 'unit_cost' not in r.text and 'KTD-000001' not in r.text and 'TD-0001' not in r.text

    # Trả 1 hộp: trừ doanh thu và giá vốn đúng lúc bán
    db.refresh(o)
    sales_returns.create(db, p['warehouse'], o.id, SalesReturnIn(version=o.version, reason='Khách trả',
                         lines=[dict(delivery_id=gh_id, order_line_no=1, qty=1, condition='TOT')]))
    a = client.post('/marketing/v1/contribution', headers=h, json=dict(order_ids=[o.id])).json()['items'][0]
    assert (a['returns'], a['net_revenue'], a['cogs_at_sale'], a['contribution_before_marketing']) == (150000, 300000, 200000, 70000)
    logs = [l.after['path'] for l in db.scalars(select(AuditLog).where(AuditLog.action == 'ai.marketing_read'))]
    assert logs == ['/contribution', '/contribution']


def test_khoa_tach_rieng(client, db, stocked):
    p = stocked
    o = _delivered(db, p)
    read = {'Authorization': 'Bearer ' + ai_agent.create_key(db, p['director'], 'growth-os')}
    lai = {'Authorization': 'Bearer ' + ai_agent.create_key(db, p['director'], 'growth-os-lai')}
    # Khóa đọc thường không tính được lãi; khóa lãi không đọc được đường khác
    assert client.post('/marketing/v1/contribution', headers=read, json=dict(order_ids=[o.id])).status_code == 403
    for path in ('/marketing/v1/products', '/marketing/v1/orders', '/customers', '/sales-orders'):
        assert client.get(path, headers=lai, follow_redirects=False).status_code in (401, 403, 303, 307), path
    assert client.get('/marketing/v1/contribution', headers=lai).status_code == 405
    assert client.post('/marketing/v1/contribution', headers=lai, json=dict(order_ids=[])).status_code == 422
    assert client.post('/marketing/v1/contribution', headers=lai, json=dict(order_ids=[o.id], marketing_cost=-1)).status_code == 422
    assert client.post('/marketing/v1/contribution', headers={**lai, 'cf-ray': 'x'}, json=dict(order_ids=[o.id])).status_code == 403
    import pytest
    from app.errors import BusinessError
    with pytest.raises(BusinessError):
        ai_agent.create_key(db, p['sales'], 'growth-os-lai')
