"""Trợ lý AI (TUẤN 01/11/2026): khóa riêng, chỉ xem, nhắc việc có giới hạn, đề xuất chờ giám đốc duyệt."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import ai_agent as svc, notifications, service_tickets
from app.errors import BusinessError
from app.models import AiAction, AuditLog, ServiceTicket
from test_service_tickets import people, _new  # noqa: F401
from test_web import service_login


@pytest.fixture
def ai(db, people, make_user):
    director = make_user('giam.doc', 'director')
    raw = svc.create_key(db, director)
    return dict(people, director=director, raw=raw, h={'Authorization': 'Bearer ' + raw})


def test_khoa_chan_dung_cach(client, db, ai):
    assert client.get('/ai/tong-quan').status_code == 401
    assert client.get('/ai/tong-quan', headers={'Authorization': 'Bearer tdai_sai'}).status_code == 401
    # Đi qua Cloudflare (Internet) thì khóa đúng cũng bị chặn: chỉ dùng trong máy chủ.
    assert client.get('/ai/tong-quan', headers={**ai['h'], 'cf-connecting-ip': '1.2.3.4'}).status_code == 403
    assert client.get('/ai/tong-quan', headers=ai['h']).status_code == 200
    svc.set_paused(db, ai['director'], True)
    r = client.get('/ai/tong-quan', headers=ai['h'])
    assert r.status_code == 423 and r.json()['error'] == 'AI_PAUSED'
    svc.set_paused(db, ai['director'], False)
    new = svc.create_key(db, ai['director'])
    assert client.get('/ai/tong-quan', headers=ai['h']).status_code == 401
    assert client.get('/ai/tong-quan', headers={'Authorization': 'Bearer ' + new}).status_code == 200
    svc.revoke_key(db, ai['director'])
    assert client.get('/ai/tong-quan', headers={'Authorization': 'Bearer ' + new}).status_code == 401
    with pytest.raises(BusinessError):
        svc.create_key(db, ai['dp'])
    with pytest.raises(BusinessError):  # trợ lý không có trong danh sách thì không tạo khóa được
        svc.create_key(db, ai['director'], 'khong-co')


def test_khoa_sai_pham_vi_bi_chan(client, db, ai):
    from app.models import AiKey
    key = svc.active_key(db)
    key.scopes = ['dang_web']
    db.commit()
    r = client.get('/ai/tong-quan', headers=ai['h'])
    assert r.status_code == 403 and r.json()['error'] == 'AI_SCOPE'


def test_tong_quan_khong_co_tien(client, db, ai):
    t = _new(db, ai)
    service_tickets.assign(db, ai['dp'], t.id, ai['t1'].id, t.version)
    data = client.get('/ai/tong-quan', headers=ai['h']).json()
    assert [x['so'] for x in data['phieu_dich_vu']] == [t.number]
    assert data['phieu_dich_vu'][0]['tho'] == ai['t1'].full_name
    tho = next(p for p in data['nguoi'] if p['id'] == ai['t1'].id)
    assert tho['phieu_dang_mo'] == 1 and tho['viec']
    text = json.dumps(data)
    for word in ('amount', 'total', 'cost', 'gia_von', 'luong', 'password'):
        assert word not in text


def test_nhac_viec_toi_da_3_lan_hien_o_chuong(client, db, ai):
    t = _new(db, ai)
    for i in range(3):
        r = client.post('/ai/nhac-viec', headers=ai['h'], json=dict(nguoi_id=ai['t1'].id, noi_dung=f'Nhắc {i}', phieu=t.number))
        assert r.status_code == 201, r.text
    r = client.post('/ai/nhac-viec', headers=ai['h'], json=dict(nguoi_id=ai['t1'].id, noi_dung='Lần 4'))
    assert r.status_code == 429 and r.json()['error'] == 'AI_REMIND_LIMIT'
    items = [i for i in notifications.items(db, ai['t1']) if i['id'].startswith('ai_remind:')]
    assert len(items) == 3 and items[0]['title'] == 'Nhắc từ Trợ lý AI' and items[0]['link'] == f'/tho/phieu/{t.id}'
    assert not [i for i in notifications.items(db, ai['t2']) if i['id'].startswith('ai_remind:')]
    logs = db.scalars(select(AuditLog).where(AuditLog.action == 'ai.remind')).all()
    assert len(logs) == 3 and all(l.user_id is None for l in logs)


def test_de_xuat_giao_viec_chi_chay_khi_giam_doc_duyet(client, db, ai):
    t = _new(db, ai)
    r = client.post('/ai/de-xuat', headers=ai['h'], json=dict(loai='GIAO_VIEC', phieu=t.number, tho_id=ai['t2'].id, ly_do='Bình đang rảnh'))
    assert r.status_code == 201, r.text
    action_id = r.json()['id']
    db.expire_all()
    assert db.get(ServiceTicket, t.id).assignee_id is None  # chưa duyệt thì chưa giao
    dup = client.post('/ai/de-xuat', headers=ai['h'], json=dict(loai='GIAO_VIEC', phieu=t.number, tho_id=ai['t1'].id, ly_do='x'))
    assert dup.status_code == 409
    with pytest.raises(BusinessError) as e:
        svc.approve(db, ai['dp'], action_id)
    assert e.value.status == 403
    row = svc.approve(db, ai['director'], action_id)
    assert row.status == 'DA_DUYET' and 'Đã giao' in row.result
    db.expire_all()
    ticket = db.get(ServiceTicket, t.id)
    assert ticket.assignee_id == ai['t2'].id and ticket.status == 'DA_GIAO'
    step = db.scalar(select(AuditLog).where(AuditLog.action == 'service_ticket.assign'))
    assert step.user_id == ai['director'].id
    with pytest.raises(BusinessError) as e:
        svc.approve(db, ai['director'], action_id)
    assert e.value.code == 'ALREADY_DECIDED'


def test_phieu_doi_sau_de_xuat_thi_khong_chay(client, db, ai):
    t = _new(db, ai)
    r = client.post('/ai/de-xuat', headers=ai['h'], json=dict(loai='GIAO_VIEC', phieu=t.number, tho_id=ai['t2'].id, ly_do='x'))
    service_tickets.assign(db, ai['dp'], t.id, ai['t1'].id, t.version)
    row = svc.approve(db, ai['director'], r.json()['id'])
    assert row.status == 'LOI' and 'thay đổi' in row.result
    db.expire_all()
    assert db.get(ServiceTicket, t.id).assignee_id == ai['t1'].id


def test_tu_choi_va_het_han(client, db, ai):
    t = _new(db, ai)
    r = client.post('/ai/de-xuat', headers=ai['h'], json=dict(loai='GIAO_VIEC', phieu=t.number, tho_id=ai['t2'].id, ly_do='x'))
    row = svc.reject(db, ai['director'], r.json()['id'], 'Khách quen của An')
    assert row.status == 'TU_CHOI'
    back = client.get('/ai/de-xuat', headers=ai['h']).json()
    assert back[0]['ly_do_tu_choi'] == 'Khách quen của An'
    r = client.post('/ai/de-xuat', headers=ai['h'], json=dict(loai='GIAO_VIEC', phieu=t.number, tho_id=ai['t2'].id, ly_do='y'))
    row = db.get(AiAction, r.json()['id'])
    row.expires_at = svc._now() - timedelta(minutes=1)
    db.commit()
    with pytest.raises(BusinessError):
        svc.approve(db, ai['director'], row.id)
    db.expire_all()
    assert db.get(AiAction, row.id).status == 'HET_HAN'


def test_de_xuat_nhap_hang_bao_thu_kho_khong_dung_kho(client, db, ai):
    r = client.post('/ai/de-xuat', headers=ai['h'], json=dict(loai='DE_XUAT_NHAP', ma_hang='TD-0001', so_luong=5, ly_do='Sắp hết'))
    assert r.status_code == 201, r.text
    svc.approve(db, ai['director'], r.json()['id'])
    items = [i for i in notifications.items(db, ai['kho']) if i['id'].startswith('ai_restock:')]
    assert len(items) == 1 and 'TD-0001' in items[0]['text']
    bad = client.post('/ai/de-xuat', headers=ai['h'], json=dict(loai='THU_TIEN', ly_do='x'))
    assert bad.status_code == 400


def test_man_tro_ly_ai_chi_giam_doc(client, db, ai):
    t = _new(db, ai)
    client.post('/ai/de-xuat', headers=ai['h'], json=dict(loai='GIAO_VIEC', phieu=t.number, tho_id=ai['t2'].id, ly_do='Bình đang rảnh'))
    service_login(client, ai['dp'])
    assert client.get('/tro-ly-ai').status_code == 403
    assert 'data-ai-notify' not in client.get('/dich-vu').text
    csrf = service_login(client, ai['director'])
    page = client.get('/tro-ly-ai')
    assert page.status_code == 200 and 'Bình đang rảnh' in page.text and 'Duyệt' in page.text
    assert 'data-ai-notify' in page.text
    brief = client.get('/tro-ly-ai/cho-duyet.json').json()
    assert brief['count'] == 1 and brief['items'][0]['form_key']
    action_id = brief['items'][0]['id']
    r = client.post(f'/tro-ly-ai/{action_id}/duyet', data={'csrf': csrf, 'next': '/tong-quan'}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/tong-quan'
    db.expire_all()
    assert db.get(ServiceTicket, t.id).assignee_id == ai['t2'].id
    assert client.get('/tro-ly-ai?tab=nhat-ky').status_code == 200
    r = client.post('/tro-ly-ai/khoa/tao', data={'csrf': csrf, 'tro_ly': 'dieu-phoi'}, follow_redirects=True)
    import re
    shown = re.search(r'id="ai-key-value">(tdai_[^<]+)<', r.text).group(1)
    assert 'chỉ hiện một lần' in r.text and svc.active_key(db).key_hash == svc._hash(shown)
    assert shown not in client.get('/tro-ly-ai?khoa_moi=1').text
