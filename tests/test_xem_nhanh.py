"""Hộp xem nhanh phiếu (07/11/2026): /xem-nhanh?u=<trang phiếu> trả mẩu HTML chỉ đọc, quyền đúng như trang đầy đủ."""
from pathlib import Path

from app import sales_receipts
from app.schemas import SalesReceiptReport
from test_my_work import people, purchase_people, ticket, order  # noqa: F401
from test_technician_stock import held, transfer, charges  # noqa: F401
from test_web import service_login, d5_image  # noqa: F401
from test_sy3_dong_bo import _finished

ROOT = Path(__file__).resolve().parents[1] / 'app' / 'web'


def _ok(client, url):
    return client.get(url).status_code == 200


def _quick(client, path):
    return client.get('/xem-nhanh', params={'u': path})


def test_quick_view_follows_full_page_permission_for_every_role(client, db, held):
    p, _, _ = held
    t = ticket(db, p, 'DANG_LAM')
    o = order(db, p, 'DA_CHOT')
    for role in ('director', 'finance', 'warehouse', 'sales', 'dispatcher', 'reception', 'technician', 'other_tech', 'other_sales'):
        service_login(client, p[role])
        for path in (f'/don-ban/{o.id}',):
            assert (_quick(client, path).status_code == 200) == _ok(client, path), (role, path)
        # Phiếu dịch vụ: xem nhanh được khi mở được trang đầy đủ hoặc bản xem chỉ đọc.
        can_ticket = _ok(client, f'/dich-vu/{t.id}') or _ok(client, f'/xem-dich-vu/{t.id}')
        assert (_quick(client, f'/dich-vu/{t.id}').status_code == 200) == can_ticket, role


def test_ticket_quick_view_is_read_only_and_points_to_page_user_can_open(client, db, held):
    p, _, _ = held
    t = _finished(db, p)
    r = sales_receipts.report_for_ticket(db, p['technician'], t.id, SalesReceiptReport(version=t.version, method='TIEN_MAT', amount=1000))
    service_login(client, p['finance'])
    res = _quick(client, f'/xem-dich-vu/{t.id}')
    html = res.text
    assert res.status_code == 200 and t.number in html and 'Bước tiếp' in html and 'Còn thu' in html
    assert '<form' not in html and '<html' not in html and 'giá vốn' not in html.lower()
    assert f'href="/xem-dich-vu/{t.id}"' in html and 'Mở đầy đủ' in html and f'href="/dich-vu/{t.id}"' not in html
    # Báo thu: số tiền, phiếu nguồn là link xem được.
    rec = _quick(client, f'/bao-thu/{r.id}').text
    assert r.number in rec and 'Chờ xác nhận' in rec and f'href="/xem-dich-vu/{t.id}"' in rec
    # Điều phối mở trang đầy đủ.
    service_login(client, p['dispatcher'])
    assert f'href="/dich-vu/{t.id}"' in _quick(client, f'/dich-vu/{t.id}').text


def test_order_quick_view_shows_money_and_next_step(client, db, held):
    p, _, _ = held
    o = order(db, p, 'DA_GIAO', amount=250000)
    service_login(client, p['sales'])
    html = _quick(client, f'/don-ban/{o.id}').text
    assert o.number in html and 'Công lắp đặt' in html and '250.000' in html and 'Còn nợ' in html and 'Bước tiếp' in html
    assert f'href="/don-ban/{o.id}"' in html


def test_unknown_or_bad_path_is_404(client, db, held):
    p, _, _ = held
    service_login(client, p['director'])
    for path in ('/khach-hang/KTD-000001', '/don-ban/abc', 'https://evil.example/don-ban/1', '/don-ban/999999'):
        assert _quick(client, path).status_code == 404, path


def test_script_and_patterns_stay_in_sync():
    from app.web.quick_view import PATTERNS
    base = (ROOT / 'templates' / 'base.jinja').read_text()
    js = (ROOT / 'static' / 'quick-view.js').read_text()
    assert "asset('quick-view.js')" in base
    block = js.split('var PATTERNS = [', 1)[1].split('];', 1)[0]
    assert len(PATTERNS) == block.count('/^\\/')
    # Màn thợ giữ như cũ; nút/hành động không bị chặn; Ctrl/Cmd+bấm mở trang.
    assert '(tho|giao-don|hang-toi-giu)' in js and '.btn, .primary' in js and 'e.metaKey || e.ctrlKey' in js
