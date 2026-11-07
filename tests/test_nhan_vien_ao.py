"""Nhân viên AI đợt 1 (TUẤN duyệt 07/11/2026): Điều phối AI, Nhắc hẹn AI, Thư ký phiếu, trang /nhan-vien-ao."""
import re
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from app import dispatch_alerts, nhan_vien_ao as nva, notifications, service_tickets
from app.errors import BusinessError
from app.models import (AttendancePunch, Customer, DispatchZaloSent, ServiceStep, ServiceTicket, User, VirtualAgent,
                        VirtualAgentLog, ZaloChat)
from app.schemas import ServiceTicketIn
from test_web import csrf_of, service_login


@pytest.fixture
def people(db, make_user):
    db.add(Customer(code='KTD-000001', name='Minh Phát', search_text='minh phat', address='58 Mã Lò, Q. Bình Tân, HCM'))
    db.commit()
    return dict(director=make_user('nva.gd', 'director'), dispatcher=make_user('nva.dieuphoi', 'dispatcher'),
                phong=make_user('nva.phong', 'technician'), a=make_user('nva.a', 'technician'))


@pytest.fixture
def zalo_on(monkeypatch):
    settings = nva.get_settings().model_copy(update=dict(zalo_bot_token='token', dispatch_zalo=True))
    monkeypatch.setattr(nva, 'get_settings', lambda: settings)
    monkeypatch.setattr(dispatch_alerts, 'get_settings', lambda: settings)
    monkeypatch.setattr(nva, '_working', lambda session, now: True)
    return settings


def clock_in(db, user):
    db.add(AttendancePunch(user_id=user.id, kind='IN', at=service_tickets._now(), source='APP', off_hours=False))
    db.commit()


def old_ticket(db, p, kind='MUC_DINH_KY', priority='THUONG', minutes=45):
    t = service_tickets.create(db, p['dispatcher'], ServiceTicketIn(customer_code='KTD-000001', kind=kind, symptom='Hết mực',
                                                                    priority=priority))
    db.execute(text("UPDATE service_tickets SET created_at = now() - make_interval(mins => :m) WHERE id = :id"), dict(m=minutes, id=t.id))
    db.commit(); db.expire_all()
    return db.get(ServiceTicket, t.id)


def link_zalo(db, user, chat_id):
    db.add(ZaloChat(chat_id=chat_id, chat_type='PRIVATE', display_name=user.full_name, user_id=user.id))
    db.commit()


def test_seeded_on_and_master_switch(db, people):
    rows = nva.rows(db)
    assert set(rows) == {'TAT_CA', 'DIEU_PHOI', 'NHAC_HEN', 'THU_KY'}
    assert all(r.enabled for r in rows.values()) and rows['DIEU_PHOI'].mode == 'TU_LAM'
    c = nva.cfg(rows['DIEU_PHOI'])
    assert c['kinds'] == ['MUC_DINH_KY', 'SUA_TAI_CHO'] and c['priorities'] == ['THUONG'] and c['undo_minutes'] == 10
    assert c['max_per_day'] == 0 and c['max_per_tech'] == 0
    rows['TAT_CA'].enabled = False
    db.commit()
    assert nva.active(db, 'DIEU_PHOI') is None


