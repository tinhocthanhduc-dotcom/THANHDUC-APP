"""SV1: selecting/creating a machine is atomic with its service ticket."""
from html.parser import HTMLParser

import pytest
from sqlalchemy import select

from app import equipment, equipment_custody, service_tickets as svc
from app.errors import BusinessError
from app.models import (AuditLog, Customer, CustomerSite, Equipment, EquipmentCustody,
                        FundLedger, SalesReceipt, ServiceStep, ServiceTicket, StockDoc, StockLedger)
from app.schemas import EquipmentIn, ServiceKindSet, ServiceMachineSet, ServiceTicketIn
from conftest import PASSWORD
from test_my_work import people, purchase_people, ticket
from test_mv1_workshop import photo
from test_web import service_form, service_login


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def find(self, tag, **attrs):
        return [data for name, data in self.tags if name == tag and all(data.get(k) == v for k, v in attrs.items())]

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def headers(client, user):
    r = client.post('/auth/login', data=dict(username=user.username, password=PASSWORD))
    assert r.status_code == 200
    return dict(Authorization='Bearer ' + r.json()['access_token'])


def state(db, t):
    db.expire_all()
    db.refresh(t)
    return (svc.snapshot(t), [db.query(m).count() for m in (
        Equipment, EquipmentCustody, ServiceStep, AuditLog, StockDoc, StockLedger, SalesReceipt, FundLedger)])


@pytest.fixture
def job(db, people):
    p = people
    t = ticket(db, p, 'DA_GIAO')
    site = CustomerSite(customer_code=t.customer_code, name='Xưởng khách', created_by=p['dispatcher'].id)
    db.add(site)
    db.flush()
    t.site_id = site.id
    db.commit()
    eq = equipment.create(db, p['dispatcher'], EquipmentIn(
        serial='SV1-CO-SAN', product_code='TD-0001', customer_code=t.customer_code))
    return p, t, eq, site


@pytest.mark.parametrize('role', ['dispatcher', 'director', 'technician'])
@pytest.mark.parametrize('entry', ['api', 'web'])
def test_set_existing_machine_records_version_step_and_audit(client, db, job, role, entry):
    p, t, eq, _ = job
    version = t.version
    if entry == 'api':
        r = client.post(f'/service-tickets/{t.id}/machine', headers=headers(client, p[role]),
                        json=dict(version=version, serial=' sv1-co-san '))
    else:
        csrf = service_login(client, p[role])
        path = '/tho/phieu' if role == 'technician' else '/dich-vu'
        r = client.post(f'{path}/{t.id}/may', data=dict(csrf=csrf, version=version, machine_pick='SERIAL:' + eq.serial))
    assert r.status_code == 200, r.text
    db.refresh(t)
    assert t.serial == eq.serial and t.version == version + 1 and t.status == 'DA_GIAO'
    step = db.scalar(select(ServiceStep).where(ServiceStep.ticket_id == t.id).order_by(ServiceStep.id.desc()))
    log = db.scalar(select(AuditLog).where(AuditLog.action == 'service_ticket.set_machine'))
    assert step.action == 'set_machine' and step.actor_id == p[role].id
    assert step.from_status == step.to_status == 'DA_GIAO'
    assert log.before['serial'] is None and log.after['serial'] == eq.serial and log.user_id == p[role].id
    assert all(db.query(m).count() == 0 for m in (StockDoc, StockLedger, SalesReceipt, FundLedger))


@pytest.mark.parametrize('role', ['other_tech', 'sales', 'warehouse', 'reception'])
@pytest.mark.parametrize('entry', ['api', 'web'])
def test_machine_permission_is_403_and_atomic(client, db, job, role, entry):
    p, t, eq, _ = job
    before = state(db, t)
    if entry == 'api':
        r = client.post(f'/service-tickets/{t.id}/machine', headers=headers(client, p[role]),
                        json=dict(version=t.version, serial=eq.serial))
    else:
        csrf = service_login(client, p[role])
        r = client.post(f'/dich-vu/{t.id}/may', data=dict(csrf=csrf, version=t.version, serial=eq.serial))
    assert r.status_code == 403, r.text
    assert state(db, t) == before


@pytest.mark.parametrize('serial', ['MISSING', 'OTHER-CUSTOMER', 'INACTIVE'])
def test_bad_serial_leaves_ticket_unchanged(client, db, job, serial):
    p, t, _, _ = job
    db.add(Customer(code='KTD-000002', name='Khách khác'))
    db.commit()
    if serial != 'MISSING':
        equipment.create(db, p['dispatcher'], EquipmentIn(serial=serial, product_code='TD-0001',
            customer_code='KTD-000002' if serial == 'OTHER-CUSTOMER' else t.customer_code, active=serial != 'INACTIVE'))
    before = state(db, t)
    r = client.post(f'/service-tickets/{t.id}/machine', headers=headers(client, p['dispatcher']),
                    json=dict(version=t.version, serial=serial))
    assert r.status_code == 400 and r.json()['error'] == 'BAD_SERIAL'
    assert state(db, t) == before


