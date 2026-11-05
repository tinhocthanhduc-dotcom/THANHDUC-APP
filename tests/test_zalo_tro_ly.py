"""Trợ lý Zalo (TUẤN duyệt 05/11/2026): chỉ người đã gắn, quyền theo vai trò, chỉ đơn nháp, có nhật ký."""
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import zalo_agent as agent, zalo_bot
from app.config import get_settings
from app.models import (AuditLog, Customer, Product, SalesOrder, StockBalance, StockReservation, Warehouse, ZaloAgentLog,
                        ZaloChat, ZaloMessage, ZaloTroLy)
from test_web import service_login
from test_zalo_tin_nhan import H, SECRET, TOKEN, event

FK = '00000000-0000-4000-8000-0000000000{:02d}'


@pytest.fixture
def zalo(monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(cfg, 'zalo_webhook_secret', SECRET)
    monkeypatch.setattr(cfg, 'zalo_bot_token', TOKEN)
    monkeypatch.setattr(cfg, 'openai_api_key', 'sk-test')
    monkeypatch.setattr(cfg, 'sales_default_warehouse', '58')
    state = dict(sent=[], ai=[], script=[])

    def fake_call(method, payload=None, timeout=15):
        state['sent'].append((method, payload))
        return dict(message_id=f"out-{len(state['sent'])}")

    def fake_ai(messages, tools):
        state['ai'].append(dict(messages=json.loads(json.dumps(messages, default=str)), tools=[t['function']['name'] for t in tools]))
        step = state['script'].pop(0)
        if isinstance(step, Exception):
            raise step
        return step
    monkeypatch.setattr(zalo_bot, 'call', fake_call)
    monkeypatch.setattr(agent, 'ask_ai', fake_ai)
    return state


def call(name, **args):
    return dict(choices=[dict(message=dict(content=None, tool_calls=[dict(id=f'c-{name}', type='function',
                function=dict(name=name, arguments=json.dumps(args, ensure_ascii=False)))]))], usage=dict(prompt_tokens=1000, completion_tokens=200))


def say(text):
    return dict(choices=[dict(message=dict(content=text))], usage=dict(prompt_tokens=500, completion_tokens=100))


@pytest.fixture
def shop(db, make_user):
    users = {r: make_user('tl.' + r, r) for r in ('technician', 'sales', 'director', 'finance')}
    db.add_all([
        Customer(code='KTD-000001', name='Công ty An Phú', address='12 Nguyễn Văn Linh', search_text='ktd-000001 cong ty an phu'),
        Customer(code='KTD-000002', name='Đã ngừng', status='NGUNG', search_text='da ngung'),
        Product(code='TD-0142', name='Hộp mực HP 12A chính hãng', unit='hộp', sale_price=820000, search_text='td-0142 hop muc hp 12a chinh hang'),
        Product(code='TD-0317', name='Hộp mực Renew 12A', unit='hộp', sale_price=250000, search_text='td-0317 hop muc renew 12a'),
        Warehouse(code='58', name='Kho chính'),
    ])
    db.flush()
    db.add(Warehouse(code='T-PHONG', name='Kho thợ Phong', kind='THO', holder_id=users['technician'].id))
    db.flush()
    db.add_all([StockBalance(warehouse_code='58', product_code='TD-0142', qty=Decimal(8), value=8 * 600000, avg_cost=600000),
                StockBalance(warehouse_code='T-PHONG', product_code='TD-0142', qty=Decimal(2), value=2 * 600000, avg_cost=600000)])
    db.add(ZaloTroLy(id=1, enabled=True, draft_user_id=users['sales'].id))
    db.commit()
    return users


def chat_of(db, chat='c1'):
    db.expire_all()
    return db.scalars(select(ZaloChat).where(ZaloChat.chat_id == chat)).one()


def linked(client, db, chat='c1', user=None, customer=None, name='Người hỏi'):
    """Gửi tin đầu (bot báo lần đầu), rồi gắn trực tiếp như giám đốc gắn."""
    client.post('/zalo/webhook', json=event(mid=f'{chat}-0', text='chào', chat=chat, name=name), headers=H)
    row = chat_of(db, chat)
    row.user_id, row.customer_code = (user.id if user else None), customer
    db.commit()
    return row


def texts(state):
    return [p['text'] for m, p in state['sent'] if m == 'sendMessage']


def last_log(db):
    db.expire_all()
    return db.scalars(select(ZaloAgentLog).order_by(ZaloAgentLog.id.desc())).first()


def test_tat_thi_khong_lam_gi(client, db, shop, zalo):
    db.get(ZaloTroLy, 1).enabled = False
    db.commit()
    linked(client, db, user=shop['technician'])
    client.post('/zalo/webhook', json=event(mid='m1', text='còn 12A không'), headers=H)
    assert zalo['ai'] == [] and db.scalars(select(ZaloAgentLog)).all() == [] and texts(zalo) == [zalo_bot.NOTICE]


def test_nguoi_chua_gan_chi_bao_da_nhan_mot_lan(client, db, shop, zalo):
    client.post('/zalo/webhook', json=event(mid='m1', text='shop còn mực 12A không, bao nhiêu'), headers=H)
    client.post('/zalo/webhook', json=event(mid='m2', text='alo'), headers=H)
    assert zalo['ai'] == []
    assert texts(zalo) == [zalo_bot.NOTICE, agent.RECEIVED_TEXT]
    rows = db.scalars(select(ZaloAgentLog).order_by(ZaloAgentLog.id)).all()
    assert [(r.asker, r.result, r.cost_vnd) for r in rows] == [('CHUA_GAN', 'KHONG_TRA_LOI', 0)] * 2
    rows[0].created_at -= timedelta(hours=13)
    db.commit()
    client.post('/zalo/webhook', json=event(mid='m3', text='alo'), headers=H)
    assert texts(zalo)[-1] == agent.RECEIVED_TEXT and len(texts(zalo)) == 3


def test_tho_chi_thay_so_luong_khong_thay_gia(client, db, shop, zalo):
    linked(client, db, user=shop['technician'])
    zalo['script'] = [call('tra_ton', tu_khoa='12A'), say('Có 2 mã 12A. TD-0142: Kho chính 8, Kho thợ Phong 2.')]
    client.post('/zalo/webhook', json=event(mid='m1', text='còn hộp mực 12A ko'), headers=H)
    assert zalo['ai'][0]['tools'] == ['tra_ton']  # không có tìm khách, lên đơn
    tool_msg = zalo['ai'][1]['messages'][-1]
    data = json.loads(tool_msg['content'])
    assert tool_msg['role'] == 'tool' and len(data['ket_qua']) == 2
    hp = next(r for r in data['ket_qua'] if r['ma'] == 'TD-0142')
    assert {k['kho']: k['so_luong'] for k in hp['kho']} == {'Kho chính': 8, 'Kho thợ Phong': 2}
    raw = tool_msg['content']
    assert 'gia_ban' not in raw and '820000' not in raw and '600000' not in raw  # không giá bán, không giá vốn
    assert texts(zalo)[-1].startswith('Có 2 mã 12A')
    log = last_log(db)
    assert (log.asker, log.result, log.user_id) == ('NHAN_VIEN', 'TRA_LOI', shop['technician'].id)
    assert log.tools[0]['ten'] == 'tra_ton' and log.tokens_in == 1500 and log.cost_vnd > 0
    assert db.scalars(select(SalesOrder)).all() == []


def test_kinh_doanh_thay_gia_ban_va_len_don_nhap(client, db, shop, zalo):
    linked(client, db, user=shop['sales'])
    zalo['script'] = [call('tra_ton', tu_khoa='12A'), call('tim_khach', tu_khoa='An Phú'),
                      call('tao_don_nhap', ma_khach='KTD-000001', dong=[dict(ma_hang='TD-0142', so_luong=2, can_chon_ma='TD-0317 Renew 12A')],
                           hen_giao='', giao_tan_noi=True, ghi_chu=''),
                      say('Đã tạo đơn nháp cho An Phú.')]
    client.post('/zalo/webhook', json=event(mid='m1', text='lên đơn cty An Phú 2 hộp 12A giao mai'), headers=H)
    assert zalo['ai'][0]['tools'] == ['tra_ton', 'tim_khach', 'lich_su_mua', 'tao_don_nhap']
    assert '820000' in zalo['ai'][1]['messages'][-1]['content'] and '600000' not in json.dumps(zalo['ai'][-1]['messages'])
    order = db.scalars(select(SalesOrder)).one()
    assert (order.status, order.customer_code, order.created_by, order.fulfillment) == ('NHAP', 'KTD-000001', shop['sales'].id, 'THO_GIAO')
    assert order.lines[0].unit_price == 820000 and 'Cần chọn mã' in order.note and 'Tin gốc' in order.note
    assert db.scalars(select(StockReservation)).all() == []  # đơn nháp không giữ hàng
    assert order.number in texts(zalo)[-1]
    log = last_log(db)
    assert (log.result, log.order_id) == ('DON_NHAP', order.id) and 'TD-0317' in log.need_pick
    assert db.scalars(select(AuditLog).where(AuditLog.action == 'sales_order.create')).one().user_id == shop['sales'].id


def test_chi_tao_mot_don_nhap_moi_tin_va_bao_loi_ma_sai(client, db, shop, zalo):
    linked(client, db, user=shop['sales'])
    line = [dict(ma_hang='TD-0142', so_luong=1, can_chon_ma='')]
    zalo['script'] = [call('tao_don_nhap', ma_khach='KTD-000001', dong=[dict(ma_hang='TD-9999', so_luong=1, can_chon_ma='')], hen_giao='', giao_tan_noi=False, ghi_chu=''),
                      call('tao_don_nhap', ma_khach='KTD-000002', dong=line, hen_giao='', giao_tan_noi=False, ghi_chu=''),
                      call('tao_don_nhap', ma_khach='KTD-000001', dong=line, hen_giao='2020-01-01', giao_tan_noi=False, ghi_chu=''),
                      call('tao_don_nhap', ma_khach='KTD-000001', dong=line, hen_giao='', giao_tan_noi=False, ghi_chu=''),
                      say('Xong')]
    client.post('/zalo/webhook', json=event(mid='m1', text='lên đơn'), headers=H)
    outs = [json.loads(s['messages'][-1]['content']) for s in zalo['ai'][1:]]
    assert 'TD-9999' in outs[0]['loi'] and 'ngừng' in outs[1]['loi'] and 'da_tao' in outs[2] and 'rồi' in outs[3]['loi']
    order = db.scalars(select(SalesOrder)).one()
    assert order.promised_date is None  # ngày đã qua bị bỏ


def test_khach_da_gan_dat_hang_thanh_don_nhap_khong_bao_gia(client, db, shop, zalo):
    linked(client, db, customer='KTD-000001', name='Kế toán An Phú')
    zalo['script'] = [call('lich_su_mua', tu_khoa='12A'), call('tra_ton', tu_khoa='12A'),
                      call('tao_don_nhap', dong=[dict(ma_hang='TD-0142', so_luong=2, can_chon_ma='')], hen_giao='', giao_tan_noi=True, ghi_chu=''),
                      say('ok')]
    client.post('/zalo/webhook', json=event(mid='m1', text='em ơi mai giao 2 hộp 12A về cty nhé'), headers=H)
    assert zalo['ai'][0]['tools'] == ['tra_ton', 'lich_su_mua', 'tao_don_nhap']
    assert '820000' not in zalo['ai'][2]['messages'][-1]['content']
    order = db.scalars(select(SalesOrder)).one()
    assert (order.customer_code, order.created_by, order.status) == ('KTD-000001', shop['sales'].id, 'NHAP')
    reply = texts(zalo)[-1]
    assert reply.startswith('Thành Đức đã nhận yêu cầu: 2 × Hộp mực HP 12A') and '820' not in reply and 'Kho' not in reply
    assert (last_log(db).asker, last_log(db).result) == ('KHACH', 'DON_NHAP')


def test_khach_nhan_chuyen_khac_thi_khong_tra_loi(client, db, shop, zalo):
    linked(client, db, customer='KTD-000001')
    before = len(texts(zalo))
    zalo['script'] = [say('KHONG')]
    client.post('/zalo/webhook', json=event(mid='m1', text='máy in kẹt giấy em ơi'), headers=H)
    assert len(texts(zalo)) == before and last_log(db).result == 'BO_QUA'
    assert db.scalars(select(SalesOrder)).all() == []


def test_khach_khi_chua_chon_nguoi_nhan_don_nhap(client, db, shop, zalo):
    db.get(ZaloTroLy, 1).draft_user_id = None
    db.commit()
    linked(client, db, customer='KTD-000001')
    client.post('/zalo/webhook', json=event(mid='m1', text='giao 2 hộp 12A'), headers=H)
    log = last_log(db)
    assert log.result == 'LOI' and 'người nhận đơn nháp' in log.error and zalo['ai'] == []


def test_het_luot_va_loi_ai(client, db, shop, zalo, monkeypatch):
    monkeypatch.setattr(get_settings(), 'zalo_ai_per_person', 1)
    linked(client, db, user=shop['technician'])
    zalo['script'] = [agent.AiError('OpenAI trả lỗi HTTP 500.')]
    client.post('/zalo/webhook', json=event(mid='m1', text='còn 12A'), headers=H)
    assert last_log(db).result == 'LOI' and texts(zalo)[-1] == agent.FAIL_TEXT
    client.post('/zalo/webhook', json=event(mid='m2', text='còn 12A'), headers=H)  # giới hạn 1 câu/người/ngày
    assert last_log(db).result == 'HET_LUOT' and 'hết lượt' in texts(zalo)[-1] and len(zalo['ai']) == 1


def test_nhom_va_tai_khoan_khoa_khong_tra_loi_so_lieu(client, db, shop, zalo):
    client.post('/zalo/webhook', json=event(mid='g1', text='còn 12A', chat='g', chat_type='GROUP'), headers=H)
    assert db.scalars(select(ZaloAgentLog)).all() == []
    linked(client, db, chat='c2', user=shop['technician'])
    shop['technician'].active = False
    db.commit()
    client.post('/zalo/webhook', json=event(mid='m1', text='còn 12A', chat='c2'), headers=H)
    assert last_log(db).asker == 'CHUA_GAN' and zalo['ai'] == []


@pytest.mark.parametrize('role', ['sales', 'technician', 'finance'])
def test_chi_giam_doc_gan_va_cau_hinh(client, db, shop, zalo, role):
    linked(client, db)
    chat = chat_of(db)
    csrf = service_login(client, shop[role])
    assert client.get('/zalo/tro-ly').status_code == 403
    r = client.post(f'/zalo/tin-nhan/{chat.id}/gan', data={'csrf': csrf, 'form_key': FK.format(1), 'loai': 'NHAN_VIEN', 'nhan_vien': shop[role].id})
    assert r.status_code == 403
    assert client.post('/zalo/tro-ly/cau-hinh', data={'csrf': csrf, 'form_key': FK.format(2), 'bat': '0'}).status_code == 403
    assert chat_of(db).user_id is None and db.get(ZaloTroLy, 1).enabled


def test_giam_doc_gan_nhan_vien_khach_va_bo_gan(client, db, shop, zalo):
    client.post('/zalo/webhook', json=event(mid='a', text='x', chat='c1', name='Phong Đoàn'), headers=H)
    client.post('/zalo/webhook', json=event(mid='b', text='x', chat='c2'), headers=H)
    client.post('/zalo/webhook', json=event(mid='g', text='x', chat='g1', chat_type='GROUP'), headers=H)
    c1, c2, g = chat_of(db, 'c1'), chat_of(db, 'c2'), chat_of(db, 'g1')
    csrf = service_login(client, shop['director'])
    html = client.get(f'/zalo/tin-nhan?chat={c1.id}&kq=an phu').text
    assert 'Người này là' in html and 'Công ty An Phú' in html and 'Đã ngừng' not in html
    tech = shop['technician'].id
    r = client.post(f'/zalo/tin-nhan/{c1.id}/gan', data={'csrf': csrf, 'form_key': FK.format(1), 'loai': 'NHAN_VIEN', 'nhan_vien': tech}, follow_redirects=True)
    assert r.status_code == 200 and chat_of(db, 'c1').user_id == tech
    # Một nhân viên chỉ gắn một hội thoại; nhóm không gắn được; khách ngừng không gắn được.
    assert client.post(f'/zalo/tin-nhan/{c2.id}/gan', data={'csrf': csrf, 'form_key': FK.format(2), 'loai': 'NHAN_VIEN', 'nhan_vien': tech}).status_code == 409
    assert client.post(f'/zalo/tin-nhan/{g.id}/gan', data={'csrf': csrf, 'form_key': FK.format(3), 'loai': 'KHACH', 'khach': 'KTD-000001'}).status_code == 422
    assert client.post(f'/zalo/tin-nhan/{c2.id}/gan', data={'csrf': csrf, 'form_key': FK.format(4), 'loai': 'KHACH', 'khach': 'KTD-000002'}).status_code == 422
    client.post(f'/zalo/tin-nhan/{c2.id}/gan', data={'csrf': csrf, 'form_key': FK.format(5), 'loai': 'KHACH', 'khach': 'KTD-000001'})
    assert chat_of(db, 'c2').customer_code == 'KTD-000001'
    html = client.get('/zalo/tro-ly').text
    assert 'Phong Đoàn' in html and 'Hỏi tồn' in html and 'Gửi yêu cầu đặt hàng' in html
    client.post(f'/zalo/tin-nhan/{c1.id}/gan', data={'csrf': csrf, 'form_key': FK.format(6), 'loai': 'CHUA_GAN', 've': 'tro-ly'})
    assert chat_of(db, 'c1').user_id is None
    assert db.scalars(select(AuditLog).where(AuditLog.action == 'zalo_chat.link')).all()


def test_cau_hinh_bat_tat_va_nguoi_nhan(client, db, shop, zalo):
    csrf = service_login(client, shop['director'])
    r = client.post('/zalo/tro-ly/cau-hinh', data={'csrf': csrf, 'form_key': FK.format(1), 'bat': '0', 'nguoi_nhan': ''}, follow_redirects=True)
    assert r.status_code == 200 and 'Đang tắt' in r.text
    db.expire_all()
    assert (db.get(ZaloTroLy, 1).enabled, db.get(ZaloTroLy, 1).draft_user_id) == (False, None)
    assert client.post('/zalo/tro-ly/cau-hinh', data={'csrf': csrf, 'form_key': FK.format(2), 'bat': '1',
                                                      'nguoi_nhan': shop['technician'].id}).status_code == 422
    client.post('/zalo/tro-ly/cau-hinh', data={'csrf': csrf, 'form_key': FK.format(3), 'bat': '1', 'nguoi_nhan': shop['sales'].id})
    db.expire_all()
    assert db.get(ZaloTroLy, 1).enabled and db.get(ZaloTroLy, 1).draft_user_id == shop['sales'].id


def test_nhat_ky_va_man_don_nhap_kinh_doanh(client, db, shop, zalo):
    linked(client, db, customer='KTD-000001', name='Kế toán An Phú')
    zalo['script'] = [call('tao_don_nhap', dong=[dict(ma_hang='TD-0142', so_luong=2, can_chon_ma='TD-0317')], hen_giao='', giao_tan_noi=False, ghi_chu=''), say('ok')]
    client.post('/zalo/webhook', json=event(mid='m1', text='giao 2 hộp 12A'), headers=H)
    order = db.scalars(select(SalesOrder)).one()
    service_login(client, shop['director'])
    html = client.get('/zalo/tro-ly?tab=nhat-ky').text
    assert 'giao 2 hộp 12A' in html and 'tao_don_nhap' in html and 'Cần chọn mã' in html
    csrf = service_login(client, shop['sales'])
    html = client.get('/don-nhap-zalo').text
    assert order.number in html and 'Kiểm và chốt' in html and 'Cần chọn mã: TD-0142: TD-0317' in html
    r = client.post(f'/don-nhap-zalo/{order.id}/bo', data={'csrf': csrf, 'form_key': FK.format(1), 'version': order.version}, follow_redirects=True)
    assert r.status_code == 200
    db.expire_all()
    assert db.get(SalesOrder, order.id).status == 'DA_HUY'
    assert order.number in client.get('/don-nhap-zalo?view=bo').text
    for role in ('technician', 'finance'):
        service_login(client, shop[role])
        assert client.get('/don-nhap-zalo').status_code == 403


def test_khong_bo_don_khong_phai_tu_zalo(client, db, shop, zalo):
    from app import sales_orders
    from app.schemas import SalesOrderIn
    order = sales_orders.create(db, shop['sales'], SalesOrderIn(customer_code='KTD-000001', lines=[dict(kind='HANG', product_code='TD-0142', qty='1')]))
    csrf = service_login(client, shop['sales'])
    assert client.post(f'/don-nhap-zalo/{order.id}/bo', data={'csrf': csrf, 'form_key': FK.format(1), 'version': order.version}).status_code == 404


def test_ask_ai_khong_lo_khoa(monkeypatch):
    import io
    import urllib.error
    import urllib.request
    monkeypatch.setattr(get_settings(), 'openai_api_key', 'sk-bi-mat')

    def boom(req, timeout):
        assert req.headers['Authorization'] == 'Bearer sk-bi-mat'
        raise urllib.error.HTTPError(req.full_url, 401, 'x', {}, io.BytesIO(b'{"error":{"message":"bad key sk-bi-mat"}}'))
    monkeypatch.setattr(urllib.request, 'urlopen', boom)
    with pytest.raises(agent.AiError) as exc:
        agent.ask_ai([], [])
    assert 'sk-bi-mat' not in str(exc.value) and '401' in str(exc.value)
    monkeypatch.setattr(get_settings(), 'openai_api_key', '')
    with pytest.raises(agent.AiError):
        agent.ask_ai([], [])
