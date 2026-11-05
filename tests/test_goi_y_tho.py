"""Gợi ý thợ cho điều phối + nhắc sắp trễ hẹn (TUẤN duyệt 05/11/2026, mẫu GoiYTho)."""
import re
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app import dispatch_alerts, notifications, service_suggest, service_tickets
from app.errors import BusinessError
from app.models import Customer, DispatchZaloSent, ServiceStep, ServiceTicket, ServiceVisit, ZaloChat
from app.schemas import ServiceTicketIn
from test_web import csrf_of, service_login


@pytest.fixture
def people(db, make_user):
    db.add(Customer(code='KTD-000001', name='Minh Phát', search_text='minh phat', address='58 Mã Lò, Q. Bình Tân, HCM'))
    db.add(Customer(code='KTD-000002', name='Đại Việt', search_text='dai viet', address='12 Cộng Hòa, Quận Tân Bình'))
    db.commit()
    return dict(dispatcher=make_user('gy.dieuphoi', 'dispatcher'), phong=make_user('gy.phong', 'technician'),
                a=make_user('gy.a', 'technician'))


def new_ticket(db, p, code='KTD-000001', priority='THUONG', self_claim=False):
    return service_tickets.create(db, p['dispatcher'], ServiceTicketIn(customer_code=code, kind='SUA_TAI_CHO',
        symptom='Kẹt giấy', priority=priority), self_claim=self_claim)


def booked(db, p, tech, minutes, code='KTD-000002'):
    """Phiếu đã giao thợ, đã hẹn khách sau `minutes` phút (thợ chưa đến)."""
    t = new_ticket(db, p, code=code)
    service_tickets.assign(db, p['dispatcher'], t.id, tech.id, t.version)
    db.execute(text("UPDATE service_tickets SET status='DA_HEN', appointment_at=now() + make_interval(mins => :m), appointment_slot='GIO' WHERE id=:id"),
               dict(m=minutes, id=t.id))
    db.commit(); db.expire_all()
    return db.get(ServiceTicket, t.id)


def test_area_reads_district_from_messy_addresses():
    assert service_suggest.area('58 Mã Lò, Q. Bình Tân, HCM') == 'binh tan'
    assert service_suggest.area('78-80 Cách Mạng Tháng 8, Phường 6 , Quận 3, HCM') == '3'
    assert service_suggest.area('51 Đường Số 26, Bình Trị Đông B, Bình Tân, Thành phố Hồ Chí Minh') == 'binh tan'
    assert service_suggest.area('') == ''


def test_free_technician_ranks_first_and_leave_blocks(db, people):
    p = people
    busy = new_ticket(db, p)
    service_tickets.assign(db, p['dispatcher'], busy.id, p['phong'].id, busy.version)
    t = new_ticket(db, p)
    rows = service_suggest.rank(db, t)
    assert rows[0]['id'] == p['a'].id and rows[0]['best'] and '0 việc đang mở' in rows[0]['why']
    today = service_tickets._now().astimezone(service_suggest.TZ).date()
    db.execute(text("INSERT INTO attendance_requests (number, user_id, kind, status, part, from_date, to_date, reason) "
                    "VALUES ('CC-T1', :u, 'NGHI_PHEP', 'DA_DUYET', 'CA_NGAY', :d, :d, 'Việc nhà')"), dict(u=p['a'].id, d=today))
    db.commit()
    rows = service_suggest.rank(db, t)
    blocked = next(r for r in rows if r['id'] == p['a'].id)
    assert blocked['blocked'] == 'Nghỉ cả ngày' and rows[0]['id'] == p['phong'].id


