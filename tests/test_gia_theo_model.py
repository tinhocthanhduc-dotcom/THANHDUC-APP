"""Giá theo model (TUẤN duyệt mẫu 16/11/2026): chọn model trước, sửa mọi gói sửa + dịch vụ theo model trên một trang.

Ô trống = giá nhóm, ô có giá = giá riêng model, xóa trắng / "Về giá nhóm" = bỏ giá riêng; chỉ giám đốc sửa; tra giá lấy giá riêng trước.
"""
import pytest
from sqlalchemy import select

from app import price_book as pb
from app.errors import BusinessError
from app.models import AuditLog, RepairPrice
from test_bang_gia_chung import fuser_package, group
from test_tk_tai_khoan import web_login


def setup(db, boss):
    a4 = group(db, 'LASER_A4_DON')
    s = fuser_package(db, boss)  # A4 đơn năng 250k/450k/650k, CH 600k/850k
    ink = pb.save_service(db, boss, dict(kind='PHI', loai='DV', cach='MODEL', name='Nạp mực', unit='hộp', warranty_months='0', active='1',
                                         **{f'g{a4.id}_p1': '80.000'}))
    m = pb.save_model(db, boss, 'Canon LBP 6030', a4.id)
    return s, ink, m


def test_sheet_shows_group_and_own_and_saves_per_cell(db, make_user):
    boss = make_user('gm.boss', 'director')
    s, ink, m = setup(db, boss)
    sheet = pb.model_sheet(db, m)
    row = next(r for r in sheet['packages'] if r['service'].id == s.id)
    assert row['status'] == 'nhom' and row['group'][2].price == 450_000 and not row['own'] and row['warranty'] == {1: 1, 2: 3, 3: 3}
    assert next(r for r in sheet['services'] if r['service'].id == ink.id)['status'] == 'nhom'
    # Sửa cấp 2 gói + giá nạp mực; cấp 1, 3 để trống = vẫn theo nhóm.
    n = pb.save_model_sheet(db, boss, m.id, {f's{s.id}_p1': '', f's{s.id}_p2': '600.000', f's{s.id}_p3': '', f's{ink.id}_p1': '90.000'})
    assert n == 2
    r2 = pb.lookup(db, s.id, 'Canon LBP 6030', 2)
    assert r2['price'] == 600_000 and r2['source'] == 'model' and r2['warranty'] == 3
    assert pb.lookup(db, s.id, 'Canon LBP 6030', 3)['source'] == 'nhom'
    assert pb.service_price(db, ink, m)['price'] == 90_000
    assert db.scalar(select(AuditLog).where(AuditLog.action == 'repair_price.save', AuditLog.entity_id == str(s.id))) is not None
    # Lưu lại y nguyên: không đổi gì, không ghi lịch sử thêm.
    before = len(list(db.scalars(select(AuditLog).where(AuditLog.action == 'repair_price.save'))))
    assert pb.save_model_sheet(db, boss, m.id, {f's{s.id}_p2': '600.000', f's{ink.id}_p1': '90.000'}) == 0
    assert len(list(db.scalars(select(AuditLog).where(AuditLog.action == 'repair_price.save')))) == before
    # Xóa trắng = về giá nhóm.
    pb.save_model_sheet(db, boss, m.id, {f's{s.id}_p2': '', f's{ink.id}_p1': ''})
    assert pb.lookup(db, s.id, 'Canon LBP 6030', 2)['source'] == 'nhom'
    assert pb.service_price(db, ink, m)['source'] == 'nhom'
    assert not list(db.scalars(select(RepairPrice).where(RepairPrice.model_id == m.id)))


