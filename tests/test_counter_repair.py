"""Khách mang máy tới cửa hàng (TUẤN 26/10/2026): nhận và trả máy tại quầy, không qua thợ, tiền thành Báo thu."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import equipment, equipment_custody as custody, service_charges, service_counter as counter, service_tickets as svc, service_workshop as workshop, attachments
from app.errors import BusinessError
from app.models import Equipment, EquipmentCustody, SalesReceipt, ServiceStep, ServiceTicket, ServiceVisit
from app.schemas import CustodyIntake, EquipmentIn, ServiceCharges, ServiceTicketIn
from test_my_work import people, purchase_people, NOW
from test_web import d5_image, service_login


def error(code, fn, *a, **kw):
    with pytest.raises(BusinessError) as e:fn(*a, **kw)
    assert e.value.code == code
    return e.value


@pytest.fixture
def shop(db, people):
    p = people
    eq = equipment.create(db, p['dispatcher'], EquipmentIn(serial='QUAY-01', product_code='TD-0001', customer_code='KTD-000001'))
    return p, eq


def body(**kw):
    return ServiceTicketIn(**{'customer_code': 'KTD-000001', 'serial': 'QUAY-01', 'symptom': 'Kéo giấy kêu to', **kw})


def receive(db, p, who='sales', **kw):
    return counter.intake(db, p[who], body(**kw), ['DAY_NGUON'], '', 'Vỏ trầy góc trái', ('may.png', d5_image()), NOW.date() + timedelta(days=1))


def repair(db, p, t, amount=400000):
    service_charges.set_charges(db, p['dispatcher'], t.id, ServiceCharges(version=t.version, billing='THU_TIEN', lines=[
        dict(kind='PHI', description='Công sửa', qty=1, unit_price=amount, vat_rate=8)]))
    workshop.quote_approve(db, p['dispatcher'], t.id, t.version, NOW.date() + timedelta(days=1), 'Khách đồng ý')
    attachments.add(db, p['dispatcher'], 'service_ticket', str(t.id), 'SAU', 'sau.png', d5_image())
    workshop.workshop_repaired(db, p['dispatcher'], t.id, t.version, repaired_by=p['other_tech'].id, mode='FULL', reason='',
                               fault='Mòn bánh xe', fix='Thay bánh xe kéo giấy', warranty_months=1)


def give_back(db, p, t, eq, who='sales', mode='FULL', partial='', photo=True, accessories=('DAY_NGUON',), note=''):
    return counter.return_at_counter(db, p[who], t.id, t.version, CustodyIntake(version=eq.version, accessories=list(accessories), accessories_note=note),
                                     mode, partial, 'TIEN_MAT', '', ('tra.png', d5_image()) if photo else None)


def test_sales_receives_machine_straight_into_workshop_without_technician_or_visit(db, shop):
    p, eq = shop
    t = receive(db, p)
    assert t.status == 'TAM_DUNG' and t.hold_reason == 'CHO_SUA_XUONG' and t.kind == 'MANG_VE' and t.assignee_id is None
    assert t.promised_return_on == NOW.date() + timedelta(days=1)
    assert eq.custody_location == 'KHO_KY_THUAT' and eq.custody_ticket_id == t.id and eq.custody_holder_id is None
    row = db.scalar(select(EquipmentCustody).where(EquipmentCustody.ticket_id == t.id))
    assert row.status == 'DA_NHAN' and row.accessories == ['DAY_NGUON'] and row.confirmed_by == p['sales'].id
    assert counter.walk_in(db, t.id) and db.query(ServiceVisit).filter_by(ticket_id=t.id).count() == 0
    assert t.id in [r.id for r in db.execute(workshop.queue_query(NOW))]


@pytest.mark.parametrize('case,code', [('photo', 'PHOTO_REQUIRED'), ('condition', 'CONDITION_REQUIRED'), ('role', 'FORBIDDEN'),
                                       ('held', 'MACHINE_HELD'), ('nomachine', 'SERIAL_REQUIRED')])
def test_intake_failures_leave_nothing(db, shop, case, code):
    p, eq = shop
    if case == 'held':receive(db, p)
    before = (db.query(ServiceTicket).count(), db.query(EquipmentCustody).count())
    who = p['technician'] if case == 'role' else p['sales']
    error(code, counter.intake, db, who, body(serial=None) if case == 'nomachine' else body(), ['DAY_NGUON'], '',
          '' if case == 'condition' else 'Vỏ trầy', None if case == 'photo' else ('may.png', d5_image()))
    assert (db.query(ServiceTicket).count(), db.query(EquipmentCustody).count()) == before


def test_counter_can_record_new_machine_for_walk_in_customer(db, shop):
    p, _ = shop
    t = receive(db, p, serial=None, new_machine=EquipmentIn(serial='MOI-QUAY', model_name='Canon 2900'))
    machine = db.get(Equipment, 'MOI-QUAY')
    assert t.serial == 'MOI-QUAY' and machine.customer_code == 'KTD-000001' and machine.custody_location == 'KHO_KY_THUAT'


def test_counter_return_completes_reports_receipt_and_keeps_debt(db, shop):
    p, eq = shop
    t = receive(db, p);repair(db, p, t)
    assert t.hold_reason == 'SUA_XONG_CHO_TRA' and t.receivable == 432000
    give_back(db, p, t, eq, mode='PARTIAL', partial='200.000')
    assert t.status == 'DA_DONG' and eq.custody_location == 'TAI_KHACH'
    receipt = db.scalar(select(SalesReceipt).where(SalesReceipt.ticket_id == t.id))
    assert receipt.amount == 200000 and receipt.status == 'CHO_XAC_NHAN' and receipt.reported_by == p['sales'].id
    assert db.scalar(select(ServiceStep.id).where(ServiceStep.ticket_id == t.id, ServiceStep.action == 'counter_return'))
    assert db.query(ServiceVisit).filter_by(ticket_id=t.id).count() == 0 and t.repaired_by == p['other_tech'].id


@pytest.mark.parametrize('case,code', [('photo', 'PHOTO_REQUIRED'), ('partial', 'BAD_PARTIAL_AMOUNT'), ('missing', 'MISSING_ACCESSORIES'),
                                       ('early', 'BAD_STATUS'), ('technician', 'FORBIDDEN')])
def test_counter_return_failures_roll_back(db, shop, case, code):
    p, eq = shop
    t = receive(db, p)
    if case != 'early':repair(db, p, t)
    before = (t.status, eq.custody_location, db.query(SalesReceipt).count(), db.query(EquipmentCustody).count())
    error(code, give_back, db, p, t, eq, who='technician' if case == 'technician' else 'sales', mode='PARTIAL' if case == 'partial' else 'FULL',
          partial='432.000', photo=case != 'photo', accessories=() if case == 'missing' else ('DAY_NGUON',))
    db.expire_all()
    assert (t.status, eq.custody_location, db.query(SalesReceipt).count(), db.query(EquipmentCustody).count()) == before


def test_sales_cannot_touch_technician_workshop_ticket_but_dispatcher_can_return_it(db, shop):
    p, eq = shop
    t = svc.create(db, p['dispatcher'], body(kind='SUA_TAI_CHO'))
    error('NOT_FOUND', counter.load, db, p['sales'], t.id)
    assert counter.can_counter(db, p['dispatcher'], t)


def test_web_counter_flow_for_sales(client, db, shop):
    p, eq = shop
    csrf = service_login(client, p['sales'])
    page = client.get('/quay-sua')
    assert page.status_code == 200 and 'Nhận máy khách mang tới' in page.text
    form = client.get('/quay-sua/nhan?customer_code=KTD-000001')
    assert form.status_code == 200 and 'QUAY-01' in form.text
    from test_web import csrf_of
    csrf = csrf_of(form.text)
    r = client.post('/quay-sua/nhan', data=dict(csrf=csrf, form_key=str(uuid.uuid4()), customer_code='KTD-000001', serial='QUAY-01', symptom='Kẹt giấy liên tục',
                    accessories='KHONG_CO', condition_note='Vỏ còn mới', expected=(NOW.date() + timedelta(days=2)).isoformat()),
                    files={'photo': ('may.png', d5_image(), 'image/png')}, follow_redirects=False)
    assert r.status_code == 303, r.text
    t = db.scalar(select(ServiceTicket).order_by(ServiceTicket.id.desc()))
    detail = client.get(r.headers['location'])
    assert 'Đã nhận máy vào Kho kỹ thuật' in detail.text and t.number in detail.text
    assert client.get(f'/quay-sua/{t.id}/phieu-nhan-may').status_code == 200
    assert client.get(f'/dich-vu/{t.id}').status_code == 403
    repair(db, p, t)
    detail = client.get(f'/quay-sua/{t.id}')
    assert 'Trả máy cho khách' in detail.text
    db.expire_all();t = db.get(ServiceTicket, t.id);eq = db.get(Equipment, 'QUAY-01')
    r = client.post(f'/quay-sua/{t.id}/tra', data=dict(csrf=csrf_of(detail.text), form_key=str(uuid.uuid4()), version=t.version, machine_version=eq.version,
                    accessories='KHONG_CO', mode='FULL', method='TIEN_MAT'), files={'photo': ('tra.png', d5_image(), 'image/png')}, follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    assert db.get(ServiceTicket, t.id).status == 'DA_DONG'
    assert db.scalar(select(SalesReceipt.amount).where(SalesReceipt.ticket_id == t.id)) == 432000


def test_workshop_tickets_show_real_step_instead_of_paused(client, db, shop):
    from app.web.services import status_label
    p, eq = shop
    t = receive(db, p)
    assert status_label(t) == 'Chờ sửa ở xưởng'
    csrf = service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{t.id}')
    assert page.status_code == 200 and 'Chờ sửa ở xưởng' in page.text and 'Tạm dừng: Chờ sửa' not in page.text
    repair(db, p, t)
    assert status_label(t) == 'Sửa xong, chờ trả máy'
    assert status_label({'status': 'TAM_DUNG', 'hold_reason': 'KHAC'}) == 'Tạm dừng'


def _return_assigned(db, p):
    t = receive(db, p);repair(db, p, t)
    workshop.schedule_return(db, p['dispatcher'], t.id, t.version, svc._now() + timedelta(hours=3), None, p['technician'].id, '')
    pend = custody.pending(db, 'QUAY-01');custody.confirm(db, p['technician'], pend.id, pend.version)
    svc.transition(db, p['technician'], t.id, 'DA_NHAN', t.version, '')
    return t


def test_return_technician_sees_no_receive_or_early_return_before_arriving(client, db, shop):
    p, eq = shop
    t = _return_assigned(db, p)
    service_login(client, p['technician'])
    page = client.get(f'/tho/phieu/{t.id}').text
    assert 'Đã đến khách' in page and '/giu-may/%d/tra-khach' % t.id not in page and '/giu-may/%d/nhan' % t.id not in page


def test_machine_returned_early_can_still_finish_and_collect(client, db, shop):
    """DV-26-00008: thợ bấm Đã trả khách trước Đã đến khách; phiếu vẫn vào Hoàn thành thu tiền được."""
    from test_web import csrf_of
    p, eq = shop
    t = _return_assigned(db, p)
    attachments.add(db, p['technician'], 'service_ticket', str(t.id), 'TRA_MAY', 'tra.png', d5_image())
    custody.return_customer(db, p['technician'], t.id, CustodyIntake(version=eq.version, accessories=['DAY_NGUON']))
    service_login(client, p['technician'])
    assert '/giu-may/%d/nhan' % t.id not in client.get(f'/tho/phieu/{t.id}').text
    svc.transition(db, p['technician'], t.id, 'DANG_LAM', t.version, '')
    page = client.get(f'/tho/phieu/{t.id}')
    r = client.post(f'/tho/phieu/{t.id}/hoan-thanh', data=dict(csrf=csrf_of(page.text), version=t.version, customer_amount='432000',
                    collection_mode='FULL', method='TIEN_MAT', return_to='tho'), follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    assert db.get(ServiceTicket, t.id).status == 'DA_DONG'
    assert db.scalar(select(SalesReceipt.amount).where(SalesReceipt.ticket_id == t.id)) == 432000
