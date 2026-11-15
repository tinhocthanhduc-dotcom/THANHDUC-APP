"""Đội AI đợt 3 (TUẤN duyệt 15/11/2026): giám đốc chat Zalo với Trưởng nhóm AI (Q8 làm ngay + hoàn tác, Q9 chỉ GĐ, Q10 tự báo)."""
import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import nhan_vien_ao as nva, service_tickets, truong_nhom as tn, truong_nhom_chat as tnc
from app.models import Customer, ServiceTicket, TeamTask, VirtualAgentLog
from app.schemas import ServiceTicketIn
from test_doi_ai import MON, zalo_on  # noqa: F401
from test_nhan_vien_ao import link_zalo
from test_web import csrf_of, service_login


@pytest.fixture
def crew(db, make_user):
    db.add(Customer(code='KTD-000001', name='Minh Phát', search_text='minh phat', address='58 Mã Lò'))
    db.commit()
    tn._last['sync'] = None
    p = dict(gd=make_user('tc.gd', 'director'), dp=make_user('tc.dp', 'dispatcher'),
             phong=make_user('tc.phong', 'technician'), thanh_kt=make_user('tc.thanhkt', 'technician'), thanh=make_user('tc.thanh', 'sales'))
    p['phong'].full_name, p['thanh_kt'].full_name, p['thanh'].full_name = 'Đoàn Cảnh Phong', 'Nguyễn Bình Thạnh', 'Nguyễn Thị Thanh'
    p['gd'].full_name = 'Bùi Minh Tuấn'
    db.commit()
    return p


def ticket(db, p, tech=None):
    t = service_tickets.create(db, p['dp'], ServiceTicketIn(customer_code='KTD-000001', kind='SUA_TAI_CHO', symptom='Kẹt giấy', priority='THUONG'))
    if tech is not None:
        t = service_tickets.assign(db, p['dp'], t.id, tech.id, t.version)
    return t


def test_enabled_only_director_on_z2(db, crew):
    assert tnc.enabled(db, crew['gd'], 'claude') and tnc.enabled(db, crew['gd'], 'codex')
    assert not tnc.enabled(db, crew['gd'], 'api')                      # Q7: không dùng API
    assert not tnc.enabled(db, crew['dp'], 'claude')                    # Q9: chỉ giám đốc
    row = nva.rows(db)['TRUONG_NHOM']
    row.config = dict(nva.cfg(row), chat=False)
    db.commit()
    assert not tnc.enabled(db, crew['gd'], 'claude')


def test_find_user_keeps_accents(db, crew):
    assert tnc.find_user(db, 'Thạnh')[0].id == crew['thanh_kt'].id
    assert tnc.find_user(db, 'thanh')[0].id == crew['thanh'].id
    assert tnc.find_user(db, 'phong', roles=('technician',))[0].id == crew['phong'].id
    assert tnc.find_user(db, 'Thanh', roles=('technician',))[0].id == crew['thanh_kt'].id   # chỉ thợ: không dấu vẫn ra
    assert tnc.find_user(db, 'Tuấn')[0] is None                                              # không ra lệnh cho giám đốc
    assert tnc.find_user(db, 'Nguyễn')[1].startswith('Có nhiều người khớp')


