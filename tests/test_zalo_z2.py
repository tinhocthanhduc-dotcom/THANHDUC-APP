"""Trợ lý Zalo trả lời nhân viên bằng Codex/Claude trên máy chủ Z2 (TUẤN 05/11/2026), khách vẫn qua API."""
import json

import pytest
from sqlalchemy import select

from app import ai_agent, zalo_agent as agent
from app.config import get_settings
from app.models import AiKey, SalesOrder, ZaloAiJob, ZaloTroLy
from test_web import service_login
from test_zalo_tin_nhan import H, event
from test_zalo_tro_ly import FK, call, last_log, linked, say, shop, texts, zalo  # noqa: F401


def _alive(db, shop, paused=False):
    raw = ai_agent.create_key(db, shop['director'], agent.Z2_AGENT)
    key = db.scalars(select(AiKey).where(AiKey.agent == agent.Z2_AGENT, AiKey.revoked_at.is_(None))).one()
    key.last_used_at, key.paused = agent._now(), paused
    db.commit()
    return raw


def _engine(db, engine):
    db.get(ZaloTroLy, 1).staff_engine = engine
    db.commit()


@pytest.fixture
def z2(monkeypatch):
    """Giả máy Z2: mỗi lượt lấy câu lệnh rồi trả chữ theo kịch bản, qua đúng hàm app dùng cho cửa /ai/zalo."""
    state = dict(script=[], prompts=[], engines=[])
    real_wait = agent._wait_job

    def fake_wait(session, job_id, wait):
        jobs = agent.take_jobs(session, 5)
        for j in jobs:
            state['prompts'].append(j['lenh'])
            state['engines'].append(j['may'])
            step = state['script'].pop(0)
            if isinstance(step, Exception):
                agent.fail_job(session, j['id'], str(step))
            elif step is not None:
                agent.answer_job(session, j['id'], step)
        return real_wait(session, job_id, 1)
    monkeypatch.setattr(agent, '_wait_job', fake_wait)
    return state


def test_codex_tren_z2_tra_loi_nhan_vien_khong_ton_api(client, db, shop, zalo, z2):
    _alive(db, shop)
    _engine(db, 'codex')
    linked(client, db, user=shop['sales'])
    z2['script'] = ['```json\n{"goi": [{"ten": "tra_ton", "vao": {"tu_khoa": "12A"}}]}\n```',
                    '{"tra_loi": "TD-0142 HP 12A: Kho chính 8, giá 820.000."}']
    client.post('/zalo/webhook', json=event(mid='m1', text='còn 12A không, giá bao nhiêu'), headers=H)
    assert zalo['ai'] == []  # không gọi API OpenAI
    assert z2['engines'] == ['codex', 'codex']
    assert 'còn 12A không' in z2['prompts'][0] and '"ten": "tra_ton"' in z2['prompts'][0] and 'tao_don_nhap' in z2['prompts'][0]
    second = z2['prompts'][1]
    assert '820000' in second and '600000' not in second  # dữ liệu đã lọc theo quyền, không có giá vốn
    assert texts(zalo)[-2:] == [agent.Z2_WAIT_TEXT, 'TD-0142 HP 12A: Kho chính 8, giá 820.000.']
    log = last_log(db)
    assert (log.result, log.engine, log.cost_vnd, log.tokens_in) == ('TRA_LOI', 'codex', 0, 0)
    assert log.tools[0]['ten'] == 'tra_ton'
    assert {j.status for j in db.scalars(select(ZaloAiJob))} == {'XONG'}


def test_claude_tren_z2_len_don_nhap_cho_kinh_doanh(client, db, shop, zalo, z2):
    _alive(db, shop)
    _engine(db, 'claude')
    linked(client, db, user=shop['sales'])
    z2['script'] = [json.dumps(dict(goi=[dict(ten='tao_don_nhap', vao=dict(ma_khach='KTD-000001', dong=[dict(ma_hang='TD-0142', so_luong=2, can_chon_ma='')],
                                                                        hen_giao='', giao_tan_noi=False, ghi_chu=''))])),
                    'Đã lên đơn nháp cho An Phú.']  # không đúng khuôn JSON: coi cả chữ là câu trả lời
    client.post('/zalo/webhook', json=event(mid='m1', text='lên đơn An Phú 2 hộp 12A'), headers=H)
    order = db.scalars(select(SalesOrder)).one()
    assert (order.status, order.created_by) == ('NHAP', shop['sales'].id)
    assert texts(zalo)[-1].startswith('Đã lên đơn nháp cho An Phú.') and order.number in texts(zalo)[-1]
    assert (last_log(db).result, last_log(db).engine) == ('DON_NHAP', 'claude') and z2['engines'] == ['claude', 'claude']


def test_khach_luon_qua_api_du_chon_z2(client, db, shop, zalo, z2):
    _alive(db, shop)
    _engine(db, 'codex')
    linked(client, db, customer='KTD-000001')
    zalo['script'] = [say('KHONG')]
    client.post('/zalo/webhook', json=event(mid='m1', text='máy in kẹt giấy'), headers=H)
    assert len(zalo['ai']) == 1 and z2['prompts'] == []
    assert (last_log(db).result, last_log(db).engine) == ('BO_QUA', 'api')


def test_z2_khong_chay_thi_dung_api_neu_co_khoa(client, db, shop, zalo, z2):
    _engine(db, 'codex')  # chưa có khóa Z2 = máy Z2 chưa chạy
    linked(client, db, user=shop['technician'])
    zalo['script'] = [say('Còn 8 hộp.')]
    client.post('/zalo/webhook', json=event(mid='m1', text='còn 12A'), headers=H)
    assert z2['prompts'] == [] and texts(zalo)[-1] == 'Còn 8 hộp.' and last_log(db).engine == 'api'