def test_virtual_dispatcher_assigns_easy_ticket_to_clocked_in_tech(db, people, monkeypatch):
    p = people
    monkeypatch.setattr(nva, '_working', lambda session, now: True)
    t = old_ticket(db, p)
    fresh = old_ticket(db, p, minutes=5)                       # chưa tới giờ
    urgent = old_ticket(db, p, priority='KHAN')                 # Khẩn: không tự giao
    gap = old_ticket(db, p, priority='GAP')                     # mức Gấp chưa cho phép
    # Chưa thợ nào vào ca: bỏ qua, ghi một lần, để điều phối giao.
    assert nva.dispatch_once(db) == 0
    assert nva.dispatch_once(db) == 0
    skips = db.scalars(select(VirtualAgentLog).where(VirtualAgentLog.action == 'BO_QUA')).all()
    assert [s.ticket_id for s in skips] == [t.id]
    clock_in(db, p['phong'])
    assert nva.dispatch_once(db) == 1
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    assert t.assignee_id == p['phong'].id and t.status == 'DA_GIAO'
    step = db.scalar(select(ServiceStep).where(ServiceStep.ticket_id == t.id, ServiceStep.action == 'assign'))
    actor = db.get(User, step.actor_id)
    assert actor.username == nva.SYSTEM_USERNAME and actor.active is False and step.note.startswith(nva.NOTE)
    entry = db.scalar(select(VirtualAgentLog).where(VirtualAgentLog.action == 'GIAO'))
    assert entry.ticket_id == t.id and entry.target_id == p['phong'].id and 'Phong' in entry.text
    for other in (fresh, urgent, gap):
        assert db.get(ServiceTicket, other.id).assignee_id is None
    # Không giao lại phiếu đã xử lý.
    assert nva.dispatch_once(db) == 0
    # Điều phối thấy trong chuông.
    keys = {i['id'].split(':')[0] for i in notifications.items(db, p['dispatcher'])}
    assert 'virtual_assigned' in keys


def test_system_user_cannot_log_in(db, people, client):
    nva.system_user(db)
    res = client.post('/dang-nhap', data={'username': nva.SYSTEM_USERNAME, 'password': 'x', 'next': '/'})
    assert 'Đăng xuất' not in res.text


def test_trial_mode_only_logs(db, people, monkeypatch):
    p = people
    monkeypatch.setattr(nva, '_working', lambda session, now: True)
    rows = nva.rows(db)
    rows['DIEU_PHOI'].mode = 'CHAY_THU'
    db.commit()
    clock_in(db, p['a'])
    t = old_ticket(db, p, kind=None)   # chưa chọn loại = Sửa tại chỗ
    assert nva.dispatch_once(db) == 0
    assert db.get(ServiceTicket, t.id).assignee_id is None
    entry = db.scalar(select(VirtualAgentLog).where(VirtualAgentLog.action == 'CHAY_THU'))
    assert entry.target_id == p['a'].id
    service_tickets.assign(db, p['dispatcher'], t.id, p['a'].id, t.version)
    assert nva.trial_results(db, [entry]) == {entry.id: True}


def test_limits_per_tech(db, people, monkeypatch):
    p = people
    monkeypatch.setattr(nva, '_working', lambda session, now: True)
    rows = nva.rows(db)
    rows['DIEU_PHOI'].config = dict(max_per_tech=1)
    db.commit()
    clock_in(db, p['phong'])
    old_ticket(db, p); old_ticket(db, p)
    assert nva.dispatch_once(db) == 1


def test_undo_within_window_returns_ticket_and_not_reassigned(db, people, monkeypatch, client):
    p = people
    monkeypatch.setattr(nva, '_working', lambda session, now: True)
    clock_in(db, p['phong'])
    t = old_ticket(db, p)
    nva.dispatch_once(db)
    entry = db.scalar(select(VirtualAgentLog).where(VirtualAgentLog.action == 'GIAO'))
    service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'Điều phối AI đã giao' in page and 'Hoàn tác' in page
    form = page[page.index('/dieu-phoi-ao/hoan-tac'):]
    key = re.search(r'name="form_key" value="([^"]+)"', form).group(1)
    res = client.post(f'/dich-vu/{t.id}/dieu-phoi-ao/hoan-tac', data=dict(csrf=csrf_of(page), form_key=key, log_id=entry.id), follow_redirects=False)
    assert res.status_code == 303, re.findall(r'role="alert"[^<]*>([^<]+)', res.text)
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    assert t.status == 'MOI' and t.assignee_id is None
    assert db.get(VirtualAgentLog, entry.id).undone_by == p['dispatcher'].id
    assert db.scalar(select(ServiceStep.note).where(ServiceStep.ticket_id == t.id, ServiceStep.action == 'unassign')).startswith('Hoàn tác')
    assert nva.dispatch_once(db) == 0 and db.get(ServiceTicket, t.id).assignee_id is None
    with pytest.raises(BusinessError):
        nva.undo(db, p['dispatcher'], entry.id)