def test_sheet_validation_and_permission(db, make_user):
    boss, sales = make_user('gm.boss2', 'director'), make_user('gm.sale', 'sales')
    s, ink, m = setup(db, boss)
    with pytest.raises(BusinessError) as exc:
        pb.save_model_sheet(db, sales, m.id, {f's{s.id}_p2': '500.000'})
    assert exc.value.code == 'FORBIDDEN'
    # Cấp 1 riêng cao hơn cấp 2 của nhóm: chặn (so cả giá nhóm), không lưu dòng nào.
    with pytest.raises(BusinessError) as exc:
        pb.save_model_sheet(db, boss, m.id, {f's{ink.id}_p1': '90.000', f's{s.id}_p1': '500.000'})
    assert exc.value.code == 'BAD_PRICE' and 'tính cả giá nhóm' in exc.value.message
    db.rollback()
    assert not list(db.scalars(select(RepairPrice).where(RepairPrice.model_id == m.id)))
    # Chính hãng chỉ đọc khi mở ô; không mở thì giữ chính hãng riêng đang có.
    pb.save_model_sheet(db, boss, m.id, {'co_ch': '1', f's{s.id}_p2': '500.000', f's{s.id}_ch2': '650.000'})
    assert pb.lookup(db, s.id, 'Canon LBP 6030', 2, genuine=True)['price'] == 650_000
    pb.save_model_sheet(db, boss, m.id, {f's{s.id}_p2': '520.000', f's{s.id}_ch2': ''})
    assert pb.lookup(db, s.id, 'Canon LBP 6030', 2, genuine=True)['price'] == 650_000
    assert pb.lookup(db, s.id, 'Canon LBP 6030', 2)['price'] == 520_000


def test_web_sheet_page_save_reset_and_view_only(client, db, make_user):
    boss, sales = make_user('gm.web', 'director'), make_user('gm.webs', 'sales')
    s, ink, m = setup(db, boss)
    db.commit()
    csrf = web_login(client, boss)
    assert 'href="/bang-gia/gia-model"' in client.get('/bang-gia?tab=goi').text
    assert '/bang-gia/gia-model?model=Canon%20LBP%206030' in client.get('/bang-gia/model-may').text
    page = client.get('/bang-gia/gia-model?model=canon lbp 6030').text
    assert 'Nhóm máy: Máy in Laser đen trắng A4 đơn năng' in page and f'name="s{s.id}_p2"' in page and 'placeholder="450.000"' in page
    assert f'name="s{ink.id}_p1"' in page and 'theo nhóm · 486.000 gồm VAT' in page
    r = client.post('/bang-gia/gia-model/luu', data={'csrf': csrf, 'model_id': str(m.id), f's{s.id}_p2': '600.000', f's{s.id}_p1': ''},
                    follow_redirects=False)
    assert r.status_code == 303 and 'da_luu=1' in r.headers['location']
    page = client.get(r.headers['location']).text
    assert 'value="600.000"' in page and 'riêng · nhóm 450.000' in page and 'Về giá nhóm' in page
    assert 'Giá riêng 1' in client.get('/bang-gia/gia-model?model=Canon+LBP+6030&loc=rieng').text
    bad = client.post('/bang-gia/gia-model/luu', data={'csrf': csrf, 'model_id': str(m.id), f's{s.id}_p1': '900.000'})
    assert bad.status_code == 400 and 'không thấp hơn cấp trước' in bad.text and 'value="900.000"' in bad.text
    r = client.post('/bang-gia/gia-model/luu', data={'csrf': csrf, 'model_id': str(m.id), f's{s.id}_p2': '600.000', 've_nhom': str(s.id)},
                    follow_redirects=False)
    assert r.status_code == 303
    assert pb.lookup(db, s.id, 'Canon LBP 6030', 2)['source'] == 'nhom'
    assert 'Chưa có model “Brother X” trong danh mục' in client.get('/bang-gia/gia-model?model=Brother X').text
    csrf = web_login(client, sales)
    page = client.get('/bang-gia/gia-model?model=Canon LBP 6030').text
    assert '450.000' in page and f'name="s{s.id}_p2"' not in page
    assert client.post('/bang-gia/gia-model/luu', data={'csrf': csrf, 'model_id': str(m.id), f's{s.id}_p2': '1'}).status_code == 403
