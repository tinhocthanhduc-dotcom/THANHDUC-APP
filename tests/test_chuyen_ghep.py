"""Đơn giao kèm dịch vụ cùng chuyến: nhập một lần, thợ thấy một thẻ, khách trả tổng thì app chia (đơn hàng trước)."""
import re

import pytest
from sqlalchemy import func, select

from app import sales_orders, service_tickets, technician_deliveries as svc
from app.errors import BusinessError
from app.models import SalesOrder, ServiceTicket
from app.schemas import SalesDeliveryAssign, SalesOrderOptions
from test_my_work import ticket
from test_stock_reservations import people, purchase_people, stock, order  # noqa: F401
from test_technician_deliveries import setup, assigned, move  # noqa: F401
from test_web import service_login
from web_forms import Forms


def tho_order(db, p, qty=4):
    o = order(db, p, qty)
    sales_orders.change_options(db, p['sales'], o.id, SalesOrderOptions(version=o.version, fulfillment='THO_GIAO'))
    return o


def assign_form(client, p, o):
    service_login(client, p['dispatcher'])
    page = client.get(f'/giao-don/{o.id}')
    form = next(f for f in Forms(page.text, page.url).forms if f['path'].endswith('/assign'))['fields']
    return page, form


def test_assign_with_new_service_creates_linked_ticket_for_same_technician(db, setup, client):
    p = setup; o = tho_order(db, p)
    page, form = assign_form(client, p, o)
    assert 'value="NEW"' in page.text and 'name="new_service_symptom"' in page.text
    form.update(assignee_id=str(p['technician'].id), service_ticket_id='NEW', new_service_symptom='Lắp đặt máy, cài driver')
    r = client.post(f'/giao-don/{o.id}/assign', data=form, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all(); o = db.get(SalesOrder, o.id); t = db.get(ServiceTicket, o.service_ticket_id)
    assert (t.customer_code, t.assignee_id, t.status, t.symptom) == (o.customer_code, p['technician'].id, 'DA_GIAO', 'Lắp đặt máy, cài driver')
    assert o.delivery_assignee_id == p['technician'].id


def test_new_service_bad_input_rolls_back_everything(db, setup, client):
    p = setup; o = tho_order(db, p)
    _, form = assign_form(client, p, o)
    form.update(assignee_id=str(p['technician'].id), service_ticket_id='NEW', new_service_symptom='ab')
    r = client.post(f'/giao-don/{o.id}/assign', data=form, follow_redirects=False)
    assert r.status_code == 400
    db.expire_all(); o = db.get(SalesOrder, o.id)
    assert o.delivery_assignee_id is None and o.service_ticket_id is None and db.scalar(select(func.count()).select_from(ServiceTicket)) == 0
    with pytest.raises(Exception):
        SalesDeliveryAssign(version=1, assignee_id=1, service_ticket_id=5, new_service_symptom='Lắp máy')


def test_pick_existing_ticket_from_list(db, setup, client):
    p = setup; o = tho_order(db, p); t = ticket(db, p, 'DA_GIAO')
    page, form = assign_form(client, p, o)
    assert f'<option value="{t.id}"' in page.text and t.number in page.text
    form.update(assignee_id=str(p['technician'].id), service_ticket_id=str(t.id))
    assert client.post(f'/giao-don/{o.id}/assign', data=form, follow_redirects=False).status_code == 303
    db.expire_all(); assert db.get(SalesOrder, o.id).service_ticket_id == t.id


def linked(db, p):
    t = ticket(db, p, 'DANG_LAM'); o = tho_order(db, p)
    svc.assign(db, p['dispatcher'], o.id, SalesDeliveryAssign(version=o.version, assignee_id=p['technician'].id, service_ticket_id=t.id))
    move(db, p, o, 4)
    return t, o


def test_today_groups_trip_into_one_card(db, setup, client):
    p = setup; t, o = linked(db, p)
    service_login(client, p['technician']); html = client.get('/tho').text
    assert f'data-job="service:{t.id}"' in html and f'data-job="delivery:{o.id}"' not in html
    assert 'data-trip-group' in html and f'data-trip="{o.number}"' in html and f'/giao-don/{o.id}?return_to=tho' in html


def test_ticket_and_delivery_screens_offer_trip_total(db, setup, client):
    p = setup; t, o = linked(db, p)
    service_login(client, p['technician'])
    html = client.get(f'/tho/phieu/{t.id}').text
    due = re.search(r'data-order-due="(\d+)"', html)
    assert 'data-trip-money' in html and due and int(due.group(1)) > 0 and o.number in html
    page = client.get(f'/giao-don/{o.id}?return_to=tho').text
    assert f'data-trip-delivery data-ticket="{t.id}"' in page and 'data-trip-total' in page and t.number in page