def test_assign_reassign_and_undo_by_chat(db, crew, zalo_on):
    p = crew
    t = ticket(db, p, p['phong'])
    short = re.sub(r'\D', '', t.number)[-4:]
    link_zalo(db, p['phong'], 'z-phong')
    sent = []
    out = tnc.tool_assign(db, p['gd'], short, 'Thạnh', 'chuyển phiếu đó qua Thạnh', sender=lambda *a: sent.append(a))
    assert out['da_giao'] == t.number and out['cho'] == 'Nguyễn Bình Thạnh' and out['tho_cu'] == 'Đoàn Cảnh Phong' and 'hoàn tác' in out['hoan_tac']
    db.expire_all()
    assert db.get(ServiceTicket, t.id).assignee_id == p['thanh_kt'].id
    entry = db.scalar(select(VirtualAgentLog).where(VirtualAgentLog.action == 'DOI_NGUOI'))
    assert 'Giám đốc nhắn Zalo' in entry.reason and entry.target_id == p['thanh_kt'].id
    assert any(s[1].chat_id == 'z-phong' for s in sent)                 # thợ cũ được báo
    assert 'đang giao' in tnc.tool_assign(db, p['gd'], t.number, 'Thạnh', 'x')['loi']
    back = tnc.tool_undo(db, p['gd'])
    assert back == dict(da_hoan_tac=t.number, ve='Đoàn Cảnh Phong')
    db.expire_all()
    assert db.get(ServiceTicket, t.id).assignee_id == p['phong'].id
    assert 'Không có lần đổi thợ' in tnc.tool_undo(db, p['gd'])['loi']
    fresh = ticket(db, p)
    out = tnc.tool_assign(db, p['gd'], fresh.number, 'Phong', 'giao phiếu mới cho Phong')
    db.expire_all()
    assert out['tho_cu'] == '' and db.get(ServiceTicket, fresh.id).assignee_id == p['phong'].id
    assert 'Không thấy' in tnc.tool_assign(db, p['gd'], '999999', 'Phong', 'x')['loi']
    assert 'là thợ' in tnc.tool_assign(db, p['gd'], fresh.number, 'Thị Thanh', 'x')['loi']   # nhân viên bán hàng không nhận phiếu


def test_assign_refuses_ticket_holding_stock(db, crew, monkeypatch):
    t = ticket(db, crew, crew['phong'])
    monkeypatch.setattr(tn, '_has_stock', lambda session, ticket: True)
    out = tnc.tool_assign(db, crew['gd'], t.number, 'Thạnh', 'x')
    assert 'cầm hàng' in out['loi'] and out['link'].endswith(f'/dich-vu/{t.id}')
    db.expire_all()
    assert db.get(ServiceTicket, t.id).assignee_id == crew['phong'].id


def test_remind_task_survives_sync_escalates_and_done(db, crew, zalo_on, client, monkeypatch):
    p = crew
    link_zalo(db, p['thanh'], 'z-thanh')
    link_zalo(db, p['gd'], 'z-gd')
    sent = []
    now = MON
    out = tnc.tool_remind(db, p['gd'], 'thanh', 'Gọi lại chị Hạnh Minh Phát', '2026-11-16 15:00', sender=lambda *a: sent.append(a), now=now)
    assert out['da_nhac'] == 'Nguyễn Thị Thanh' and out['han'] == '15:00 hôm nay' and out['da_nhan_zalo']
    assert sent[0][1].chat_id == 'z-thanh' and 'Giám đốc nhờ bạn: Gọi lại chị Hạnh' in sent[0][2]
    t = db.scalar(select(TeamTask).where(TeamTask.item_key == tn.GD_NHAC))
    from app import my_work
    monkeypatch.setattr(my_work, 'collect', lambda session, user, today=None, keys=None: [])
    tn.sync(db, now + timedelta(minutes=5))
    db.expire_all()
    t = db.get(TeamTask, t.id)
    assert t.done_at is None and t.due_at == t.due_override                 # gom lại không đóng, không đổi hạn
    assert 'Hạn đã qua' in tnc.tool_remind(db, p['gd'], 'thanh', 'x', '2026-11-16 08:00', now=now)['loi']
    late = now + timedelta(hours=6, minutes=5)
    sent.clear()
    tn.escalate(db, late, lambda *a: sent.append(a))
    to = {s[1].chat_id: s[2] for s in sent}
    assert 'quá hạn' in to['z-thanh'] and 'báo giám đốc' in to['z-gd'] and 'Gọi lại chị Hạnh' in to['z-gd']
    monkeypatch.undo()
    service_login(client, p['thanh'])
    page = client.get('/viec-cua-toi').text
    assert 'Giám đốc nhờ' in page and 'Gọi lại chị Hạnh' in page
    form = page[page.index(f'/viec-cua-toi/giam-doc/{t.id}/xong'):]
    key = re.search(r'name="form_key" value="([^"]+)"', form).group(1)
    monkeypatch.setattr(nva, '_sender', lambda sender: lambda *a: sent.append(a))
    monkeypatch.setattr(nva, '_zalo_ready', lambda: True)
    sent.clear()
    res = client.post(f'/viec-cua-toi/giam-doc/{t.id}/xong', data=dict(csrf=csrf_of(page), form_key=key), follow_redirects=False)
    assert res.status_code == 303
    db.expire_all()
    assert db.get(TeamTask, t.id).done_at is not None
    assert sent and sent[0][1].chat_id == 'z-gd' and 'đã xong việc anh nhắc' in sent[0][2]