@pytest.mark.parametrize('protection,code', [
    ('DA_DONG', 'BAD_STATUS'), ('DA_HUY', 'BAD_STATUS'),
    ('warranty', 'WARRANTY_MISMATCH'), ('version', 'VERSION_CONFLICT'),
    ('CHO_NHAN', 'SERIAL_LOCKED'), ('DA_NHAN', 'SERIAL_LOCKED'), ('DA_HUY_CUSTODY', 'SERIAL_LOCKED')])
def test_locked_ticket_cannot_change_or_create_equipment(client, db, job, protection, code):
    p, t, eq, _ = job
    if protection in ('DA_DONG', 'DA_HUY'):
        t.status = protection
    elif protection == 'warranty':
        t.kind = 'BAO_HANH'
        t.warranty_basis = 'HET_HAN'
    elif protection != 'version':
        pending = protection == 'CHO_NHAN'
        db.add(EquipmentCustody(serial=eq.serial, ticket_id=t.id, location='TAI_KHACH',
            accessories=[], status='CHO_NHAN' if pending else 'DA_HUY' if protection == 'DA_HUY_CUSTODY' else 'DA_NHAN',
            created_by=p['technician'].id, confirmed_by=None if pending else p['dispatcher'].id,
            confirmed_at=None if pending else svc._now()))
    db.commit()
    before = state(db, t)
    r = client.post(f'/service-tickets/{t.id}/machine', headers=headers(client, p['dispatcher']), json=dict(
        version=t.version - 1 if protection == 'version' else t.version,
        new_machine=dict(serial='SHOULD-NOT-EXIST', product_code='TD-0001')))
    assert r.status_code in (400, 409) and r.json()['error'] == code, r.text
    assert state(db, t) == before and db.get(Equipment, 'SHOULD-NOT-EXIST') is None


@pytest.mark.parametrize('entry', ['api', 'web'])
def test_new_machine_forces_customer_defaults_site_and_is_atomic(client, db, job, entry):
    p, t, _, site = job
    version = t.version
    if entry == 'api':
        r = client.post(f'/service-tickets/{t.id}/machine', headers=headers(client, p['technician']),
            json=dict(version=version, new_machine=dict(serial=' sv1-moi ', product_code='TD-0001', customer_code='KTD-999999')))
    else:
        csrf = service_login(client, p['technician'])
        r = client.post(f'/tho/phieu/{t.id}/may', data=dict(csrf=csrf, version=version, machine_pick='NEW',
            new_serial=' sv1-moi ', new_product_code='TD-0001', new_product_q='Máy in'))
    assert r.status_code == 200, r.text
    db.expire_all()
    eq = db.get(Equipment, 'SV1-MOI')
    assert t.serial == eq.serial and t.version == version + 1
    assert eq.customer_code == t.customer_code and eq.site_id == site.id
    assert db.scalar(select(AuditLog).where(AuditLog.entity == 'equipment', AuditLog.entity_id == eq.serial)).action == 'equipment.create'
    before = state(db, t)
    with pytest.raises(BusinessError) as e:
        svc.set_machine(db, p['technician'], t.id, ServiceMachineSet(version=t.version,
            new_machine=EquipmentIn(serial=eq.serial.lower(), product_code='TD-0001')))
    assert e.value.code == 'DUPLICATE_SERIAL'
    assert state(db, t) == before


def test_new_machine_rolls_back_if_later_ticket_validation_fails(db, job):
    p, t, _, _ = job
    before = state(db, t)
    # Equipment accepts an inactive site; service tickets require an active one.
    site = db.get(CustomerSite, t.site_id)
    site.active = False
    db.commit()
    with pytest.raises(BusinessError) as e:
        svc.set_machine(db, p['dispatcher'], t.id, ServiceMachineSet(version=t.version,
            new_machine=EquipmentIn(serial='ROLLBACK-SET', product_code='TD-0001')))
    assert e.value.code == 'BAD_SITE' and db.get(Equipment, 'ROLLBACK-SET') is None
    assert state(db, t) == before
    with pytest.raises(BusinessError) as e:
        svc.create_and_assign(db, p['dispatcher'], ServiceTicketIn(customer_code=t.customer_code, kind='MANG_VE',
            symptom='Máy không in', new_machine=EquipmentIn(serial='ROLLBACK-CREATE', product_code='TD-0001')), p['sales'].id)
    assert e.value.code == 'BAD_ASSIGNEE' and db.get(Equipment, 'ROLLBACK-CREATE') is None
    assert state(db, t) == before and db.query(ServiceTicket).count() == 1


