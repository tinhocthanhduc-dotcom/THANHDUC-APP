"""TUẤN 13/11/2026: nhiều máy cùng chỗ — thêm máy tại chỗ, nhiều máy mới, mã tạm, mang về chỉ máy được tick."""
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from app import equipment, notifications, service_tickets as svc, service_workshop
from app.models import Equipment, ServiceStep, ServiceTicket
from app.schemas import EquipmentIn
from test_mm1_machine_split import job, items, split, state  # noqa: F401
from test_mv1_workshop import onsite, photo  # noqa: F401
from test_technician_stock import held, transfer, balance  # noqa: F401
from test_my_work import people, purchase_people, ticket  # noqa: F401
from test_stock_reservations import fail
from test_web import service_login
from web_forms import key_for


def kids(db, t):
    db.expire_all()
    return list(db.scalars(select(ServiceTicket).where(ServiceTicket.split_from_id == t.id).order_by(ServiceTicket.id)))


def test_technician_adds_existing_and_new_machines_keeps_original(db, job):
    p, _, t, eqs = job
    root_serial = t.serial
    news = [EquipmentIn(serial='NEW-A1', model_name='Canon MF241d'), EquipmentIn(serial=svc.TEMP_SERIAL, product_code='TD-0001')]
    out = svc.add_machines(db, p['technician'], t.id, t.version, [eqs[1].serial], news)
    assert len(out) == 3 and t.serial == root_serial
    assert [c.serial for c in out][:2] == [eqs[1].serial, 'NEW-A1'] and out[2].serial.startswith('MT-')
    temp = db.get(Equipment, out[2].serial)
    assert temp.customer_code == t.customer_code and 'mã tạm' in temp.note
    assert all(c.split_from_id == t.id and c.status == t.status and c.assignee_id == t.assignee_id for c in out)
    step = db.scalars(select(ServiceStep).where(ServiceStep.ticket_id == t.id, ServiceStep.action == 'add_machines')).one()
    assert step.note.startswith('Thêm máy tại chỗ: ') and all(c.number in step.note for c in out)
    assert [x.id for x in svc.family(db, out[1])] == [t.id] + [c.id for c in out]


@pytest.mark.parametrize('invalid', ['root_serial', 'duplicate_new', 'nothing', 'foreign_tech', 'busy'])
def test_add_machines_rejects_and_rolls_back(db, job, invalid):
    p, _, t, eqs = job
    user, serials, news, code = p['technician'], [eqs[1].serial], [], 'SERIAL_DUPLICATE'
    if invalid == 'root_serial': serials = [t.serial]
    elif invalid == 'duplicate_new': news = [EquipmentIn(serial=eqs[1].serial, model_name='X')]
    elif invalid == 'nothing': serials, code = [], 'BAD_SERIAL'
    elif invalid == 'foreign_tech': user, code = p['other_tech'], 'FORBIDDEN'
    else:
        svc.add_machines(db, p['technician'], t.id, t.version, [eqs[1].serial]); db.refresh(t); code = 'SERIAL_BUSY'
    before = state(db)
    fail(code, svc.add_machines, db, user, t.id, t.version, serials, news)
    assert state(db) == before


def test_add_machines_blocked_after_machine_brought(db, job):
    p, _, t, eqs = job
    photo(db, p, t)
    service_workshop.bring_trip(db, p['technician'], t.id, t.version, items([t], eqs[:1]), [])
    db.refresh(t)
    fail('BAD_STATUS', svc.add_machines, db, p['technician'], t.id, t.version, [eqs[1].serial])


def test_warranty_root_spawns_onsite_repair(db, job):
    p, _, t, eqs = job
    t.kind = 'BAO_HANH'; db.commit()
    child = svc.add_machines(db, p['dispatcher'], t.id, t.version, [eqs[1].serial])[0]
    assert child.kind == 'SUA_TAI_CHO' and child.warranty_of_id is None