def test_undo_closed_after_tech_accepts_or_window(db, people, monkeypatch):
    p = people
    monkeypatch.setattr(nva, '_working', lambda session, now: True)
    clock_in(db, p['phong'])
    t = old_ticket(db, p)
    nva.dispatch_once(db)
    entry = db.scalar(select(VirtualAgentLog).where(VirtualAgentLog.action == 'GIAO'))
    db.execute(text("UPDATE virtual_agent_log SET at = now() - interval '11 minutes' WHERE id = :id"), dict(id=entry.id))
    db.commit(); db.expire_all()
    assert nva.ticket_note(db, db.get(ServiceTicket, t.id))['can_undo'] is False
    with pytest.raises(BusinessError) as exc:
        nva.undo(db, p['dispatcher'], entry.id)
    assert exc.value.code == 'UNDO_CLOSED'
    with pytest.raises(BusinessError) as exc:
        nva.undo(db, p['phong'], entry.id)
    assert exc.value.status == 403


def test_dispatchers_told_on_zalo(db, people, zalo_on):
    p = people
    link_zalo(db, p['dispatcher'], 'z-dp')
    clock_in(db, p['phong'])
    t = old_ticket(db, p)
    sent = []
    assert nva.dispatch_once(db, sender=lambda *a: sent.append(a)) == 1
    assert len(sent) == 1 and 'Điều phối AI vừa giao' in sent[0][2] and f'/dich-vu/{t.id}' in sent[0][2] and 'hoàn tác' in sent[0][2]


def test_remind_tech_not_accepted_and_director_late(db, people, zalo_on):
    p = people
    link_zalo(db, p['phong'], 'z-phong')
    link_zalo(db, p['director'], 'z-gd')
    t = service_tickets.create(db, p['dispatcher'], ServiceTicketIn(customer_code='KTD-000001', kind='SUA_TAI_CHO', symptom='Kẹt giấy'))
    service_tickets.assign(db, p['dispatcher'], t.id, p['phong'].id, t.version)
    # Phiếu khác đã hẹn, trễ 40 phút, thợ chưa đi.
    late = service_tickets.create(db, p['dispatcher'], ServiceTicketIn(customer_code='KTD-000001', kind='SUA_TAI_CHO', symptom='Lỗi'))
    service_tickets.assign(db, p['dispatcher'], late.id, p['a'].id, late.version)
    db.execute(text("UPDATE service_tickets SET status='DA_HEN', appointment_at=now() - interval '40 minutes', appointment_slot='GIO' WHERE id=:id"),
               dict(id=late.id))
    db.commit(); db.expire_all()
    sent = []
    later = service_tickets._now() + timedelta(minutes=20)   # bước giao chỉ thêm, không sửa được: cho đồng hồ chạy tới
    nva.remind_once(db, now=later, sender=lambda *a: sent.append(a))
    texts = {a[1].chat_id: a[2] for a in sent}
    assert 'chưa bấm Nhận' in texts['z-phong'] and f'/tho/phieu/{t.id}' in texts['z-phong']
    assert 'trễ hẹn' in texts['z-gd'] and 'Nva.A' in texts['z-gd']
    n = len(sent)
    nva.remind_once(db, now=later, sender=lambda *a: sent.append(a))
    assert len(sent) == n   # mỗi mốc một tin
    assert db.scalar(select(func.count()).select_from(VirtualAgentLog).where(VirtualAgentLog.agent == 'NHAC_HEN')) == n


