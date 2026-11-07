"""Đơn Thợ giao: thợ tự nhận, đi theo phiếu cùng chuyến, gợi ý thợ (TUẤN duyệt 07/11/2026, mẫu GiaoDonTuNhan)."""
import re
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app import delivery_claim, dispatch_alerts, my_work, sales_orders, service_self_claim, service_tickets
from app import technician_deliveries as svc
from app.errors import BusinessError
from app.models import AuditLog, SalesOrder, ServiceTicket
from app.schemas import SalesDeliveryAssign, SalesOrderIn, SalesOrderOptions, ServiceTicketIn
from test_my_work import ticket
from test_stock_reservations import people, purchase_people, stock, order, holds, fail  # noqa: F401
from test_technician_deliveries import setup  # noqa: F401
from test_web import csrf_of, service_login


def waiting(db, p, qty=4):
    """Đơn Thợ giao đã chốt, chưa có thợ."""
    o = order(db, p, qty)
    sales_orders.change_options(db, p['sales'], o.id, SalesOrderOptions(version=o.version, fulfillment='THO_GIAO'))
    db.expire_all()
    return db.get(SalesOrder, o.id)


def linked(db, p, t, confirm=True):
    """Đơn bán tạo từ phiếu dịch vụ (Kèm hàng bán): Thợ giao, nối sẵn với phiếu."""
    row = sales_orders.create(db, p['sales'], SalesOrderIn(customer_code='KTD-000001', fulfillment='THO_GIAO', service_ticket_id=t.id,
        lines=[dict(kind='HANG', product_code='TD-0001', warehouse_code='K58', qty=2, unit_price=200, vat_rate=0)]))
    if confirm:
        sales_orders.confirm(db, p['sales'], row.id, row.version)
    db.expire_all()
    return db.get(SalesOrder, row.id)


def reasons(db, o):
    return list(db.scalars(select(AuditLog.reason).where(AuditLog.entity == 'sales_order', AuditLog.entity_id == str(o.id),
                                                         AuditLog.action == 'sales_order.assign_delivery').order_by(AuditLog.id)))


def claimable_ids(db):
    return set(db.scalars(select(SalesOrder.id).where(delivery_claim.claimable())))


def form_for(page, action):
    block = page[page.index(f'action="{action}"'):]
    key = re.search(r'name="form_key" value="([^"]+)"', block).group(1)
    return {'csrf': csrf_of(page), 'form_key': key}


def test_first_technician_wins_and_order_leaves_claim_list(db, setup):
    p = setup
    o = waiting(db, p)
    assert o.id in claimable_ids(db)
    delivery_claim.claim(db, p['technician'], o.id)
    db.expire_all(); o = db.get(SalesOrder, o.id)
    assert o.delivery_assignee_id == p['technician'].id and o.status == 'DA_CHOT'
    assert reasons(db, o) == [delivery_claim.NOTE]
    assert o.id not in claimable_ids(db)
    with pytest.raises(BusinessError) as e:
        delivery_claim.claim(db, p['other_tech'], o.id)
    assert e.value.code == 'ALREADY_CLAIMED' and p['technician'].full_name in e.value.message
    # Không đổi kho: hàng vẫn giữ ở kho chính, kho bàn giao sang kho thợ như cũ.
    assert [(r.warehouse_code, r.qty) for r in holds(db, o)] == [('K58', 4)]


def test_no_expiry_order_stays_until_someone_has_it(db, setup):
    p = setup
    o = waiting(db, p)
    db.execute(text("UPDATE sales_orders SET confirmed_at = now() - interval '5 hours' WHERE id=:id"), dict(id=o.id)); db.commit()
    assert o.id in claimable_ids(db)
    svc.assign(db, p['dispatcher'], o.id, SalesDeliveryAssign(version=o.version, assignee_id=p['other_tech'].id))
    assert o.id not in claimable_ids(db)


def test_guards_role_warehouse_limit(db, setup, make_user):
    p = setup
    o = waiting(db, p)
    fail('FORBIDDEN', delivery_claim.claim, db, p['sales'], o.id)
    no_kho = make_user('gd.nokho', 'technician')
    fail('NO_TECH_WAREHOUSE', delivery_claim.claim, db, no_kho, o.id)
    # Giới hạn việc mở tính cả đơn giao (mặc định 2).
    for _ in range(2):
        delivery_claim.claim(db, p['technician'], waiting(db, p, 1).id)
    assert service_self_claim.open_jobs(db, p['technician'].id) == 2
    fail('TOO_MANY_JOBS', delivery_claim.claim, db, p['technician'], o.id)
    delivery_claim.claim(db, p['other_tech'], o.id)


def test_cancelled_or_delivered_order_cannot_be_claimed(db, setup):
    p = setup
    o = waiting(db, p)
    sales_orders.cancel(db, p['sales'], o.id, 'Khách không lấy', o.version)
    fail('BAD_STATUS', delivery_claim.claim, db, p['technician'], o.id)


def test_order_from_assigned_ticket_follows_its_technician_on_confirm(db, setup):
    p = setup
    t = ticket(db, p, 'DA_GIAO')
    o = linked(db, p, t)
    assert o.delivery_assignee_id == p['technician'].id
    assert reasons(db, o) == [f'Đi theo phiếu {t.number}']
    assert o.id not in claimable_ids(db)
    # Cùng chuyến với phiếu của chính thợ: không tính thêm việc mở.
    assert service_self_claim.open_jobs(db, p['technician'].id) == 1