def test_previous_repairer_and_distance_count(db, people):
    p = people
    old = new_ticket(db, p)
    service_tickets.assign(db, p['dispatcher'], old.id, p['phong'].id, old.version)
    db.execute(text("UPDATE service_tickets SET status='HOAN_THANH', repaired_by=:u WHERE id=:id"), dict(u=p['phong'].id, id=old.id))
    db.commit()
    t = new_ticket(db, p)
    rows = service_suggest.rank(db, t)
    assert rows[0]['id'] == p['phong'].id and 'Đã đến khách này 1 lần' in rows[0]['know']
    # A vừa đến khách khác cách 1 km; khách này đã biết vị trí từ lần trước.
    now = service_tickets._now()
    db.add(ServiceVisit(ticket_id=old.id, technician_id=p['phong'].id, started_at=now - timedelta(days=3), ended_at=now - timedelta(days=3),
                        outcome='XONG', arrive_lat=Decimal('10.760000'), arrive_lng=Decimal('106.600000')))
    other = new_ticket(db, p, code='KTD-000002')
    service_tickets.assign(db, p['dispatcher'], other.id, p['a'].id, other.version)
    db.execute(text("UPDATE service_tickets SET status='HOAN_THANH' WHERE id=:id"), dict(id=other.id))
    db.add(ServiceVisit(ticket_id=other.id, technician_id=p['a'].id, started_at=now - timedelta(minutes=20), ended_at=now - timedelta(minutes=5),
                        outcome='XONG', arrive_lat=Decimal('10.765000'), arrive_lng=Decimal('106.605000')))
    db.commit()
    rows = {r['id']: r for r in service_suggest.rank(db, t)}
    assert rows[p['a'].id]['near'].endswith(' km') and rows[p['a'].id]['near'].startswith('0,')


def test_dispatch_page_quick_assign_logs_follow_or_other(db, people, client):
    p = people
    t = new_ticket(db, p)
    service_login(client, p['dispatcher'])
    page = client.get('/dieu-phoi?view=new').text
    assert 'Nên giao' in page and 'Thợ khác ›' in page and 'Sắp trễ hẹn' in page
    block = page[page.index(f'action="/dich-vu/{t.id}/assign"'):]
    assignee = re.search(r'name="assignee_id" value="(\d+)"', block).group(1)
    key = re.search(r'name="form_key" value="([^"]+)"', block).group(1)
    res = client.post(f'/dich-vu/{t.id}/assign', data=dict(csrf=csrf_of(page), form_key=key, version=t.version, assignee_id=assignee,
                                                           goi_y=assignee, quay_ve='/dieu-phoi?view=new'), follow_redirects=False)
    assert res.status_code == 303 and res.headers['location'].startswith('/dieu-phoi?view=new&da_giao=')
    step = db.scalar(select(ServiceStep).where(ServiceStep.ticket_id == t.id, ServiceStep.action == 'assign'))
    assert step.note == service_suggest.NOTE_FOLLOW and step.assignee_id == int(assignee)
    assert 'Đã giao' in client.get(res.headers['location']).text
    # Chọn thợ khác gợi ý, có lý do: thống kê ghi lại.
    t2 = new_ticket(db, p)
    other = p['phong'].id if int(assignee) != p['phong'].id else p['a'].id
    page = client.get(f'/dich-vu/{t2.id}').text
    assert 'Gợi ý:' in page and 'Lý do chọn thợ khác' in page
    res = client.post(f'/dich-vu/{t2.id}/assign', data=dict(csrf=csrf_of(page), form_key=str(__import__('uuid').uuid4()), version=t2.version,
                                                            assignee_id=other, goi_y=assignee, ly_do_khac='Khách quen thợ khác'), follow_redirects=False)
    assert res.status_code == 303
    step = db.scalar(select(ServiceStep).where(ServiceStep.ticket_id == t2.id, ServiceStep.action == 'assign'))
    assert step.note.startswith(service_suggest.NOTE_OTHER) and step.note.endswith('· Khách quen thợ khác')
    now = service_tickets._now()
    stats = service_suggest.month_stats(db, now - timedelta(days=1), now + timedelta(minutes=1))
    assert stats['follow'] == 1 and stats['other'] == 1 and stats['reasons'] == [('Khách quen thợ khác', 1)]
    assert 'Gợi ý thợ · nhắc trễ hẹn' in client.get('/dieu-phoi/bao-cao').text


