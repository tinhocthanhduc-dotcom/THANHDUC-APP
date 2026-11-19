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
