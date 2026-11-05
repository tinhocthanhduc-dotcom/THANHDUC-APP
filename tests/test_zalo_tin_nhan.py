"""Tin nhắn Zalo Bot (TUẤN 05/11/2026): webhook có khóa, chống trùng, chỉ giám đốc, gửi lỗi vẫn thấy, không lộ token."""
import io
import json
import urllib.error

import pytest
from sqlalchemy import select

from app import zalo_bot as svc
from app.config import get_settings
from app.models import AuditLog, ZaloChat, ZaloMessage
from test_web import service_login

SECRET = 'khoa-bi-mat-de-test'
TOKEN = '123456:token-bi-mat-cua-bot'
H = {'X-Bot-Api-Secret-Token': SECRET}


def event(mid='m1', text='Xin chào', chat='c1', name='Khách A', chat_type='PRIVATE', kind='message.text.received', **extra):
    message = dict(message_id=mid, date=1750316131602, message_type='CHAT_TEXT', text=text,
                   chat=dict(id=chat, chat_type=chat_type), **{'from': dict(id='u1', display_name=name, is_bot=False)}, **extra)
    return dict(event_name=kind, message=message)


@pytest.fixture
def zalo(monkeypatch):
    """Thay lệnh gọi Zalo để test không ra Internet. `sent` ghi lại các lần gọi."""
    cfg = get_settings()
    monkeypatch.setattr(cfg, 'zalo_webhook_secret', SECRET)
    monkeypatch.setattr(cfg, 'zalo_bot_token', TOKEN)
    state = dict(sent=[], fail=None)

    def fake(method, payload=None, timeout=15):
        state['sent'].append((method, payload))
        if state['fail']:
            raise svc.ZaloError(state['fail'])
        return dict(message_id=f"out-{len(state['sent'])}")
    monkeypatch.setattr(svc, 'call', fake)
    return state


def test_webhook_tu_choi_sai_khoa_va_khi_chua_dat_khoa(client, db, zalo, monkeypatch):
    assert client.post('/zalo/webhook', json=event()).status_code == 403
    assert client.post('/zalo/webhook', json=event(), headers={'X-Bot-Api-Secret-Token': 'sai'}).status_code == 403
    assert client.post('/zalo/webhook', json=event(), headers={'X-Bot-Api-Secret-Token': 'khóa-có-dấu'.encode()}).status_code == 403
    monkeypatch.setattr(get_settings(), 'zalo_webhook_secret', '')
    assert client.post('/zalo/webhook', json=event(), headers={'X-Bot-Api-Secret-Token': ''}).status_code == 403
    assert db.scalars(select(ZaloMessage)).all() == []


def test_webhook_luu_tin_chong_trung_va_bao_mot_lan(client, db, zalo):
    assert client.post('/zalo/webhook', json=event(), headers=H).json() == {'ok': True}
    assert client.post('/zalo/webhook', json=event(), headers=H).status_code == 200  # Zalo gửi trùng
    assert client.post('/zalo/webhook', json=event(mid='m2', text='Tin hai'), headers=H).status_code == 200
    chat = db.scalars(select(ZaloChat)).one()
    assert (chat.chat_id, chat.display_name, chat.unread, chat.last_text) == ('c1', 'Khách A', 2, 'Tin hai')
    assert chat.notice_sent_at is not None
    rows = db.scalars(select(ZaloMessage).order_by(ZaloMessage.id)).all()
    received = [r for r in rows if r.direction == 'IN']
    assert [r.text for r in received] == ['Xin chào', 'Tin hai'] and received[0].sender_name == 'Khách A'
    notices = [r for r in rows if r.kind == 'thong_bao']
    assert len(notices) == 1 and notices[0].status == 'DA_GUI' and notices[0].text == svc.NOTICE
    assert [s for s in zalo['sent'] if s[0] == 'sendMessage'] == [('sendMessage', dict(chat_id='c1', text=svc.NOTICE))]


def test_webhook_du_lieu_la_khong_lam_hong(client, db, zalo):
    assert client.post('/zalo/webhook', content=b'khong phai json', headers=H).status_code == 400
    assert client.post('/zalo/webhook', json=[1], headers=H).status_code == 400
    assert client.post('/zalo/webhook', content=b'x' * (256 * 1024 + 1), headers=H).status_code == 413
    for body in ({}, {'event_name': 'webhook.test'}, {'message': 'x'}, {'message': {'chat': {}}}, {'message': {'chat': {'id': 'x' * 65}}}):
        assert client.post('/zalo/webhook', json=body, headers=H).status_code == 200
    assert db.scalars(select(ZaloMessage)).all() == []
    assert client.post('/zalo/webhook', json=event(text='co\x00nul', date_bad=1), headers=H).status_code == 200
    assert db.scalars(select(ZaloMessage).where(ZaloMessage.direction == 'IN')).one().text == 'conul'
    raw = json.dumps(event(mid='lo', text='a')).replace('"a"', '"\\ud83d"').encode()  # surrogate lẻ
    assert client.post('/zalo/webhook', content=raw, headers=H).status_code == 200


