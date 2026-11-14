"""Khách lâu chưa gọi mực (TUẤN duyệt mẫu 06/11/2026): nhịp theo các lần lấy mực, ghi kết quả gọi tạm ẩn khách."""
from datetime import date, datetime, timedelta

from app import ink_rhythm, weekly_zalo
from app.models import CustomerInkCall, DispatchZaloSent, ServiceTicket, StockLedger, FundLedger, ZaloChat
from app.web import customer_ink
from test_muc_nhan_don import form
from test_my_work import people, purchase_people  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_web import service_login

TZ = ink_rhythm.TZ
TODAY = date(2026, 11, 9)   # thứ Hai


def _tickets(client, db, p, days_ago=(100, 70, 40)):
    csrf = service_login(client, p['dispatcher'])
    for n in days_ago:
        assert client.post('/dich-vu/moi', data=form(csrf), follow_redirects=False).status_code == 303
        t = db.query(ServiceTicket).order_by(ServiceTicket.id.desc()).first()
        t.created_at = datetime.combine(TODAY - timedelta(days=n), datetime.min.time(), TZ) + timedelta(hours=9)
        t.status, t.assignee_id = 'DA_DONG', p['technician'].id
    db.commit()


def test_rhythm_needs_three_distinct_days_and_uses_median():
    d = date(2026, 1, 1)
    assert ink_rhythm.rhythm([d, d + timedelta(days=30)]) is None
    assert ink_rhythm.rhythm([d, d, d + timedelta(days=30)]) is None
    assert ink_rhythm.rhythm([d, d + timedelta(days=30), d + timedelta(days=60), d + timedelta(days=150)]) == 30
    assert ink_rhythm.rhythm([d, d + timedelta(days=2), d + timedelta(days=4)]) == ink_rhythm.MIN_RHYTHM


def test_soon_then_overdue_and_open_ticket_hides(client, db, held):  # noqa: F811
    p, a, b = held
    _tickets(client, db, p)
    data = ink_rhythm.collect(db, TODAY)                               # 40 ngày, nhịp 30, ngưỡng 45
    assert [r['code'] for r in data['sap']] == ['KTD-000001'] and data['goi'] == []
    data = ink_rhythm.collect(db, TODAY + timedelta(days=10))
    row = data['goi'][0]
    assert row['rhythm'] == 30 and row['since'] == 50 and row['over'] == 20 and row['times'] == 3 and row['ink']
    # Khách đang có phiếu mực mở thì không hiện.
    t = db.query(ServiceTicket).order_by(ServiceTicket.id.desc()).first()
    t.status = 'DANG_LAM'
    db.commit()
    assert ink_rhythm.collect(db, TODAY + timedelta(days=10))['goi'] == []


def test_page_and_call_result_hides_then_returns(client, db, held, monkeypatch):  # noqa: F811
    p, a, b = held
    _tickets(client, db, p)
    later = TODAY + timedelta(days=10)
    monkeypatch.setattr(customer_ink, '_today', lambda: later)
    before = [db.query(m).count() for m in (StockLedger, FundLedger)]
    csrf = service_login(client, p['sales'])
    html = client.get('/khach-hang/nhac-muc').text
    assert 'Khách lâu chưa gọi mực' in html and 'Minh Phát' in html and 'quá 20 ngày' in html
    assert 'Tạo phiếu mực' in html              # TUẤN 06/11: kinh doanh được nhận đơn Mực định kỳ
    r = client.post('/khach-hang/nhac-muc/KTD-000001/goi', data=dict(csrf=csrf, result='HEN_LAI', note='Hẹn thứ Hai', tab='goi'), follow_redirects=False)
    assert r.status_code == 303 and 'da_luu=' in r.headers['location']
    call = db.query(CustomerInkCall).one()
    assert call.result == 'HEN_LAI' and call.called_by == p['sales'].id and call.note == 'Hẹn thứ Hai'
    call.called_at = datetime.combine(later, datetime.min.time(), TZ) + timedelta(hours=9)
    db.commit()
    data = ink_rhythm.collect(db, later)
    assert data['goi'] == [] and data['hen'][0]['call']['label'] == 'Hẹn gọi lại'
    assert ink_rhythm.collect(db, later + timedelta(days=7))['goi']                 # 7 ngày sau hiện lại
    # Khách đặt mực: mở sẵn màn nhận đơn với khách này.
    r = client.post('/khach-hang/nhac-muc/KTD-000001/goi', data=dict(csrf=csrf, result='DAT_MUC', tab='goi'), follow_redirects=False)
    assert r.headers['location'] == '/dich-vu/moi?customer_code=KTD-000001'
    # Chuyển nơi khác: sang tab riêng.
    client.post('/khach-hang/nhac-muc/KTD-000001/goi', data=dict(csrf=csrf, result='CHUYEN_NOI_KHAC', tab='goi'))
    lost = db.query(CustomerInkCall).filter_by(result='CHUYEN_NOI_KHAC').one()
    lost.called_at = datetime.combine(later + timedelta(days=1), datetime.min.time(), TZ) + timedelta(hours=9)
    db.commit()
    assert [r['code'] for r in ink_rhythm.collect(db, later + timedelta(days=60))['mat']] == ['KTD-000001']
    assert [db.query(m).count() for m in (StockLedger, FundLedger)] == before