@pytest.mark.parametrize('kind', ['MANG_VE', 'SUA_TAI_CHO'])
@pytest.mark.parametrize('entry', ['api', 'web'])
def test_intake_requires_machine_only_for_mang_ve(client, db, job, kind, entry):
    p, t, eq, _ = job
    before = state(db, t)
    if entry == 'api':
        r = client.post('/service-tickets', headers=headers(client, p['dispatcher']),
                        json=dict(customer_code=t.customer_code, kind=kind, symptom='Giữ nguyên mô tả nhận máy'))
    else:
        csrf = service_login(client, p['dispatcher'])
        r = client.post('/dich-vu/moi', data=service_form(csrf, kind=kind, symptom='Giữ nguyên mô tả nhận máy'))
    if kind == 'MANG_VE':
        assert r.status_code == 400, r.text
        assert (r.json()['error'] == 'SERIAL_REQUIRED' if entry == 'api' else 'Phiếu Mang về phải chọn máy của khách.' in r.text)
        if entry == 'web':
            parsed = Tags(r.text)
            assert parsed.find('textarea', name='symptom') and 'Giữ nguyên mô tả nhận máy' in r.text
            assert 'required' in parsed.find('select', name='serial')[0]
        assert state(db, t) == before
        if entry == 'api':
            r = client.post('/service-tickets', headers=headers(client, p['dispatcher']),
                json=dict(customer_code=t.customer_code, kind=kind, symptom='Nhận máy có serial', serial=eq.serial))
        else:
            r = client.post('/dich-vu/moi', data=service_form(csrf, kind=kind, serial=eq.serial))
    assert r.status_code == (201 if entry == 'api' else 200), r.text
    assert db.query(ServiceTicket).count() == 2


@pytest.mark.parametrize('entry', ['api', 'web'])
def test_intake_with_new_machine_and_duplicate_retains_form(client, db, job, entry):
    p, t, _, site = job
    if entry == 'api':
        auth = headers(client, p['dispatcher'])
        data = dict(customer_code=t.customer_code, site_id=site.id, kind='MANG_VE', symptom='Máy mới cần sửa',
            new_machine=dict(serial='INTAKE-NEW', product_code='TD-0001', customer_code='KTD-999999'))
        r = client.post('/service-tickets', headers=auth, json=data)
    else:
        csrf = service_login(client, p['dispatcher'])
        data = service_form(csrf, kind='MANG_VE', site_id=site.id, create_machine='1', new_serial='INTAKE-NEW',
            new_product_code='TD-0001', new_product_q='Máy in', symptom='Máy mới cần sửa')
        r = client.post('/dich-vu/moi', data=data)
    assert r.status_code == (201 if entry == 'api' else 200), r.text
    db.expire_all()
    eq = db.get(Equipment, 'INTAKE-NEW')
    saved = db.scalar(select(ServiceTicket).where(ServiceTicket.serial == eq.serial))
    assert eq.customer_code == saved.customer_code == t.customer_code and eq.site_id == site.id
    before = state(db, t)
    r = client.post('/service-tickets', headers=auth, json=data) if entry == 'api' else client.post('/dich-vu/moi', data=data)
    assert r.status_code == 409, r.text
    if entry == 'api':
        assert r.json()['error'] == 'DUPLICATE_SERIAL'
    else:
        parsed = Tags(r.text)
        assert parsed.find('input', name='new_serial', value='INTAKE-NEW')
        assert parsed.find('input', name='new_product_code', value='TD-0001')
        assert parsed.find('input', name='create_machine')[0].get('checked', 'missing') != 'missing'
        assert 'Máy mới cần sửa' in r.text
    assert state(db, t) == before


def test_clear_machine_only_when_not_mang_ve_and_set_kind_remains_allowed(db, job):
    p, t, eq, _ = job
    svc.set_machine(db, p['technician'], t.id, ServiceMachineSet(version=t.version, serial=eq.serial))
    svc.set_machine(db, p['technician'], t.id, ServiceMachineSet(version=t.version, serial=None))
    assert t.serial is None
    svc.set_kind(db, p['technician'], t.id, ServiceKindSet(version=t.version, kind='MANG_VE'))
    assert t.kind == 'MANG_VE' and t.serial is None
    svc.set_machine(db, p['technician'], t.id, ServiceMachineSet(version=t.version, serial=eq.serial))
    before = state(db, t)
    with pytest.raises(BusinessError) as e:
        svc.set_machine(db, p['technician'], t.id, ServiceMachineSet(version=t.version, serial=None))
    assert e.value.code == 'SERIAL_REQUIRED' and 'phải chọn máy' in e.value.message
    assert state(db, t) == before


