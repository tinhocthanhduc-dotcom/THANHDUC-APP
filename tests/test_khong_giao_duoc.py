"""Thợ không giao được: 3 lựa chọn Khách hẹn lại / Sai địa chỉ giao / Hủy đơn (bán hàng duyệt)."""
import re
from datetime import timedelta

from app import attendance, my_work, notifications, sales_orders, technician_deliveries as svc
from app.models import AuditLog, SalesDeliveryAttempt, SalesOrder
from app.schemas import SalesDeliveryFailed
from test_stock_reservations import people, purchase_people, stock, order, fail  # noqa: F401
from test_technician_deliveries import setup, assigned, move, deliver  # noqa: F401
from test_web import service_login
from web_forms import Forms


def fail_form(client, o):
    page = client.get(f'/giao-don/{o.id}?return_to=tho')
    assert page.status_code == 200
    return page, next(f for f in Forms(page.text, page.url).forms if f['path'].endswith('/failed'))['fields']


def groups(db, user):
    return {g['key']: g for g in my_work.collect(db, user)}


def test_page_shows_three_choices_and_quick_dates(db, setup, client):
    p = setup; o = assigned(db, p); move(db, p, o, 4)
    service_login(client, p['technician'])
    page, _ = fail_form(client, o)
    assert re.findall(r'name="reason" value="(\w+)"', page.text) == ['HEN_LAI', 'SAI_DIA_CHI', 'DE_NGHI_HUY']
    d0 = attendance.today()
    for d in (d0, d0 + timedelta(days=1), d0 + timedelta(days=2)):
        assert f'data-set-date="{d.isoformat()}"' in page.text
    assert 'name="new_address"' in page.text and 'name="slot" value="CHIEU"' in page.text


def test_hen_lai_sets_date_and_slot(db, setup, client):
    p = setup; o = assigned(db, p); move(db, p, o, 4)
    service_login(client, p['technician'])
    _, form = fail_form(client, o)
    day = attendance.today() + timedelta(days=1)
    form.update(reason='HEN_LAI', promised_date=day.isoformat(), slot='CHIEU', note='', new_address='')
    r = client.post(f'/giao-don/{o.id}/failed', data=form, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all(); row = db.get(SalesOrder, o.id)
    attempt = db.query(SalesDeliveryAttempt).one()
    assert row.promised_date == day and row.status == 'DA_CHOT' and attempt.reason == 'HEN_LAI' and attempt.note == 'Hẹn buổi chiều'


def test_hen_lai_requires_date(db, setup):
    p = setup; o = assigned(db, p)
    fail('PROMISED_DATE_REQUIRED', svc.failed, db, p['technician'], o.id, SalesDeliveryFailed(version=o.version, reason='HEN_LAI'))


def test_wrong_address_updates_delivery_address(db, setup, client):
    p = setup; o = assigned(db, p); move(db, p, o, 4)
    fail('ADDRESS_REQUIRED', svc.failed, db, p['technician'], o.id, SalesDeliveryFailed(version=o.version, reason='SAI_DIA_CHI', new_address='  '))
    service_login(client, p['technician'])
    _, form = fail_form(client, o)
    form.update(reason='SAI_DIA_CHI', new_address='12 Lê Lợi, Q1', promised_date='', note='')
    assert client.post(f'/giao-don/{o.id}/failed', data=form, follow_redirects=False).status_code == 303
    db.expire_all(); row = db.get(SalesOrder, o.id)
    assert row.delivery_address == '12 Lê Lợi, Q1' and row.status == 'DA_CHOT' and row.delivery_assignee_id == p['technician'].id
    assert db.query(SalesDeliveryAttempt).one().note == 'Địa chỉ mới: 12 Lê Lợi, Q1'
    assert db.query(AuditLog).filter(AuditLog.action == 'sales_delivery_attempt.create').count() == 1


def test_cancel_request_goes_to_sales_and_clears_after_cancel(db, setup, client):
    p = setup; o = assigned(db, p); move(db, p, o, 4)
    fail('REASON_REQUIRED', svc.failed, db, p['technician'], o.id, SalesDeliveryFailed(version=o.version, reason='DE_NGHI_HUY', note=' '))
    service_login(client, p['technician'])
    _, form = fail_form(client, o)
    form.update(reason='DE_NGHI_HUY', note='Liên hệ nhiều lần không được', promised_date='', new_address='')
    assert client.post(f'/giao-don/{o.id}/failed', data=form, follow_redirects=False).status_code == 303
    db.expire_all(); row = db.get(SalesOrder, o.id)
    # Thợ chỉ đề nghị: đơn vẫn chốt, chưa hủy, hàng vẫn giữ.
    assert row.status == 'DA_CHOT'
    sales = groups(db, p['sales'])['orders_cancel_requested']
    assert sales['count'] == 1 and sales['rows'][0]['id'] == o.id
    assert any(i['id'] == f'orders_cancel_requested:{o.id}' for i in notifications.items(db, p['sales']))
    today = client.get('/tho').text
    assert '>Chờ hủy</span>' in today and 'Đã đề nghị hủy · chờ bán hàng' in today and 'Chờ bán hàng hủy đơn' in today
    assert 'Chờ bán hàng hủy đơn' in client.get(f'/giao-don/{o.id}').text
    tech = groups(db, p['technician'])['technician_deliveries']['rows'][0]
    assert tech['next_label'] == 'Đã đề nghị hủy · chờ bán hàng'
    sales_orders.cancel(db, p['sales'], o.id, 'Thợ báo khách không mua', row.version)
    assert groups(db, p['sales']).get('orders_cancel_requested', {'count': 0})['count'] == 0


def test_cancel_request_only_before_any_delivery(db, setup):
    p = setup; o = assigned(db, p); move(db, p, o, 4)
    deliver(db, p, o, qty=2)
    db.expire_all(); o = db.get(SalesOrder, o.id)
    assert o.status == 'GIAO_MOT_PHAN'
    fail('CANCEL_PARTIAL', svc.failed, db, p['technician'], o.id, SalesDeliveryFailed(version=o.version, reason='DE_NGHI_HUY', note='Khách không lấy nữa'))


def test_sales_page_shows_request_and_cancels(db, setup, client):
    p = setup; o = assigned(db, p); move(db, p, o, 4)
    svc.failed(db, p['technician'], o.id, SalesDeliveryFailed(version=o.version, reason='DE_NGHI_HUY', note='Khách không mua nữa'))
    assert groups(db, p['sales'])['orders_cancel_requested']['rows'][0]['link'] == f'/don-ban/{o.id}#de-nghi-huy'
    service_login(client, p['sales'])
    page = client.get(f'/don-ban/{o.id}')
    assert page.status_code == 200 and 'id="de-nghi-huy"' in page.text and 'Khách không mua nữa' in page.text
    form = next(f for f in Forms(page.text, page.url).forms if f['path'].endswith('/cancel'))['fields']
    assert '>Thợ đề nghị: Khách không mua nữa</textarea>' in page.text
    form['reason'] = 'Thợ đề nghị: Khách không mua nữa'
    assert client.post(f'/don-ban/{o.id}/cancel', data=form, follow_redirects=False).status_code == 303
    db.expire_all(); assert db.get(SalesOrder, o.id).status == 'DA_HUY'
    assert 'id="de-nghi-huy"' not in client.get(f'/don-ban/{o.id}').text
