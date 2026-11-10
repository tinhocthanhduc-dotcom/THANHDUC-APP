"""Báo cáo sáng qua Zalo cho giám đốc (TUẤN duyệt mẫu 05/11/2026)."""
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app import dashboard, finance_reports, morning_report as svc, sales_reports, zalo_bot
from app.models import AttendanceHoliday, DispatchZaloSent, FundLedger, StockLedger, ZaloChat, ZaloMessage
from test_dashboard import people, fee  # noqa: F401
from test_purchase_reports import report_people  # noqa: F401
from test_supplier_debts import m3_people  # noqa: F401
from test_purchase_orders import purchase_people  # noqa: F401
from test_purchase_reports import completed, received
from test_web import service_login

MONDAY = date(2026, 10, 19)          # dữ liệu fixture ghi lúc 12:00 Chủ nhật 18/10
AT = datetime(2026, 10, 19, 7, 31, tzinfo=svc.TZ)


def _on(monkeypatch, **kw):
    settings = svc.get_settings().model_copy(update={'zalo_bot_token': 'token', 'morning_report': True, **kw})
    monkeypatch.setattr(svc, 'get_settings', lambda: settings)
    from app.web import zalo as web_zalo
    monkeypatch.setattr(web_zalo, 'get_settings', lambda: settings)
    return settings


def _link(db, user, chat_id='zalo-tuan'):
    chat = ZaloChat(chat_id=chat_id, chat_type='PRIVATE', display_name=user.full_name, user_id=user.id)
    db.add(chat)
    db.commit()
    return chat


def test_monday_covers_saturday_and_sunday_and_holiday_is_skipped(db, people):
    assert svc.period(db, MONDAY) == (date(2026, 10, 17), date(2026, 10, 18))
    assert svc.period(db, date(2026, 10, 21)) == (date(2026, 10, 20), date(2026, 10, 20))
    db.add(AttendanceHoliday(day=date(2026, 10, 20), name='Nghỉ lễ'))
    db.commit()
    assert svc.period(db, date(2026, 10, 21)) == (date(2026, 10, 19), date(2026, 10, 20))
    assert not svc.is_workday(db, date(2026, 10, 20)) and not svc.is_workday(db, date(2026, 10, 18))


def test_numbers_come_from_existing_reports_and_nothing_is_written(db, people):
    p = people
    received(db, p, qty=30, vat=0)
    fee(db, p, 250000)
    completed(db, p)
    before = [db.query(m).count() for m in (StockLedger, FundLedger)]
    d = svc.build(db, p['director'], MONDAY, AT)
    start, end = date(2026, 10, 17), date(2026, 10, 18)
    sales = sales_reports.totals(db, p['director'], start, end)
    assert d['sales_revenue'] == sales['subtotal'] == 250000 and d['orders'] == sales['orders'] == 1
    assert d['service_revenue'] > 0 and d['services_done'] == 1
    assert d['revenue'] == d['sales_revenue'] + d['service_revenue']
    cash = finance_reports.category_totals(db, p['director'], start, end)
    assert (d['income'], d['expense']) == (cash['income'], cash['expense'])
    # Tháng 10 đến hết Chủ nhật 18/10 = doanh thu Tổng quan của ngày 18/10.
    assert d['month_revenue'] == dashboard.summary(db, p['director'], end)['revenue_month']
    assert d['debt_total'] == finance_reports.debt_totals(db, p['director'])['remaining']
    assert d['margin'] is not None
    assert [db.query(m).count() for m in (StockLedger, FundLedger)] == before

    text = svc.render_text(d, 'https://vd.test')
    assert d['margin_day'] is not None and d['margin_day_rate'] is not None
    for part in ('5 SỐ SÁNG T2 19/10 · số T7 17/10–CN 18/10', '1️⃣ Doanh thu:', '   Tháng 10:', '2️⃣ Lãi gộp:',
                 '3️⃣ Tiền đã thu:', '4️⃣ Nợ quá hạn', '5️⃣ Việc trễ', '──────────', '🏦 TIỀN & CÔNG NỢ', 'Quỹ:', 'Nợ NCC đến hạn 7 ngày tới', '🔧 DỊCH VỤ', 'Lịch hẹn hôm nay',
                 'Xem đủ: https://vd.test/tong-quan'):
        assert part in text, part
    assert 'KHO' not in text and 'CẦN ANH XEM' not in text
    assert len(text) <= zalo_bot.MAX_TEXT


