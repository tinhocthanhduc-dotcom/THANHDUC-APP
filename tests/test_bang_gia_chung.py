"""Bảng giá chung + gói sửa chữa (TUẤN duyệt 14/11/2026, đợt 1 quy trình báo giá sửa chữa).

Điều kiện xong đợt 1: tra đúng giá theo model, cấp và ngoại lệ; không đoán giá (Cần xác minh); chỉ giám đốc sửa;
giá vốn chỉ giám đốc/kế toán; bảng giá cho khách không lộ mã kho, giá vốn; mọi lần đổi giá có lịch sử.
"""
import pytest
from sqlalchemy import func, select

from app import price_book as pb
from app.errors import BusinessError
from app.models import AuditLog, PriceService, Product, RepairGroup, RepairModel, RepairPrice, StockBalance, Warehouse
from test_tk_tai_khoan import web_login


def fail(code, fn, *args, **kwargs):
    with pytest.raises(BusinessError) as exc:
        fn(*args, **kwargs)
    assert exc.value.code == code, str(exc.value)
    return exc.value


def group(db, code):
    return db.scalar(select(RepairGroup).where(RepairGroup.code == code))


def fuser_package(db, boss, **extra):
    """Gói Sửa chữa cụm sấy: Laser A4 đơn năng 250k/450k/650k (CH 600k/850k), Laser A3 cấp 2–3."""
    a4, a3 = group(db, 'LASER_A4_DON'), group(db, 'LASER_A3')
    form = dict(kind='GOI', name='Sửa chữa cụm sấy', description='Khắc phục lỗi cụm sấy.', technical_note='Kiểm tra nhiệt',
                scope_1='Vệ sinh, chỉnh', hours_1='1', scope_2='Thay 1 linh kiện chính', hours_2='1,5', scope_3='Thay từ 2', hours_3='2,5',
                option_rows='3', opt_name_0='Thay bao lụa sấy', opt_hint_0='bao lụa', opt_main_0='1', opt_name_1='Thay rulo ép',
                opt_main_1='1', opt_name_2='Thay bạc ép', active='1',
                **{f'g{a4.id}_p1': '250.000', f'g{a4.id}_bh1': '1', f'g{a4.id}_p2': '450.000', f'g{a4.id}_ch2': '600.000', f'g{a4.id}_bh2': '3',
                   f'g{a4.id}_p3': '650.000', f'g{a4.id}_ch3': '850.000', f'g{a4.id}_bh3': '3',
                   f'g{a3.id}_p2': '900.000', f'g{a3.id}_bh2': '3', f'g{a3.id}_p3': '1.300.000', f'g{a3.id}_bh3': '3'})
    form.update(extra)
    return pb.save_service(db, boss, form)


