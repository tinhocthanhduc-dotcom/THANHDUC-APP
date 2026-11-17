"""Kế toán tự xác nhận báo thu do mình lập trong hạn mức, giám đốc soát sau (TUẤN duyệt mẫu 17/11/2026).
Hạn mức 0 (mặc định trong code) giữ nguyên luật cũ; thợ không bao giờ tự xác nhận; trên hạn mức chờ giám đốc."""
from datetime import date, datetime

import pytest
from sqlalchemy import select

from app import approval_limits, nhan_vien_ao as nva, sales_orders as orders, sales_receipts as svc, tin_gop, tu_xac_nhan, zalo_bot
from app.errors import BusinessError
from app.models import AuditLog, CashDoc, FundLedger, ZaloChat, ZaloMessage
from app.schemas import SalesOrderIn, SalesReceiptReport
from app.service_sla import TZ
from test_sales_orders import people  # noqa: F401
from test_tk_tai_khoan import web_login

pytestmark = pytest.mark.usefixtures('configured_finance')


@pytest.fixture
def order(db, people, monkeypatch):
    monkeypatch.setattr(svc, '_today', lambda: date(2026, 11, 17))
    o = orders.create(db, people['sales'], SalesOrderIn(customer_code='KTD-000001', lines=[
        dict(kind='KHAC', description='Đơn mười triệu', qty=1, unit_price=10_000_000, vat_rate=0)]))
    orders.confirm(db, people['sales'], o.id, o.version)
    orders.deliver(db, people['sales'], o.id, o.version)
    return o


def report(db, user, o, amount, method='TIEN_MAT'):
    return svc.report(db, user, o.id, SalesReceiptReport(version=o.version, method=method, amount=amount))


def fail(code, fn, *args):
    with pytest.raises(BusinessError) as exc:
        fn(*args)
    assert exc.value.code == code, str(exc.value)
    return exc.value


def set_limit(db, people, value):
    approval_limits.save(db, people['director'], dict(BO_SUNG=1, NGHI=2, TAM_UNG=2_000_000, HUY_HANG=500_000, TU_XAC_NHAN=value))


def test_limit_zero_keeps_old_rule_and_limit_allows_own_receipt(db, people, order, make_user):
    thanh = make_user('thanh.kt', 'sales', 'finance')
    own = report(db, thanh, order, 1_250_000)
    assert approval_limits.limits(db)['TU_XAC_NHAN'] == 0
    assert fail('SELF_CONFIRM', svc.confirm, db, thanh, own.id, own.version).status == 403
    set_limit(db, people, 3_000_000)
    svc.confirm(db, thanh, own.id, own.version)
    db.refresh(own)
    assert own.status == 'DA_XAC_NHAN' and own.decided_by == thanh.id and own.cash_doc_id is not None
    # Tiền vào quỹ và số đã thu y như xác nhận thường.
    assert db.query(CashDoc).count() == 1 and db.query(FundLedger).count() == 1
    assert svc.balance(order)['paid'] == 1_250_000
    log = db.scalar(select(AuditLog).where(AuditLog.action == 'sales_receipt.confirm', AuditLog.entity_id == str(own.id)))
    assert log.user_id == thanh.id and log.reason == svc.SELF_CONFIRM_NOTE
    assert svc.is_self_confirmed(db, own)


def test_over_limit_technician_and_plain_sales_still_blocked(db, people, order, make_user):
    set_limit(db, people, 3_000_000)
    thanh = make_user('thanh.kt', 'sales', 'finance')
    big = report(db, thanh, order, 3_000_001)
    exc = fail('SELF_CONFIRM', svc.confirm, db, thanh, big.id, big.version)
    assert exc.status == 403 and '3.000.000' in exc.message
    edge = report(db, thanh, order, 3_000_000)
    svc.confirm(db, thanh, edge.id, edge.version)
    # Người vừa là thợ vừa là kế toán: không bao giờ tự xác nhận.
    both = make_user('tho.kt', 'sales', 'technician', 'finance')
    mine = report(db, both, order, 100_000)
    fail('SELF_CONFIRM', svc.confirm, db, both, mine.id, mine.version)
    # Bán hàng không có quyền kế toán: vẫn bị chặn như cũ.
    plain = report(db, people['sales'], order, 100_000)
    assert fail('FORBIDDEN', svc.confirm, db, people['sales'], plain.id, plain.version).status == 403
    # Kế toán khác xác nhận phiếu trên hạn mức như cũ; phiếu đó không tính là tự xác nhận.
    svc.confirm(db, people['finance'], big.id, big.version)
    db.refresh(big)
    assert not svc.is_self_confirmed(db, big)
    assert db.query(CashDoc).count() == 2


def test_director_own_receipt_is_not_listed_as_self_confirmed(db, people, order, make_user):
    set_limit(db, people, 3_000_000)
    thanh = make_user('thanh.kt', 'sales', 'finance')
    a = report(db, thanh, order, 800_000)
    b = report(db, people['director'], order, 500_000, 'CHUYEN_KHOAN')
    svc.confirm(db, thanh, a.id, a.version)
    svc.confirm(db, people['director'], b.id, b.version)
    rows = tu_xac_nhan.day_rows(db, datetime.now(TZ).date())
    assert [x['receipt'].id for x in rows] == [a.id]
    assert rows[0]['source'] == order.number and rows[0]['who'] == thanh.full_name
    assert tu_xac_nhan.totals(rows) == dict(n=1, total=800_000, cash=800_000, bank=0)