def test_tin_anh_va_nhom(client, db, make_user, zalo):
    client.post('/zalo/webhook', json=event(mid='a', text='', kind='message.image.received', chat='g1', chat_type='GROUP'), headers=H)
    chat = db.scalars(select(ZaloChat)).one()
    assert chat.chat_type == 'GROUP' and chat.last_text == 'Hình ảnh' and chat.display_name == ''  # không lấy tên người gửi làm tên nhóm
    assert db.scalars(select(ZaloMessage).where(ZaloMessage.direction == 'IN')).one().kind == 'image'
    service_login(client, make_user('giam.doc', 'director'))
    html = client.get(f'/zalo/tin-nhan?chat={chat.id}').text
    assert 'Nhóm' in html and '[Hình ảnh]' in html


@pytest.mark.parametrize('role', ['admin', 'finance', 'sales', 'dispatcher', 'technician', 'warehouse', 'reception'])
def test_chi_giam_doc_xem_va_tra_loi(client, db, make_user, zalo, role):
    client.post('/zalo/webhook', json=event(), headers=H)
    chat = db.scalars(select(ZaloChat)).one()
    csrf = service_login(client, make_user('nv', role))
    before = len(zalo['sent'])
    assert client.get('/zalo/tin-nhan').status_code == 403
    assert client.post(f'/zalo/tin-nhan/{chat.id}/tra-loi', data={'csrf': csrf, 'form_key': '00000000-0000-4000-8000-000000000001', 'noi_dung': 'x'}).status_code == 403
    assert len(zalo['sent']) == before


def test_giam_doc_xem_danh_dau_da_doc_va_tra_loi(client, db, make_user, zalo):
    client.post('/zalo/webhook', json=event(text='<script>alert(1)</script>'), headers=H)
    chat_pk = db.scalars(select(ZaloChat)).one().id
    director = make_user('giam.doc', 'director')
    csrf = service_login(client, director)
    html = client.get('/zalo/tin-nhan').text
    assert 'Khách A' in html and '1 mới' in html
    html = client.get(f'/zalo/tin-nhan?chat={chat_pk}').text
    assert '<script>alert(1)</script>' not in html and '&lt;script&gt;alert(1)' in html
    db.expire_all()
    assert db.get(ZaloChat, chat_pk).unread == 0
    r = client.post(f'/zalo/tin-nhan/{chat_pk}/tra-loi', data={'csrf': csrf, 'noi_dung': ' Dạ em chào anh '}, follow_redirects=True)
    assert r.status_code == 200 and 'Dạ em chào anh' in r.text and 'Đã gửi' in r.text
    assert zalo['sent'][-1] == ('sendMessage', dict(chat_id='c1', text='Dạ em chào anh'))
    row = db.scalars(select(ZaloMessage).where(ZaloMessage.kind == 'text', ZaloMessage.direction == 'OUT')).one()
    assert row.sent_by == director.id and row.zalo_message_id
    log = db.scalars(select(AuditLog).where(AuditLog.action == 'zalo.reply')).one()
    assert 'Dạ em' not in json.dumps(log.after, ensure_ascii=False)  # nhật ký không chứa nội dung


def test_tra_loi_kiem_tra_noi_dung(client, db, make_user, zalo):
    client.post('/zalo/webhook', json=event(), headers=H)
    chat_pk = db.scalars(select(ZaloChat)).one().id
    csrf = service_login(client, make_user('giam.doc', 'director'))
    before = len(zalo['sent'])
    for text in ('   ', 'x' * 2001):
        assert client.post(f'/zalo/tin-nhan/{chat_pk}/tra-loi', data={'csrf': csrf, 'noi_dung': text}).status_code == 422
    assert client.post('/zalo/tin-nhan/999999/tra-loi', data={'csrf': csrf, 'noi_dung': 'x'}).status_code == 404
    assert client.post(f'/zalo/tin-nhan/{chat_pk}/tra-loi', data={'csrf': 'sai', 'noi_dung': 'x'}).status_code == 403
    assert len(zalo['sent']) == before