def test_seed_groups_and_lookup_priority_model_then_group_then_verify(db, make_user):
    boss = make_user('bg.boss', 'director')
    assert [g.name for g in pb.groups(db)] == ['Laser đen trắng A4 đơn năng', 'Laser đen trắng A4 đa năng', 'Laser màu A4', 'Laser A3',
                                               'Phun hộp mực', 'Phun bình mực', 'In kim, nhiệt']
    s = fuser_package(db, boss)
    assert [(o.name, o.is_main) for o in pb.options(db, s.id)] == [('Thay bao lụa sấy', True), ('Thay rulo ép', True), ('Thay bạc ép', False)]
    assert [pb.level_by_options(n) for n in (0, 1, 2, 3)] == [1, 2, 3, 3]
    a4 = group(db, 'LASER_A4_DON')
    pb.save_model(db, boss, 'Canon LBP 2900', a4.id)
    pb.save_model(db, boss, 'HP LaserJet M402', a4.id)
    pb.save_model(db, boss, 'Canon LBP 3500', group(db, 'LASER_A3').id)
    pb.save_model(db, boss, 'Epson L3110', group(db, 'PHUN_BINH').id)
    r = pb.lookup(db, s.id, 'canon lbp 2900', 2)
    assert r['ok'] and r['price'] == 450_000 and r['price_vat'] == 486_000 and r['warranty'] == 3 and r['source'] == 'nhom'
    assert r['name'] == 'Sửa chữa cụm sấy máy Canon LBP 2900'
    assert pb.lookup(db, s.id, 'Canon LBP 2900', 3, genuine=True)['price'] == 850_000
    # Cấp 1 không có giá chính hãng: không lấy giá thường thay, phải xác minh.
    r = pb.lookup(db, s.id, 'Canon LBP 2900', 1, genuine=True)
    assert not r['ok'] and 'chính hãng' in r['reason']
    # Model ghi đè chỉ cấp 2; cấp 3 vẫn theo nhóm.
    pb.save_model_price(db, boss, s.id, 'HP LaserJet M402', {'m_p2': '550.000', 'm_bh2': '3'})
    assert pb.lookup(db, s.id, 'HP LaserJet M402', 2)['price'] == 550_000
    assert pb.lookup(db, s.id, 'HP LaserJet M402', 2)['source'] == 'model'
    assert pb.lookup(db, s.id, 'HP LaserJet M402', 3)['price'] == 650_000
    # Nhóm A3 không có cấp 1; nhóm phun không làm dịch vụ; model lạ → Cần xác minh, không đoán.
    assert not pb.lookup(db, s.id, 'Canon LBP 3500', 1)['ok']
    assert pb.lookup(db, s.id, 'Canon LBP 3500', 2)['price'] == 900_000
    assert not pb.lookup(db, s.id, 'Epson L3110', 2)['ok']
    r = pb.lookup(db, s.id, 'Brother HL-1111 lạ', 2)
    assert not r['ok'] and 'chưa có trong danh mục' in r['reason'] and r['price'] is None
    pb.delete_model_price(db, boss, s.id, pb.find_model(db, 'HP LaserJet M402').id)
    assert pb.lookup(db, s.id, 'HP LaserJet M402', 2)['source'] == 'nhom'


def test_only_director_edits_and_validation(db, make_user):
    boss, sales, kt = make_user('bg.boss2', 'director'), make_user('bg.sale', 'sales'), make_user('bg.kt', 'finance')
    fail('FORBIDDEN', pb.save_service, db, sales, dict(kind='PHI', name='Phí kiểm tra', price='100.000'))
    fail('FORBIDDEN', pb.save_service, db, kt, dict(kind='PHI', name='Phí kiểm tra', price='100.000'))
    fail('FORBIDDEN', pb.save_model, db, kt, 'Canon LBP 2900', 1)
    fail('BAD_PRICE', pb.save_service, db, boss, dict(kind='PHI', name='Phí kiểm tra', price='abc'))
    fail('BAD_PRICE', pb.save_service, db, boss, dict(kind='PHI', name='Phí kiểm tra', price=''))
    a4 = group(db, 'LASER_A4_DON')
    fail('BAD_PRICE', fuser_package, db, boss, **{f'g{a4.id}_p1': '500.000'})  # cấp 1 cao hơn cấp 2
    db.rollback()
    fail('BAD_PRICE', fuser_package, db, boss, **{f'g{a4.id}_ch2': '100.000'})  # chính hãng thấp hơn thường
    db.rollback()
    assert db.scalar(select(PriceService)) is None
    fee = pb.save_service(db, boss, dict(kind='PHI', name='Phí kiểm tra máy', price='100.000', unit='lần', warranty_months='0'))
    assert fee.price == 100_000 and pb.with_vat(fee.price) == 108_000
    fail('DUPLICATE_SERVICE', pb.save_service, db, boss, dict(kind='PHI', name='phí KIỂM TRA máy', price='1'))
    pb.save_model(db, boss, 'Canon LBP 2900', a4.id)
    fail('DUPLICATE_MODEL', pb.save_model, db, boss, 'canon lbp 2900', a4.id)
    fail('VERSION_CONFLICT', pb.save_service, db, boss, dict(id=str(fee.id), version='99', kind='PHI', name='Phí kiểm tra máy', price='1'))


