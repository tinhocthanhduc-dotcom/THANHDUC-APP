"""SY5 (báo cáo đồng bộ 25/10/2026, F6): nút Cấp và số điền sẵn dùng khả dụng (tồn − đã giữ), cùng logic reservation của lõi."""
import pytest
from app import part_requests as svc
from app.errors import BusinessError
from test_my_work import people, purchase_people, ticket  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_stock_reservations import order
from test_dot7_linh_kien_ncc import ask
from test_web import service_login


def test_supply_button_hidden_when_stock_is_reserved_for_orders(client, db, held):
    p, a, b = held
    r = ask(db, p, ticket(db, p, 'DANG_LAM'))  # yêu cầu 2 cái TD-0001; K58 tồn 10
    service_login(client, p['warehouse'])
    page = client.get('/yeu-cau-linh-kien').text
    assert 'value="K58" class="btn-ok">Cấp từ K58' in page and 'K58 · cấp được 10' in page
    # Đơn bán chốt giữ hết 10 cái: tồn vật lý còn 10 nhưng khả dụng 0 → không mời bấm, nói rõ lý do.
    order(db, p, qty=10)
    page = client.get('/yeu-cau-linh-kien').text
    assert 'Cấp từ K58' not in page and 'Hàng đang giữ cho đơn bán, không cấp được' in page
    assert 'K58 · cấp được 0' in page and 'tồn 10, giữ 10' in page and 'ps-no' in page
    # Bộ lọc "Thiếu hàng" xếp yêu cầu này vào thiếu, không còn ở "Cấp được ngay".
    assert r.number in client.get('/yeu-cau-linh-kien?ton=thieu').text
    assert r.number not in client.get('/yeu-cau-linh-kien?ton=du').text
    # Lõi vẫn chặn nếu cố cấp: RESERVED_STOCK, không ghi gì.
    with pytest.raises(BusinessError) as e:
        svc.supply(db, p['warehouse'], r.id, 'K58', r.version)
    assert e.value.code == 'RESERVED_STOCK'
    db.expire_all(); assert db.get(type(r), r.id).status == 'CHO_CAP'


def test_pick_list_shows_available_not_physical(client, db, held):
    p, a, b = held; t = ticket(db, p, 'DANG_LAM')
    order(db, p, qty=4)
    service_login(client, p['technician'])
    html = client.get(f'/dich-vu/{t.id}/tim-linh-kien?q=may').text
    assert 'K58: 6' in html and 'K58: 10' not in html
