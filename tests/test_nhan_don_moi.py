"""Màn Nhận đơn mới (TUẤN duyệt mẫu 14/11/2026): nhận nhiều máy một lần (mỗi máy một phiếu), địa điểm khác nhập tay,
lỗi riêng từng máy, cùng hẹn và cùng thợ."""
import re

from sqlalchemy import select

from app.models import CustomerSite, Equipment, ServiceTicket
from test_web import service_form, service_login, service_people  # noqa: F401


def _site(db):
    return db.scalar(select(CustomerSite).where(CustomerSite.customer_code == 'KTD-000001', CustomerSite.name == 'Văn phòng'))


def _machine(db, people, serial, site=None):
    db.add(Equipment(serial=serial, product_code='TD-0001', customer_code='KTD-000001', site_id=site.id if site else None,
                     search_text=serial.lower(), created_by=people['dispatcher'].id))
    db.commit()


def _tickets(db):
    db.expire_all()
    return list(db.scalars(select(ServiceTicket).order_by(ServiceTicket.id)))


def test_many_machines_one_intake_split_with_own_symptoms(client, db, service_people):
    p = service_people
    site = _site(db)
    _machine(db, p, 'CD456', site)
    csrf = service_login(client, p['dispatcher'])
    page = client.get('/dich-vu/moi?customer_code=KTD-000001').text
    assert 'name="pick" value="AB123"' in page and 'name="pick" value="CD456"' in page and 'value="NEW"' in page
    data = service_form(csrf, kind='MANG_VE', site_id=str(site.id), assignee_id=str(p['technician'].id), symptom='Kẹt giấy, in mờ',
                        pick=['AB123', 'CD456'], own_AB123='Kẹt giấy khay 1', own_CD456='',
                        create_machine='1', nm_idx=['0', '1'], nm_serial=['NJXA280148', ''], nm_product_code=['TD-0001', ''],
                        nm_product_q=['Máy in', 'Canon LBP 6030'], nm_no_serial=['1'], nm_own=['', 'Không lên nguồn'])
    r = client.post('/dich-vu/moi', data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    rows = _tickets(db)
    assert len(rows) == 4
    root, kids = rows[0], rows[1:]
    assert r.headers['location'].startswith(f'/dich-vu/{root.id}?da_luu=1&nhan=')
    assert r.headers['location'].endswith(','.join(t.number for t in rows))
    assert root.split_from_id is None and all(k.split_from_id == root.id for k in kids)
    assert [t.serial for t in rows[:3]] == ['AB123', 'CD456', 'NJXA280148'] and re.fullmatch(r'MT-26-\d{5}', rows[3].serial)
    assert {t.assignee_id for t in rows} == {p['technician'].id} and {t.status for t in rows} == {'DA_GIAO'}
    assert {(t.site_id, t.kind, t.appointment_at, t.appointment_slot) for t in rows} == {(root.site_id, 'MANG_VE', root.appointment_at, root.appointment_slot)}
    assert [t.symptom for t in rows] == ['Kẹt giấy khay 1', 'Kẹt giấy, in mờ', 'Kẹt giấy, in mờ', 'Không lên nguồn']
    new = db.get(Equipment, rows[3].serial)
    assert new.customer_code == 'KTD-000001' and new.site_id == site.id and new.model_name == 'Canon LBP 6030'
    page = client.get(r.headers['location']).text
    assert 'data-intake-done' in page and 'tạo 4 phiếu' in page


def test_reception_can_take_many_existing_machines_and_self_claim_opens_each(client, db, service_people, make_user):
    p = service_people
    site = _site(db)
    _machine(db, p, 'CD456', site)
    csrf = service_login(client, make_user('dv.letan', 'reception'))
    # Máy đang ghi ở Văn phòng vẫn nhận chung đơn tại Địa chỉ chính (người nhận đơn chọn địa điểm cho cả nhóm).
    r = client.post('/dich-vu/moi', data=service_form(csrf, site_id='', pick=['AB123', 'CD456'], tu_nhan='1'), follow_redirects=False)
    assert r.status_code == 303, r.text
    rows = _tickets(db)
    assert [t.serial for t in rows] == ['AB123', 'CD456'] and rows[1].split_from_id == rows[0].id
    assert all(t.assignee_id is None and t.status == 'MOI' and t.site_id is None for t in rows)
    from app import service_self_claim
    if service_self_claim.minutes():
        assert all(t.self_claim_until is not None for t in rows)


def test_other_location_typed_saved_or_only_for_this_ticket(client, db, service_people):
    p = service_people
    csrf = service_login(client, p['dispatcher'])
    base = dict(site_id='NEW', site_address='Chi nhánh Q7 · 45 Nguyễn Thị Thập', site_contact='Anh Khoa', site_phone='0908112233')
    r = client.post('/dich-vu/moi', data=service_form(csrf, **base, site_keep='1'), follow_redirects=False)
    assert r.status_code == 303, r.text
    t = _tickets(db)[-1]
    kept = db.get(CustomerSite, t.site_id)
    assert kept.active and kept.customer_code == 'KTD-000001' and kept.address == base['site_address']
    assert (kept.contact_name, kept.contact_phone) == ('Anh Khoa', '0908112233')
    assert 'Chi nhánh Q7' in client.get('/dich-vu/chon-khach?customer_code=KTD-000001').text
    # Bỏ tick "Lưu vào địa điểm của khách": phiếu vẫn có địa chỉ, danh sách lần sau không hiện.
    r = client.post('/dich-vu/moi', data=service_form(csrf, **{**base, 'site_address': 'Nhà riêng giám đốc · 12 Cộng Hòa'}), follow_redirects=False)
    assert r.status_code == 303, r.text
    once = db.get(CustomerSite, _tickets(db)[-1].site_id)
    assert not once.active and once.address == 'Nhà riêng giám đốc · 12 Cộng Hòa'
    assert 'Cộng Hòa' not in client.get('/dich-vu/chon-khach?customer_code=KTD-000001').text
    # Thiếu địa chỉ: giữ form, không tạo gì.
    before = (len(_tickets(db)), db.query(CustomerSite).count())
    r = client.post('/dich-vu/moi', data=service_form(csrf, site_id='NEW', site_address='', site_contact='Chị Hà', symptom='Giữ mô tả'))
    assert r.status_code == 400 and 'nhập địa chỉ' in r.text and 'value="Chị Hà"' in r.text and 'Giữ mô tả</textarea>' in r.text
    assert (len(_tickets(db)), db.query(CustomerSite).count()) == before


def test_busy_machine_rolls_back_everything(client, db, service_people):
    p = service_people
    site = _site(db)
    _machine(db, p, 'CD456', site)
    csrf = service_login(client, p['dispatcher'])
    assert client.post('/dich-vu/moi', data=service_form(csrf, site_id=str(site.id), pick=['CD456']), follow_redirects=False).status_code == 303
    first = _tickets(db)[0]
    page = client.get('/dich-vu/moi?customer_code=KTD-000001').text
    assert 'data-sv-open' in page and first.number in page
    assert re.search(r'name="pick" value="CD456"\s+disabled', page) and f'Đang ở {first.number}' in page
    sites = db.query(CustomerSite).count()
    r = client.post('/dich-vu/moi', data=service_form(csrf, site_id='NEW', site_address='Kho mới · 1 Lê Lợi', site_keep='1', pick=['AB123', 'CD456'],
                                                       create_machine='1', nm_idx='0', nm_serial='ROLLBACK-1', nm_product_code='TD-0001', nm_product_q='Máy in'))
    assert r.status_code in (400, 409) and 'Chưa lưu được' in r.text
    assert len(_tickets(db)) == 1 and db.query(CustomerSite).count() == sites and db.get(Equipment, 'ROLLBACK-1') is None
    assert 'value="AB123" checked' in r.text and 'value="Kho mới · 1 Lê Lợi"' in r.text


def test_mang_ve_needs_a_machine_and_duplicates_blocked(client, db, service_people):
    p = service_people
    csrf = service_login(client, p['dispatcher'])
    r = client.post('/dich-vu/moi', data=service_form(csrf, kind='MANG_VE'))
    assert r.status_code == 400 and 'Phiếu Mang về phải chọn máy của khách.' in r.text
    r = client.post('/dich-vu/moi', data=service_form(csrf, pick=['AB123'], create_machine='1', nm_idx='0', nm_serial='ab123',
                                                       nm_product_code='TD-0001', nm_product_q='Máy in'))
    assert r.status_code == 400 and 'trùng' in r.text
    assert _tickets(db) == []