def test_history_records_every_price_change(db, make_user):
    boss = make_user('bg.boss3', 'director')
    s = fuser_package(db, boss)
    a4 = group(db, 'LASER_A4_DON')
    pb.save_service(db, boss, dict(id=str(s.id), version=str(s.version), kind='GOI', name='Sửa chữa cụm sấy', active='1',
                                   **{f'g{a4.id}_p1': '300.000', f'g{a4.id}_p2': '450.000', f'g{a4.id}_ch2': '600.000',
                                      f'g{a4.id}_bh2': '3', f'g{a4.id}_p3': '650.000', f'g{a4.id}_ch3': '850.000', f'g{a4.id}_bh3': '3'}))
    log = db.scalars(select(AuditLog).where(AuditLog.action == 'repair_price.save').order_by(AuditLog.id)).all()
    last = log[-1]
    assert last.before['levels']['1']['price'] == 250_000 and last.after['levels']['1']['price'] == 300_000
    # Lưu nhóm A3 không đổi gì → không ghi thêm dòng rác. Nhóm không gửi lên form thì giữ nguyên.
    assert db.scalar(select(RepairPrice).where(RepairPrice.group_id == group(db, 'LASER_A3').id, RepairPrice.level == 3)).price == 1_300_000
    rows, total = pb.history(db)
    assert total >= 3 and any('C1 250.000 → 300.000' in r['what'] for r in rows)


def stocked(db, boss, code, name, group_name, price, cost, qty=5, serial=False):
    from app.models import ProductGroup
    if group_name and not db.scalar(select(ProductGroup).where(ProductGroup.name == group_name)):
        db.add(ProductGroup(name=group_name))
    p = Product(code=code, name=name, group_name=group_name, sale_price=price, track_serial=serial, search_text=name.lower())
    db.add(p)
    if not db.get(Warehouse, 'KHO-T'):
        db.add(Warehouse(code='KHO-T', name='Kho test', address=''))
        db.flush()
    db.flush()
    db.add(StockBalance(warehouse_code='KHO-T', product_code=code, qty=qty, value=cost * qty, avg_cost=cost))
    db.commit()
    return p


def test_web_tabs_cost_only_for_director_and_finance_and_customer_export(client, db, make_user):
    boss, sales, kt, tho = make_user('bg.b', 'director'), make_user('bg.s', 'sales'), make_user('bg.k', 'finance'), make_user('bg.t', 'technician')
    stocked(db, boss, 'TD-9001', 'Hộp mực 12A tương thích', 'Hộp mực', 180_000, 95_000)
    stocked(db, boss, 'TD-9002', 'Drum Brother DR-2385', 'Drum', 260_000, 275_000)
    stocked(db, boss, 'TD-9003', 'Máy in Canon LBP 2900', 'Máy in', 3_500_000, 3_000_000, serial=True)
    stocked(db, boss, 'TD-9004', 'Giấy A4 Double A', 'Văn phòng phẩm', 75_000, 60_000)
    fuser_package(db, boss)
    pb.save_service(db, boss, dict(kind='PHI', name='Phụ phí sửa tận nơi', price='50.000'))
    web_login(client, sales)
    page = client.get('/bang-gia?tab=muc').text
    assert 'Hộp mực 12A tương thích' in page and '194.400' in page and 'Drum Brother' in page and 'Máy in Canon' not in page
    assert '95.000' not in page and 'Giá vốn' not in page and 'Giá dưới vốn' not in page
    assert 'Máy in Canon LBP 2900' in client.get('/bang-gia?tab=may-in').text
    assert 'Giấy A4 Double A' in client.get('/bang-gia?tab=linh-kien').text
    goi = client.get(f'/bang-gia?tab=goi&nhom={group(db, "LASER_A4_DON").id}').text
    assert 'Sửa chữa cụm sấy' in goi and '450.000' in goi and '486.000' in goi and '/bang-gia/dich-vu/' not in goi
    assert client.get('/bang-gia/excel?tab=muc').status_code == 403
    out = client.get('/bang-gia/in?tab=muc').text
    assert '194.400' in out and 'TD-9001' not in out and '95.000' not in out and 'đã bao gồm thuế GTGT' in out
    out = client.get(f'/bang-gia/in?tab=goi&nhom={group(db, "LASER_A4_DON").id}').text
    assert 'Sửa chữa cụm sấy' in out and '486.000' in out and 'Kiểm tra nhiệt' not in out and 'bao lụa' not in out
    web_login(client, kt)
    page = client.get('/bang-gia?tab=muc').text
    assert '95.000' in page and '47%' in page and 'Giá dưới vốn · 1' in page
    web_login(client, tho)
    assert client.get('/bang-gia?tab=dich-vu').status_code == 200
    web_login(client, boss)
    assert client.get('/bang-gia/excel?tab=muc').status_code == 200