def test_z2_tam_dung_va_khong_co_khoa_api_thi_bao_loi(client, db, shop, zalo, z2, monkeypatch):
    monkeypatch.setattr(get_settings(), 'openai_api_key', '')
    _alive(db, shop, paused=True)
    _engine(db, 'claude')
    linked(client, db, user=shop['technician'])
    client.post('/zalo/webhook', json=event(mid='m1', text='còn 12A'), headers=H)
    log = last_log(db)
    assert log.result == 'LOI' and 'Claude' in log.error and texts(zalo)[-1] == agent.FAIL_TEXT and zalo['ai'] == []


def test_z2_bao_khong_lam_duoc_hoac_qua_gio(client, db, shop, zalo, z2):
    _alive(db, shop)
    _engine(db, 'codex')
    linked(client, db, user=shop['technician'])
    z2['script'] = [RuntimeError('codex hết lượt')]
    client.post('/zalo/webhook', json=event(mid='m1', text='còn 12A'), headers=H)
    assert last_log(db).result == 'LOI' and 'hết lượt' in last_log(db).error and texts(zalo)[-1] == agent.FAIL_TEXT
    z2['script'] = [None]  # máy Z2 lấy lượt nhưng không trả lời
    client.post('/zalo/webhook', json=event(mid='m2', text='còn 12A'), headers=H)
    assert 'không trả lời sau' in last_log(db).error
    db.expire_all()
    late = db.scalars(select(ZaloAiJob).order_by(ZaloAiJob.id.desc())).first()
    assert late.status == 'LOI'
    with pytest.raises(Exception) as exc:  # trả muộn bị từ chối
        agent.answer_job(db, late.id, '{"tra_loi": "muộn"}')
    assert getattr(exc.value, 'status', None) == 409


def test_cua_ai_zalo_chi_khoa_dung_pham_vi(client, db, shop, zalo):
    raw = ai_agent.create_key(db, shop['director'], agent.Z2_AGENT)
    web = ai_agent.create_key(db, shop['director'], 'dang-web')
    linked(client, db, user=shop['technician'])  # tin đầu tạo một dòng nhật ký
    log_row = last_log(db)
    db.add(ZaloAiJob(log_id=log_row.id, engine='claude', prompt='câu lệnh'))
    db.commit()
    assert client.get('/ai/zalo/viec', headers={'Authorization': 'Bearer ' + web}).status_code == 403
    assert client.get('/ai/zalo/viec', headers={'Authorization': 'Bearer ' + raw, 'cf-ray': 'x'}).status_code == 403
    jobs = client.get('/ai/zalo/viec', headers={'Authorization': 'Bearer ' + raw}).json()
    assert [(j['may'], j['lenh']) for j in jobs] == [('claude', 'câu lệnh')]
    assert client.get('/ai/zalo/viec', headers={'Authorization': 'Bearer ' + raw}).json() == []  # đã lấy rồi
    ok = client.post(f"/ai/zalo/viec/{jobs[0]['id']}/tra-loi", json={'tra_loi': '{"tra_loi": "ok"}'}, headers={'Authorization': 'Bearer ' + raw})
    assert ok.status_code == 200
    again = client.post(f"/ai/zalo/viec/{jobs[0]['id']}/khong-lam-duoc", json={'ly_do': 'x'}, headers={'Authorization': 'Bearer ' + raw})
    assert again.status_code == 409
    db.expire_all()
    assert agent.z2_alive(db)


def test_z2_parse_va_prompt():
    assert agent.z2_parse('Trả lời thường')['choices'][0]['message']['content'] == 'Trả lời thường'
    got = agent.z2_parse('Đây: {"goi": [{"ten": "tra_ton", "vao": {"tu_khoa": "12A"}}, {"vao": {}}]}', 3)
    calls = got['choices'][0]['message']['tool_calls']
    assert len(calls) == 1 and calls[0]['function']['name'] == 'tra_ton' and calls[0]['id'] == 'z3-0'
    assert agent.z2_parse('{"tra_loi": "Còn 8"}')['choices'][0]['message']['content'] == 'Còn 8'
    msgs = [dict(role='system', content='Luật'), dict(role='user', content='hỏi'),
            dict(role='assistant', content=None, tool_calls=calls), dict(role='tool', tool_call_id='z3-0', content='{"ket_qua": []}')]
    text = agent.z2_prompt(msgs, [agent.T_STOCK])
    assert text.startswith('Luật') and '"ban_da_goi"' in text and '"ket_qua": "tra_ton"' in text and 'KHÔNG chạy lệnh' in text


def test_giam_doc_chon_may_tra_loi_va_thay_tinh_trang(client, db, shop, zalo):
    csrf = service_login(client, shop['director'])
    html = client.get('/zalo/tro-ly').text
    assert 'Ai trả lời nhân viên' in html and 'Codex Z2' in html and 'chưa có khóa' in html
    r = client.post('/zalo/tro-ly/cau-hinh', data={'csrf': csrf, 'form_key': FK.format(1), 'bat': '1', 'nguoi_nhan': shop['sales'].id, 'may': 'claude'},
                    follow_redirects=True)
    assert r.status_code == 200
    db.expire_all()
    assert db.get(ZaloTroLy, 1).staff_engine == 'claude'
    assert client.post('/zalo/tro-ly/cau-hinh', data={'csrf': csrf, 'form_key': FK.format(2), 'bat': '1', 'may': 'gpt'}).status_code == 422
    _alive(db, shop)
    assert 'đang chạy' in client.get('/zalo/tro-ly').text
