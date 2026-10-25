"""Thiết bị ngoài danh mục Hàng hóa: nhập tên máy, không cần mã hàng; vẫn hiện ở danh sách, trang khách, phiếu dịch vụ."""
import pytest
from sqlalchemy import select

from app import equipment
from app.models import AuditLog, Equipment, ServiceTicket
from app.schemas import EquipmentIn
from test_my_work import people, purchase_people  # noqa: F401 (fixtures)
from test_sv1_may_tren_phieu import Tags
from test_web import d1_catalog, device_form, service_form, service_login, web_login  # noqa: F401 (fixtures)


def outside_form(csrf, **kw):
    data = device_form(csrf, serial='NGOAI-01', product_source='TEN', model_name='Canon LBP 2900', product_code='', product_q='')
    data.update(kw)
    return data


def test_form_has_no_summary_line_and_offers_outside_catalog(client, web_login, d1_catalog):
    web_login('dieu.phoi', 'dispatcher')
    page = client.get('/thiet-bi/moi').text
    assert 'Bảo hành đến —' not in page and 'hỗ trợ tận nơi đến —' not in page
    assert 'Không có trong danh mục' in page and 'name="model_name"' in page
    assert 'Có trong Hàng hóa' in page and 'name="product_q"' in page


def test_create_outside_catalog_device_without_product(client, web_login, d1_catalog, db):
    csrf = web_login('dieu.phoi', 'dispatcher')
    r = client.post('/thiet-bi/moi', data=outside_form(csrf), follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].startswith('/thiet-bi/NGOAI-01'), r.text
    eq = db.get(Equipment, 'NGOAI-01')
    assert eq.product_code is None and eq.model_name == 'Canon LBP 2900' and eq.customer_code == 'KTD-000001'
    assert 'canon lbp 2900' in eq.search_text and eq.warranty_until.isoformat() == '2027-12-31'
    assert db.scalar(select(AuditLog).where(AuditLog.entity == 'equipment', AuditLog.entity_id == 'NGOAI-01')).action == 'equipment.create'
    # Form sửa mở lại đúng nhánh "Không có trong danh mục" và tên máy.
    page = client.get('/thiet-bi/NGOAI-01').text
    parsed = Tags(page)
    assert 'checked' in parsed.find('input', name='product_source', value='TEN')[0]
    assert parsed.find('input', name='model_name', value='Canon LBP 2900')
    # Danh sách và tìm kiếm theo tên máy tự nhập.
    listing = client.get('/thiet-bi').text
    assert 'Canon LBP 2900' in listing and 'Ngoài danh mục' in listing
    assert 'NGOAI-01' in client.get('/thiet-bi?q=lbp+2900').text
    assert 'NGOAI-01' in client.get('/thiet-bi?q=lbp+2900&phan=1').text
    # Trang khách liệt kê máy không có mã hàng.
    assert 'Canon LBP 2900' in client.get('/khach-hang/KTD-000001?tab=may').text


def test_outside_catalog_requires_model_name_and_keeps_form(client, web_login, d1_catalog, db):
    csrf = web_login('dieu.phoi', 'dispatcher')
    r = client.post('/thiet-bi/moi', data=outside_form(csrf, model_name='   ', note='Giữ ghi chú'))
    assert r.status_code == 400 and 'Chưa nhập tên máy' in r.text and 'Giữ ghi chú' in r.text
    assert db.get(Equipment, 'NGOAI-01') is None
    # Nhánh "Có trong Hàng hóa" vẫn bắt buộc chọn hàng dù có gõ tên máy.
    r = client.post('/thiet-bi/moi', data=outside_form(csrf, product_source='DM'))
    assert r.status_code == 400 and 'Chưa chọn hàng' in r.text


def test_switch_between_catalog_and_free_text_on_update(client, web_login, d1_catalog, db):
    csrf = web_login('dieu.phoi', 'dispatcher')
    assert client.post('/thiet-bi/moi', data=device_form(csrf, serial='SW-01'), follow_redirects=False).status_code == 303
    r = client.post('/thiet-bi/SW-01', data=outside_form(csrf, version=1, model_name='HP LaserJet 1020'), follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    eq = db.get(Equipment, 'SW-01')
    assert eq.product_code is None and eq.model_name == 'HP LaserJet 1020' and 'laserjet' in eq.search_text
    r = client.post('/thiet-bi/SW-01', data=device_form(csrf, version=2, serial='SW-01', product_source='DM', model_name='bỏ qua'), follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    eq = db.get(Equipment, 'SW-01')
    assert eq.product_code == 'TD-0001' and eq.model_name == ''


def test_schema_requires_product_or_model_name():
    with pytest.raises(ValueError):
        EquipmentIn(serial='X1')
    assert EquipmentIn(serial='X1', model_name='Brother HL-2321D').product_code is None


def test_service_intake_new_machine_outside_catalog(client, db, people):
    p = people
    csrf = service_login(client, p['dispatcher'])
    data = service_form(csrf, kind='MANG_VE', create_machine='1', new_serial='DV-NGOAI-1', new_machine_source='TEN',
                        new_model_name='Epson L3110', new_product_code='', new_product_q='', symptom='Kẹt giấy')
    r = client.post('/dich-vu/moi', data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    eq = db.get(Equipment, 'DV-NGOAI-1')
    assert eq.product_code is None and eq.model_name == 'Epson L3110' and eq.customer_code == 'KTD-000001'
    t = db.scalar(select(ServiceTicket).where(ServiceTicket.serial == 'DV-NGOAI-1'))
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'Epson L3110' in page
    assert 'Epson L3110' in client.get('/dich-vu?phan=1').text
    # Thiếu tên máy thì giữ form, không tạo gì.
    bad = client.post('/dich-vu/moi', data={**data, 'new_serial': 'DV-NGOAI-2', 'new_model_name': ''})
    assert bad.status_code == 400 and 'Chưa nhập tên máy' in bad.text
    assert db.get(Equipment, 'DV-NGOAI-2') is None