def test_web_director_edits_package_model_symptom_and_lookup(client, db, make_user):
    boss, sales = make_user('bg.web', 'director'), make_user('bg.webs', 'sales')
    a4 = group(db, 'LASER_A4_DON')
    csrf = web_login(client, boss)
    r = client.post('/bang-gia/dich-vu/luu', data=dict(csrf=csrf, kind='GOI', name='Sửa chữa nguồn', active='1', option_rows='0',
                                                       **{f'g{a4.id}_p1': '300.000', f'g{a4.id}_p2': '450.000', f'g{a4.id}_bh2': '3'}),
                    follow_redirects=False)
    assert r.status_code == 303
    s = db.scalar(select(PriceService).where(PriceService.name == 'Sửa chữa nguồn'))
    page = client.get(f'/bang-gia/dich-vu/{s.id}').text
    assert 'Sửa chữa nguồn máy Canon LBP 2900' in page and 'value="450.000"' in page
    bad = client.post('/bang-gia/dich-vu/luu', data=dict(csrf=csrf, id=str(s.id), version=str(s.version), kind='GOI', name='Sửa chữa nguồn',
                                                         **{f'g{a4.id}_p1': '900.000', f'g{a4.id}_p2': '450.000'}))
    assert bad.status_code == 400 and 'không thấp hơn cấp trước' in bad.text and 'value="900.000"' in bad.text
    assert client.post('/bang-gia/model-may/luu', data=dict(csrf=csrf, name='Canon LBP 2900', group_id=str(a4.id)),
                       follow_redirects=False).status_code == 303
    assert client.post(f'/bang-gia/dich-vu/{s.id}/gia-model', data=dict(csrf=csrf, model='Canon LBP 2900', m_p2='500.000', m_bh2='6'),
                       follow_redirects=False).status_code == 303
    res = client.get(f'/bang-gia/tra-gia?dv={s.id}&model=Canon+LBP+2900&cap=2').text
    assert 'data-lookup="ok"' in res and '540.000' in res and 'Giá riêng của model' in res
    res = client.get(f'/bang-gia/tra-gia?dv={s.id}&model=Canon+LBP+2900&cap=3').text
    assert 'data-lookup="xac-minh"' in res and 'Cần kỹ thuật xác nhận' in res
    assert client.post('/bang-gia/hien-tuong/luu', data=dict(csrf=csrf, name='Bật không lên nguồn', service_id=str(s.id), level_min='1',
                                                              level_max='2'), follow_redirects=False).status_code == 303
    assert 'Bật không lên nguồn' in client.get('/bang-gia?tab=hien-tuong').text
    assert 'Sửa chữa nguồn' in client.get('/bang-gia/lich-su').text
    csrf = web_login(client, sales)
    assert client.post('/bang-gia/dich-vu/luu', data=dict(csrf=csrf, kind='PHI', name='X', price='1')).status_code == 403
    assert client.get('/bang-gia/dich-vu/moi').status_code == 403


