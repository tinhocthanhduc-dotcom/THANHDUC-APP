"""Tất cả tin Zalo (TUẤN duyệt mẫu zalo.quanlythanhduc.com 19/11/2026, đợt 1).

Kinh doanh, điều phối xem/trả lời hội thoại khách và người chưa gắn; không thấy tin nhân viên hay nhóm.
Giám đốc thấy tất cả. Kế toán, thợ, kho không vào. Công nợ chỉ hiện cho giám đốc/kế toán.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import Customer, ZaloChat, ZaloMessage, ZaloTriage
from test_web import service_login
from test_zalo_tin_nhan import zalo  # noqa: F401  (fixture: không gọi Zalo thật)

FK = '00000000-0000-4000-8000-0000000000{:02d}'


@pytest.fixture
def chats(db, make_user):
    now = datetime.now(timezone.utc)
    staff = make_user('tho.a', 'technician')
    db.add(Customer(code='KTD-000001', name='Công ty Hưng Phát', search_text='ktd-000001 cong ty hung phat'))
    db.flush()
    rows = dict(
        khach=ZaloChat(chat_id='c-khach', chat_type='PRIVATE', display_name='Long Zalo', customer_code='KTD-000001',
                       last_message_at=now, last_text='Máy sửa xong chưa em?', unread=1),
        la=ZaloChat(chat_id='c-la', chat_type='PRIVATE', display_name='Người lạ', last_message_at=now - timedelta(minutes=5),
                    last_text='Canon 2900 kẹt giấy'),
        nv=ZaloChat(chat_id='c-nv', chat_type='PRIVATE', display_name='Thợ A', user_id=staff.id,
                    last_message_at=now - timedelta(minutes=9), last_text='tồn 12A còn mấy hộp'),
        nhom=ZaloChat(chat_id='g-1', chat_type='GROUP', display_name='Nhóm nội bộ', last_message_at=now - timedelta(minutes=12),
                      last_text='họp 8h'),
    )
    db.add_all(rows.values())
    db.flush()
    msg = ZaloMessage(chat_pk=rows['khach'].id, direction='IN', kind='text', text='Máy sửa xong chưa em?', status='DA_NHAN', message_at=now)
    db.add(msg)
    db.flush()
    db.add(ZaloTriage(message_id=msg.id, status='XONG', intent='SUA', summary='Hỏi máy sửa xong'))
    db.commit()
    return {k: v.id for k, v in rows.items()}


@pytest.mark.parametrize('role', ['sales', 'dispatcher'])
def test_nhan_vien_chi_thay_hoi_thoai_khach_va_nguoi_la(client, make_user, chats, role):
    service_login(client, make_user('nv', role))
    html = client.get('/zalo/hop-thu').text
    assert 'Công ty Hưng Phát' in html and 'Người lạ' in html and 'Cần trả lời' in html
    assert 'Thợ A' not in html and 'Nhóm nội bộ' not in html and 'loc=nhan-vien' not in html
    assert 'Người lạ' not in client.get('/zalo/hop-thu?loc=khach').text
    assert 'Công ty Hưng Phát' not in client.get('/zalo/hop-thu?loc=chua-gan').text
    for key in ('nv', 'nhom'):
        assert client.get(f"/zalo/hop-thu?chat={chats[key]}").status_code == 404


def test_giam_doc_thay_tat_ca_va_cong_no(client, make_user, chats):
    service_login(client, make_user('gd', 'director'))
    html = client.get('/zalo/hop-thu').text
    assert all(name in html for name in ('Công ty Hưng Phát', 'Người lạ', 'Thợ A', 'Nhóm nội bộ'))
    assert 'Thợ A' in client.get('/zalo/hop-thu?loc=nhan-vien').text
    html = client.get(f"/zalo/hop-thu?chat={chats['khach']}").text
    assert 'Còn nợ' in html and 'KTD-000001' in html and 'Máy sửa xong chưa em?' in html


def test_kinh_doanh_khong_thay_cong_no_va_danh_dau_da_doc(client, db, make_user, chats):
    service_login(client, make_user('kd', 'sales'))
    html = client.get(f"/zalo/hop-thu?chat={chats['khach']}").text
    assert 'KTD-000001' in html and 'Còn nợ' not in html and 'Trả lời khách' in html
    db.expire_all()
    assert db.get(ZaloChat, chats['khach']).unread == 0


@pytest.mark.parametrize('role', ['finance', 'technician', 'warehouse', 'reception', 'admin'])
def test_vai_khac_khong_vao(client, make_user, chats, zalo, role):
    csrf = service_login(client, make_user('x', role))
    assert client.get('/zalo/hop-thu').status_code == 403
    r = client.post(f"/zalo/hop-thu/{chats['khach']}/tra-loi", data={'csrf': csrf, 'form_key': FK.format(1), 'noi_dung': 'x'})
    assert r.status_code == 403 and zalo['sent'] == []


def test_kinh_doanh_tra_loi_khach_qua_bot(client, db, make_user, chats, zalo):
    seller = make_user('kd', 'sales')
    csrf = service_login(client, seller)
    r = client.post(f"/zalo/hop-thu/{chats['khach']}/tra-loi", data={'csrf': csrf, 'form_key': FK.format(2), 'noi_dung': ' Dạ máy xong rồi ạ '},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].startswith(f"/zalo/hop-thu?chat={chats['khach']}")
    assert zalo['sent'][-1] == ('sendMessage', dict(chat_id='c-khach', text='Dạ máy xong rồi ạ'))
    row = db.scalars(select(ZaloMessage).where(ZaloMessage.direction == 'OUT')).one()
    assert row.sent_by == seller.id and row.status == 'DA_GUI'
    before = len(zalo['sent'])
    for i, text in enumerate(('   ', 'x' * 2001)):
        assert client.post(f"/zalo/hop-thu/{chats['khach']}/tra-loi", data={'csrf': csrf, 'form_key': FK.format(10 + i), 'noi_dung': text}).status_code == 422
    for i, key in enumerate(('nv', 'nhom')):  # tin nhân viên, nhóm: không trả lời thay được
        assert client.post(f"/zalo/hop-thu/{chats[key]}/tra-loi", data={'csrf': csrf, 'form_key': FK.format(20 + i), 'noi_dung': 'x'}).status_code == 404
    assert len(zalo['sent']) == before


def test_hop_zalo_tab_dau_la_tat_ca_tin(client, make_user, chats):
    service_login(client, make_user('kd', 'sales'))
    r = client.get('/hop-zalo', follow_redirects=False)
    assert r.headers['location'] == '/zalo/hop-thu'


# ---------- Đợt 2: AI soạn sẵn câu trả lời ----------

@pytest.fixture
def z2(db, monkeypatch):
    from app import zalo_agent
    from app.models import ZaloTroLy
    db.add(ZaloTroLy(id=1, enabled=False, staff_engine='claude'))
    db.commit()
    monkeypatch.setattr(zalo_agent, 'z2_alive', lambda session: True)


def _finish(db, answer, status='XONG'):
    from app.models import ZaloAiJob
    job = db.scalars(select(ZaloAiJob).order_by(ZaloAiJob.id.desc())).first()
    job.answer, job.status = answer, status
    db.commit()
    return job


def test_ai_soan_khong_gui_nguoi_duyet_moi_gui(client, db, make_user, chats, zalo, z2):
    from app.models import AuditLog, ZaloAiJob, ZaloReplyDraft
    seller = make_user('kd', 'sales')
    csrf = service_login(client, seller)
    r = client.post(f"/zalo/hop-thu/{chats['khach']}/ai-soan", data={'csrf': csrf, 'form_key': FK.format(30)}, follow_redirects=False)
    assert r.status_code == 303
    job = db.scalars(select(ZaloAiJob)).one()
    assert 'Công ty Hưng Phát' in job.prompt and 'Máy sửa xong chưa em?' in job.prompt and 'Còn nợ' not in job.prompt
    assert 'AI đang soạn' in client.get(f"/zalo/hop-thu?chat={chats['khach']}").text
    _finish(db, '<<<Dạ anh, máy đang chờ linh kiện, mai 9999 em báo lại ạ.>>>')
    html = client.get(f"/zalo/hop-thu?chat={chats['khach']}").text
    assert 'AI đã soạn sẵn' in html and 'Dạ anh, máy đang chờ linh kiện' in html and '9999' in html.split('không có trong dữ liệu', 1)[1][:200]
    assert zalo['sent'] == []  # AI không tự gửi
    draft = db.scalars(select(ZaloReplyDraft)).one()
    r = client.post(f"/zalo/hop-thu/{chats['khach']}/tra-loi",
                    data={'csrf': csrf, 'form_key': FK.format(31), 'noi_dung': 'Dạ anh, máy đang chờ linh kiện ạ.', 'ban_soan': str(draft.id)})
    assert r.status_code == 200
    assert zalo['sent'][-1] == ('sendMessage', dict(chat_id='c-khach', text='Dạ anh, máy đang chờ linh kiện ạ.'))
    db.expire_all()
    draft = db.get(ZaloReplyDraft, draft.id)
    assert draft.status == 'DA_GUI' and draft.closed_by == seller.id and draft.sent_message_id
    log = db.scalars(select(AuditLog).where(AuditLog.action == 'zalo.ai_reply_sent')).one()
    assert log.after['edited'] is True


def test_ai_soan_khi_z2_tat_va_ngoai_pham_vi(client, db, make_user, chats, zalo):
    from app.models import ZaloAiJob
    csrf = service_login(client, make_user('kd', 'sales'))
    r = client.post(f"/zalo/hop-thu/{chats['khach']}/ai-soan", data={'csrf': csrf, 'form_key': FK.format(40)}, follow_redirects=True)
    assert r.status_code == 200 and 'Máy Z2 (Codex/Claude) đang không chạy' in r.text
    assert db.scalars(select(ZaloAiJob)).all() == []
    for i, key in enumerate(('nv', 'nhom')):
        assert client.post(f"/zalo/hop-thu/{chats[key]}/ai-soan", data={'csrf': csrf, 'form_key': FK.format(41 + i)}).status_code == 404


def test_bo_ban_soan_va_z2_loi(client, db, make_user, chats, zalo, z2):
    from app.models import ZaloReplyDraft
    csrf = service_login(client, make_user('dp', 'dispatcher'))
    assert client.post(f"/zalo/hop-thu/{chats['la']}/ai-soan", data={'csrf': csrf, 'form_key': FK.format(49)}).status_code == 422  # chưa có tin khách
    db.add(ZaloMessage(chat_pk=chats['la'], direction='IN', kind='text', text='alo', status='DA_NHAN', message_at=datetime.now(timezone.utc)))
    db.commit()
    client.post(f"/zalo/hop-thu/{chats['la']}/ai-soan", data={'csrf': csrf, 'form_key': FK.format(50)})
    _finish(db, '', status='LOI')
    assert 'AI chưa soạn được' in client.get(f"/zalo/hop-thu?chat={chats['la']}").text
    draft = db.scalars(select(ZaloReplyDraft)).one()
    client.post(f"/zalo/hop-thu/{chats['la']}/bo-soan/{draft.id}", data={'csrf': csrf, 'form_key': FK.format(51)})
    db.expire_all()
    assert db.get(ZaloReplyDraft, draft.id).status == 'BO'
    assert 'AI soạn câu trả lời' in client.get(f"/zalo/hop-thu?chat={chats['la']}").text


ZALO = {'host': 'zalo.testserver', 'accept': 'text/html'}


def test_ten_mien_zalo_menu_rieng_va_trang_ngoai_ve_trang_chinh(client, make_user, chats):
    from app.web import sites
    assert sites.key_of('zalo.quanlythanhduc.com') == 'zalo'
    service_login(client, make_user('kd', 'sales'))
    r = client.get('/zalo/hop-thu', headers=ZALO)
    assert r.status_code == 200 and 'Thành Đức Zalo' in r.text and 'site-zalo' in r.text
    for label in ('Tất cả tin', 'Cần trả lời', 'Chờ gửi khách', 'Đơn &amp; phiếu nháp'):
        assert label in r.text, label
    assert 'Trả lời tự động' not in r.text.split('class="role-nav"', 1)[1].split('</nav>', 1)[0]  # chỉ giám đốc
    r = client.get('/', headers=ZALO, follow_redirects=False)
    assert r.headers['location'] == '/zalo/hop-thu'
    r = client.get('/don-ban', headers={'host': 'zalo.testserver', 'sec-fetch-dest': 'document'}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == 'http://testserver/don-ban'
    assert 'Thành Đức Zalo' not in client.get('/zalo/hop-thu').text  # tên miền chính giữ nguyên
