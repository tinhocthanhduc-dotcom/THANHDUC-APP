"""Trả lời tự động Zalo (TUẤN duyệt mẫu 19/11/2026, đợt 3).

Mặc định tắt. Chỉ hội thoại riêng của khách/người lạ. Mỗi tin khách tối đa một tin tự động, không gửi khi đã có người trả lời.
Tin có giá không bao giờ tự gửi. Chỉ giám đốc cài.
"""
from datetime import datetime

import pytest
from sqlalchemy import select

from app import zalo_tu_tra_loi as svc
from app.models import Customer, ZaloChat, ZaloMessage, ZaloReplyDraft, ZaloTriage, ZaloTroLy
from app.service_sla import TZ
from test_web import service_login
from test_zalo_tin_nhan import zalo  # noqa: F401

WORK = datetime(2026, 11, 18, 10, 0, tzinfo=TZ)     # Thứ Tư, trong giờ
NIGHT = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)    # Thứ Tư tối
LUNCH = datetime(2026, 11, 18, 12, 0, tzinfo=TZ)
SUNDAY = datetime(2026, 11, 22, 9, 0, tzinfo=TZ)
FK = '00000000-0000-4000-8000-0000000001{:02d}'


@pytest.fixture
def setup(db, make_user, monkeypatch):
    monkeypatch.setattr(svc, '_now', lambda: NIGHT)
    db.add(ZaloTroLy(id=1, enabled=False, auto_on=True))
    db.add(Customer(code='KTD-000001', name='Công ty Hưng Phát', search_text='ktd-000001 cong ty hung phat'))
    staff = make_user('tho.b', 'technician')
    db.flush()
    chats = dict(khach=ZaloChat(chat_id='c-k', chat_type='PRIVATE', customer_code='KTD-000001'),
                 la=ZaloChat(chat_id='c-l', chat_type='PRIVATE'),
                 nv=ZaloChat(chat_id='c-n', chat_type='PRIVATE', user_id=staff.id),
                 nhom=ZaloChat(chat_id='g-n', chat_type='GROUP'))
    db.add_all(chats.values())
    db.commit()
    return chats


def tin(db, chat, text, at):
    m = ZaloMessage(chat_pk=chat.id, direction='IN', kind='text', text=text, status='DA_NHAN', message_at=at)
    db.add(m)
    db.commit()
    return m


def outs(db, chat):
    return [m.text for m in db.scalars(select(ZaloMessage).where(ZaloMessage.chat_pk == chat.id, ZaloMessage.direction == 'OUT').order_by(ZaloMessage.id))]


def test_ngoai_gio_bao_mot_lan_moi_12_gio(db, setup, zalo):
    chat = setup['khach']
    assert svc.on_message(db, tin(db, chat, 'Máy in kẹt giấy', NIGHT).id) == 'gui'
    assert outs(db, chat) == [svc.OFF_HOURS_TEXT.format(back='07:30 sáng mai')]
    assert svc.on_message(db, tin(db, chat, 'Alo shop', NIGHT).id) == ''
    assert len(outs(db, chat)) == 1


def test_gio_trua_va_chu_nhat(db, setup):
    assert svc.back_at(db, LUNCH).strftime('%H:%M') == '13:30'
    assert svc.back_at(db, WORK) is None
    back = svc.back_at(db, SUNDAY)
    assert back.astimezone(TZ).date().isoformat() == '2026-11-23' and back.astimezone(TZ).strftime('%H:%M') == '07:30'


def test_tat_chung_thi_khong_lam_gi(db, setup, zalo):
    db.get(ZaloTroLy, 1).auto_on = False
    db.commit()
    assert svc.on_message(db, tin(db, setup['khach'], 'alo', NIGHT).id) == ''
    assert zalo['sent'] == []


def test_khong_tra_loi_nhom_nhan_vien_va_tin_da_co_nguoi_tra_loi(db, setup, zalo):
    for key in ('nv', 'nhom'):
        assert svc.on_message(db, tin(db, setup[key], 'alo', NIGHT).id) == ''
    chat = setup['la']
    m = tin(db, chat, 'alo', WORK)
    db.add(ZaloMessage(chat_pk=chat.id, direction='OUT', kind='text', text='Dạ em nghe', status='DA_GUI', message_at=WORK))
    db.commit()
    assert svc.on_message(db, m.id) == ''
    assert zalo['sent'] == []


def test_nguoi_la_trong_gio_hoi_ten(db, setup, zalo):
    chat = setup['la']
    assert svc.on_message(db, tin(db, chat, 'shop ơi', WORK).id) == 'gui'
    assert outs(db, chat) == [svc.STRANGER_TEXT]