def test_order_waits_for_unassigned_ticket_then_follows_claim(db, setup):
    p = setup
    t = service_tickets.create(db, p['dispatcher'], ServiceTicketIn(customer_code='KTD-000001', kind='SUA_TAI_CHO',
                               symptom='Kẹt giấy'), self_claim=True)
    o = linked(db, p, t)
    assert o.delivery_assignee_id is None and o.id not in claimable_ids(db)
    fail('FOLLOWS_TICKET', delivery_claim.claim, db, p['technician'], o.id)
    assert delivery_claim.linked_orders(db, [t.id]) == {t.id: [o.number]}
    service_self_claim.claim(db, p['other_tech'], t.id)
    db.expire_all(); o = db.get(SalesOrder, o.id)
    assert o.delivery_assignee_id == p['other_tech'].id


def test_order_follows_when_dispatcher_assigns_ticket(db, setup):
    p = setup
    t = service_tickets.create(db, p['dispatcher'], ServiceTicketIn(customer_code='KTD-000001', kind='SUA_TAI_CHO', symptom='Kẹt giấy'))
    o = linked(db, p, t)
    service_tickets.assign(db, p['dispatcher'], t.id, p['technician'].id, t.version)
    db.expire_all(); o = db.get(SalesOrder, o.id)
    assert o.delivery_assignee_id == p['technician'].id


def test_ticket_tech_without_warehouse_leaves_order_waiting(db, setup, make_user):
    p = setup
    no_kho = make_user('gd.nokho2', 'technician')
    t = service_tickets.create(db, p['dispatcher'], ServiceTicketIn(customer_code='KTD-000001', kind='SUA_TAI_CHO', symptom='Kẹt giấy'))
    o = linked(db, p, t)
    service_tickets.assign(db, p['dispatcher'], t.id, no_kho.id, t.version)
    db.expire_all(); o = db.get(SalesOrder, o.id)
    assert o.delivery_assignee_id is None and o.id in claimable_ids(db)


def test_rank_prefers_stock_in_technician_warehouse(db, setup):
    p = setup
    stock(db, p, 5, warehouse='K502')
    o = waiting(db, p)
    rows = delivery_claim.rank_orders(db, [o])[o.id]
    best = rows[0]
    assert best['best'] and best['id'] == p['other_tech'].id
    assert 'Kho thợ có sẵn hàng của đơn' in best['why']


def test_rank_prefers_technician_with_ticket_at_same_customer(db, setup):
    p = setup
    ticket(db, p, 'DA_GIAO', who='other_tech')
    o = waiting(db, p)
    best = delivery_claim.rank_orders(db, [o])[o.id][0]
    assert best['id'] == p['other_tech'].id and any(w.startswith('Đang có phiếu') for w in best['why'])


def test_remind_dispatcher_after_30_minutes_once(db, setup):
    p = setup
    o = waiting(db, p)
    group = lambda: next(g for g in my_work.collect(db, p['dispatcher'], keys=('delivery_unclaimed',)))
    assert not group()['rows']
    def age(now):
        db.execute(text("UPDATE sales_orders SET confirmed_at = :at WHERE id=:id"), dict(id=o.id, at=now - timedelta(minutes=31))); db.commit()
    age(my_work._now())  # hàng việc dùng đồng hồ đã cố định trong fixture
    assert [r['id'] for r in group()['rows']] == [o.id]
    age(service_tickets._now())
    marks = [m for m, _ in dispatch_alerts.events(db, service_tickets._now()) if m.startswith('don-giao:')]
    assert marks == [f'don-giao:{o.id}']
    # Vẫn hiện cho thợ nhận.
    assert o.id in claimable_ids(db)


def test_web_technician_sees_and_claims_order(db, setup, client):
    p = setup
    o = waiting(db, p)
    service_login(client, p['technician'])
    page = client.get('/tho').text
    assert f'data-claim-order="{o.id}"' in page and 'Nhận giao đơn này' in page
    r = client.post(f'/tho/nhan-don/{o.id}', data=form_for(page, f'/tho/nhan-don/{o.id}'), follow_redirects=False)
    assert r.status_code == 303 and f'nhan={o.number}' in r.headers['location']
    db.expire_all()
    assert db.get(SalesOrder, o.id).delivery_assignee_id == p['technician'].id
    service_login(client, p['other_tech'])
    assert f'data-claim-order="{o.id}"' not in client.get('/tho').text


def test_web_dispatcher_assigns_from_suggestion(db, setup, client):
    p = setup
    stock(db, p, 5, warehouse='K501')
    o = waiting(db, p)
    service_login(client, p['dispatcher'])
    page = client.get('/dieu-phoi').text
    assert 'data-delivery-suggest' in page and 'Chờ thợ nhận' in page
    data = form_for(page, f'/giao-don/{o.id}/assign')
    data.update(version=str(o.version), assignee_id=str(p['technician'].id), goi_y=str(p['technician'].id), quay_ve='/dieu-phoi')
    r = client.post(f'/giao-don/{o.id}/assign', data=data, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].startswith('/dieu-phoi?da_giao=')
    db.expire_all(); o = db.get(SalesOrder, o.id)
    assert o.delivery_assignee_id == p['technician'].id and reasons(db, o) == ['Theo gợi ý']


def test_web_order_page_shows_suggestion_and_claim_hint(db, setup, client):
    p = setup
    o = waiting(db, p)
    service_login(client, p['dispatcher'])
    page = client.get(f'/giao-don/{o.id}').text
    assert 'data-delivery-best' in page and 'data-claim-open' in page and 'id="giao-tho"' in page
