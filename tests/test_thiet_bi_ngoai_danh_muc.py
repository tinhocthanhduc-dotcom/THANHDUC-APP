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
    # Một ô duy nhất: không bấm chọn hàng (product_code trống) thì tên đã gõ là tên máy.
    data = device_form(csrf, serial='NGOAI-01', product_code='', product_q='Canon LBP 2900')
    data.update(kw)
    return data


def test_form_is_one_machine_box_with_customer_first(client, web_login, d1_catalog):
    web_login('dieu.phoi', 'dispatcher')
    page = client.get('/thiet-bi/moi').text
    assert 'Bảo hành đến —' not in page and 'hỗ trợ tận nơi đến —' not in page
    assert 'name="product_q"' in page and 'name="product_source"' not in page and 'name="model_name"' not in page
    assert 'lưu là máy ngoài danh mục' in page
    # Khách hàng đứng trước serial và máy; địa điểm chỉ hiện khi khách có địa điểm.
    assert page.index('name="customer_q"') < page.index('name="serial"') < page.index('name="product_q"')
    assert 'Địa điểm của khách' not in page
    # Danh sách gợi ý có dòng "Ngoài danh mục" để bấm dùng tên đã gõ.
    options = client.get('/thiet-bi/tim-hang?q=brother+hl').text
    assert 'data-device-free' in options and 'Ngoài danh mục' in options and 'brother hl' in options
    assert 'data-device-free' not in client.get('/thiet-bi/tim-hang?q=').text


def test_site_block_only_when_customer_has_sites(client, web_login, d1_catalog, db):
    from app.models import CustomerSite
    web_login('dieu.phoi', 'dispatcher')
    assert 'Địa điểm của khách' not in client.get('/thiet-bi/dia-diem?customer_code=KTD-000001').text
    db.add(CustomerSite(customer_code='KTD-000001', name='Chi nhánh 58', created_by=1)); db.commit()
    page = client.get('/thiet-bi/dia-diem?customer_code=KTD-000001').text
    assert 'Địa điểm của khách' in page and 'Chi nhánh 58' in page and '<fieldset' not in page


def test_create_outside_catalog_device_without_product(client, web_login, d1_catalog, db):
    csrf = web_login('dieu.phoi', 'dispatcher')
    r = client.post('/thiet-bi/moi', data=outside_form(csrf), follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].startswith('/thiet-bi/NGOAI-01'), r.text
    eq = db.get(Equipment, 'NGOAI-01')
    assert eq.product_code is None and eq.model_name == 'Canon LBP 2900' and eq.customer_code == 'KTD-000001'
    assert 'canon lbp 2900' in eq.search_text and eq.warranty_until.isoformat() == '2027-12-31'
    assert db.scalar(select(AuditLog).where(AuditLog.entity == 'equipment', AuditLog.entity_id == 'NGOAI-01')).action == 'equipment.create'
    # Form sửa mở lại với tên máy trong ô máy, không có mã hàng.
    page = client.get('/thiet-bi/NGOAI-01').text
    parsed = Tags(page)
    assert parsed.find('input', name='product_q', value='Canon LBP 2900') and 'Máy ngoài danh mục: Canon LBP 2900' in page
    assert parsed.find('input', name='product_code', value='')
    # Danh sách và tìm kiếm theo tên máy tự nhập.
    listing = client.get('/thiet-bi').text
    assert 'Canon LBP 2900' in listing and 'Ngoài danh mục' in listing
    assert 'NGOAI-01' in client.get('/thiet-bi?q=lbp+2900').text
    assert 'NGOAI-01' in client.get('/thiet-bi?q=lbp+2900&phan=1').text
    # Trang khách liệt kê máy không có mã hàng.
    assert 'Canon LBP 2900' in client.get('/khach-hang/KTD-000001?tab=may').text


def test_empty_machine_box_keeps_form_and_exact_name_picks_product(client, web_login, d1_catalog, db):
    csrf = web_login('dieu.phoi', 'dispatcher')
    r = client.post('/thiet-bi/moi', data=outside_form(csrf, product_q='   ', note='Giữ ghi chú'))
    assert r.status_code == 400 and 'Chưa có máy' in r.text and 'Giữ ghi chú' in r.text
    assert db.get(Equipment, 'NGOAI-01') is None
    # Gõ đúng tên hoặc mã của một hàng trong danh mục (không bấm chọn) vẫn ra mã hàng, không thành máy ngoài danh mục.
    r = client.post('/thiet-bi/moi', data=outside_form(csrf, serial='EXACT-1', product_q='td-0001'), follow_redirects=False)
    assert r.status_code == 303, r.text
    eq = db.get(Equipment, 'EXACT-1')
    assert eq.product_code == 'TD-0001' and eq.model_name == ''


def test_switch_between_catalog_and_free_text_on_update(client, web_login, d1_catalog, db):
    csrf = web_login('dieu.phoi', 'dispatcher')
    assert client.post('/thiet-bi/moi', data=device_form(csrf, serial='SW-01'), follow_redirects=False).status_code == 303
    r = client.post('/thiet-bi/SW-01', data=outside_form(csrf, version=1, serial='SW-01', product_q='HP LaserJet 1020'), follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    eq = db.get(Equipment, 'SW-01')
    assert eq.product_code is None and eq.model_name == 'HP LaserJet 1020' and 'laserjet' in eq.search_text
    r = client.post('/thiet-bi/SW-01', data=device_form(csrf, version=2, serial='SW-01'), follow_redirects=False)
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
    data = service_form(csrf, kind='MANG_VE', create_machine='1', new_serial='DV-NGOAI-1',
                        new_product_code='', new_product_q='Epson L3110', symptom='Kẹt giấy')
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
    bad = client.post('/dich-vu/moi', data={**data, 'new_serial': 'DV-NGOAI-2', 'new_product_q': ''})
    assert bad.status_code == 400 and 'Chưa có máy' in bad.text
    assert db.get(Equipment, 'DV-NGOAI-2') is None
