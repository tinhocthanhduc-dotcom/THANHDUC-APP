"""Đợt 6 tối ưu thao tác (TUẤN duyệt mẫu 18/11/2026): Việc kho trong ngày, Giao đủ / Nhận đủ tại dòng."""
from decimal import Decimal

from sqlalchemy import select

from app import purchase_orders, sales_orders
from app.models import Product, PurchaseOrder, SalesOrder, StockBalance
from app.schemas import PurchaseOrderIn, SalesOrderIn
from test_my_work import DAY, groups, people  # noqa: F401
from test_purchase_orders import purchase_people  # noqa: F401
from test_purchase_reports import received
from test_web import service_login


def _sale(db, p, qty=2, product='TD-0002', fulfillment='TAI_QUAY'):
    row = sales_orders.create(db, p['sales'], SalesOrderIn(customer_code='KTD-000001', fulfillment=fulfillment, lines=[
        dict(kind='HANG', product_code=product, warehouse_code='K58', qty=qty, unit_price=300000, vat_rate=0)]))
    sales_orders.confirm(db, p['sales'], row.id, row.version)
    db.refresh(row)
    return row


def _stock(db, product='TD-0002'):
    return db.scalar(select(StockBalance.qty).where(StockBalance.warehouse_code == 'K58', StockBalance.product_code == product).execution_options(populate_existing=True))


def test_giao_du_tai_dong_khi_du_hang_khong_serial(client, db, people):
    p = people
    received(db, p, qty=10, product='TD-0002')
    sale = _sale(db, p)
    row = next(r for r in groups(db, p, 'warehouse')['orders_waiting']['rows'] if r['id'] == sale.id)
    assert row['act']['kind'] == 'giao' and row['act']['ok'] == 'Giao đủ' and 'K58 đủ hàng' in row['reason']
    csrf = service_login(client, p['warehouse'])
    page = client.get('/viec-cua-toi').text
    assert 'Việc kho trong ngày' in page and '1. Giao hàng' in page and f'action="/viec-cua-toi/kho/giao/{sale.id}"' in page
    # Bán hàng không phải thủ kho: chặn.
    sales_csrf = service_login(client, p['sales'])
    assert client.post(f'/viec-cua-toi/kho/giao/{sale.id}', data=dict(csrf=sales_csrf, version=sale.version), headers={'X-Tai-Dong': '1'}).status_code == 403
    csrf = service_login(client, p['warehouse'])
    stale = client.post(f'/viec-cua-toi/kho/giao/{sale.id}', data=dict(csrf=csrf, version=sale.version - 1), headers={'X-Tai-Dong': '1'})
    assert stale.status_code == 409 and stale.json()['ok'] is False
    r = client.post(f'/viec-cua-toi/kho/giao/{sale.id}', data=dict(csrf=csrf, version=sale.version), headers={'X-Tai-Dong': '1'})
    assert r.status_code == 200 and r.json() == {'ok': True}
    assert db.get(SalesOrder, sale.id, populate_existing=True).status == 'DA_GIAO'
    assert _stock(db) == Decimal(8)


def test_khong_co_nut_khi_can_serial_thieu_hang_hoac_tho_giao(db, people):
    p = people
    received(db, p, qty=1, product='TD-0002')
    ok = _sale(db, p, qty=1)
    big = _sale(db, p, qty=5)
    db.get(Product, 'TD-0001').track_serial = True; db.commit()
    received(db, p, qty=3, product='TD-0001')
    serial = _sale(db, p, qty=1, product='TD-0001')
    rows = {r['id']: r for r in groups(db, p, 'warehouse')['orders_waiting']['rows']}
    assert rows[ok.id].get('act')
    assert 'act' not in rows[big.id] and rows[big.id]['why_not'] == 'thiếu hàng ở K58'
    assert 'act' not in rows[serial.id] and rows[serial.id]['why_not'] == 'cần nhập serial'


def test_nhan_du_don_mua_tai_dong(client, db, people):
    p = people
    a = purchase_orders.create(db, p['warehouse'], PurchaseOrderIn(supplier_code='NCC-0001', order_date=DAY,
        lines=[dict(product_code='TD-0002', warehouse_code='K58', qty=3, unit_cost=1000),
               dict(product_code='TD-0001', warehouse_code='K58', qty=1, unit_cost=5000)]))
    purchase_orders.order(db, p['warehouse'], a.id, a.version)
    db.refresh(a)
    row = next(r for r in groups(db, p, 'warehouse')['purchases_waiting']['rows'] if r['id'] == a.id)
    assert row['act']['kind'] == 'nhan-mua' and row['reason'] == '2 dòng · về K58'
    csrf = service_login(client, p['warehouse'])
    r = client.post(f'/viec-cua-toi/kho/nhan-mua/{a.id}', data=dict(csrf=csrf, version=a.version), follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/viec-cua-toi?xong=tai-dong'
    a = db.get(PurchaseOrder, a.id, populate_existing=True)
    assert all(l.received_qty == l.qty for l in a.lines) and a.status not in ('DA_DAT', 'NHAN_MOT_PHAN')
    assert _stock(db) == Decimal(3)
    assert client.post(f'/viec-cua-toi/kho/xoa/{a.id}', data=dict(csrf=csrf, version=a.version), headers={'X-Tai-Dong': '1'}).status_code == 404


def test_nhan_may_vao_kho_tai_dong(client, db, people):
    from app import equipment
    from app.models import EquipmentCustody
    from app.schemas import EquipmentIn
    import test_equipment_custody as ec
    p = people
    eq = equipment.create(db, p['dispatcher'], EquipmentIn(serial='AB123', product_code='TD-0001', customer_code='KTD-000001'))
    t = ec.ticket(db, p); ec.receive(db, p, eq, t); handed = ec.handover(db, p, eq, t)
    row = next(r for r in groups(db, p, 'warehouse')['machines_warehouse']['rows'] if r['id'] == t.id)
    assert row['act'] == dict(kind='nhan-may', id=handed.id, version=handed.version, ok='Nhận vào kho', done='Đã nhận vào kho',
                              url=f'/viec-cua-toi/kho/nhan-may/{handed.id}', inline_open=True)
    csrf = service_login(client, p['warehouse'])
    r = client.post(row['act']['url'], data=dict(csrf=csrf, version=handed.version), headers={'X-Tai-Dong': '1'})
    assert r.status_code == 200 and r.json() == {'ok': True}
    assert db.get(EquipmentCustody, handed.id, populate_existing=True).status == 'DA_NHAN'
    assert 'machines_warehouse' not in {k for k, g in groups(db, p, 'warehouse').items() if g['count']}
