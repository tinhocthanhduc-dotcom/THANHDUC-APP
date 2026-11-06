"""Định mức vật tư sản xuất (TUẤN duyệt mẫu 06/11/2026): chỉ điền sẵn phiếu SX, không ghi kho, không đổi giá vốn."""
import json
import re
from decimal import Decimal

import pytest

from app import production_bom as bom, production_report as rep
from app.errors import BusinessError
from app.models import AuditLog, ProductionBom, ProductionOrder, StockLedger
from test_production import shop, create, finish, receive  # noqa: F401
from test_my_work import people, purchase_people  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_web import service_login


def _err(code, fn, *a, **kw):
    with pytest.raises(BusinessError) as e:
        fn(*a, **kw)
    assert e.value.code == code


def test_save_plan_and_permissions(db, shop):  # noqa: F811
    p = shop[0]
    ledger = db.query(StockLedger).count()
    _err('FORBIDDEN', bom.save, db, p['technician'], 'TD-0100', [('TD-0101', '0,08')])
    _err('BAD_MATERIAL', bom.save, db, p['warehouse'], 'TD-0100', [('TD-0100', '1')])
    _err('BAD_MATERIAL', bom.save, db, p['warehouse'], 'TD-0100', [('TD-0102', '1'), ('TD-0102', '2')])
    _err('BAD_QTY', bom.save, db, p['warehouse'], 'TD-0100', [('TD-0102', '1,2345')])
    bom.save(db, p['dispatcher'], 'TD-0100', [('TD-0101', '0,08'), ('TD-0102', '1'), ('TD-0103', '0')], 'Vỏ khách trả')
    boms = bom.bom_map(db)
    assert boms == {'TD-0100': [('TD-0101', Decimal('0.080')), ('TD-0102', Decimal('1.000'))]}
    assert bom.plan_lines(boms, [('TD-0100', 10)]) == [('TD-0101', Decimal('0.800')), ('TD-0102', Decimal('10.000'))]
    assert db.get(ProductionBom, 'TD-0100').note == 'Vỏ khách trả'
    bom.save(db, p['warehouse'], 'TD-0100', [('TD-0102', '2')])
    assert bom.bom_map(db) == {'TD-0100': [('TD-0102', Decimal('2.000'))]}
    assert db.query(AuditLog).filter_by(action='production.bom').count() == 2
    bom.save(db, p['warehouse'], 'TD-0100', [])                      # bỏ hết dòng = xóa định mức
    assert db.get(ProductionBom, 'TD-0100') is None and bom.bom_map(db) == {}
    assert db.query(StockLedger).count() == ledger


def test_learn_from_order_and_deviation(db, shop):  # noqa: F811
    row = finish(db, shop, good=9, scrap=1)                           # dự kiến 1/2/5, thật dùng 0,5/1/4
    learned = bom.from_order(row)
    assert learned == [('TD-0101', Decimal('0.056')), ('TD-0102', Decimal('0.111')), ('TD-0103', Decimal('0.444'))]
    assert set(bom.deviations(row)) == {1, 2, 3}
    assert rep.build(db, *rep.week_of(row.finished_at.astimezone(rep.TZ).date()))['deviate'] == 1
    assert bom.from_order(create(db, shop)) is None                   # chưa hoàn thành


def test_pages_prefill_and_roles(client, db, shop):  # noqa: F811
    p = shop[0]
    row = finish(db, shop, good=9, scrap=1)
    service_login(client, p['technician'])
    assert client.get('/san-xuat/dinh-muc', follow_redirects=False).status_code in (303, 403)
    assert 'href="/san-xuat/dinh-muc"' not in client.get('/san-xuat').text
    csrf = service_login(client, p['warehouse'])
    assert 'href="/san-xuat/dinh-muc"' in client.get('/san-xuat').text
    page = client.get('/san-xuat/' + row.number).text
    assert 'Lưu làm định mức' in page and f'/san-xuat/dinh-muc/TD-0100?tu={row.number}' in page and 'data-sx-dev' in page
    html = client.get('/san-xuat/dinh-muc?tab=chua').text
    assert 'Hộp mực tái sinh' in html and f'Lưu từ {row.number}' in html
    edit = client.get(f'/san-xuat/dinh-muc/TD-0100?tu={row.number}').text
    assert 'data-bom-source' in edit and 'value="0,056"' in edit
    r = client.post('/san-xuat/dinh-muc/TD-0100', data=dict(csrf=csrf, material_product=['TD-0101', 'TD-0102'], bom_qty=['0,06', '1'], note=''),
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/san-xuat/dinh-muc?da_luu=TD-0100'
    assert bom.bom_map(db)['TD-0100'] == [('TD-0101', Decimal('0.060')), ('TD-0102', Decimal('1.000'))]
    assert 'Lưu làm định mức' not in client.get('/san-xuat/' + row.number).text
    html = client.get('/san-xuat/dinh-muc').text
    assert 'Mực' in html and 'Vốn ước tính' in html and 'Sửa' in html
    r = client.post('/san-xuat/dinh-muc/TD-0100', data=dict(csrf=csrf, material_product=['TD-0100'], bom_qty=['1'], note=''), follow_redirects=False)
    assert r.status_code == 400 and 'trùng thành phẩm' in r.text
    # Form lập phiếu mang định mức để điền sẵn vật tư dự kiến.
    form = client.get('/san-xuat/moi').text
    boms = json.loads(re.search(r'<script type="application/json" id="sx-boms">(.*?)</script>', form, re.S).group(1))
    assert boms['TD-0100'] == [['TD-0101', '0.06'], ['TD-0102', '1']]
    assert 'data-bom-undo' in form and 'data-sx-materials-box' in form
