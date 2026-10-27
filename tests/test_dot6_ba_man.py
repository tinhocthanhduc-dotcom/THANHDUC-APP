"""Đợt 6: Việc của tôi theo phân hệ, Máy đang giữ đủ thông tin, Dịch vụ cùng khuôn danh sách. Chỉ giao diện."""
from datetime import timedelta

from app import equipment_custody as svc, service_tickets
from app.schemas import ServiceTicketIn
from test_equipment_custody import handover, machine, receive, ticket  # noqa: F401
from test_my_work import NOW, people, purchase_people  # noqa: F401
from test_web import service_login


def test_held_list_shows_customer_fault_place_next_step_and_filters(client, db, machine, monkeypatch):
    p, eq = machine
    t = ticket(db, p)
    monkeypatch.setattr(svc, '_now', lambda: NOW - timedelta(days=9))
    receive(db, p, eq, t)
    monkeypatch.setattr(svc, '_now', lambda: NOW)
    handover(db, p, eq, t)
    service_login(client, p['warehouse'])
    page = client.get('/may-dang-giu').text
    row = page[page.index('data-held-serial="AB123"'):page.index('</tr>', page.index('data-held-serial="AB123"'))]
    assert t.number in row and 'Máy cần mang về sửa' in row and 'Thợ giữ' in row and '9 ngày' in row
    # Bàn giao đang chờ kho nhận: bước tiếp là kho nhận máy, nút dẫn thẳng tới màn giữ máy.
    assert 'Nhận máy vào kho kỹ thuật' in row and f'href="/giu-may/{t.id}"' in row and 'Nhận máy ›' in row
    assert 'lt-alert' in row and 'class="page-head"' in page and 'class="tiles keep-phone"' in page
    assert '<span class="t-label">Chờ nhận</span><span class="t-n">1</span>' in page
    assert '<span class="t-label">Quá hạn giữ</span><span class="t-n">1</span>' in page
    assert 'AB123' in client.get('/may-dang-giu?xem=cho-nhan').text
    assert 'AB123' in client.get('/may-dang-giu?q=ab123').text and 'AB123' not in client.get('/may-dang-giu?q=khong-co').text
    assert 'AB123' not in client.get('/may-dang-giu?location=KHO_KY_THUAT').text
    assert client.get('/may-dang-giu?location=XYZ&xem=abc').status_code == 200


def test_my_work_urgent_band_on_top_money_band_collapsed_last(client, db, machine):
    p, eq = machine
    late = service_tickets.create(db, p['dispatcher'], ServiceTicketIn(customer_code='KTD-000001', serial='AB123', kind='MANG_VE', symptom='In mờ'))
    late.due_at = NOW - timedelta(hours=3)
    db.commit()
    service_login(client, p['dispatcher'])
    page = client.get('/viec-cua-toi').text
    hot = page[page.index('class="card work-now"'):page.index('</section>', page.index('class="card work-now"'))]
    # Phiếu quá hạn lên khung Làm ngay, ghi trễ bao lâu, không lặp ở khung bên dưới.
    assert late.number in hot and 'class="work-hot work-overdue"' in hot and 'Trễ 3 giờ' in hot and 'data-work-group' not in hot
    assert '<span class="t-label">Làm ngay</span>' in page and 'href="/viec-cua-toi?xem=now"' in page and 'href="/viec-cua-toi?khu=sv"' in page
    only_now = client.get('/viec-cua-toi?xem=now').text
    assert 'work-now' in only_now and 'work-day' not in only_now and 'work-cash' not in only_now
    assert 'work-now' in client.get('/viec-cua-toi?khu=late').text
    assert late.number in client.get('/viec-cua-toi?khu=sv').text
    assert client.get('/viec-cua-toi?khu=xyz&xem=abc').status_code == 200


def test_money_groups_sit_below_and_open_only_when_nothing_urgent(client, db, people, configured_finance):
    from test_my_work import order, report
    p = people
    o = order(db, p, status='DA_GIAO', amount=50000)
    report(db, p, o, 20000)
    service_login(client, p['director'])
    page = client.get('/viec-cua-toi').text
    money = page.index('class="card work-cash"')
    assert 'data-work-group="receipts_pending"' in page[money:] and 'data-work-group="orders_unreported"' in page[money:]
    assert 'work-now' not in page[money:] and 'Tiền · làm sau' in page
    # Không có việc gấp nào: khung tiền tự mở.
    if 'class="card work-now"' not in page:
        assert 'id="tien-lam-sau" open' in page and 'Không có việc gấp' in page
    else:
        assert page.index('class="card work-now"') < money


def test_service_list_uses_list_frame_without_pink_rows(client, db, machine):
    p, eq = machine
    t = ticket(db, p)
    service_login(client, p['dispatcher'])
    page = client.get('/dich-vu?assignee_id=chua&loai=MANG_VE').text
    assert 'class="filter-chip"' in page and 'Loại: Mang về' in page and 'Không có phiếu phù hợp.' in page
    page = client.get('/dich-vu').text
    assert 'class="page-head"' in page and 'class="card list-frame"' in page and 'class="list-table lt-cards svl-table"' in page
    assert f'data-href="/dich-vu/{t.id}"' in page and 'svl-row' not in page


def test_out_of_stock_rows_stay_in_day_band_not_urgent(client, db, people):
    # TUẤN 2026-10-26: mua hàng chưa phải gấp; tồn bằng 0 không kéo lên Làm ngay.
    from app import stock_min_levels
    from app.schemas import StockMinLevelIn
    p = people
    stock_min_levels.set_level(db, p['warehouse'], 'K58', 'TD-0001', StockMinLevelIn(min_qty=2, version=0))
    service_login(client, p['warehouse'])
    page = client.get('/viec-cua-toi').text
    assert 'class="card work-now"' not in page or 'TD-0001' not in page[page.index('class="card work-now"'):page.index('</section>', page.index('class="card work-now"'))]
    # TUẤN 2026-10-27: gập thành một dòng "Mua hàng · làm sau" như phần tiền.
    buy = page[page.index('class="card work-cash work-buy" id="mua-hang">'):]
    assert 'data-work-group="stock_low"' in buy and 'TD-0001' in buy and 'Hết hàng' in buy and 'ở khung Làm ngay' not in buy
    assert 'Hàng dưới mức tối thiểu · 1' in buy.split('</summary>')[0]
    assert 'class="card work-day"' not in page
