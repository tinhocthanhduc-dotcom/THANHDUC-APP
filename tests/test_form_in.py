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


# ---- Đợt 2: Phiếu thu/chi, Đơn mua NCC, Phiếu trả hàng trên cùng khuôn A4 ----
from tests.test_cash import cash_people, make_doc  # noqa: E402,F401
from tests.test_web import service_login, purchase_people, purchase_form  # noqa: E402,F401


def test_cash_print_pt_pc_cq_signatures_watermark_and_partner(client, db, cash_people):
    p = cash_people
    pt = make_doc(db, p['finance'], partner_name='Nguyễn Văn An', description='Thu tiền hộp mực')
    pc = make_doc(db, p['finance'], kind='PC', amount=250000, partner_name='Trần B', description='Chi tiền xăng')
    cq = make_doc(db, p['director'], kind='CQ', amount=3000000, to_fund_code='TK-ACB', description='Nộp tiền vào ngân hàng')
    service_login(client, p['finance'])
    html = client.get(f'/thu-chi/{pt.id}/in').text
    assert 'PHIẾU THU' in html and pt.number in html and 'BẢN NHÁP' in html and 'logo-thanh-duc.jpg' in html
    assert 'Nguyễn Văn An' in html and 'Thu tiền hộp mực' in html and 'Thu bán hàng' in html
    assert 'Một triệu đồng' in html and 'TỔNG SỐ TIỀN' in html and '1.000.000 đồng' in html
    for who in ('Người lập phiếu', 'Thủ quỹ', 'Người nộp tiền', 'Giám đốc'):
        assert who in html
    assert html.count('<td><b>') == 4 and 'Người nhận tiền' not in html
    html = client.get(f'/thu-chi/{pc.id}/in').text
    assert 'PHIẾU CHI' in html and 'Người nhận tiền' in html and 'Chi khác' in html and 'Hai trăm năm mươi nghìn đồng' in html
    html = client.get(f'/thu-chi/{cq.id}/in').text
    assert 'PHIẾU CHUYỂN QUỸ' in html and 'Thủ quỹ giao' in html and 'Thủ quỹ nhận' in html and 'QUY-TM → TK-ACB' in html
    # Nhân viên bán hàng không mở được phiếu thu chi; chưa đăng nhập thì về đăng nhập.
    service_login(client, p['sales'])
    assert client.get(f'/thu-chi/{pt.id}/in').status_code == 403
    client.cookies.clear()
    assert client.get(f'/thu-chi/{pt.id}/in', follow_redirects=False).status_code in (302, 303)


def test_purchase_order_print_uses_frame_with_supplier_and_totals(client, db, purchase_people):
    from sqlalchemy import select
    from app.models import PurchaseOrder
    p = purchase_people
    csrf = service_login(client, p['warehouse'])
    assert client.post('/don-mua/moi', data=purchase_form(csrf, qty='2', vat_0='8')).status_code == 200
    o = db.scalar(select(PurchaseOrder))
    html = client.get(f'/don-mua/{o.id}/in').text
    assert 'ĐƠN ĐẶT HÀNG' in html and o.number in html and 'BẢN NHÁP' in html and 'Minh Phát' in html and 'NCC-0001' in html
    assert 'Kho 58' in html and 'TD-0001' in html and '120.000' in html and '240.000 đồng' in html and '8%' in html
    assert 'Tiền hàng chưa VAT' in html and 'Thuế GTGT' in html and 'TỔNG THANH TOÁN' in html and 'Bằng chữ' in html
    assert html.count('<td><b>') == 2 and 'Nhà cung cấp' in html and 'Đại diện Công ty Thành Đức' in html
    assert re.search(r'ngày 18 tháng 10 năm 2026', html)
    assert client.post(f'/don-mua/{o.id}/order', data={'csrf': csrf, 'version': o.version}).status_code == 200
    html = client.get(f'/don-mua/{o.id}/in').text
    assert 'BẢN NHÁP' not in html and 'Đã đặt' in html


# ---- Đợt 3: Phiếu tiếp nhận máy và Biên bản nghiệm thu dịch vụ ----
from test_my_work import people, purchase_people as mw_purchase_people  # noqa: E402,F401
from test_technician_stock import held  # noqa: E402,F401
from test_mv1_workshop import onsite, brought, repaired  # noqa: E402,F401