def test_technician_cannot_open_or_write(client, db, held):  # noqa: F811
    p, a, b = held
    csrf = service_login(client, p['technician'])
    assert client.get('/khach-hang/nhac-muc', follow_redirects=False).status_code in (303, 403)
    client.post('/khach-hang/nhac-muc/KTD-000001/goi', data=dict(csrf=csrf, result='HEN_LAI'), follow_redirects=False)
    assert db.query(CustomerInkCall).count() == 0


def test_monday_zalo_to_sales_once(client, db, held, monkeypatch):  # noqa: F811
    p, a, b = held
    _tickets(client, db, p, days_ago=(110, 80, 50))
    settings = weekly_zalo.get_settings().model_copy(update={'zalo_bot_token': 'token'})
    monkeypatch.setattr(weekly_zalo, 'get_settings', lambda: settings)
    db.add(ZaloChat(chat_id='c-sales', chat_type='PRIVATE', display_name='Thanh', user_id=p['sales'].id))
    db.commit()
    sent = []
    sender = lambda session, chat, text, user, kind: sent.append((chat.chat_id, text, kind))  # noqa: E731
    assert weekly_zalo.run_once(db, now=datetime(2026, 11, 9, 7, 50, tzinfo=TZ), sender=sender) == 0
    assert weekly_zalo.run_once(db, now=datetime(2026, 11, 9, 8, 5, tzinfo=TZ), sender=sender) == 1
    assert sent[0][0] == 'c-sales' and sent[0][2] == 'nhac_muc' and 'Minh Phát: quá 20 ngày' in sent[0][1]
    assert weekly_zalo.run_once(db, now=datetime(2026, 11, 9, 9, 0, tzinfo=TZ), sender=sender) == 0
    assert weekly_zalo.run_once(db, now=datetime(2026, 11, 10, 8, 5, tzinfo=TZ), sender=sender) == 0    # thứ Ba không gửi
    assert db.query(DispatchZaloSent).filter(DispatchZaloSent.mark == 'nhac-muc:2026-11-09').count() == 1


def test_sales_creates_ink_ticket_only(client, db, held):  # noqa: F811
    """TUẤN 06/11/2026: Thanh (kinh doanh) tự nhận đơn Mực định kỳ, không vào Phiếu dịch vụ, không lập loại khác."""
    p, a, b = held
    csrf = service_login(client, p['sales'])
    page = client.get('/dich-vu/moi?customer_code=KTD-000001').text
    assert 'name="kind" value="MUC_DINH_KY" checked' in page and 'value="SUA_TAI_CHO"' not in page
    assert client.get('/dich-vu/chon-khach?customer_code=KTD-000001').status_code == 200
    assert client.get('/dich-vu', follow_redirects=False).status_code == 403
    before = db.query(ServiceTicket).count()
    r = client.post('/dich-vu/moi', data=form(csrf, kind='SUA_TAI_CHO', symptom='Máy kẹt giấy'), follow_redirects=False)
    assert r.status_code == 400 and 'chỉ nhận đơn Mực định kỳ' in r.text and db.query(ServiceTicket).count() == before
    r = client.post('/dich-vu/moi', data=form(csrf), follow_redirects=False)
    t = db.query(ServiceTicket).order_by(ServiceTicket.id.desc()).first()
    assert r.status_code == 303 and r.headers['location'] == f'/khach-hang/nhac-muc?da_tao={t.number}'
    assert t.kind == 'MUC_DINH_KY' and t.created_by == p['sales'].id
    assert f'Đã nhận đơn {t.number}' in client.get(r.headers['location']).text
    assert client.get(f'/dich-vu/{t.id}', follow_redirects=False).status_code == 403
