"""SY3 (báo cáo đồng bộ 25/10/2026, F2): link chỉ hiện khi mở được; bản xem chỉ đọc phiếu dịch vụ cho kế toán và kho."""
from sqlalchemy import select
from app import attachments, sales_receipts, service_tickets, part_requests
from app.schemas import PartRequestIn, SalesReceiptReport
from app.models import PartRequest
from test_my_work import people, purchase_people, ticket  # noqa: F401
from test_technician_stock import held, transfer, charges  # noqa: F401
from test_web import service_login, d5_image, web_login, d1_catalog  # noqa: F401


def _finished(db, p):
    transfer(db, p, qty=2); t = ticket(db, p, 'DANG_LAM'); charges(db, p, t)
    attachments.add(db, p['technician'], 'service_ticket', str(t.id), 'SAU', 'sau.png', d5_image())
    service_tickets.transition(db, p['technician'], t.id, 'HOAN_THANH', t.version)
    db.expire_all(); db.refresh(t)
    return t


def test_finance_receipt_source_opens_read_only_view_without_cost(client, db, held):
    p, _, _ = held; t = _finished(db, p)
    r = sales_receipts.report_for_ticket(db, p['technician'], t.id, SalesReceiptReport(version=t.version, method='TIEN_MAT', amount=1000))
    service_login(client, p['finance'])
    page = client.get('/bao-thu').text
    assert f'href="/xem-dich-vu/{t.id}"' in page and f'href="/dich-vu/{t.id}' not in page
    view = client.get(f'/xem-dich-vu/{t.id}')
    assert view.status_code == 200
    html = view.text
    assert t.number in html and 'Bản xem chỉ đọc' in html and r.number in html and 'Còn thiếu' in html
    # Không giá vốn, không nút thao tác, không mở phiếu đầy đủ.
    assert 'giá vốn' not in html.lower()
    assert '<form' not in html.split('<main>')[-1] and 'Mở phiếu đầy đủ' not in html
    assert client.get(f'/dich-vu/{t.id}').status_code == 403
    # Tổng quan: ô Dịch vụ không còn là link dẫn vào 403.
    overview = client.get('/tong-quan').text
    assert 'href="/dich-vu"' not in overview and 'href="/dich-vu?overdue=1"' not in overview and 'Dịch vụ đang mở' in overview


def test_warehouse_sees_ticket_from_parts_queue_and_held_machines(client, db, held):
    p, _, _ = held; t = ticket(db, p, 'DANG_LAM')
    r = part_requests.create(db, p['technician'], t.id, PartRequestIn(version=t.version, product_code='TD-0001', qty=1, reason='Cần drum'))
    service_login(client, p['warehouse'])
    page = client.get('/yeu-cau-linh-kien').text
    assert f'href="/xem-dich-vu/{t.id}"' in page and f'href="/dich-vu/{t.id}"' not in page
    # Ô đếm trên trang kho (macro import không kèm context) vẫn là link khi kho mở được.
    assert 'class="tile' in page and 'href="/yeu-cau-linh-kien?ton=du"' in page and 't-static' not in page
    view = client.get(f'/xem-dich-vu/{t.id}')
    assert view.status_code == 200 and r.number in view.text and 'Còn thiếu' not in view.text and 'Tiền phiếu' not in view.text
    assert client.get(f'/dich-vu/{t.id}').status_code == 403
    # Vai khác không có bản xem: kinh doanh, thợ khác vẫn 403.
    service_login(client, p['other_sales'])
    assert client.get(f'/xem-dich-vu/{t.id}').status_code == 403
    # Điều phối vẫn mở trang đầy đủ, link đầy đủ.
    service_login(client, p['dispatcher'])
    assert client.get(f'/xem-dich-vu/{t.id}').status_code == 403
    assert client.get(f'/dich-vu/{t.id}').status_code == 200


def test_links_outside_role_render_as_text(client, web_login, d1_catalog, db):
    # Thiết bị: ô "Đang ở công ty" không link tới Máy đang giữ với lễ tân; điều phối vẫn có link.
    web_login('le.tan', 'reception')
    page = client.get('/thiet-bi').text
    assert 'Đang ở công ty' in page and 'href="/may-dang-giu"' not in page
    web_login('dieu.phoi', 'dispatcher')
    assert 'href="/may-dang-giu"' in client.get('/thiet-bi').text
