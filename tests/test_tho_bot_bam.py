"""Đợt 3 tối ưu thao tác (TUẤN duyệt mẫu 17/11/2026): thợ bấm ngay trên thẻ việc màn Hôm nay.

Tự nhận 1 lần bấm (Hoàn tác ở trình duyệt), "Nhận và đi", Nhận việc / Đi ngay / Đã đến khách ngay trên thẻ.
"""
import re
from datetime import timedelta

from sqlalchemy import select

from app import service_suggest, service_tickets
from app.models import ServiceStep, ServiceTicket
from test_tho_tu_nhan import claim_form, new_ticket, people  # noqa: F401
from test_web import csrf_of, service_login


def _card_form(page, action):
    block = page[page.index(f'action="{action}"'):]
    return {'form_key': re.search(r'name="form_key" value="([^"]+)"', block).group(1), 'csrf': csrf_of(page)}


def test_claim_one_tap_and_claim_and_go(db, people, client):
    p = people
    t = new_ticket(db, p)
    service_login(client, p['technician'])
    assert 'data-claim-go' not in client.get('/tho').text  # chưa có hẹn: chỉ Nhận việc
    t.appointment_at = service_tickets._now() + timedelta(hours=2); db.commit()
    page = client.get('/tho').text
    assert 'data-claim-go' in page and 'data-undo-form' in page and 'tho-claim-ask' not in page and 'tho-the-viec.js' in page
    res = client.post(f'/tho/nhan/{t.id}', data={**claim_form(page, t.id), 'di': '1'}, follow_redirects=False)
    assert res.status_code == 303 and res.headers['location'].startswith(f'/tho?nhan={t.number}&hen=di')
    db.expire_all()
    assert db.get(ServiceTicket, t.id).status == 'DA_NHAN'
    assert db.scalar(select(ServiceStep.id).where(ServiceStep.ticket_id == t.id, ServiceStep.action == service_suggest.DEPART)) is not None


def test_accept_and_arrive_from_card(db, people, client):
    p = people
    t = new_ticket(db, p, self_claim=False)
    service_tickets.assign(db, p['dispatcher'], t.id, p['technician'].id, t.version, '')
    db.expire_all(); t = db.get(ServiceTicket, t.id)
    service_login(client, p['technician'])
    page = client.get('/tho').text
    accept = f'/dich-vu/{t.id}/transition?ve=hom-nay'
    assert f'action="{accept}"' in page and 'data-card-accept' in page
    res = client.post(accept, data={**_card_form(page, accept), 'version': t.version, 'to_status': 'DA_NHAN', 'return_to': 'tho'},
                      follow_redirects=False)
    assert res.status_code == 303 and res.headers['location'].startswith(f'/tho?nhan={t.number}')
    db.expire_all(); t = db.get(ServiceTicket, t.id)
    assert t.status == 'DA_NHAN'
    # Có hẹn: thẻ có Đi ngay và Đã đến khách; Đã đến khách mở luôn phiếu để làm.
    t.appointment_at = service_tickets._now() + timedelta(hours=3); db.commit()
    page = client.get('/tho').text
    arrive = f'/dich-vu/{t.id}/transition'
    assert 'data-card-go' in page and 'data-card-arrive' in page and 'data-gps-form' in page
    res = client.post(arrive, data={**_card_form(page, arrive), 'version': t.version, 'to_status': 'DANG_LAM', 'return_to': 'tho'},
                      follow_redirects=False)
    assert res.status_code == 303 and res.headers['location'].startswith(f'/tho/phieu/{t.id}')
    db.expire_all()
    assert db.get(ServiceTicket, t.id).status == 'DANG_LAM'