def test_intake_print_shows_machine_condition_accessories_and_link(client, db, brought):
    p, a, b, t, eq = brought
    service_login(client, p['technician'])
    html = client.get(f'/dich-vu/{t.id}/phieu-nhan-may').text
    assert 'PHIẾU TIẾP NHẬN MÁY' in html and t.number in html and 'MV1-MAY' in html and 'Vỏ xước nhẹ' in html
    assert 'Không kèm phụ kiện' in html and 'Máy bị kẹt giấy' in html and 'Người nhận máy' in html and 'logo-thanh-duc.jpg' in html
    assert html.count('<td><b>') == 2 and 'Thợ giữ' in html
    assert re.search(r'[Nn]gày \d{2} tháng \d{2} năm 20\d{2}', html)
    detail = client.get(f'/dich-vu/{t.id}').text
    assert f'href="/dich-vu/{t.id}/phieu-nhan-may"' in detail
    # Phiếu chưa nhận máy không có phiếu tiếp nhận.
    other = ticket_without_intake(db, p)
    assert client.get(f'/dich-vu/{other.id}/phieu-nhan-may').status_code == 404


def ticket_without_intake(db, p):
    from test_my_work import ticket
    return ticket(db, p, 'DANG_LAM')


def test_acceptance_report_preview_then_repaired_money_and_warranty(client, db, repaired):
    p, a, b, t, eq = repaired
    service_login(client, p['other_tech'])
    html = client.get(f'/dich-vu/{t.id}/bien-ban').text
    assert 'BIÊN BẢN NGHIỆM THU DỊCH VỤ' in html and t.number in html and 'BẢN XEM TRƯỚC' in html
    assert 'Máy bị kẹt giấy' in html and 'Đã sửa xong' in html and 'Chưa hoàn thành' in html
    # Tiền: dòng chưa VAT, ô tổng có VAT và tổng đã gồm VAT; chưa hoàn thành nên chưa có Đã thanh toán.
    assert '500.000 đồng' in html and '40.000 đồng' in html and '540.000 đồng' in html and 'Năm trăm bốn mươi nghìn đồng' in html
    assert 'Đã thanh toán' not in html and 'Bảo hành' in html
    assert html.count('<td><b>') == 2 and 'Đại diện khách hàng' in html and 'Kỹ thuật viên' in html
    # Thợ khác không xem được phiếu không phải của mình.
    service_login(client, p['technician'])
    assert client.get(f'/dich-vu/{t.id}/bien-ban').status_code in (403, 404)


# ---- Bổ sung 26/10 chiều (TUẤN): in phiếu giao hàng trước khi giao để thợ mang theo ----
from test_technician_deliveries import setup, assigned, move  # noqa: E402,F401


def test_pending_delivery_print_for_technician_trip(client, db, setup):
    from app.models import User
    p = setup
    o = assigned(db, p)
    service_login(client, p['sales'])
    page = client.get(f'/don-ban/{o.id}').text
    assert f'href="/giao-don/{o.id}/in"' in page
    html = client.get(f'/giao-don/{o.id}/in').text
    assert 'PHIẾU GIAO HÀNG' in html and o.number in html and 'chưa ghi nhận giao' in html and 'TD-0001' in html
    # 4 cái × 200 đồng chưa VAT; chưa thu gì nên thu khi giao = cả đơn.
    assert 'Tiền hàng chưa VAT' in html and '800 đồng' in html and 'Thu khi giao' in html and 'Đã thanh toán' in html
    assert 'Tám trăm đồng' in html and html.count('<td><b>') == 4 and 'Khách hàng ký nhận' in html
    assert 'ĐÃ HỦY' not in html and 'BẢN NHÁP' not in html
    assert f'href="/giao-don/{o.id}/in"' in client.get(f'/giao-don/{o.id}').text
    # Thợ được giao in được (về trang giao đơn của thợ); thợ khác không.
    service_login(client, p['technician'])
    html = client.get(f'/giao-don/{o.id}/in').text
    assert 'PHIẾU GIAO HÀNG' in html and f'href="/giao-don/{o.id}?return_to=tho"' in html
    service_login(client, p['other_tech'])
    assert client.get(f'/giao-don/{o.id}/in').status_code == 403