def test_unassigned_equipment_can_be_selected(db, job):
    p, t, _, _ = job
    eq = equipment.create(db, p['dispatcher'], EquipmentIn(serial='UNASSIGNED', product_code='TD-0001'))
    svc.set_machine(db, p['dispatcher'], t.id, ServiceMachineSet(version=t.version, serial=eq.serial))
    assert t.serial == eq.serial


def test_reception_has_no_new_machine_controls_or_create_permission(client, db, job):
    p, t, _, _ = job
    csrf = service_login(client, p['reception'])
    for path in ('/dich-vu/moi?customer_code=' + t.customer_code, '/dich-vu/chon-khach?customer_code=' + t.customer_code):
        r = client.get(path)
        assert r.status_code == 200 and '+ Máy mới' not in r.text
    before = state(db, t)
    r = client.post('/dich-vu/moi', data=service_form(csrf, create_machine='1', new_serial='NO-PERMISSION', new_product_code='TD-0001'))
    assert r.status_code == 403, r.text
    assert db.get(Equipment, 'NO-PERMISSION') is None and state(db, t) == before


def test_legacy_mang_ve_select_create_then_bring_full_flow(client, db, job):
    p, original, _, _ = job
    t = ticket(db, p, 'DANG_LAM')
    # Existing pre-SV1 tickets may already be MANG_VE without a serial.
    t.kind = 'MANG_VE'
    db.commit()
    csrf = service_login(client, p['technician'])
    page = client.get(f'/tho/phieu/{t.id}').text
    parsed = Tags(page)
    swipe = next(data for tag, data in parsed.tags if tag == 'button' and 'data-tho-primary' in data)
    assert 'disabled' in swipe and 'form' not in swipe
    assert 'Chọn máy trước khi mang về' in page and 'gọi điều phối thêm serial' not in page
    assert parsed.find('form', action=f'/tho/phieu/{t.id}/may') and not parsed.find('form', id='workshop-bring')
    r = client.post(f'/tho/phieu/{t.id}/may', data=dict(csrf=csrf, version=t.version, machine_pick='NEW',
        new_serial='LEGACY-MACHINE', new_product_code='TD-0001', new_product_q='Máy in'), follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].startswith(f'/tho/phieu/{t.id}'), r.text
    db.expire_all()
    eq = db.get(Equipment, 'LEGACY-MACHINE')
    page = client.get(r.headers['location']).text
    swipe = next(data for tag, data in Tags(page).tags if tag == 'button' and 'data-tho-primary' in data)
    assert 'disabled' not in swipe and swipe['form'] == 'workshop-bring'
    photo(db, p, t)
    r = client.post(f'/dich-vu/{t.id}/mang-ve-xuong', data=dict(csrf=csrf, version=t.version,
        machine_version=eq.version, accessories='KHONG_CO', condition_note='Máy kẹt giấy, vỏ trầy', return_to='tho'))
    assert r.status_code == 200, r.text
    db.expire_all()
    assert t.kind == 'MANG_VE' and t.status == 'TAM_DUNG' and t.hold_reason == 'CHO_SUA_XUONG'
    assert eq.custody_location == 'THO_GIU' and eq.custody_holder_id == p['technician'].id
    assert db.query(EquipmentCustody).filter_by(ticket_id=t.id).count() == 2
    assert db.query(EquipmentCustody).filter_by(ticket_id=t.id, status='CHO_NHAN').one().location == 'KHO_KY_THUAT'
    assert all(db.query(m).count() == 0 for m in (StockDoc, StockLedger, SalesReceipt, FundLedger))


def test_machine_form_retains_duplicate_input(client, db, job):
    p, t, eq, _ = job
    csrf = service_login(client, p['dispatcher'])
    before = state(db, t)
    r = client.post(f'/dich-vu/{t.id}/may', data=dict(csrf=csrf, version=t.version,
        machine_pick='NEW', new_serial=eq.serial, new_product_code='TD-0001', new_product_q='Máy in đã chọn'))
    assert r.status_code == 409, r.text
    parsed = Tags(r.text)
    assert parsed.find('input', name='new_serial', value=eq.serial)[0]['aria-invalid'] == 'true'
    assert parsed.find('input', name='new_product_q', value='Máy in đã chọn')
    assert parsed.find('input', name='machine_pick', value='NEW')[0].get('checked', 'missing') != 'missing'
    assert parsed.find('form', action=f'/dich-vu/{t.id}/may')[0].get('data-busy', 'missing') != 'missing'
    assert state(db, t) == before