def test_excel_roundtrip_preview_then_apply(client, db, make_user):
    boss = make_user('bg.ex', 'director')
    stocked(db, boss, 'TD-9101', 'Hộp mực 26A', 'Hộp mực', 320_000, 190_000)
    s = fuser_package(db, boss)
    a4 = group(db, 'LASER_A4_DON')
    text = pb.export_csv(db, 'goi')
    assert text.startswith('﻿') and 'Sửa chữa cụm sấy,Laser đen trắng A4 đơn năng,2,450000,600000,3' in text
    edited = text.replace('Sửa chữa cụm sấy,Laser đen trắng A4 đơn năng,2,450000,600000,3', 'Sửa chữa cụm sấy;Laser đen trắng A4 đơn năng;2;480000;600000;3')
    edited = edited.replace(',', ';')
    plan = pb.plan_import(db, 'goi', edited.encode('utf-8'))
    assert not plan['errors'] and [c['new'] for c in plan['changes']] == [480_000]
    bad = pb.plan_import(db, 'goi', (edited + 'Gói lạ;Laser A3;2;1;;0\n').encode('utf-8'))
    assert bad['errors'] and 'Dòng' in bad['errors'][0]
    fail('IMPORT_ERRORS', pb.apply_import, db, boss, 'goi', (edited + 'Gói lạ;Laser A3;2;1;;0\n').encode('utf-8'))
    assert pb.apply_import(db, boss, 'goi', edited.encode('utf-8')) == 1
    assert pb.lookup(db, s.id, '', 2)['ok'] is False
    assert db.scalar(select(RepairPrice).where(RepairPrice.group_id == a4.id, RepairPrice.level == 2)).price == 480_000
    csrf = web_login(client, boss)
    products = pb.export_csv(db, 'muc').replace('TD-9101,Hộp mực 26A,Hộp mực,320000', 'TD-9101,Hộp mực 26A,Hộp mực,330000')
    r = client.post('/bang-gia/nhap-excel', data=dict(csrf=csrf, tab='muc'), files={'file': ('gia.csv', products.encode('utf-8'), 'text/csv')})
    assert r.status_code == 200 and '330.000' in r.text and 'Áp dụng 1 thay đổi' in r.text
    assert db.get(Product, 'TD-9101').sale_price == 320_000
    r = client.post('/bang-gia/nhap-excel', data=dict(csrf=csrf, tab='muc', ap_dung='1', content=products), follow_redirects=False)
    assert r.status_code == 303
    assert db.get(Product, 'TD-9101', populate_existing=True).sale_price == 330_000
    log = db.scalar(select(AuditLog).where(AuditLog.action == 'product.price'))
    assert log.before['sale_price'] == 320_000 and log.after['sale_price'] == 330_000 and log.reason == 'Nhập Excel bảng giá'
    assert any('320.000 → 330.000' in r['what'] for r in pb.history(db)[0])
    fail('BAD_FILE', pb.plan_import, db, 'muc', b'M\xe3 h\xe0ng,T\xean\n')
    fail('BAD_FILE', pb.plan_import, db, 'muc', 'sai,tieu,de\n'.encode('utf-8'))


def test_sample_price_book_script_adds_once_and_never_overwrites(db, make_user):
    from scripts import bang_gia_mau
    boss = make_user('bg.mau', 'director')
    s = fuser_package(db, boss)  # gói đã có: script không đụng giá anh đã đặt
    assert bang_gia_mau.run(db, boss, apply=False) and db.scalar(select(func.count()).select_from(RepairModel)) == 0
    log = bang_gia_mau.run(db, boss, apply=True)
    assert not any('Sửa chữa cụm sấy ·' in line for line in log)
    assert db.scalar(select(RepairPrice).where(RepairPrice.service_id == s.id, RepairPrice.level == 2,
                                               RepairPrice.group_id == group(db, 'LASER_A4_DON').id)).price == 450_000
    assert pb.find_model(db, 'Canon LBP 2900').group_id == group(db, 'LASER_A4_DON').id
    nguon = db.scalar(select(PriceService).where(PriceService.name == 'Sửa chữa nguồn'))
    assert pb.lookup(db, nguon.id, 'Canon LBP 3500', 2)['price'] == 810_000  # A3 hệ số 1,8
    assert pb.lookup(db, nguon.id, 'Epson L3110', 1)['price'] == 300_000
    head = db.scalar(select(PriceService).where(PriceService.name == 'Sửa chữa cụm đầu phun'))
    assert not pb.lookup(db, head.id, 'Canon LBP 2900', 1)['ok']  # máy laser không có gói đầu phun
    assert pb.lookup(db, head.id, 'Epson L3110', 3, genuine=True)['price'] == 1_000_000
    assert len(pb.symptoms(db)) == len(bang_gia_mau.SYMPTOMS)
    assert bang_gia_mau.run(db, boss, apply=True) == []