def test_web_button_tab_and_limit_page(client, db, people, order, make_user):
    thanh = make_user('thanh.kt', 'sales', 'finance')
    own = report(db, thanh, order, 1_250_000, 'CHUYEN_KHOAN')
    big = report(db, thanh, order, 5_400_000)
    csrf = web_login(client, thanh)
    page = client.get('/bao-thu').text
    assert 'data-self-confirm' not in page and 'Bạn lập, người khác xác nhận' in page
    r = client.post(f'/bao-thu/{own.id}/confirm', data=dict(csrf=csrf, version=own.version, origin='queue'))
    assert r.status_code == 403 and 'SELF_CONFIRM' in r.text
    # Giám đốc đặt hạn mức trên trang Hạn mức duyệt.
    csrf = web_login(client, people['director'])
    assert 'name="TU_XAC_NHAN"' in client.get('/han-muc-duyet').text
    r = client.post('/han-muc-duyet', data=dict(csrf=csrf, BO_SUNG='1', NGHI='2', TAM_UNG='2.000.000', HUY_HANG='500.000',
                                                CHI_PHI='500.000', TU_XAC_NHAN='3.000.000'), follow_redirects=False)
    assert r.status_code == 303 and approval_limits.limits(db)['TU_XAC_NHAN'] == 3_000_000
    csrf = web_login(client, thanh)
    page = client.get('/bao-thu').text
    assert 'data-self-confirm' in page and 'Trên hạn mức tự xác nhận' in page
    one = client.get(f'/bao-thu/{own.id}').text
    assert 'data-self-confirm' in one and 'receipt-confirm-%d' % own.id in one
    assert 'trên hạn mức tự xác nhận' in client.get(f'/bao-thu/{big.id}').text.lower()
    r = client.post(f'/bao-thu/{big.id}/confirm', data=dict(csrf=csrf, version=big.version, origin='queue'))
    assert r.status_code == 403 and 'SELF_CONFIRM' in r.text
    r = client.post(f'/bao-thu/{own.id}/confirm', data=dict(csrf=csrf, version=own.version, origin='queue'), follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(type(own), own.id).status == 'DA_XAC_NHAN' and db.get(type(big), big.id).status == 'CHO_XAC_NHAN'
    tab = client.get('/bao-thu?tab=tu-xac-nhan').text
    assert own.number in tab and big.number not in tab and tab.count('data-self-row') == 1 and '1.250.000' in tab
    assert 'data-self-confirmed' in client.get(f'/bao-thu/{own.id}').text
    assert 'data-self-row' not in client.get('/bao-thu?tab=tu-xac-nhan&ngay=2026-11-01').text


def _at(h, m):
    now = datetime.now(TZ)
    return datetime(now.year, now.month, now.day, h, m, tzinfo=TZ)


def test_zalo_note_joins_last_digest_once(db, people, order, make_user, monkeypatch):
    set_limit(db, people, 3_000_000)
    thanh = make_user('thanh.kt', 'sales', 'finance')
    chat = ZaloChat(chat_id='zalo-gd', chat_type='PRIVATE', display_name='GĐ', user_id=people['director'].id)
    db.add(chat)
    db.commit()
    calls = []
    monkeypatch.setattr(zalo_bot, 'call', lambda method, payload=None, timeout=15: calls.append(payload) or {'message_id': str(len(calls))})
    send = zalo_bot.send_text
    # Không có phiếu tự xác nhận: không nhắn.
    assert tu_xac_nhan.run_once(db, _at(17, 48), send) == 0
    a = report(db, thanh, order, 800_000)
    b = report(db, thanh, order, 2_200_000, 'CHUYEN_KHOAN')
    svc.confirm(db, thanh, a.id, a.version)
    svc.confirm(db, thanh, b.id, b.version)
    # Trước mốc cuối ngày (17:45 + 2 phút): chưa nhắn.
    assert tu_xac_nhan.run_once(db, _at(13, 48), send) == 0 and tu_xac_nhan.run_once(db, _at(17, 46), send) == 0
    if datetime.now(TZ) >= _at(17, 47):  # chạy test sau 17:47 thì phiếu vừa xác nhận thuộc cửa sổ ngày mai
        return
    assert tu_xac_nhan.run_once(db, _at(17, 48), send) == 1
    held = db.scalar(select(ZaloMessage).where(ZaloMessage.kind == 'tu_xac_nhan'))
    assert held.status == 'CHO_GOP' and calls == []
    assert '2 phiếu · 3.000.000 đ' in held.text and a.number in held.text and 'Tiền mặt 800.000 · Chuyển khoản 2.200.000' in held.text
    assert tu_xac_nhan.run_once(db, _at(17, 50), send) == 0
    assert tin_gop.run_once(db, _at(17, 48)) == 1 and 'KẾ TOÁN TỰ XÁC NHẬN' in calls[-1]['text']
    # Tắt tin gộp: gửi thẳng, vẫn một lần mỗi ngày.
    assert nva.rows(db)['TIN_GOP'] is not None
