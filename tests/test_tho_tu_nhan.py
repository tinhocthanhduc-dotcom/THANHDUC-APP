"""Thợ tự nhận phiếu dịch vụ chưa giao trong 30 phút (TUẤN 04/11/2026, mẫu ThoTuNhan đã duyệt)."""
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app import my_work, notifications, service_self_claim, service_tickets
from app.errors import BusinessError
from app.models import ServiceStep, ServiceTicket
from app.schemas import ServiceTicketIn
from app.models import Customer
from test_web import csrf_of, service_login


@pytest.fixture
def people(db, make_user):
    db.add(Customer(code='KTD-000001', name='Minh Phát', search_text='minh phat'))
    db.commit()
    return dict(dispatcher=make_user('tn.dieuphoi', 'dispatcher'), technician=make_user('tn.phong', 'technician'),
                other_tech=make_user('tn.thanh', 'technician'))


def new_ticket(db, p, self_claim=True, priority='THUONG', kind='SUA_TAI_CHO'):
    return service_tickets.create(db, p['dispatcher'], ServiceTicketIn(customer_code='KTD-000001', kind=kind,
        symptom='Máy bị kẹt giấy', priority=priority), self_claim=self_claim)


def expire(db, t):
    db.execute(text("UPDATE service_tickets SET self_claim_until = now() - interval '1 minute' WHERE id=:id"), dict(id=t.id))
    db.commit(); db.expire_all()


def claim_form(page, ticket_id):
    import re
    block = page[page.index(f'action="/tho/nhan/{ticket_id}"'):]
    key = re.search(r'name="form_key" value="([^"]+)"', block).group(1)
    return {'csrf': csrf_of(page), 'form_key': key}


def unassigned_ids(db, user):
    group = next(g for g in my_work.collect(db, user, keys=('service_unassigned',)))
    return {r['id'] for r in group['rows']}


def test_create_opens_window_only_for_eligible_tickets(db, people):
    p = people
    t = new_ticket(db, p)
    assert t.self_claim_until is not None
    left = (t.self_claim_until - service_tickets._now()).total_seconds() / 60
    assert 29 < left <= 30
    assert new_ticket(db, p, self_claim=False).self_claim_until is None
    assert new_ticket(db, p, priority='KHAN').self_claim_until is None


def test_first_technician_wins_and_second_sees_who_claimed(db, people):
    p = people
    t = new_ticket(db, p)
    service_self_claim.claim(db, p['technician'], t.id)
    db.expire_all(); t = db.get(ServiceTicket, t.id)
    assert t.assignee_id == p['technician'].id and t.status == 'DA_NHAN'
    steps = list(db.scalars(select(ServiceStep).where(ServiceStep.ticket_id == t.id).order_by(ServiceStep.id)))
    claim_step = next(s for s in steps if s.action == 'assign')
    assert service_self_claim.is_self_claim(claim_step) and claim_step.actor_id == p['technician'].id
    with pytest.raises(BusinessError) as err:
        service_self_claim.claim(db, p['other_tech'], t.id)
    assert err.value.code == 'ALREADY_CLAIMED' and err.value.status == 409
    assert p['technician'].full_name in err.value.message


def test_expired_ticket_goes_back_to_dispatcher_queue(db, people):
    p = people
    t = new_ticket(db, p)
    assert t.id not in unassigned_ids(db, p['dispatcher'])
    expire(db, t)
    assert t.id in unassigned_ids(db, p['dispatcher'])
    with pytest.raises(BusinessError) as err:
        service_self_claim.claim(db, p['technician'], t.id)
    assert err.value.code == 'CLAIM_EXPIRED'
    assert db.get(ServiceTicket, t.id).assignee_id is None


def test_busy_technician_cannot_claim(db, people):
    p = people
    for _ in range(2):
        busy = new_ticket(db, p, self_claim=False)
        service_tickets.assign(db, p['dispatcher'], busy.id, p['technician'].id, busy.version)
    t = new_ticket(db, p)
    with pytest.raises(BusinessError) as err:
        service_self_claim.claim(db, p['technician'], t.id)
    assert err.value.code == 'TOO_MANY_JOBS'
    service_self_claim.claim(db, p['other_tech'], t.id)


