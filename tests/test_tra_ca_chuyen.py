"""Trả máy cả chuyến (TUẤN duyệt mẫu 23/11/2026): giao thợ trả nhiều máy một lần, thợ trả cả chuyến một lần vuốt."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import equipment, service_return_trip as trip, service_tickets, service_workshop as ws, equipment_custody as custody
from app.errors import BusinessError
from app.models import SalesReceipt, ServiceTicket
from app.schemas import CustodyHandover, EquipmentIn
from test_my_work import people, purchase_people, ticket  # noqa: F401
from test_technician_stock import held, transfer  # noqa: F401
from test_mv1_workshop import photo, intake  # noqa: F401
from test_xg1_workshop import quote, approve, repaired
from test_web import service_login


def _ready(db, p, serial):
    t = ticket(db, p, 'DANG_LAM')
    eq = equipment.create(db, p['dispatcher'], EquipmentIn(serial=serial, product_code='TD-0001', customer_code=t.customer_code))
    t.serial = eq.serial; db.commit()
    photo(db, p, t)
    ws.bring(db, p['technician'], t.id, t.version, intake(eq), [])
    ws.hand_to_workshop(db, p['technician'], t.id, t.version, CustodyHandover(version=eq.version, location='KHO_KY_THUAT'))
    hand = custody.pending(db, eq.serial); custody.confirm(db, p['warehouse'], hand.id, hand.version)
    quote(db, p, t); approve(db, p, t); photo(db, p, t, 'SAU', who='dispatcher'); repaired(db, p, t)
    db.refresh(t)
    assert t.status == 'TAM_DUNG' and t.hold_reason == 'SUA_XONG_CHO_TRA'
    return t, eq


@pytest.fixture
def two(db, held):
    p, a, b = held; transfer(db, p, qty=4)
    p['dispatcher'].roles = [*p['dispatcher'].roles, 'sales']; db.commit()
    t1, e1 = _ready(db, p, 'TCC-1')
    t2, e2 = _ready(db, p, 'TCC-2')
    return p, (t1, e1), (t2, e2)


def _form(pairs):
    out = {}
    for k, v in pairs:
        out.setdefault(k, []).append(v)
    return {k: v[0] if len(v) == 1 else v for k, v in out.items()}


def _schedule(client, p, rows, tech):
    csrf = service_login(client, p['dispatcher'])
    data = [('csrf', csrf), ('khach', rows[0].customer_code), ('appointment_choice', 'GIO'),
            ('appointment_at', (service_tickets._now() + timedelta(days=1)).astimezone(service_tickets.service_sla.TZ).strftime('%Y-%m-%dT%H:%M')),
            ('assignee_id', str(tech.id)), ('note', 'Gọi trước')]
    for t in rows:
        data += [('p', str(t.id)), (f'v_{t.id}', str(t.version))]
    return client.post('/tra-may-ca-chuyen', data=_form(data), follow_redirects=False)


def test_board_offers_trip_and_schedule_many_is_atomic(client, db, two):
    p, (t1, e1), (t2, e2) = two
    csrf = service_login(client, p['dispatcher'])
    board = client.get('/kiem-tra-may?view=s6').text
    assert f'/tra-may-ca-chuyen?khach={t1.customer_code}' in board and 'Giao thợ trả 2 máy' in board
    page = client.get(f'/tra-may-ca-chuyen?khach={t1.customer_code}').text
    assert t1.number in page and t2.number in page and "Phải thu cả chuyến" in page
    # Một phiếu sai version thì không phiếu nào được giao.
    when = (service_tickets._now() + timedelta(days=1)).astimezone(service_tickets.service_sla.TZ).strftime('%Y-%m-%dT%H:%M')
    r = client.post('/tra-may-ca-chuyen', data=_form([('csrf', csrf), ('khach', t1.customer_code), ('appointment_choice', 'GIO'),
        ('appointment_at', when), ('assignee_id', str(p['technician'].id)), ('p', str(t1.id)), (f'v_{t1.id}', str(t1.version)),
        ('p', str(t2.id)), (f'v_{t2.id}', str(t2.version + 5))]), follow_redirects=False)
    assert r.status_code == 409, r.text
    db.expire_all()
    assert db.get(ServiceTicket, t1.id).status == 'TAM_DUNG' and db.get(ServiceTicket, t2.id).status == 'TAM_DUNG'
    r = _schedule(client, p, [db.get(ServiceTicket, t1.id), db.get(ServiceTicket, t2.id)], p['technician'])
    assert r.status_code == 303, r.text
    db.expire_all()
    a, b = db.get(ServiceTicket, t1.id), db.get(ServiceTicket, t2.id)
    assert a.status == b.status and a.status in trip.ON_TRIP and a.assignee_id == b.assignee_id == p['technician'].id
    assert a.appointment_at == b.appointment_at


def test_schedule_many_forbidden_for_technician(db, two):
    p, (t1, _), (t2, _) = two
    with pytest.raises(BusinessError) as e:
        trip.schedule_many(db, p['technician'], [(t1.id, t1.version), (t2.id, t2.version)], service_tickets._now() + timedelta(days=1), None, p['technician'].id)
    assert e.value.status == 403


def _on_trip(client, db, p, t1, t2):
    assert _schedule(client, p, [t1, t2], p['technician']).status_code == 303
    db.expire_all()
    csrf = service_login(client, p['technician'])
    if any(custody.pending(db, t.serial) for t in (t1, t2)):
        assert 'Đã nhận 2 máy từ kho' in client.get(f'/tho/chuyen-tra/{t1.id}').text
        assert client.post(f'/tho/chuyen-tra/{t1.id}/nhan-may', data=dict(csrf=csrf), follow_redirects=False).status_code == 303
    db.expire_all()
    assert not any(custody.pending(db, t.serial) for t in (t1, t2))
    return db.get(ServiceTicket, t1.id), db.get(ServiceTicket, t2.id)


def test_technician_returns_whole_trip_with_one_photo_and_partial_payment(client, db, two):
    from app.models import Equipment
    p, (t1, _), (t2, _) = two
    t1, t2 = _on_trip(client, db, p, t1, t2)
    total = t1.receivable + t2.receivable
    csrf = service_login(client, p['technician'])
    home = client.get('/tho').text
    assert f'/tho/chuyen-tra/{t1.id}' in home and 'Trả 2 máy' in home
    assert f'/tho/chuyen-tra/{t2.id}' in client.get(f'/tho/phieu/{t2.id}').text
    from test_web import d5_image
    r = client.post(f'/tho/chuyen-tra/{t1.id}/anh', data=dict(csrf=csrf), files={'file': ('tra.png', d5_image(), 'image/png')}, follow_redirects=False)
    assert r.status_code == 303, r.text
    page = client.get(f'/tho/chuyen-tra/{t1.id}').text
    assert 'Còn thiếu ảnh' not in page and f'/dich-vu/{t1.id}/bien-ban-gop' in page
    db.expire_all(); t1, t2 = db.get(ServiceTicket, t1.id), db.get(ServiceTicket, t2.id)
    e1, e2 = db.get(Equipment, t1.serial), db.get(Equipment, t2.serial)
    form = dict(csrf=csrf, mode='PARTIAL', amount=f'{t1.receivable + 1000:,}'.replace(',', '.'), method='TIEN_MAT')
    data = list(form.items()) + [('p', str(t1.id)), (f'v_{t1.id}', str(t1.version)), (f'mv_{t1.id}', str(e1.version)),
                                 ('p', str(t2.id)), (f'v_{t2.id}', str(t2.version)), (f'mv_{t2.id}', str(e2.version))]
    r = client.post(f'/tho/chuyen-tra/{t1.id}/hoan-thanh', data=_form(data), follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all(); a, b = db.get(ServiceTicket, t1.id), db.get(ServiceTicket, t2.id)
    assert a.status in ('HOAN_THANH', 'DA_DONG') and b.status in ('HOAN_THANH', 'DA_DONG')
    assert db.get(Equipment, a.serial).custody_location == 'TAI_KHACH' and db.get(Equipment, b.serial).custody_location == 'TAI_KHACH'
    receipts = {r.ticket_id: r.amount for r in db.scalars(select(SalesReceipt))}
    # Thu thiếu: trừ phiếu đầu đủ, phần còn lại vào phiếu sau.
    assert receipts == {a.id: a.receivable, b.id: 1000} and sum(receipts.values()) < total


def test_trip_overpay_rolls_back_everything(client, db, two):
    from app.models import Equipment
    from app import attachments
    from test_web import d5_image
    p, (t1, _), (t2, _) = two
    t1, t2 = _on_trip(client, db, p, t1, t2)
    for t in (t1, t2):
        attachments.add(db, p['technician'], 'service_ticket', str(t.id), 'TRA_MAY', 'tra.png', d5_image())
    db.expire_all(); t1, t2 = db.get(ServiceTicket, t1.id), db.get(ServiceTicket, t2.id)
    picks = [(t.id, t.version, db.get(Equipment, t.serial).version) for t in (t1, t2)]
    before = [(t.status, t.version) for t in (t1, t2)]
    with pytest.raises(BusinessError) as e:
        trip.complete(db, p['technician'], t1.id, picks, t1.receivable + t2.receivable + 1, 'TIEN_MAT')
    assert e.value.code == 'OVER_PAYMENT'
    db.expire_all()
    assert [(db.get(ServiceTicket, t.id).status, db.get(ServiceTicket, t.id).version) for t in (t1, t2)] == before
    assert db.query(SalesReceipt).count() == 0
    assert db.get(Equipment, t1.serial).custody_location == 'THO_GIU'
    # Thợ khác không trả được chuyến của người khác.
    with pytest.raises(BusinessError):
        trip.complete(db, p['other_tech'], t1.id, picks, 0, 'TIEN_MAT')