def test_render_short_forms_and_empty_lines():
    d = dict(today=date(2026, 11, 6), start=date(2026, 11, 5), end=date(2026, 11, 5), revenue=18450000, sales_revenue=12300000,
             service_revenue=6150000, orders=9, services_done=7, income=21700000, expense=8200000,
             month_start=date(2026, 11, 1), month_revenue=86900000, prev_revenue=77600000, prev_month=date(2026, 10, 1),
             margin=24100000, margin_rate=27.7, margin_day=4620000, margin_day_rate=25.04, fund_cash=18200000, fund_bank=124300000, pending_count=0, pending_amount=0,
             debt_total=96400000, overdue_total=31200000,
             ncc_overdue=0, ncc_week=22000000, open=14, late=1,
             late_rows=[dict(number='DV-2611-0412', customer='Cty Sao Mai', tech='Đoàn Cảnh Phong', minutes=2 * 24 * 60 + 5)],
             appointments=6, appointments_free=1)
    text = svc.render_text(d)
    assert text.startswith('☀️ 5 SỐ SÁNG T6 06/11 · số T5 05/11\n\n'
                           '1️⃣ Doanh thu: 18.450.000đ\n'
                           '   Tháng 11: 86,9tr (+12% cùng kỳ T10)\n'
                           '2️⃣ Lãi gộp: 4.620.000đ (25%)\n'
                           '   Tháng 11: 24,1tr (28%)\n'
                           '3️⃣ Tiền đã thu: 21.700.000đ\n'
                           '   Chờ KT xác nhận: không có\n'
                           '4️⃣ Nợ quá hạn: 31.200.000đ\n'
                           '   Tổng khách nợ: 96,4tr\n'
                           '5️⃣ Việc trễ: 1 phiếu quá hẹn\n'
                           '   Cũ nhất: DV-2611-0412 Cty Sao Mai trễ 2 ngày (Phong)\n'
                           '──────────\n💰 HÔM QUA\n')
    assert '· Bán hàng 12.300.000 · Dịch vụ 6.150.000' in text
    assert '· Thực chi: 8.200.000đ' in text
    assert 'Quỹ: 142.500.000đ (TM 18,2tr · NH 124,3tr)' in text
    assert 'Nợ NCC đến hạn 7 ngày tới: 22.000.000đ' in text
    assert '· DV-2611-0412 Cty Sao Mai – trễ 2 ngày (Phong)' in text
    assert 'Lịch hẹn hôm nay: 6 lượt (1 chưa giao thợ)' in text
    # Không trùng dòng với 5 số, không liệt kê tên khách nợ.
    assert text.count('Doanh thu') == 1 and 'Khách nợ:' not in text and '📈' not in text

    # Không có lãi gộp (không phải giám đốc), không nợ quá hạn, không việc trễ, có báo thu chờ.
    d2 = dict(d, margin=None, margin_rate=None, margin_day=None, margin_day_rate=None, overdue_total=0, late=0, late_rows=[],
              pending_count=2, pending_amount=3200000, prev_revenue=0)
    text = svc.render_text(d2)
    assert '2️⃣' not in text and '   Tháng 11: 86,9tr\n3️⃣' in text
    assert '   Chờ KT xác nhận: 2 phiếu · 3,2tr' in text
    assert '4️⃣ Nợ quá hạn: không có' in text and '5️⃣ Việc trễ: không có phiếu quá hẹn' in text

