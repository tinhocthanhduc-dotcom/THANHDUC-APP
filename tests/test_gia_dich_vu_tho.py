"""Bảng giá màn thợ 3 tab, tab Giá dịch vụ (TUẤN duyệt mẫu D1–D3, 15/11/2026; migration 0099).

Điều kiện xong: dịch vụ Bảng giá chung có Loại (Dịch vụ / Phụ phí) và Cách tính giá (Một giá / Theo nhóm máy / model, mỗi nhóm
một giá, model ghi đè nhóm). Thợ chọn model → dịch vụ (ô xổ xuống, −/+ số lượng) → phụ phí; tổng gồm VAT. Dịch vụ theo model
chưa có giá cho máy đó thì không hiện (không đoán giá). Phụ phí chỉ là dòng Loại = Phụ phí; tên "Phí…/Phụ phí…" mặc định phụ phí.
"""
from app import price_book as pb
from test_bang_gia_chung import fail
from test_bao_gia_nhanh import group, setup_book
from test_tk_tai_khoan import web_login


def _dv_book(db, make_user):
    boss = make_user('dv.boss', 'director')
    setup_book(db, boss)
    a4, a3 = group(db, 'LASER_A4_DON'), group(db, 'LASER_A3')
    ink = pb.save_service(db, boss, dict(kind='PHI', loai='DV', cach='MODEL', name='Nạp mực', unit='hộp', warranty_months='0', active='1',
                                         **{f'g{a4.id}_p1': '100.000', f'g{a3.id}_p1': ''}))
    pb.save_model(db, boss, 'HP LaserJet M402dn', a4.id)
    pb.save_model_price(db, boss, ink.id, 'HP LaserJet M402dn', {'m_p1': '150.000'})
    clean = pb.save_service(db, boss, dict(kind='PHI', loai='DV', cach='MOT', name='Vệ sinh, bảo trì máy in', price='150.000',
                                           warranty_months='1', active='1'))
    db.commit()
    return boss, ink, clean


def _ids(db):
    return {s.name: s for s in pb.services(db, 'PHI')}


def test_service_types_default_by_name_and_model_price_lookup(db, make_user):
    boss, ink, clean = _dv_book(db, make_user)
    s = _ids(db)
    # Tên "Phí…" / "Phụ phí…" mặc định phụ phí; còn lại là dịch vụ.
    assert s['Phí kiểm tra máy'].is_fee and s['Phụ phí sửa tận nơi nội thành'].is_fee
    assert not s['Cài đặt máy in, driver'].is_fee and not clean.is_fee and ink.by_model and not clean.by_model
    assert [f.name for f in pb.fee_services(db)] == sorted(['Phí kiểm tra máy', 'Phụ phí sửa tận nơi nội thành'])
    m2900, m3500, m402 = (pb.find_model(db, n) for n in ('Canon LBP 2900', 'Canon LBP 3500', 'HP LaserJet M402dn'))
    assert pb.service_price(db, ink, m2900)['price_vat'] == 108_000  # giá nhóm A4 + 8% VAT
    assert pb.service_price(db, ink, m402) == dict(price=150_000, price_vat=162_000, warranty=0, source='model')  # model ghi đè nhóm
    assert pb.service_price(db, ink, m3500) is None and pb.service_price(db, ink, None) is None  # A3 để trống: không làm, không đoán
    assert pb.service_price(db, clean, None)['price_vat'] == 162_000
    # Phụ phí luôn một giá dù form gửi cách tính theo model; dịch vụ theo model không bắt buộc giá chung.
    fee = pb.save_service(db, boss, dict(id=str(s['Phí kiểm tra máy'].id), version=str(s['Phí kiểm tra máy'].version), loai='PHI',
                                         cach='MODEL', name='Phí kiểm tra máy', price='100.000', active='1'))
    assert fee.is_fee and not fee.by_model
    fail('BAD_PRICE', pb.save_service, db, boss, dict(kind='PHI', loai='DV', cach='MOT', name='Dịch vụ thiếu giá', price='', active='1'))
    # Giá riêng model chỉ cho gói sửa và dịch vụ theo model.
    fail('BAD_KIND', pb.save_model_price, db, boss, clean.id, 'Canon LBP 2900', {'m_p1': '1'})


