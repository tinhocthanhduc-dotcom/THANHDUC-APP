"""Tra giá sửa màn thợ (TUẤN duyệt mẫu T1–T3, 15/11/2026): chọn model, chọn lỗi, bấm Tra giá.

Điều kiện xong: tab Bảng giá của thợ mở /tra-gia; chọn model hiện lỗi của loại máy; Tra giá ra thẻ gói sửa với ô cấp 1/2/3
giá đã gồm VAT, ô cấp đã khai sáng, có dòng chính hãng; chưa chọn lỗi thì báo; model chưa có không đoán giá, gợi ý máy gần giống;
"Máy đang sửa của tôi" lấy từ phiếu chưa đóng của thợ; vai trò ngoài người xem bảng giá bị 403.
"""
from datetime import datetime, timezone

from app import price_book as pb
from app.models import Customer, RepairInspection, ServiceTicket
from test_bao_gia_nhanh import group, setup_book
from test_tk_tai_khoan import web_login


def _book(db, make_user):
    boss = make_user('tg.boss', 'director')
    fuser, feed = setup_book(db, boss)
    a4 = group(db, 'LASER_A4_DON')
    pb.save_service(db, boss, dict(id=str(fuser.id), version=str(fuser.version), kind='GOI', name='Sửa chữa cụm sấy', active='1',
                                   option_rows='0', **{f'g{a4.id}_p1': '250.000', f'g{a4.id}_p2': '450.000', f'g{a4.id}_p3': '650.000',
                                                       f'g{a4.id}_ch2': '600.000'}))
    db.commit()
    return boss, fuser, feed


def test_technician_picks_model_and_symptom_then_sees_vat_prices(client, db, make_user):
    _, fuser, feed = _book(db, make_user)
    tech = make_user('tg.tho', 'technician')
    web_login(client, tech)
    assert 'href="/tra-gia"' in client.get('/tho').text
    page = client.get('/tra-gia', params={'model': 'canon lbp 2900'}).text
    assert 'Canon LBP 2900' in page and 'value="Kẹt giấy"' in page and 'value="In nhăn giấy"' in page
    assert 'Máy in kim kêu' not in page  # lỗi của nhóm máy khác
    # Bấm Tra giá chưa chọn lỗi: báo, không ra giá.
    page = client.get('/tra-gia', params={'model': 'Canon LBP 2900', 'xem': '1'}).text
    assert 'Chọn ít nhất một lỗi của máy.' in page and 'tg2-card' not in page
    page = client.get('/tra-gia', params={'model': 'Canon LBP 2900', 'ht': 'Kẹt giấy', 'xem': '1'}).text
    assert f'data-tg2-service="{fuser.id}"' in page and f'data-tg2-service="{feed.id}"' in page
    # Cụm sấy 250/450/650k chưa VAT → 270.000/486.000/702.000; chính hãng 600k → 648.000. Khai C2 nên chỉ ô C2 sáng.
    card = page.split(f'data-tg2-service="{fuser.id}"')[1].split('</section>')[0]
    for v in ('270.000', '486.000', '702.000', '648.000'):
        assert v in card
    assert card.count('class="on"') == 1 and card.index('class="on"') < card.index('486.000') < card.index('702.000')
    feed_card = page.split(f'data-tg2-service="{feed.id}"')[1].split('</section>')[0]
    assert feed_card.count('class="on"') == 2 and 'chính hãng' not in feed_card


def test_unknown_model_never_guesses_and_suggests_similar(client, db, make_user):
    _book(db, make_user)
    tech = make_user('tg.tho2', 'technician')
    web_login(client, tech)
    page = client.get('/tra-gia', params={'model': 'Canon LBP 29', 'ht': 'Kẹt giấy', 'xem': '1'}).text
    assert 'Chưa có model “Canon LBP 29” trong bảng giá.' in page and 'tg2-card' not in page
    assert '/tra-gia?model=Canon%20LBP%202900' in page
    tim = client.get('/tra-gia/tim', params={'q': 'lbp'}).text
    assert 'Canon LBP 2900' in tim and 'Canon LBP 3500' in tim and 'Epson' not in tim
    assert 'Không thấy model' in client.get('/tra-gia/tim', params={'q': 'zzzz'}).text