def test_finish_only_owner(db, crew):
    out = tnc.tool_remind(db, crew['gd'], 'phong', 'Mang máy Sao Mai về', '', now=tn._now())
    t = db.scalar(select(TeamTask).where(TeamTask.item_key == tn.GD_NHAC))
    assert out['da_nhac'] == 'Đoàn Cảnh Phong' and not out['da_nhan_zalo']
    with pytest.raises(Exception):
        tnc.finish(db, crew['thanh'], t.id)
    tnc.finish(db, crew['phong'], t.id)
    assert db.get(TeamTask, t.id).done_at is not None


def test_move_due_is_kept_by_sync(db, crew, monkeypatch):
    from app import my_work
    rows = [dict(id=7, number='BG-0119', customer='Sao Mai', link='/bao-gia/7')]
    monkeypatch.setattr(my_work, 'collect', lambda session, user, today=None, keys=None:
                        [dict(key='orders_draft', title='Đơn nháp chưa chốt', count=1, rows=rows)] if user.id == crew['thanh'].id else [])
    tn.sync(db, MON)
    out = tnc.tool_due(db, crew['gd'], '0119', '2026-11-17 09:00', 'khách đi vắng', now=MON)
    assert out['da_doi'] == 'BG-0119' and out['han_moi'] == '09:00 17/11' and out['nguoi'] == 'Nguyễn Thị Thanh'
    tn.sync(db, MON + timedelta(minutes=10))
    t = db.scalar(select(TeamTask).where(TeamTask.number == 'BG-0119'))
    assert t.due_at.astimezone(tn.TZ).strftime('%d/%m %H:%M') == '17/11 09:00' and 'khách đi vắng' in t.note
    assert 'Không thấy' in tnc.tool_due(db, crew['gd'], 'XYZ', '2026-11-17 09:00', '', now=MON)['loi']


def test_status_lists_people_and_late(db, crew, monkeypatch):
    for i in range(3):
        db.add(TeamTask(item_key='orders_draft', ref=f'r{i}', number=f'X-{i}', title='Đơn nháp', owner_id=crew['phong'].id,
                        due_at=MON - timedelta(hours=1) if i == 0 else MON + timedelta(hours=2), first_seen_at=MON, last_seen_at=MON))
    db.commit()
    tn._last['sync'] = MON
    out = tnc.tool_status(db, '', now=MON)
    assert out['moi_nguoi'][0] == dict(nguoi='Đoàn Cảnh Phong', so_viec=3, tre=1) and out['tong_tre'] == 1
    assert out['viec_tre'][0]['so'] == 'X-0' and out['viec_tre'][0]['han'].startswith('Trễ') and 'tin_khach_chua_tra_loi' in out
    one = tnc.tool_status(db, 'Phong', now=MON)
    assert len(one['viec_cua_nguoi']) == 3
    assert 'Không thấy' in tnc.tool_status(db, 'Ai đó', now=MON)['loi']


