"""R1–R3 (báo cáo kiểm tra lại 25/10/2026): payload đổi loại có kind; 3 link theo quyền; CXL khả dụng 0 không mời thao tác."""
import re
from datetime import timedelta
from pathlib import Path

from app import sales_orders
from app.models import PendingDisposition
from test_alerts import people, ticket as overdue_ticket, NOW, m3_people, report_people, clock  # noqa: F401
from test_purchase_orders import purchase_people  # noqa: F401
from test_receipt_cash import t3_people, ticket as paid_ticket, report  # noqa: F401
from test_stock_reservations import stock, order  # noqa: F401
from test_cxl2a_dispositions import lot, make  # noqa: F401
from test_cxl1_pending_items import assert_pending_invariant
from test_web import service_login

JS = Path(__file__).resolve().parents[1] / 'app' / 'web' / 'static' / 'service-kind.js'


def test_r1_form_data_read_before_select_disabled():
    # Control disabled không vào FormData: phải lấy dữ liệu trước khi khóa ô chọn (đã kiểm Chromium: scripts/kiem_service_kind_browser.js).
    src = JS.read_text(encoding='utf-8')
    body = src.index('new FormData(form)')
    locked = src.index('select.disabled = true')
    assert body < locked, 'FormData phải được lấy trước khi select.disabled = true'
    assert src.count('select.disabled = true') == 1 and 'select.disabled = false' in src


def test_r2_sales_sees_stock_doc_number_without_link(client, db, people):  # noqa: F811
    p = people; stock(db, p, 5); o = order(db, p, 2)
    sales_orders.deliver(db, p['warehouse'], o.id, o.version)
    db.expire_all(); doc_id = db.get(type(o), o.id).lines[0].stock_doc_id
    assert doc_id
    service_login(client, p['sales'])
    page = client.get(f'/don-ban/{o.id}')
    assert page.status_code == 200 and 'Phiếu xuất kho' in page.text
    assert f'href="/phieu-kho/{doc_id}"' not in page.text and 'class="doc-ref"' in page.text
    assert client.get(f'/phieu-kho/{doc_id}').status_code == 403
    service_login(client, p['warehouse'])
    assert f'href="/phieu-kho/{doc_id}"' in client.get(f'/don-ban/{o.id}').text


def test_r2_finance_overview_alert_links_to_read_only_ticket_view(client, db, people, monkeypatch):  # noqa: F811
    p = people; t = overdue_ticket(db, p, monkeypatch, NOW - timedelta(days=2))
    service_login(client, p['finance'])
    page = client.get('/tong-quan')
    assert page.status_code == 200 and t.number in page.text
    assert f'href="/xem-dich-vu/{t.id}"' in page.text and f'href="/dich-vu/{t.id}"' not in page.text
    assert client.get(f'/xem-dich-vu/{t.id}').status_code == 200 and client.get(f'/dich-vu/{t.id}').status_code == 403
    service_login(client, p['director'])
    assert f'href="/dich-vu/{t.id}"' in client.get('/tong-quan').text


def test_r2_dispatcher_closed_ticket_has_no_receivables_link(client, db, t3_people, configured_finance):  # noqa: F811
    p = t3_people; t = paid_ticket(db, p); report(db, p, t, amount=500000)
    service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{t.id}')
    assert page.status_code == 200 and 'Thu tiền' in page.text
    assert 'href="/dich-vu/can-thu"' not in page.text
    assert client.get('/dich-vu/can-thu').status_code == 403
    service_login(client, p['director'])
    assert 'href="/dich-vu/can-thu"' in client.get(f'/dich-vu/{t.id}').text


def test_r3_pending_destruction_hides_disposition_forms_and_core_still_rejects(client, db, lot):  # noqa: F811
    p, item = lot; row = make(db, p, item, 'HUY', reason='Chờ hủy toàn bộ')
    assert row.status == 'CHO_DUYET'
    csrf = service_login(client, p['warehouse'])
    page = client.get(f'/cho-xu-ly/{item.id}')
    assert page.status_code == 200
    assert 'data-disposition-form' not in page.text and 'data-disposition-panel' not in page.text
    assert 'Không còn số lượng để xử lý' in page.text and 'đang chờ duyệt yêu cầu hủy' in page.text
    # Lõi vẫn từ chối khi gửi thẳng; không sinh phiếu xử lý mới.
    before = db.query(PendingDisposition).count()
    r = client.post(f'/cho-xu-ly/{item.id}/xu-ly', data=dict(csrf=csrf, kind='SUA_XONG', qty='1', version=item.version, to_warehouse_code='K58'))
    assert r.status_code >= 400 and db.query(PendingDisposition).count() == before
    assert_pending_invariant(db)
    # Duyệt hủy một phần bớt đi: còn số lượng thì form hiện lại (hủy 10/10 nên sau duyệt phiếu đóng, không còn DANG_CHO).
    assert re.search(r'Còn xử lý được', page.text) is None