def test_clerk_digest_once_per_slot(db, people, zalo_on, monkeypatch, make_user):
    p = people
    sales = make_user('nva.thanh', 'sales', 'finance')
    link_zalo(db, sales, 'z-thanh')
    link_zalo(db, p['director'], 'z-gd')
    groups = [dict(key='orders_unreported', title='Đã giao · chưa báo thu đủ', count=2, rows=[dict(number='DB-26-00001'), dict(number='DB-26-00002')])]
    from app import my_work
    monkeypatch.setattr(my_work, 'collect', lambda session, user, keys=None: groups)
    monkeypatch.setattr(nva, 'clerk_slot', lambda session, now, times: '11:15')
    sent = []
    assert nva.clerk_once(db, sender=lambda *a: sent.append(a)) == 1
    assert sent[0][1].chat_id == 'z-thanh' and 'DB-26-00001' in sent[0][2] and '/viec-cua-toi' in sent[0][2]
    assert nva.clerk_once(db, sender=lambda *a: sent.append(a)) == 0
    assert db.scalar(select(func.count()).select_from(DispatchZaloSent)) == 1


def test_clerk_slot_window(db):
    from datetime import datetime
    now = datetime(2026, 11, 9, 11, 20, tzinfo=nva.TZ)   # thứ Hai
    assert nva.clerk_slot(db, now, ['11:15', '17:15']) == '11:15'
    assert nva.clerk_slot(db, now.replace(hour=10), ['11:15']) is None
    assert nva.clerk_slot(db, datetime(2026, 11, 8, 11, 20, tzinfo=nva.TZ), ['11:15']) is None   # Chủ nhật


def test_page_director_only_and_save(db, people, client):
    p = people
    service_login(client, p['dispatcher'])
    assert client.get('/nhan-vien-ao').status_code == 403
    service_login(client, p['director'])
    page = client.get('/nhan-vien-ao').text
    assert 'Điều phối AI' in page and 'Nhắc hẹn AI' in page and 'Thư ký phiếu' in page and 'Dừng tất cả' in page
    form = page[page.index('action="/nhan-vien-ao/DIEU_PHOI"'):]
    key = re.search(r'name="form_key" value="([^"]+)"', form).group(1)
    res = client.post('/nhan-vien-ao/DIEU_PHOI', data=dict(csrf=csrf_of(page), form_key=key, cai_dat='1', bat='1', mode='CHAY_THU',
        kinds=['MUC_DINH_KY', 'BAO_HANH'], priorities=['THUONG', 'KHAN'], wait_minutes='20', undo_minutes='15', max_per_day='0', max_per_tech='3'),
        follow_redirects=False)
    assert res.status_code == 303
    db.expire_all()
    row = db.get(VirtualAgent, 'DIEU_PHOI')
    assert row.mode == 'CHAY_THU' and row.config['kinds'] == ['MUC_DINH_KY'] and row.config['priorities'] == ['THUONG']
    assert row.config['undo_minutes'] == 15 and row.config['max_per_tech'] == 3
    assert db.scalar(select(VirtualAgentLog).where(VirtualAgentLog.action == 'CAI_DAT')).target_id == p['director'].id
    page = client.get('/nhan-vien-ao').text
    form = page[page.index('action="/nhan-vien-ao/THU_KY"'):]
    key = re.search(r'name="form_key" value="([^"]+)"', form).group(1)
    res = client.post('/nhan-vien-ao/THU_KY', data=dict(csrf=csrf_of(page), form_key=key, cai_dat='1', bat='1', times='25:00'))
    assert res.status_code == 400 and 'chưa đúng' in res.text
    page = client.get('/nhan-vien-ao').text
    form = page[page.index('action="/nhan-vien-ao/TAT_CA"'):]
    key = re.search(r'name="form_key" value="([^"]+)"', form).group(1)
    client.post('/nhan-vien-ao/TAT_CA', data=dict(csrf=csrf_of(page), form_key=key, bat='0'))
    db.expire_all()
    assert db.get(VirtualAgent, 'TAT_CA').enabled is False
    assert 'Đã tắt tất cả' in client.get('/nhan-vien-ao').text


def test_system_account_hidden_from_account_list(db, people, client, make_user):
    nva.system_user(db)
    admin = make_user('nva.admin', 'admin')
    service_login(client, admin)
    page = client.get('/tai-khoan').text
    assert 'Nva.Admin' in page and nva.SYSTEM_USERNAME not in page and 'Nhân viên AI' not in page