def test_technician_service_tab_model_service_qty_fee_total(client, db, make_user):
    _, ink, clean = _dv_book(db, make_user)
    s = _ids(db)
    tech = make_user('dv.tho', 'technician')
    web_login(client, tech)
    page = client.get('/tra-gia/dich-vu').text
    assert 'Giá sửa' in page and 'Giá dịch vụ' in page and 'Giá mực' in page and 'aria-current="page">Giá dịch vụ' in page
    assert 'data-tg2-find="/tra-gia/dich-vu/tim"' in page and '/tra-gia/dich-vu?chung=1' in page
    assert '/tra-gia/dich-vu?model=Canon%20LBP%202900' in client.get('/tra-gia/dich-vu/tim', params={'q': '2900'}).text
    page = client.get('/tra-gia/dich-vu', params={'model': 'canon lbp 2900'}).text
    # Ô xổ xuống: "Theo máy" trước, rồi dịch vụ chung; phụ phí không lẫn vào dịch vụ và ngược lại.
    assert '<optgroup label="Theo máy Canon LBP 2900">' in page and '<optgroup label="Dịch vụ chung">' in page
    dv_select = page.split('data-tgdv-pick')[1].split('</select>')[0]
    assert 'Nạp mực · 108.000' in dv_select and 'Vệ sinh, bảo trì máy in · 162.000' in dv_select and 'Cài đặt máy in, driver · 108.000' in dv_select
    assert 'Phí kiểm tra' not in dv_select and 'Phụ phí' not in dv_select
    fee_select = page.split('data-tg3-feepick')[1].split('</select>')[0]
    assert 'Phí kiểm tra máy · 108.000' in fee_select and 'Cài đặt' not in fee_select and 'Nạp mực' not in fee_select
    tn = s['Phụ phí sửa tận nơi nội thành'].id
    page = client.get('/tra-gia/dich-vu', params=[('model', 'Canon LBP 2900'), ('dv', f'{ink.id}:2'), ('dv', f'{clean.id}:1'), ('pp', tn),
                                                   ('dv', '999999:1'), ('dv', f'{s["Phí kiểm tra máy"].id}:1'), ('pp', 999999)]).text
    # 2 × 108.000 + 162.000 = 378.000; + phụ phí tận nơi 54.000 = 432.000 (mẫu D1). Id lạ / phụ phí gửi như dịch vụ bỏ qua.
    assert 'Dịch vụ 378.000 · Phụ phí 54.000' in page and 'data-tg3-total>432.000<' in page
    assert f'data-sid="{ink.id}" data-price="108000" data-qty="2"' in page and f'<option value="{ink.id}" disabled>' in page
    assert 'data-tg3-total>10.692.000<' in client.get('/tra-gia/dich-vu', params=[('model', 'Canon LBP 2900'), ('dv', f'{ink.id}:500')]).text  # tối đa 99
    # Model ghi đè; nhóm máy chưa khai giá thì dịch vụ theo model không hiện; "chỉ dịch vụ chung" không có dịch vụ theo model.
    assert 'Nạp mực · 162.000' in client.get('/tra-gia/dich-vu', params={'model': 'HP LaserJet M402dn'}).text
    page = client.get('/tra-gia/dich-vu', params={'model': 'Canon LBP 3500'}).text
    assert 'Nạp mực' not in page and 'Vệ sinh, bảo trì máy in' in page and 'Theo máy' not in page
    page = client.get('/tra-gia/dich-vu', params={'chung': '1'}).text
    assert 'Chỉ dịch vụ chung' in page and 'Nạp mực' not in page and 'Vệ sinh, bảo trì máy in' in page
    page = client.get('/tra-gia/dich-vu', params={'model': 'Canon LBP 29'}).text
    assert 'Chưa có model “Canon LBP 29” trong bảng giá.' in page and 'data-tgdv-row' not in page
    # Giá sửa: phụ phí chỉ còn dòng Loại = Phụ phí.
    page = client.get('/tra-gia', params={'model': 'Canon LBP 2900'}).text
    assert 'Phí kiểm tra máy' in page and 'Cài đặt máy in, driver' not in page
    # Nhớ tab Giá dịch vụ.
    client.get('/tra-gia/dich-vu')
    r = client.get('/tra-gia', follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/tra-gia/dich-vu'
    client.cookies.clear()
    web_login(client, make_user('dv.admin', 'admin'))
    assert client.get('/tra-gia/dich-vu', params={'model': 'Canon LBP 2900'}).status_code == 403
    assert client.get('/tra-gia/dich-vu/tim', params={'q': '2900'}).status_code == 403


def test_director_form_list_and_excel_columns(client, db, make_user):
    boss, ink, clean = _dv_book(db, make_user)
    web_login(client, boss)
    page = client.get(f'/bang-gia/dich-vu/{ink.id}').text
    assert 'name="loai" value="DV" checked' in page and 'name="cach" value="MODEL" checked' in page
    assert 'Giá theo nhóm máy' in page and 'value="100.000"' in page and 'data-override="HP LaserJet M402dn"' in page and 'name="m_p1"' in page
    page = client.get(f'/bang-gia/dich-vu/{clean.id}').text
    assert 'name="cach" value="MOT" checked' in page and 'id="gia-model"' not in page
    lst = client.get('/bang-gia', params={'tab': 'dich-vu'}).text
    row = lst.split(f'data-service-row="{ink.id}"')[1].split('</tr>')[0]
    assert 'Theo model' in row and 'theo nhóm máy / model' in row
    fee_row = lst.split(f'data-service-row="{_ids(db)["Phí kiểm tra máy"].id}"')[1].split('</tr>')[0]
    assert 'data-service-type>Phụ phí<' in fee_row and '100.000' in fee_row
    # Excel dịch vụ thêm cột Loại; dịch vụ theo model nằm trong file gói (cấp 1).
    text = pb.export_csv(db, 'dich-vu')
    assert text.splitlines()[0].endswith('Loại') and 'Nạp mực,hộp,0,0,Dịch vụ theo model' in text and 'Phí kiểm tra máy,lần,100000,0,Phụ phí' in text
    goi = pb.export_csv(db, 'goi')
    a4 = group(db, 'LASER_A4_DON')
    assert f'Nạp mực,{a4.name},1,100000,,0' in goi and 'Nạp mực,HP LaserJet M402dn,1,150000,,0' in goi
    head = ','.join(pb.CSV_HEAD['goi'])
    plan = pb.plan_import(db, 'goi', f'{head}\nNạp mực,{a4.name},1,120000,,0\n'.encode())
    assert not plan['errors'] and plan['changes'][0]['old'] == 100_000 and plan['changes'][0]['new'] == 120_000
    assert 'chỉ ghi cấp 1' in pb.plan_import(db, 'goi', f'{head}\nNạp mực,{a4.name},2,120000,,0\n'.encode())['errors'][0]
    assert pb.apply_import(db, boss, 'goi', f'{head}\nNạp mực,{a4.name},1,120000,,0\n'.encode()) == 1
    assert pb.service_price(db, ink, pb.find_model(db, 'Canon LBP 2900'))['price'] == 120_000
    # Cột Loại: đổi loại qua Excel; file cũ 4 cột vẫn nhập được (giữ loại cũ, mới thì đoán theo tên).
    dhead = ','.join(pb.CSV_HEAD['dich-vu'])
    assert pb.apply_import(db, boss, 'dich-vu', f'{dhead},Loại\nCài đặt máy in,lần,100000,0,Phụ phí\nPhí đi xa,lần,30000,0\n'.encode()) == 2
    s = _ids(db)
    assert s['Cài đặt máy in, driver'].is_fee is False and s['Phí đi xa'].is_fee
    assert 'cột Loại' in pb.plan_import(db, 'dich-vu', f'{dhead},Loại\nX,lần,1,0,Lạ\n'.encode())['errors'][0]
