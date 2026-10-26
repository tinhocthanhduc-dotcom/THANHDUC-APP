"""Form in phiếu (Đợt 1, duyệt 26/10/2026): khuôn A4 chung cho Báo giá, Phiếu giao hàng, Phiếu kho."""
import re

from tests.test_web import stock_create_web, stock_form, web_login, stock_catalog  # noqa: F401 (fixture + helper)


def _doc_id(client, csrf, **kw):
    return stock_create_web(client, csrf, **kw)


def test_stock_receipt_print_shows_cost_words_and_draft_watermark(client, web_login, stock_catalog, db):
    csrf = web_login('thu.kho', 'warehouse')
    doc_id = _doc_id(client, csrf)
    page = client.get(f'/phieu-kho/{doc_id}/in')
    assert page.status_code == 200
    html = page.text
    assert 'PHIẾU NHẬP KHO' in html and 'BẢN NHÁP' in html and 'Minh Phát' in html and 'Kho 58' in html
    assert 'logo-thanh-duc.jpg' in html and '0313015734' in html and 'www.thanhduc.vn' in html and 'Đường Mã Lò' in html
    # Giá nhập chưa VAT × số lượng và cộng tiền hàng; bằng chữ đọc đúng số.
    assert '120.000' in html and '240.000 đồng' in html and 'Hai trăm bốn mươi nghìn đồng' in html
    assert 'SN-01' in html and 'Dòng đầu' in html and 'Giữ ghi chú' in html and 'Nhập mua hàng' in html
    assert html.count('<td><b>') == 3 and 'Người giao hàng' in html and 'Thủ kho' in html and 'Người lập phiếu' in html
    assert f'href="/phieu-kho/{doc_id}/in"' in client.get(f'/phieu-kho/{doc_id}').text


def test_stock_issue_print_hides_cost_and_posted_has_no_watermark(client, web_login, stock_catalog, db):
    from app.models import StockDoc
    csrf = web_login('thu.kho', 'warehouse')
    _doc_id(client, csrf, action='post')
    px = _doc_id(client, csrf, kind='PX', supplier_code='', unit_cost=[''], qty=['1'], reason='Xuất cho thợ')
    r = client.post(f'/phieu-kho/{px}/post', data=stock_form(csrf, version='1', kind='PX', supplier_code='', unit_cost=[''], qty=['1'], reason='Xuất cho thợ'), follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    assert db.get(StockDoc, px).status == 'DA_GHI'
    html = client.get(f'/phieu-kho/{px}/in').text
    assert 'PHIẾU XUẤT KHO' in html and 'BẢN NHÁP' not in html and 'ĐÃ HỦY' not in html
    assert 'Giá nhập' not in html and 'Bằng chữ' not in html and '120.000' not in html and 'không in giá vốn' in html
    assert 'Xuất cho thợ' in html and 'Người nhận hàng' in html and 'Tổng số lượng' in html
    assert re.search(r'[Nn]gày \d{2} tháng 10 năm 2026', html)
    # Kế toán mở được trang in (chỉ đọc); bán hàng không vào mục phiếu kho (403 theo menu); chưa đăng nhập thì về đăng nhập.
    web_login('ke.toan', 'finance')
    assert client.get(f'/phieu-kho/{px}/in').status_code == 200
    web_login('ban.hang', 'sales')
    assert client.get(f'/phieu-kho/{px}/in').status_code == 403
    client.cookies.clear()
    assert client.get(f'/phieu-kho/{px}/in', follow_redirects=False).status_code in (302, 303)


def test_stock_print_missing_doc_is_404(client, web_login, stock_catalog):
    web_login('thu.kho', 'warehouse')
    assert client.get('/phieu-kho/999999/in').status_code == 404