def test_only_technicians_claim_and_only_open_tickets(db, people):
    p = people
    t = new_ticket(db, p)
    with pytest.raises(BusinessError) as err:
        service_self_claim.claim(db, p['dispatcher'], t.id)
    assert err.value.status == 403
    closed = new_ticket(db, p, self_claim=False)
    with pytest.raises(BusinessError) as err:
        service_self_claim.claim(db, p['technician'], closed.id)
    assert err.value.code == 'NOT_FOUND'


def test_urgent_edit_closes_window(db, people):
    p = people
    t = new_ticket(db, p)
    service_tickets.update_info(db, p['dispatcher'], t.id, t.version, t.symptom, 'KHAN', t.site_id)
    db.expire_all()
    assert t.id in unassigned_ids(db, p['dispatcher'])
    with pytest.raises(BusinessError) as err:
        service_self_claim.claim(db, p['technician'], t.id)
    assert err.value.code == 'CLAIM_EXPIRED'


def test_notifications_for_technicians_and_dispatcher(db, people):
    p = people
    t = new_ticket(db, p)
    ids = {row['id'] for row in notifications.items(db, p['other_tech'])}
    assert f'self_claim_open:{t.id}' in ids
    service_self_claim.claim(db, p['technician'], t.id)
    ids = {row['id'] for row in notifications.items(db, p['other_tech'])}
    assert f'self_claim_open:{t.id}' not in ids
    rows = notifications.items(db, p['dispatcher'])
    claimed = [r for r in rows if r['id'].startswith('self_claimed:')]
    assert claimed and claimed[0]['text'] == t.number and p['technician'].full_name in claimed[0]['title']


def test_tho_screen_claim_and_dispatcher_tiles(db, people, client):
    p = people
    t = new_ticket(db, p)
    service_login(client, p['dispatcher'])
    page = client.get('/dieu-phoi?view=claim').text
    assert 'Chờ thợ tự nhận' in page and t.number in page and 'Chờ thợ nhận · còn' in page
    assert t.number not in client.get('/dieu-phoi?view=new').text
    service_login(client, p['technician'])
    page = client.get('/tho').text
    assert 'Việc chờ nhận · 1' in page and f'action="/tho/nhan/{t.id}"' in page
    res = client.post(f'/tho/nhan/{t.id}', data=claim_form(page, t.id), follow_redirects=False)
    assert res.status_code == 303 and res.headers['location'].startswith(f'/tho?nhan={t.number}')
    page = client.get(res.headers['location']).text
    assert f'Đã nhận {t.number}' in page and 'Việc chờ nhận' not in page
    service_login(client, p['other_tech'])
    stale = client.get('/tho').text  # màn đã mở trước khi Phong nhận
    assert 'Việc chờ nhận' not in stale
    res = client.post(f'/tho/nhan/{t.id}', data={'csrf': csrf_of(stale), 'form_key': str(__import__('uuid').uuid4())})
    assert res.status_code == 409 and 'đã nhận phiếu' in res.text


def test_expired_shows_red_on_dispatch(db, people, client):
    p = people
    t = new_ticket(db, p)
    expire(db, t)
    service_login(client, p['dispatcher'])
    page = client.get('/dieu-phoi?view=new').text
    assert t.number in page and 'Hết giờ tự nhận · cần giao' in page


def test_two_technicians_tapping_at_once_only_one_wins(db, people):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    from app.models import User
    p = people
    t = new_ticket(db, p)
    engine = db.get_bind()
    barrier = Barrier(2)

    def tap(uid):
        with Session(engine, expire_on_commit=False) as session:
            user = session.get(User, uid)
            barrier.wait(timeout=10)
            try:
                service_self_claim.claim(session, user, t.id)
                return 'ok'
            except BusinessError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(tap, [p['technician'].id, p['other_tech'].id]))
    assert results == ['ALREADY_CLAIMED', 'ok']
    db.expire_all()
    assert db.scalar(select(ServiceStep.id).where(ServiceStep.ticket_id == t.id, ServiceStep.action == 'assign')) is not None
    assert len(list(db.scalars(select(ServiceStep).where(ServiceStep.ticket_id == t.id, ServiceStep.action == 'assign')))) == 1
