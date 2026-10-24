"""Đợt 2a: bốn màn danh sách dùng chung một khuôn (ô đếm, tab có số, lọc tức thì, tổng trên toàn bộ kết quả, 25 dòng/trang)."""
import re
from datetime import date

from app import purchase_orders as orders, stock_docs
from app.models import Product
from app.schemas import PurchaseOrderIn, StockDocIn
from test_cash import cash_people, make_doc, opening, posted
from test_purchase_orders import purchase_people
from test_my_work import people
from test_web import service_login, stock_catalog, web_login


def tab(html, label):
    m = re.search(r'class="ltab[^"]*"[^>]*>' + re.escape(label) + r' <span>(\d+)</span>', html)
    return int(m.group(1)) if m else None


def purchase(db, user, product='TD-0001', qty=1, cost=100000):
    return orders.create(db, user, PurchaseOrderIn(supplier_code='NCC-0001', order_date=date(2026, 10, 18),
        lines=[dict(product_code=product, warehouse_code='K58', qty=qty, unit_cost=cost, vat_rate=0)]))


def test_purchase_list_pages_25_totals_all_rows_and_no_100_cap(db, client, purchase_people):
    p = purchase_people
    for _ in range(104):
        purchase(db, p['warehouse'])
    service_login(client, p['warehouse'])
    page = client.get('/don-mua').text
    assert page.count('data-purchase-row=') == 25 and 'Hiện <b>1–25</b> / 104 đơn' in page
    assert tab(page, 'Tất cả') == 104 and tab(page, 'Nháp') == 104 and tab(page, 'Đã đặt') == 0
    # Tổng ở chân là của cả 104 đơn, không chỉ trang đang xem.
    assert 'Tổng <b>10.400.000</b> đ' in page
    last = client.get('/don-mua?trang=5').text
    assert last.count('data-purchase-row=') == 4 and 'Trang 5/5' in last
    assert 'Lọc</button>' not in page.replace('<noscript><button>Lọc</button></noscript>', '')


def test_purchase_list_search_by_supplier_name_and_product_code_with_tabs(db, client, purchase_people):
    p = purchase_people
    a = purchase(db, p['warehouse'])
    b = purchase(db, p['warehouse'], product='TD-0002', qty=3)
    b = orders.order(db, p['warehouse'], b.id, b.version)
    service_login(client, p['warehouse'])
    by_name = client.get('/don-mua?q=minh+phat').text
    assert a.number in by_name and b.number in by_name
    by_code = client.get('/don-mua?q=TD-0002').text
    assert b.number in by_code and a.number not in by_code
    ordered = client.get('/don-mua?status=DA_DAT').text
    assert b.number in ordered and a.number not in ordered
    assert tab(ordered, 'Tất cả') == 2 and tab(ordered, 'Đã đặt') == 1 and 'class="ltab on"' in ordered
    chip = client.get('/don-mua?supplier_code=NCC-0001').text
    assert 'NCC: Minh Phát' in chip and 'Xóa lọc' in chip


def test_stock_list_search_by_product_name_tabs_and_draft_alert(db, client, web_login, stock_catalog):
    user_csrf = web_login('thu.kho', 'warehouse')
    from app.models import User
    user = db.query(User).filter_by(username='thu.kho').one()
    doc = stock_docs.create(db, user, StockDocIn(kind='PN', warehouse_code='K58', doc_date=date(2026, 10, 17), supplier_code='NCC-0001',
        reason='Nhập', lines=[dict(product_code='TD-0002', qty=2, unit_cost=50000)]))
    other = stock_docs.create(db, user, StockDocIn(kind='PN', warehouse_code='K145', doc_date=date(2026, 10, 17), supplier_code='NCC-0001',
        reason='Nhập', lines=[dict(product_code='TD-0001', qty=1, unit_cost=1000)]))
    page = client.get('/phieu-kho?q=drum&tu=2026-10-01&den=2026-10-31').text
    assert f'data-stock-doc="{doc.id}"' in page and f'data-stock-doc="{other.id}"' not in page
    assert tab(page, 'Tất cả') == 1 and tab(page, 'Nháp') == 1
    assert 'class="lt-alert"' in page and '100.000' in page
    chip = client.get('/phieu-kho?kho=K58&tu=2026-10-01&den=2026-10-31').text
    assert f'data-stock-doc="{other.id}"' not in chip and 'Xóa lọc' in chip and user_csrf


def test_cash_list_tabs_tiles_and_footer_totals(db, client, cash_people):
    p = cash_people
    opening(db, p)
    posted(db, p['finance'], amount=200000)
    posted(db, p['finance'], kind='PC', amount=50000)
    draft = make_doc(db, p['finance'], amount=7000, description='Thu tiền mực khách Lan')
    service_login(client, p['finance'])
    page = client.get('/thu-chi?from=2026-10-01&to=2026-10-31').text
    assert tab(page, 'Tất cả') == 4 and tab(page, 'Nháp') == 1 and tab(page, 'Đã ghi') == 3
    assert 'thu <b>5.200.000</b>' in page and 'chi <b>50.000</b>' in page
    found = client.get('/thu-chi?from=2026-10-01&to=2026-10-31&q=muc+khach').text
    assert draft.number in found and tab(found, 'Tất cả') == 1
    drafts = client.get('/thu-chi?from=2026-10-01&to=2026-10-31&trang_thai=NHAP').text
    assert draft.number in drafts and ' lt-alert"' in drafts


def test_pending_list_tabs(db, client, people):
    from test_cxl1_pending_items import defect, manual
    from test_stock_reservations import stock
    p = people
    defect(db)
    stock(db, p, 10)
    item = manual(db, p, qty=2)
    service_login(client, p['warehouse'])
    page = client.get('/cho-xu-ly').text
    assert item.number in page and tab(page, 'Đang chờ') == 1 and tab(page, 'Tất cả') == 1 and tab(page, 'Xong') == 0
    assert client.get('/cho-xu-ly?status=tat-ca').status_code == 200
    assert item.number not in client.get('/cho-xu-ly?status=XONG').text
    assert item.number in client.get('/cho-xu-ly?q=' + item.number).text