def test_at_risk_levels_depart_and_late(db, people, client):
    p = people
    soon = booked(db, p, p['phong'], 20)
    later = booked(db, p, p['a'], 5 * 60)
    tech_ids = {i['ticket'].id for i in service_suggest.at_risk(db, level='tech')}
    assert soon.id in tech_ids and later.id not in tech_ids
    assert not service_suggest.at_risk(db, level='dispatch')
    # Thợ thấy chuông + khung Đi ngay / Báo trễ.
    assert any(r['id'].startswith('appointment_soon:') for r in notifications.items(db, p['phong']))
    service_login(client, p['phong'])
    page = client.get('/tho').text
    assert f'data-soon="{soon.id}"' in page and 'Đi ngay' in page
    block = page[page.index(f'action="/tho/phieu/{soon.id}/bao-tre"'):]
    key = re.search(r'name="form_key" value="([^"]+)"', block).group(1)
    res = client.post(f'/tho/phieu/{soon.id}/bao-tre', data=dict(csrf=csrf_of(page), form_key=key, tre='30'), follow_redirects=False)
    assert res.status_code == 303 and res.headers['location'] == '/tho?hen=tre'
    assert any(r['id'].startswith('tech_late:') for r in notifications.items(db, p['dispatcher']))
    # Còn 5 phút: điều phối thấy ô Sắp trễ hẹn, có nút đổi sang thợ khác.
    db.execute(text("UPDATE service_tickets SET appointment_at=now() + interval '5 minutes' WHERE id=:id"), dict(id=soon.id))
    db.commit()
    db.expire_all()
    assert [i['ticket'].id for i in service_suggest.at_risk(db, level='dispatch')] == [soon.id]
    service_login(client, p['dispatcher'])
    page = client.get('/dieu-phoi?view=late').text
    assert soon.number in page and 'Chưa đi' in page and 'Đổi sang' in page and 'Thợ báo: trễ 30 phút' in page
    # Thợ bấm Đi: thôi nhắc.
    service_suggest.depart(db, p['phong'], soon.id)
    assert soon.id not in {i['ticket'].id for i in service_suggest.at_risk(db, level='tech')}
    # Dời hẹn thì mốc mới, nhắc lại.
    db.expire_all()
    t = db.get(ServiceTicket, soon.id)
    service_tickets.reschedule(db, p['dispatcher'], t.id, t.version, service_tickets._now() + timedelta(minutes=25), 'GIO', 'Khách dời')
    assert soon.id in {i['ticket'].id for i in service_suggest.at_risk(db, level='tech')}
    with pytest.raises(BusinessError):
        service_suggest.depart(db, p['a'], soon.id)  # không phải thợ giữ phiếu
    with pytest.raises(BusinessError):
        service_suggest.report_late(db, p['phong'], soon.id, '5')


def test_zalo_to_dispatcher_once_per_event(db, people, monkeypatch):
    p = people
    db.add(ZaloChat(chat_id='zalo-thanh', chat_type='PRIVATE', display_name='Thạnh', user_id=p['dispatcher'].id))
    db.commit()
    new_ticket(db, p, priority='KHAN')
    booked(db, p, p['phong'], 5)
    settings = dispatch_alerts.get_settings().model_copy(update=dict(zalo_bot_token='token', dispatch_zalo=True))
    monkeypatch.setattr(dispatch_alerts, 'get_settings', lambda: settings)
    sent = []
    monkeypatch.setattr(dispatch_alerts, 'working_now', lambda session, now: False)
    assert dispatch_alerts.run_once(db, sender=lambda *a: sent.append(a)) == 0
    monkeypatch.setattr(dispatch_alerts, 'working_now', lambda session, now: True)
    assert dispatch_alerts.run_once(db, sender=lambda *a: sent.append(a)) == 2
    texts = [a[2] for a in sent]
    assert any('phiếu Khẩn' in x and 'Gợi ý:' in x for x in texts) and any('chưa đi' in x for x in texts)
    assert all('/dich-vu/' in x for x in texts)
    assert dispatch_alerts.run_once(db, sender=lambda *a: sent.append(a)) == 0
    assert db.scalar(select(text('count(*)')).select_from(DispatchZaloSent)) == 2
    # Không gắn Zalo / tắt cấu hình: không gửi.
    monkeypatch.setattr(dispatch_alerts, 'get_settings', lambda: settings.model_copy(update=dict(dispatch_zalo=False)))
    new_ticket(db, p, priority='KHAN')
    assert dispatch_alerts.run_once(db, sender=lambda *a: sent.append(a)) == 0
