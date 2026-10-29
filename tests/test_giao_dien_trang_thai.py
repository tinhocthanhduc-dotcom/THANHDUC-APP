"""Làm xong một việc thì màn đổi theo: ẩn form vừa bấm, hiện trạng thái mới và nút làm lại (vd Giao thợ khác)."""
from app import sales_orders, service_tickets
from app.schemas import SalesOrderOptions
from test_my_work import ticket
from test_stock_reservations import people, purchase_people, stock, order  # noqa: F401
from test_technician_deliveries import setup, assigned, move  # noqa: F401
from test_web import service_login
from web_forms import Forms


def forms(page):
    return [f['path'] for f in Forms(page.text, page.url).forms]


def test_delivery_order_assign_form_turns_into_assigned_state(db, setup, client):
    p = setup; o = order(db, p, 4)
    sales_orders.change_options(db, p['sales'], o.id, SalesOrderOptions(version=o.version, fulfillment='THO_GIAO'))
    service_login(client, p['warehouse'])
    assert 'data-transfer-wait' in client.get(f'/giao-don/{o.id}').text  # kho chờ có thợ mới bàn giao
    service_login(client, p['dispatcher'])
    page = client.get(f'/giao-don/{o.id}')
    assert 'data-assigned' not in page.text and '<h2>Giao thợ</h2>' in page.text
    form = next(f for f in Forms(page.text, page.url).forms if f['path'].endswith('/assign'))['fields']
    form.update(assignee_id=str(p['technician'].id))
    assert client.post(f'/giao-don/{o.id}/assign', data=form, follow_redirects=False).status_code == 303
    page = client.get(f'/giao-don/{o.id}')
    assert 'data-assigned' in page.text and 'Đã giao thợ' in page.text and p['technician'].full_name in page.text
    assert '<h2>Giao thợ</h2>' not in page.text and 'Giao thợ khác</summary>' in page.text and '(đang giao)' in page.text
    # Đổi thợ vẫn dùng form trong nút Giao thợ khác.
    form = next(f for f in Forms(page.text, page.url).forms if f['path'].endswith('/assign'))['fields']
    form.update(assignee_id=str(p['other_tech'].id))
    assert client.post(f'/giao-don/{o.id}/assign', data=form, follow_redirects=False).status_code == 303
    db.expire_all(); assert db.get(type(o), o.id).delivery_assignee_id == p['other_tech'].id
    assert 'Đã giao thợ ' + p['other_tech'].full_name in client.get('/dieu-phoi').text


def test_full_handover_collapses_transfer_and_warns_before_reassign(db, setup, client):
    p = setup; o = assigned(db, p); move(db, p, o, 4)
    service_login(client, p['dispatcher'])
    page = client.get(f'/giao-don/{o.id}').text
    assert 'tự chuyển sang kho thợ mới' in page and 'Muốn đổi thợ phải Trả kho trước' not in page
    service_login(client, p['warehouse'])
    page = client.get(f'/giao-don/{o.id}').text
    assert 'data-transfer-done' in page and 'Bước tiếp theo' not in page
    html = client.get(f'/kho/giao/{o.id}').text
    assert 'data-tech-done' in html and 'data-next-primary' not in html


def test_service_ticket_assign_turns_into_assigned_state(db, setup, client):
    p = setup; t = ticket(db, p, 'MOI')
    service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{t.id}')
    assert 'data-assigned' not in page.text and f'/dich-vu/{t.id}/assign' in forms(page)
    form = next(f for f in Forms(page.text, page.url).forms if f['path'].endswith('/assign'))['fields']
    form.update(assignee_id=str(p['technician'].id))
    assert client.post(f'/dich-vu/{t.id}/assign', data=form, follow_redirects=False).status_code == 303
    page = client.get(f'/dich-vu/{t.id}')
    assert 'data-assigned' in page.text and 'Giao thợ khác</summary>' in page.text and 'đang giữ phiếu' in page.text
    assert page.text.count('id="service-assign"') == 1
    assert 'form="service-DA_NHAN" data-next-primary' not in page.text  # Nhận việc là việc của thợ, không làm nút chính của điều phối
    # Không chọn sẵn thợ cũ: bấm lưu nhầm không ra lỗi "đang giao cho thợ này rồi".
    block = page.text.split('id="service-assign"', 1)[1].split('</form>', 1)[0]
    assert 'checked' not in block
    form = next(f for f in Forms(page.text, page.url).forms if f['path'].endswith('/assign'))['fields']
    form.update(assignee_id=str(p['other_tech'].id))
    assert client.post(f'/dich-vu/{t.id}/assign', data=form, follow_redirects=False).status_code == 303
    db.expire_all(); assert service_tickets.load(db, p['dispatcher'], t.id).assignee_id == p['other_tech'].id
