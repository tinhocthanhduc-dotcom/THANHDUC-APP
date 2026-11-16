"""Bảng giá màn thợ (TUẤN duyệt mẫu M1–M4, 15/11/2026): B1 model → B2 gói sửa (chọn cấp) → B3 phụ phí, tổng gồm VAT;
Giá sản phẩm thêm hàng −/+ và phụ phí.

Điều kiện xong: tab Bảng giá của thợ mở /tra-gia; chọn model hiện mọi gói có giá cho nhóm máy với ô cấp 1/2/3/chính hãng
gồm VAT (mặc định cấp 2, ô chưa có giá ghi —); phụ phí từ Bảng giá chung; tổng = cấp đã chọn + phụ phí; model chưa có không đoán
giá, gợi ý máy gần giống;
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


def _card(page, sid):
    return page.split(f'data-sid="{sid}"')[1].split('data-tg3-g')[0]


def test_three_steps_model_package_level_fee_total(client, db, make_user):
    _, fuser, feed = _book(db, make_user)
    tech = make_user('tg.tho', 'technician')
    web_login(client, tech)
    assert 'href="/tra-gia"' in client.get('/tho').text
    page = client.get('/tra-gia', params={'model': 'canon lbp 2900'}).text
    # B2: mọi gói có giá cho nhóm máy, không còn bước "Lỗi của máy"; mỗi gói đủ ô cấp gồm VAT, mặc định Cấp 2.
    assert 'Canon LBP 2900' in page and 'Gói sửa chữa' in page and 'Lỗi của máy' not in page
    card = _card(page, fuser.id)
    for v in ('270.000', '486.000', '702.000', '648.000'):  # 250/450/650k + chính hãng 600k, +8% VAT
        assert v in card
    assert 'value="2" data-price="486000" checked' in card and 'class="tg3-g"' not in card.split('>')[0]
    feed_card = _card(page, feed.id)
    assert 'C.hãng<b>—</b>' in feed_card and f'<option value="{feed.id}" >Sửa chữa bộ kéo giấy · 162.000–432.000</option>' in page
    # TUẤN 15/11: chọn gói bằng ô xổ xuống; thẻ gói chưa chọn ẩn, gói đã chọn thì mờ trong ô xổ xuống.
    assert 'data-tg3-pick' in page and 'hidden data-tg3-g' in page  # chưa có giá chính hãng: không đoán
    # B3: phụ phí từ Bảng giá chung, gồm VAT.
    assert 'Phí kiểm tra máy' in page and 'Miễn phí nếu khách đồng ý sửa.' in page and '108.000' in page
    fee_ids = {f.name: f.id for f in pb.services(db, 'PHI')}
    page = client.get('/tra-gia', params=[('model', 'Canon LBP 2900'), ('g', fuser.id), ('cap_' + str(fuser.id), 'CH'), ('g', feed.id),
                                           ('cap_' + str(feed.id), '1'), ('pp', fee_ids['Phụ phí sửa tận nơi nội thành']),
                                           ('g', 999999), ('pp', 999999)]).text
    # 648.000 (sấy chính hãng) + 162.000 (kéo giấy C1) + 54.000 (tận nơi) = 864.000; id lạ bỏ qua.
    assert '2 gói · Phụ phí 54.000' in page and 'data-tg3-total>864.000<' in page
    # TUẤN 15/11: phụ phí cũng chọn bằng ô xổ xuống; phí đã chọn hiện dòng, mờ trong ô xổ xuống, phí chưa chọn ẩn.
    tn = fee_ids['Phụ phí sửa tận nơi nội thành']
    assert 'data-tg3-feepick' in page and f'<option value="{tn}" disabled>' in page
    assert f'class="tg3-fee on"  data-tg3-fee="{tn}"' in page and f'hidden data-tg3-fee="{fee_ids["Phí kiểm tra máy"]}"' in page
    assert 'value="CH" data-price="648000" checked' in _card(page, fuser.id)
    assert f'<option value="{fuser.id}" disabled>' in page and 'hidden data-tg3-g' not in page
    # Cấp không có giá thì về mặc định.
    page = client.get('/tra-gia', params=[('model', 'Canon LBP 2900'), ('g', feed.id), ('cap_' + str(feed.id), 'CH')]).text
    assert 'data-tg3-total>270.000<' in page
    # Nhóm máy khác chỉ hiện gói có giá cho nhóm đó.
    page = client.get('/tra-gia', params={'model': 'Canon LBP 3500'}).text
    assert f'data-sid="{fuser.id}"' in page and f'data-sid="{feed.id}"' not in page and '1.404.000' in page


def test_unknown_model_never_guesses_and_suggests_similar(client, db, make_user):
    _book(db, make_user)
    tech = make_user('tg.tho2', 'technician')
    web_login(client, tech)
    page = client.get('/tra-gia', params={'model': 'Canon LBP 29', 'g': '1'}).text
    assert 'Chưa có model “Canon LBP 29” trong bảng giá.' in page and 'data-tg3-g' not in page and 'data-tg3-total' not in page
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
    """Mẫu SP1–SP2 + M4: giá bán gồm VAT, BH; chọn từ danh sách xổ xuống; không giá vốn, không tồn kho, không hàng ngừng bán; nhớ tab."""
    from test_bang_gia_chung import stocked
    boss = make_user('tgsp.boss', 'director')
    stocked(db, boss, 'TD-9012', 'Hop muc HP 12A chinh hang', 'Muc', 1_250_000, 987_654, qty=7)
    stocked(db, boss, 'TD-9040', 'Hop muc 12A tuong thich', 'Muc', 250_000, 123_456, qty=0)
    p = stocked(db, boss, 'TD-9112', 'May in HP 1020 cu 12', 'May in cu', 1_500_000, 900_000, qty=1)
    p.warranty_months = 3
    stocked(db, boss, 'TD-9099', 'Hop muc 12A ngung ban', 'Muc', 200_000, 100_000, qty=4).active = False
    stocked(db, boss, 'TD-9077', 'Hop muc 12A tai che', 'Hộp mực renew', 150_000, 80_000, qty=3)  # TUẤN 16/11: bỏ nhóm renew
    db.commit()
    tech = make_user('tgsp.tho', 'technician')
    web_login(client, tech)
    page = client.get('/tra-gia/san-pham', params={'q': '12'}).text
    # Mẫu D2 (TUẤN 15/11): tab đổi tên Giá mực, chỉ hộp mực; ba tab Giá sửa · Giá dịch vụ · Giá mực.
    assert 'Giá mực' in page and 'Giá sửa' in page and 'Giá dịch vụ' in page and 'Giá sản phẩm' not in page
    assert 'TD-9112' not in page and 'May in HP 1020' not in page  # máy in không hiện ở Giá mực
    card = page.split('data-tgsp-code="TD-9012"')[1].split('</button>')[0]
    assert '1.350.000' in card and 'BH' not in card  # 1.250.000 + 8% VAT
    card = page.split('data-tgsp-code="TD-9040"')[1].split('</button>')[0]
    assert '270.000' in card
    assert 'Còn hàng' not in page and 'Hết hàng' not in page  # TUẤN 15/11: không hiện tồn
    assert 'TD-9099' not in page
    assert 'TD-9077' not in page  # nhóm Hộp mực renew không hiện ở Giá mực (lọc theo nhóm, tên không có chữ renew)
    for secret in ('987.654', '123.456', '>7<'):  # giá vốn, số tồn
        assert secret not in page
    # Danh sách xổ xuống (TUẤN 15/11): mỗi dòng bấm để thêm; tìm không ra thì báo, không đoán.
    assert 'class="tgsp-drop"' in page and 'data-tgsp-add data-tgsp-code="TD-9012"' in page
    assert 'Không thấy hộp mực này.' in client.get('/tra-gia/san-pham/tim', params={'q': 'zzzz'}).text
    assert 'data-tgsp-code="TD-9012"' in client.get('/tra-gia/san-pham/tim', params={'q': 'hp 12a'}).text
    # M4: hàng đã chọn (sp=mã:số lượng) + phụ phí ra tổng gồm VAT; hàng ngừng bán / mã lạ bỏ qua, số lượng tối đa 99.
    fee = next(f for f in pb.services(db, 'PHI') if f.name == 'Phí giao hàng') if any(f.name == 'Phí giao hàng' for f in pb.services(db, 'PHI')) \
        else pb.save_service(db, boss, dict(kind='PHI', name='Phí giao hàng', price='30.000', active='1'))
    db.commit()
    page = client.get('/tra-gia/san-pham', params=[('q', '12'), ('sp', 'TD-9012:2'), ('sp', 'td-9040:1'), ('sp', 'TD-9099:3'), ('sp', 'TD-0000:1'),
                                                    ('sp', 'TD-9112:1'), ('sp', 'TD-9077:1'), ('pp', fee.id)]).text
    assert 'Phí giao hàng' in page and '32.400' in page  # 30.000 + 8% VAT
    # 2 × 1.350.000 + 270.000 = 2.970.000; + 32.400 = 3.002.400.
    assert 'Mực 2.970.000 · Phụ phí 32.400' in page and 'data-code="TD-9112"' not in page  # máy in không vào giỏ Giá mực and 'data-tg3-total>3.002.400<' in page
    assert 'data-code="TD-9012"' in page and 'data-qty="2"' in page and 'data-code="TD-9099"' not in page
    assert 'data-code="TD-9077"' not in page  # renew không vào giỏ
    assert 'Đã chọn 2' in page.split('data-tgsp-code="TD-9012"')[1].split('</button>')[0]
    assert 'data-tg3-total>133.650.000<' in client.get('/tra-gia/san-pham', params={'sp': 'TD-9012:500'}).text  # tối đa 99 × 1.350.000
    # Nhớ tab: đã mở Giá sản phẩm thì bấm tab Bảng giá về lại Giá sản phẩm; bấm Giá sửa thì ở Giá sửa.
    r = client.get('/tra-gia', follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/tra-gia/san-pham'
    assert client.get('/tra-gia?tab=sua').status_code == 200
    assert client.get('/tra-gia', follow_redirects=False).status_code == 200
    client.cookies.clear()
    web_login(client, make_user('tgsp.admin', 'admin'))
    assert client.get('/tra-gia/san-pham', params={'q': '12'}).status_code == 403
