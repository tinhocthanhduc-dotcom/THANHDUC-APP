"""Nhắc nợ khách quá hạn qua Zalo cho kế toán, sáng thứ Hai (TUẤN duyệt mẫu 06/11/2026)."""
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app import debt_reminder as svc, zalo_bot
from app.models import (AttendanceHoliday, Customer, CustomerOpeningDebt, CustomerPhone, DispatchZaloSent, FundLedger,
                        StockLedger, ZaloChat, ZaloMessage)
from test_dashboard import people  # noqa: F401
from test_purchase_reports import report_people  # noqa: F401
from test_supplier_debts import m3_people  # noqa: F401
from test_purchase_orders import purchase_people  # noqa: F401
from test_web import service_login

MONDAY = date(2026, 11, 9)
AT = datetime(2026, 11, 9, 7, 46, tzinfo=svc.TZ)


def _on(monkeypatch, **kw):
    settings = svc.get_settings().model_copy(update={'zalo_bot_token': 'token', 'debt_reminder': True, **kw})
    monkeypatch.setattr(svc, 'get_settings', lambda: settings)
    from app.web import zalo as web_zalo
    monkeypatch.setattr(web_zalo, 'get_settings', lambda: settings)
    return settings


def _link(db, user, chat_id):
    chat = ZaloChat(chat_id=chat_id, chat_type='PRIVATE', display_name=user.full_name, user_id=user.id)
    db.add(chat)
    db.commit()
    return chat


def _debts(db, director):
    for code, name, amount, days, phone in (('KN-1', 'Cty An Phát', 12_500_000, 74, '0903123456'),
                                            ('KN-2', 'Trường MN Hoa Sen', 8_200_000, 52, ''),
                                            ('KN-3', 'Khách mới nợ', 3_000_000, 10, '')):
        db.add(Customer(code=code, name=name))
        db.flush()
        if phone:
            db.add(CustomerPhone(customer_code=code, phone=phone))
        db.add(CustomerOpeningDebt(number='NDK-' + code, customer_code=code, amount=amount,
                                   as_of=MONDAY - timedelta(days=days), created_by=director.id))
    db.commit()


def test_list_matches_debt_page_tab_and_writes_nothing(db, people):
    p = people
    _debts(db, p['director'])
    before = [db.query(m).count() for m in (StockLedger, FundLedger)]
    d = svc.build(db, p['finance'], MONDAY)
    from app.web.customer_debts import _rows
    tab = [r for r in _rows(db, p['finance'], MONDAY) if r['old']]
    assert [i['name'] for i in d['items']] == ['Cty An Phát', 'Trường MN Hoa Sen']      # nợ 10 ngày không vào
    assert d['total'] == sum(r['unpaid'] for r in tab) == 20_700_000 and d['oldest'] == 74
    assert [db.query(m).count() for m in (StockLedger, FundLedger)] == before
    text = svc.render_text(d, 'https://vd.test')
    assert text.startswith('📒 NHẮC NỢ QUÁ HẠN · T2 09/11\nKhách chưa trả quá 30 ngày: 2 khách\nTổng: 20.700.000đ · cũ nhất 74 ngày')
    assert '1. Cty An Phát – 12.500.000đ\n   74 ngày · 0903123456 · NDK-KN-1' in text
    assert '2. Trường MN Hoa Sen – 8.200.000đ\n   52 ngày · NDK-KN-2' in text
    assert text.endswith('Báo thu: https://vd.test/cong-no-khach-hang?tab=qua-han')