def test_my_open_tickets_listed_and_permissions(client, db, make_user):
    _book(db, make_user)
    tech = make_user('tg.tho3', 'technician')
    db.add(Customer(code='KTD-000001', name='Công ty Phú Gia', kind='CONG_TY', search_text='ktd-000001 cong ty phu gia'))
    db.flush()
    open_t = ServiceTicket(number='DV-26-00001', customer_code='KTD-000001', created_by=tech.id, assignee_id=tech.id, status='MOI',
                           symptom='Kẹt giấy')
    closed = ServiceTicket(number='DV-26-00002', customer_code='KTD-000001', created_by=tech.id, assignee_id=tech.id,
                           status='HOAN_THANH', symptom='Kẹt giấy')
    db.add_all([open_t, closed])
    db.flush()
    now = datetime.now(timezone.utc)
    db.add_all([RepairInspection(ticket_id=open_t.id, model_name='canon lbp 2900', inspected_by=tech.id, inspected_at=now),
                RepairInspection(ticket_id=closed.id, model_name='Epson L3110', inspected_by=tech.id, inspected_at=now)])
    db.commit()
    web_login(client, tech)
    page = client.get('/tra-gia').text
    assert 'Máy đang sửa của tôi' in page and 'DV-26-00001' in page and 'Canon LBP 2900' in page
    assert 'DV-26-00002' not in page and 'Epson L3110' not in page
    client.cookies.clear()
    admin = make_user('tg.admin', 'admin')
    web_login(client, admin)
    assert client.get('/tra-gia').status_code == 403
    assert client.get('/tra-gia/tim', params={'q': 'lbp'}).status_code == 403


def test_technician_sees_product_sale_price_with_vat_only(client, db, make_user):
    """Mẫu SP1–SP2: giá bán gồm VAT, BH, Còn/Hết hàng; không giá vốn, không số tồn, không hàng ngừng bán; nhớ tab đã chọn."""
    from test_bang_gia_chung import stocked
    boss = make_user('tgsp.boss', 'director')
    stocked(db, boss, 'TD-9012', 'Hop muc HP 12A chinh hang', 'Muc', 1_250_000, 987_654, qty=7)
    stocked(db, boss, 'TD-9040', 'Hop muc 12A tuong thich', 'Muc', 250_000, 123_456, qty=0)
    p = stocked(db, boss, 'TD-9112', 'May in HP 1020 cu 12', 'May in cu', 1_500_000, 900_000, qty=1)
    p.warranty_months = 3
    stocked(db, boss, 'TD-9099', 'Hop muc 12A ngung ban', 'Muc', 200_000, 100_000, qty=4).active = False
    db.commit()
    tech = make_user('tgsp.tho', 'technician')
    web_login(client, tech)
    page = client.get('/tra-gia/san-pham', params={'q': '12'}).text
    assert 'Giá sản phẩm' in page and 'Giá sửa' in page
    card = page.split('data-tgsp-code="TD-9012"')[1].split('</section>')[0]
    assert '1.350.000' in card and 'Còn hàng' in card and 'BH' not in card  # 1.250.000 + 8% VAT
    card = page.split('data-tgsp-code="TD-9040"')[1].split('</section>')[0]
    assert '270.000' in card and 'Hết hàng' in card
    assert 'BH 3 tháng' in page.split('data-tgsp-code="TD-9112"')[1].split('</section>')[0]
    assert 'TD-9099' not in page
    for secret in ('987.654', '123.456', '900.000', '>7<'):  # giá vốn, số tồn
        assert secret not in page
    # Lọc nhóm hàng; tìm không ra thì báo, không đoán.
    page = client.get('/tra-gia/san-pham', params={'q': '12', 'nhom': 'May in cu'}).text
    assert 'TD-9112' in page and 'TD-9012' not in page
    assert 'Không thấy hàng này.' in client.get('/tra-gia/san-pham/tim', params={'q': 'zzzz'}).text
    assert 'data-tgsp-code="TD-9012"' in client.get('/tra-gia/san-pham/tim', params={'q': 'hp 12a'}).text
    # Nhớ tab: đã mở Giá sản phẩm thì bấm tab Bảng giá về lại Giá sản phẩm; bấm Giá sửa thì ở Giá sửa.
    r = client.get('/tra-gia', follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/tra-gia/san-pham'
    assert client.get('/tra-gia?tab=sua').status_code == 200
    assert client.get('/tra-gia', follow_redirects=False).status_code == 200
    client.cookies.clear()
    web_login(client, make_user('tgsp.admin', 'admin'))
    assert client.get('/tra-gia/san-pham', params={'q': '12'}).status_code == 403