def test_sends_once_per_day_only_to_linked_director_in_window(db, people, monkeypatch):
    p = people
    _on(monkeypatch)
    _link(db, p['director'])
    _link(db, p['sales'], 'zalo-thanh')     # nhân viên khác đã gắn Zalo: không nhận báo cáo
    sent = []
    send = lambda *a: sent.append(a)  # noqa: E731
    assert svc.run_once(db, now=AT.replace(hour=7, minute=29), sender=send) == 0      # trước giờ gửi
    assert svc.run_once(db, now=AT.replace(hour=10, minute=1), sender=send) == 0      # quá trễ, không gửi giữa trưa
    assert svc.run_once(db, now=AT - timedelta(days=1), sender=send) == 0            # Chủ nhật
    assert svc.run_once(db, now=AT, sender=send) == 1
    assert sent[0][1].user_id == p['director'].id and sent[0][3] is None and sent[0][4] == 'bao_cao_sang'
    assert '5 SỐ SÁNG T2 19/10' in sent[0][2]
    assert svc.run_once(db, now=AT + timedelta(minutes=5), sender=send) == 0         # mỗi ngày một tin
    assert db.scalar(select(func.count()).select_from(DispatchZaloSent)) == 1
    # Ngày lễ không gửi; tắt cấu hình hoặc thiếu token cũng không gửi.
    db.add(AttendanceHoliday(day=date(2026, 10, 20), name='Nghỉ lễ'))
    db.commit()
    assert svc.run_once(db, now=AT + timedelta(days=1), sender=send) == 0
    _on(monkeypatch, morning_report=False)
    assert svc.run_once(db, now=AT + timedelta(days=2), sender=send) == 0
    _on(monkeypatch, zalo_bot_token='')
    assert svc.run_once(db, now=AT + timedelta(days=2), sender=send) == 0
    _on(monkeypatch)
    assert svc.run_once(db, now=AT + timedelta(days=2), sender=send) == 1


def test_preview_page_and_test_send_director_only(db, people, client, monkeypatch):
    p = people
    _link(db, p['director'])
    _on(monkeypatch)
    calls = []
    monkeypatch.setattr(zalo_bot, 'call', lambda method, payload=None, timeout=15: calls.append((method, payload)) or {'message_id': 'm1'})
    service_login(client, p['sales'])
    assert client.get('/zalo/bao-cao-sang').status_code == 403
    csrf = service_login(client, p['director'])
    page = client.get('/zalo/bao-cao-sang')
    assert page.status_code == 200 and '5 SỐ SÁNG' in page.text and 'Gửi thử cho tôi qua Zalo' in page.text
    from test_web import csrf_of
    csrf = csrf_of(page.text) or csrf
    form_key = page.text.split('name="form_key" value="')[1].split('"')[0]
    r = client.post('/zalo/bao-cao-sang/gui-thu', data={'csrf': csrf, 'form_key': form_key}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].endswith('gui=ok')
    assert calls and calls[0][0] == 'sendMessage' and calls[0][1]['chat_id'] == 'zalo-tuan'
    row = db.scalar(select(ZaloMessage).order_by(ZaloMessage.id.desc()))
    assert row.kind == 'bao_cao_sang' and row.status == 'DA_GUI'
    # Gửi thử không ghi mốc, 7:30 hôm sau vẫn gửi bình thường.
    assert db.scalar(select(func.count()).select_from(DispatchZaloSent)) == 0



def test_overview_shows_five_numbers_for_director_only(db, people, client):
    p = people
    fee(db, p, 250000)
    service_login(client, p['director'])
    page = client.get('/tong-quan')
    assert page.status_code == 200 and '5 số chính · số' in page.text and 'Nợ quá hạn' in page.text and 'Việc trễ' in page.text
    service_login(client, p['finance'])      # kế toán xem Tổng quan nhưng không có khối 5 số
    page = client.get('/tong-quan')
    assert page.status_code == 200 and '5 số chính' not in page.text