def test_render_caps_lines_and_length():
    items = [dict(name=f'Khách số {n} có tên khá dài để thử cắt', amount=1_000_000 * (40 - n), days=40 + n,
                  phone='0900000000', docs=['DB-1', 'DV-2', 'NDK-3', 'DB-4']) for n in range(30)]
    d = dict(today=MONDAY, days=30, items=items, total=sum(i['amount'] for i in items), oldest=69, pending=1_500_000)
    text = svc.render_text(d)
    assert len(text) <= zalo_bot.MAX_TEXT
    assert '10. ' in text and '11. ' not in text and '+20 khách nữa, xem đủ trong app' in text
    assert 'DB-1, DV-2, NDK-3 +1' in text and 'Đã thu, đang ở nhân viên: 1.500.000đ' in text
    empty = svc.render_text(dict(today=MONDAY, days=30, items=[], total=0, oldest=0, pending=0))
    assert 'Không có khách chưa trả quá 30 ngày.' in empty


def test_sends_once_a_week_to_linked_accountant_only(db, people, monkeypatch):
    p = people
    _on(monkeypatch)
    _debts(db, p['director'])
    _link(db, p['director'], 'zalo-tuan')       # giám đốc không nhận tin này
    _link(db, p['finance'], 'zalo-thanh')
    _link(db, p['sales'], 'zalo-sales')         # không có quyền kế toán
    sent = []
    send = lambda *a: sent.append(a)  # noqa: E731
    assert svc.run_once(db, now=AT.replace(hour=7, minute=44), sender=send) == 0
    assert svc.run_once(db, now=AT.replace(hour=10, minute=1), sender=send) == 0
    assert svc.run_once(db, now=AT + timedelta(days=1), sender=send) == 0           # thứ Ba
    assert svc.run_once(db, now=AT, sender=send) == 1
    assert sent[0][1].user_id == p['finance'].id and sent[0][4] == 'nhac_no' and 'Cty An Phát' in sent[0][2]
    assert svc.run_once(db, now=AT + timedelta(minutes=30), sender=send) == 0
    assert db.scalar(select(func.count()).select_from(DispatchZaloSent)) == 1
    # Thứ Hai tuần sau nghỉ lễ: gửi thứ Ba, không gửi thứ Tư.
    db.add(AttendanceHoliday(day=MONDAY + timedelta(days=7), name='Nghỉ lễ'))
    db.commit()
    assert svc.run_once(db, now=AT + timedelta(days=7), sender=send) == 0
    assert svc.run_once(db, now=AT + timedelta(days=8), sender=send) == 1
    assert svc.run_once(db, now=AT + timedelta(days=9), sender=send) == 0
    _on(monkeypatch, debt_reminder=False)
    assert svc.run_once(db, now=AT + timedelta(days=14), sender=send) == 0
    _on(monkeypatch, zalo_bot_token='')
    assert svc.run_once(db, now=AT + timedelta(days=14), sender=send) == 0


def test_preview_and_test_send_director_only(db, people, client, monkeypatch):
    p = people
    chat = _link(db, p['finance'], 'zalo-thanh')
    _on(monkeypatch)
    calls = []
    monkeypatch.setattr(zalo_bot, 'call', lambda method, payload=None, timeout=15: calls.append((method, payload)) or {'message_id': 'm1'})
    service_login(client, p['finance'])
    assert client.get('/zalo/nhac-no').status_code == 403
    service_login(client, p['director'])
    page = client.get('/zalo/nhac-no')
    assert page.status_code == 200 and 'NHẮC NỢ QUÁ HẠN' in page.text and f"Gửi thử cho {p['finance'].full_name}" in page.text
    from test_web import csrf_of
    csrf = csrf_of(page.text)
    form_key = page.text.split('name="form_key" value="')[1].split('"')[0]
    r = client.post('/zalo/nhac-no/gui-thu', data={'csrf': csrf, 'form_key': form_key, 'chat': str(chat.id)}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].endswith('gui=ok')
    assert calls[0][1]['chat_id'] == 'zalo-thanh'
    assert db.scalar(select(ZaloMessage).order_by(ZaloMessage.id.desc())).kind == 'nhac_no'
    assert db.scalar(select(func.count()).select_from(DispatchZaloSent)) == 0
