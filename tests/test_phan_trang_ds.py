"""Phân trang 25 dòng cho Đơn bán, Báo giá, Điều phối (nguyên tắc 23/10/2026): không cắt ngầm, tổng tính trên mọi dòng đã lọc."""
import re

from app import quotes, sales_orders as svc
from app.schemas import QuoteIn
from test_sales_orders import GOOD, body, people  # noqa: F401
from test_web import new_service, service_login, service_people  # noqa: F401


def orders(html):
    return re.findall(r'class="code qlink" href="/don-ban/(\d+)"', html)


def quote_ids(html):
    return re.findall(r'href="/bao-gia/(\d+)/in"', html)


def test_sales_orders_paged_no_silent_cap_and_totals_over_all(client, db, people):
    made = [svc.create(db, people['sales'], body(lines=[GOOD])) for _ in range(105)]
    service_login(client, people['sales'])
    first = client.get('/don-ban').text
    last = client.get('/don-ban?trang=5').text
    assert len(orders(first)) == 25 and len(orders(last)) == 5
    assert 'Hiện <b>1–25</b> / 105 đơn' in first and 'Hiện <b>101–105</b> / 105 đơn' in last
    # Đơn cũ nhất (trước đây bị cắt ở 100 đơn) nay nằm ở trang cuối.
    assert str(made[0].id) in orders(last)
    seen = set()
    for n in range(1, 6):
        page = orders(client.get(f'/don-ban?trang={n}').text)
        assert not seen & set(page)
        seen |= set(page)
    assert seen == {str(o.id) for o in made}
    # Tổng tiền tính trên cả 105 đơn, không chỉ trang đang xem.
    total = sum(o.total for o in made)
    assert f'{total:,}'.replace(',', '.') + ' đ' in first
    # Trang quá lớn mở trang cuối; lọc trực tiếp (phan=1) giữ bộ lọc trong link trang.
    assert orders(client.get('/don-ban?trang=99').text) == orders(last)
    part = client.get('/don-ban?phan=1&status=NHAP&q=&nguoi=&fulfillment=').text
    assert '<html' not in part and 'href="?status=NHAP&amp;trang=2"' in part
    assert len(orders(client.get('/don-ban?so=100').text)) == 100


def test_quotes_paged_without_cap(client, db, people):
    made = [quotes.create(db, people['sales'], QuoteIn(customer_code='KTD-000001', lines=[dict(description='Lắp đặt', qty=1, unit_price=500)]))
            for _ in range(103)]
    service_login(client, people['sales'])
    first = client.get('/bao-gia').text
    last = client.get('/bao-gia?trang=5').text
    assert len(quote_ids(first)) == 25 and len(quote_ids(last)) == 3
    assert 'Hiện <b>101–103</b> / 103 báo giá' in last and str(made[0].id) in quote_ids(last)
    assert 'href="?status=NHAP&amp;trang=2"' in client.get('/bao-gia?status=NHAP').text


def test_dispatch_open_tickets_paged(client, db, service_people):
    for _ in range(27):
        new_service(db, service_people)
    service_login(client, service_people['dispatcher'])
    first = client.get('/dieu-phoi').text
    second = client.get('/dieu-phoi?trang=2').text
    assert 'Hiện <b>1–25</b> / 27 phiếu' in first and 'Hiện <b>26–27</b> / 27 phiếu' in second
    assert '<h2 class="dp-title">Đơn đang mở <span>27</span></h2>' in first
    assert 'href="?view=new&amp;trang=2"' in client.get('/dieu-phoi?view=new').text