def test_tinh_trang_tu_gui_va_cho_duyet(db, setup, zalo, monkeypatch):
    chat = setup['khach']
    # Không có phiếu/đơn mở: để người trả lời.
    assert svc.on_message(db, tin(db, chat, 'Máy sửa xong chưa em?', WORK).id) == ''
    monkeypatch.setattr(svc, 'status_text', lambda session, code: '\n'.join([svc.STATUS_HEAD, '- DV-1: Hoàn thành.', svc.STATUS_TAIL]))
    assert svc.on_message(db, tin(db, chat, 'Máy sửa xong chưa em?', WORK).id) == 'gui'
    assert 'DV-1: Hoàn thành' in outs(db, chat)[-1]
    assert svc.on_message(db, tin(db, chat, 'cảm ơn em', WORK).id) == ''  # không hỏi tình trạng
    db.get(ZaloTroLy, 1).auto_tinh_trang = 'CHO_DUYET'
    db.commit()
    before = len(zalo['sent'])
    assert svc.on_message(db, tin(db, chat, 'tới đâu rồi em', WORK).id) == 'cho_duyet'
    assert len(zalo['sent']) == before
    draft = db.scalars(select(ZaloReplyDraft).where(ZaloReplyDraft.status == 'XONG')).one()
    assert draft.created_by is None and 'DV-1' in draft.text


def test_tu_khoa_tinh_trang():
    assert svc.asks_status('Máy sửa XONG CHƯA em') and svc.asks_status('tới đâu rồi') and svc.asks_status('khi nào giao')
    assert not svc.asks_status('cho chị báo giá hộp mực 12A') and not svc.asks_status('cảm ơn')


def test_bao_hong_va_hoi_gia_sau_khi_chia_loai(db, setup, zalo, monkeypatch):
    chat = setup['khach']
    m = tin(db, chat, 'máy in kẹt giấy', WORK)
    row = ZaloTriage(message_id=m.id, status='XONG', intent='SUA')
    db.add(row)
    db.commit()
    assert svc.on_triage(db, row) == 'gui'
    assert outs(db, chat)[-1] == svc.BROKEN_TEXT.format(what='sửa máy')
    m2 = tin(db, chat, 'giá hộp mực 12A bao nhiêu', WORK)
    row2 = ZaloTriage(message_id=m2.id, status='XONG', intent='HOI_GIA')
    db.add(row2)
    db.commit()
    assert svc.on_triage(db, row2) == ''  # Z2 không chạy: không soạn, không gửi
    from app import zalo_agent
    db.get(ZaloTroLy, 1).staff_engine = 'claude'
    db.commit()
    monkeypatch.setattr(zalo_agent, 'z2_alive', lambda session: True)
    before = len(zalo['sent'])
    assert svc.on_triage(db, row2) == 'ai_soan'
    assert len(zalo['sent']) == before  # giá không bao giờ tự gửi
    assert db.scalars(select(ZaloReplyDraft).where(ZaloReplyDraft.status == 'DANG')).one().created_by is None


def test_chi_giam_doc_cai_va_luu(client, db, make_user, setup):
    for role in ('sales', 'dispatcher', 'finance'):
        service_login(client, make_user('x.' + role, role))
        assert client.get('/zalo/tu-tra-loi').status_code == 403
    csrf = service_login(client, make_user('gd', 'director'))
    html = client.get('/zalo/tu-tra-loi').text
    assert 'Khách nhắn ngoài giờ làm' in html and 'Không bao giờ tự gửi giá' in html
    form = {'csrf': csrf, 'form_key': FK.format(1), 'auto_on': '1', 'auto_ngoai_gio': 'TU_GUI', 'auto_tinh_trang': 'CHO_DUYET',
            'auto_hoi_gia': 'CHO_DUYET', 'auto_bao_hong': 'TAT', 'auto_nguoi_la': 'TU_GUI'}
    assert client.post('/zalo/tu-tra-loi', data=form, follow_redirects=False).status_code == 303
    db.expire_all()
    cfg = db.get(ZaloTroLy, 1)
    assert (cfg.auto_tinh_trang, cfg.auto_bao_hong) == ('CHO_DUYET', 'TAT')
    bad = {**form, 'form_key': FK.format(2), 'auto_hoi_gia': 'TU_GUI'}  # tin giá không được tự gửi
    assert client.post('/zalo/tu-tra-loi', data=bad).status_code == 422
