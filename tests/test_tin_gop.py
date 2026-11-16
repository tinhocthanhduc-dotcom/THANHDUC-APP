"""Tin gộp giám đốc (đợt 2 rà soát, TUẤN duyệt mẫu 16/11/2026)."""
from datetime import datetime

from sqlalchemy import select

from app import nhan_vien_ao as nva, tin_gop, zalo_bot
from app.models import ZaloChat, ZaloMessage
from app.service_sla import TZ
from test_dashboard import people  # noqa: F401
from test_purchase_reports import report_people  # noqa: F401
from test_supplier_debts import m3_people  # noqa: F401
from test_purchase_orders import purchase_people  # noqa: F401


def _link(db, user, chat_id):
    chat = ZaloChat(chat_id=chat_id, chat_type='PRIVATE', display_name=user.full_name, user_id=user.id)
    db.add(chat)
    db.commit()
    return chat


def _at(h, m):
    return datetime(2026, 11, 17, h, m, tzinfo=TZ)


def test_director_scheduled_messages_held_then_sent_once_per_slot(db, people, monkeypatch):
    p = people
    boss = _link(db, p['director'], 'zalo-gd')
    staff = _link(db, p['sales'], 'zalo-nv')
    calls = []
    monkeypatch.setattr(zalo_bot, 'call', lambda method, payload=None, timeout=15: calls.append(payload) or {'message_id': str(len(calls))})
    # Tin theo giờ gửi giám đốc: giữ lại, không gọi Zalo.
    a = zalo_bot.send_text(db, boss, 'BÁO CÁO SÁNG\n5 số', None, 'bao_cao_sang')
    b = zalo_bot.send_text(db, boss, 'Chấm công: Phong chưa vào ca', None, 'nhan_su_bao')
    assert a.status == b.status == 'CHO_GOP' and calls == []
    # Tin gấp và tin gửi nhân viên vẫn đi ngay.
    zalo_bot.send_text(db, boss, 'Trễ hẹn lâu', None, 'nhan_vien_ao')
    zalo_bot.send_text(db, staff, 'Báo cáo sáng nhân viên', None, 'bao_cao_sang')
    assert len(calls) == 2
    # Chưa tới mốc (08:31 < 08:30 + 2 phút): chưa gửi.
    assert tin_gop.run_once(db, _at(8, 31)) == 0
    assert tin_gop.run_once(db, _at(8, 33)) == 1
    text = calls[-1]['text']
    assert calls[-1]['chat_id'] == 'zalo-gd' and text.startswith('TIN GỘP lúc 08:30 · 17/11 (2 mục)')
    assert 'BÁO CÁO SÁNG' in text and 'Phong chưa vào ca' in text and text.index('BÁO CÁO') < text.index('Phong')
    assert {m.status for m in db.scalars(select(ZaloMessage).where(ZaloMessage.id.in_((a.id, b.id))))} == {'DA_GOP'}
    # Cùng mốc không gửi lại; tin giữ sau đó chờ mốc 13:45.
    zalo_bot.send_text(db, boss, 'Sản xuất treo', None, 'san_xuat')
    assert tin_gop.run_once(db, _at(8, 40)) == 0
    assert tin_gop.run_once(db, _at(13, 48)) == 1 and 'Sản xuất treo' in calls[-1]['text']


def test_switch_off_sends_held_now_and_stops_holding(db, people, monkeypatch):
    p = people
    boss = _link(db, p['director'], 'zalo-gd')
    calls = []
    monkeypatch.setattr(zalo_bot, 'call', lambda method, payload=None, timeout=15: calls.append(payload) or {'message_id': 'x' + str(len(calls))})
    zalo_bot.send_text(db, boss, 'Tóm tắt cuối ngày', None, 'tom_tat_doi')
    rows = nva.rows(db)
    rows['TIN_GOP'].enabled = False
    db.commit()
    assert tin_gop.run_once(db, _at(10, 0)) == 1 and 'Tóm tắt cuối ngày' in calls[-1]['text']
    sent = zalo_bot.send_text(db, boss, 'Báo cáo sáng', None, 'bao_cao_sang')
    assert sent.status == 'DA_GUI' and calls[-1]['text'] == 'Báo cáo sáng'


def test_mark_kind_from_agents(db, people, monkeypatch):
    p = people
    boss = _link(db, p['director'], 'zalo-gd')
    got = []
    send = lambda session, chat, body, user, kind: got.append(kind)  # noqa: E731
    nva._send(db, boss, p['director'].id, 'nsa-cc:2026-11-17:0830', 'x', send)
    nva._send(db, boss, p['director'].id, 'tn-cuoi:2026-11-17', 'y', send)
    nva._send(db, boss, p['director'].id, 'nva-gd:abc', 'z', send)
    assert got == ['nhan_su_bao', 'tom_tat_doi', 'nhan_vien_ao']