def test_gui_loi_van_luu_va_hien_ly_do(client, db, make_user, zalo):
    client.post('/zalo/webhook', json=event(), headers=H)
    chat_pk = db.scalars(select(ZaloChat)).one().id
    csrf = service_login(client, make_user('giam.doc', 'director'))
    zalo['fail'] = 'Zalo từ chối yêu cầu.'
    r = client.post(f'/zalo/tin-nhan/{chat_pk}/tra-loi', data={'csrf': csrf, 'noi_dung': 'Tin lỗi'}, follow_redirects=True)
    assert r.status_code == 200 and 'Tin chưa gửi được' in r.text and 'Gửi lỗi' in r.text and 'Zalo từ chối yêu cầu.' in r.text
    row = db.scalars(select(ZaloMessage).where(ZaloMessage.text == 'Tin lỗi')).one()
    assert row.status == 'LOI'


def test_loi_bao_lan_dau_khong_lam_hong_webhook(client, db, zalo):
    zalo['fail'] = 'Lỗi mạng'
    assert client.post('/zalo/webhook', json=event(), headers=H).status_code == 200
    assert db.scalars(select(ZaloMessage).where(ZaloMessage.kind == 'thong_bao')).one().status == 'LOI'
    assert db.scalars(select(ZaloMessage).where(ZaloMessage.direction == 'IN')).one().text == 'Xin chào'


def test_tim_kiem_va_phan_trang(client, db, make_user, zalo):
    client.post('/zalo/webhook', json=event(mid='1', text='báo giá máy in', chat='c1', name='Anh Một'), headers=H)
    client.post('/zalo/webhook', json=event(mid='2', text='100% ok', chat='c2', name='Chị Hai'), headers=H)
    service_login(client, make_user('giam.doc', 'director'))
    html = client.get('/zalo/tin-nhan?q=máy in').text
    assert 'Anh Một' in html and 'Chị Hai' not in html
    html = client.get('/zalo/tin-nhan?q=%25').text  # % là chữ thường, không phải ký tự đại diện
    assert 'Chị Hai' in html and 'Anh Một' not in html
    assert client.get('/zalo/tin-nhan?chat=khong-phai-so').status_code == 200
    assert client.get('/zalo/tin-nhan?chat=99999').status_code == 200


def test_cai_webhook_hien_ket_qua(client, db, make_user, zalo):
    director = make_user('giam.doc', 'director')
    csrf = service_login(client, director)
    r = client.post('/zalo/tin-nhan/cai-webhook', data={'csrf': csrf}, follow_redirects=True)
    assert r.status_code == 200 and 'Đã cài webhook' in r.text
    method, payload = zalo['sent'][-1]
    assert method == 'setWebhook' and payload == dict(url='https://quanlythanhduc.com/zalo/webhook', secret_token=SECRET)
    log = db.scalars(select(AuditLog).where(AuditLog.action == 'zalo.webhook_set')).one()
    assert SECRET not in json.dumps(log.after) and TOKEN not in r.text and SECRET not in r.text


def test_cai_webhook_loi_va_khoa_ngan(client, db, make_user, zalo, monkeypatch):
    csrf = service_login(client, make_user('giam.doc', 'director'))
    zalo['fail'] = 'Token sai'
    r = client.post('/zalo/tin-nhan/cai-webhook', data={'csrf': csrf}, follow_redirects=True)
    assert 'Chưa cài được webhook' in r.text and 'Token sai' in r.text
    monkeypatch.setattr(get_settings(), 'zalo_webhook_secret', 'ngan')
    r = client.post('/zalo/tin-nhan/cai-webhook', data={'csrf': csrf}, follow_redirects=True)
    assert 'từ 8 đến 256' in r.text


def test_call_khong_lo_token_trong_loi(monkeypatch):
    monkeypatch.setattr(get_settings(), 'zalo_bot_token', TOKEN)

    def http_error(req, timeout):
        assert TOKEN in req.full_url
        body = io.BytesIO(json.dumps(dict(ok=False, description=f'bad {TOKEN}', error_code=400)).encode())
        raise urllib.error.HTTPError(req.full_url, 400, 'Bad', {}, body)
    monkeypatch.setattr(urllib.request, 'urlopen', http_error)
    with pytest.raises(svc.ZaloError) as exc:
        svc.call('sendMessage', dict(chat_id='c', text='x'))
    assert TOKEN not in str(exc.value) and '***' in str(exc.value)

    def net_error(req, timeout):
        raise urllib.error.URLError(f'mất mạng {req.full_url}')
    monkeypatch.setattr(urllib.request, 'urlopen', net_error)
    with pytest.raises(svc.ZaloError) as exc:
        svc.call('sendMessage', {})
    assert TOKEN not in str(exc.value) and exc.value.__suppress_context__

    monkeypatch.setattr(get_settings(), 'zalo_bot_token', '')
    with pytest.raises(svc.ZaloError, match='Chưa cấu hình'):
        svc.call('sendMessage', {})