def test_overload_alert_and_summary(db, crew, zalo_on):
    link_zalo(db, crew['gd'], 'z-gd')
    for i in range(9):
        db.add(TeamTask(item_key='orders_draft', ref=f'r{i}', number=f'X-{i}', title='x', owner_id=crew['phong'].id,
                        due_at=MON - timedelta(minutes=5), first_seen_at=MON, last_seen_at=MON))
    db.commit()
    sent = []
    assert tn.overload_alert(db, MON, lambda *a: sent.append(a)) == 1
    assert 'Đoàn Cảnh Phong đang 9 việc' in sent[0][2] and sent[0][1].chat_id == 'z-gd'
    assert tn.overload_alert(db, MON, lambda *a: sent.append(a)) == 0      # mỗi người mỗi ngày một lần
    text = tn.summary_text(db, MON, 'https://x')
    assert 'còn 9 việc trễ (Đoàn Cảnh Phong 9)' in text and 'Tin khách chưa trả lời: 0' in text
    evening = MON.replace(hour=17, minute=20)
    assert tn.summary_once(db, evening, lambda *a: sent.append(a)) == 1
    assert tn.summary_once(db, evening, lambda *a: sent.append(a)) == 0
    assert tn.summary_once(db, MON, lambda *a: sent.append(a)) == 0         # chưa tới 17:15


def test_director_chat_on_z2_gets_team_tools_and_history(client, db, monkeypatch):
    from test_zalo_tin_nhan import H, event
    from test_zalo_tro_ly import SECRET, TOKEN, linked
    from test_zalo_z2 import _alive, _engine
    from app import zalo_agent as agent, zalo_bot
    from app.config import get_settings
    from app.models import User, ZaloTroLy
    from app.security import hash_password
    cfg = get_settings()
    monkeypatch.setattr(cfg, 'zalo_webhook_secret', SECRET)
    monkeypatch.setattr(cfg, 'zalo_bot_token', TOKEN)
    state = dict(sent=[])
    monkeypatch.setattr(zalo_bot, 'call', lambda method, payload=None, timeout=15: state['sent'].append((method, payload)) or dict(message_id=f"o{len(state['sent'])}"))
    gd = User(username='tc2.gd', full_name='Bùi Minh Tuấn', password_hash=hash_password('x' * 12), roles=['director'])
    tho = User(username='tc2.tho', full_name='Đoàn Cảnh Phong', password_hash=hash_password('x' * 12), roles=['technician'])
    db.add_all([gd, tho, ZaloTroLy(id=1, enabled=True)])
    db.commit()
    _alive(db, dict(director=gd))
    _engine(db, 'claude')
    prompts = []
    script = ['{"goi": [{"ten": "tinh_hinh_doi", "vao": {"nguoi": ""}}]}', '{"tra_loi": "Dạ anh, chưa có việc trễ."}',
              '{"tra_loi": "Dạ."}']
    real_wait = agent._wait_job

    def fake_wait(session, job_id, wait):
        for j in agent.take_jobs(session, 5):
            prompts.append(j['lenh'])
            agent.answer_job(session, j['id'], script.pop(0))
        return real_wait(session, job_id, 1)
    monkeypatch.setattr(agent, '_wait_job', fake_wait)
    linked(client, db, user=gd)
    client.post('/zalo/webhook', json=event(mid='m1', text='hôm nay ai trễ việc gì'), headers=H)
    assert '"ten": "tinh_hinh_doi"' in prompts[0] and '"ten": "giao_phieu"' in prompts[0] and 'Trưởng nhóm AI' in prompts[0]
    assert '"moi_nguoi"' in prompts[1]
    client.post('/zalo/webhook', json=event(mid='m2', text='ừ cảm ơn'), headers=H)
    assert len(prompts) == 3
    convo = prompts[2][prompts[2].index('Đoạn chat trước'):][:600]
    assert 'hôm nay ai trễ việc gì' in convo, convo
    assert 'chưa có việc trễ' in convo, convo
    assert agent.Z2_WAIT_TEXT not in prompts[2].split('Tin mới')[0]
    assert any('chưa có việc trễ' in t for t in [p['text'] for m, p in state['sent'] if m == 'sendMessage'])