def test_trip_brings_only_ticked_machines(db, job):
    p, _, t, eqs = job
    tickets = split(db, p, t, eqs)
    for x in tickets[:2]: photo(db, p, x)
    parts = items(tickets, eqs)[:2]
    service_workshop.bring_trip(db, p['technician'], t.id, t.version, parts, [])
    db.expire_all()
    left = db.get(ServiceTicket, tickets[2].id)
    assert left.status == 'DANG_LAM' and db.get(Equipment, eqs[2].serial).custody_location == 'TAI_KHACH'
    assert db.get(Equipment, eqs[1].serial).custody_location != 'TAI_KHACH'


def test_trip_from_child_takes_root_sibling(db, job):
    p, _, t, eqs = job
    tickets = split(db, p, t, eqs[:2])
    child = tickets[1]
    for x in tickets: photo(db, p, x)
    parts = items([child, t], [eqs[1], eqs[0]])
    service_workshop.bring_trip(db, p['technician'], child.id, child.version, parts, [])
    db.expire_all()
    assert db.get(Equipment, eqs[0].serial).custody_location != 'TAI_KHACH'


def test_web_technician_adds_many_new_machines_with_temp_code(db, job, client):
    p, _, t, eqs = job
    csrf = service_login(client, p['technician'])
    path = f'/tho/phieu/{t.id}/them-may'
    response = client.post(path, data=dict(csrf=csrf, form_key=key_for(client, f'/tho/phieu/{t.id}'), version=t.version,
        add_pick=['SERIAL:' + eqs[1].serial, 'NEW'], nm_idx=['0', '3'], nm_serial=['WEB-N1', ''], nm_product_q=['Ricoh SP 230', 'Brother HL'],
        nm_product_code=['', ''], nm_no_serial=['3']), follow_redirects=False)
    assert response.status_code == 303, response.text[:500]
    query = parse_qs(urlsplit(response.headers['location']).query)
    children = kids(db, t)
    assert query['tach'][0].split(',') == [c.number for c in children] and len(children) == 3
    assert children[1].serial == 'WEB-N1' and children[2].serial.startswith('MT-')
    assert db.get(Equipment, 'WEB-N1').model_name == 'Ricoh SP 230'


def test_web_add_missing_serial_shows_error_keeps_blocks(db, job, client):
    p, _, t, eqs = job
    csrf = service_login(client, p['technician'])
    response = client.post(f'/tho/phieu/{t.id}/them-may', data=dict(csrf=csrf, version=t.version, add_pick=['NEW'],
        nm_idx=['0'], nm_serial=[''], nm_product_q=['Ricoh SP 230'], nm_product_code=['']))
    assert response.status_code == 400 and 'Không có serial' in response.text and 'Ricoh SP 230' in response.text
    assert kids(db, t) == []


def test_web_set_route_accepts_several_new_machines(db, job, client):
    p, _, t, eqs = job
    t.serial = None; db.commit()
    csrf = service_login(client, p['dispatcher'])
    response = client.post(f'/dich-vu/{t.id}/may', data=dict(csrf=csrf, version=t.version, machine_pick=['NEW'], nm_idx=['0', '1'],
        nm_serial=['SET-1', 'SET-2'], nm_product_q=['A', 'B'], nm_product_code=['', '']), follow_redirects=False)
    assert response.status_code == 303
    db.expire_all()
    assert db.get(ServiceTicket, t.id).serial == 'SET-1' and [c.serial for c in kids(db, t)] == ['SET-2']


def test_pages_show_add_card_family_and_dispatcher_notice(db, job, client):
    p, _, t, eqs = job
    svc.add_machines(db, p['technician'], t.id, t.version, [eqs[1].serial])
    service_login(client, p['technician'])
    page = client.get(f'/tho/phieu/{t.id}').text
    assert 'data-machine-family' in page and 'Máy cùng chỗ (2)' in page
    assert 'data-add-machine' in page
    notes = [x for x in notifications.items(db, p['dispatcher']) if x['id'].startswith('machines_added:')]
    assert len(notes) == 1 and 'thêm 1 máy tại chỗ' in notes[0]['title']
