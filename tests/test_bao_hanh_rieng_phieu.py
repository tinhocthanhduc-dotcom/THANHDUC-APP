"""Bảo hành riêng phiếu + Giá chốt gõ gồm VAT (TUẤN duyệt mẫu STA8zzFHSzoMeKoxbfeohE 27/11/2026, phiếu DV-26-00160).

Điều kiện xong: GĐ/kinh doanh chọn bảo hành khi Duyệt giá, chọn sẵn theo gói; khác gói thì ghi lịch sử; bằng gói = bỏ;
thợ đổi gói/cấp thì bỏ; bản khách, tin Zalo và Sửa xong dùng đúng số đã duyệt; Giá chốt gõ gồm VAT thì app tự tách.
"""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import repair_inspection as ri
from app import repair_quotes as rq
from app import service_tickets
from app import service_workshop as svc
from app.errors import BusinessError
from app.models import ServiceStep, ServiceTicket
from test_gia_rieng_phieu import _inspected
from test_kiem_tra_may import form
from test_mv1_workshop import onsite, photo  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_my_work import people, purchase_people  # noqa: F401
from test_web import service_login
from test_xg1_workshop import holding, workshop  # noqa: F401


def _setup(db, workshop, make_user):
    p, a, b, t, eq = workshop
    p = dict(p, workshop=workshop)
    t, fuser, opts, boss = _inspected(db, p, make_user)
    return p, t, fuser, opts


def test_custom_warranty_flows_to_quote_and_repair_done(client, db, workshop, make_user):
    p, t, fuser, opts = _setup(db, workshop, make_user)
    disp = p['dispatcher']
    row = ri.inspection(db, t.id)
    q = ri.quote(db, t, row)
    assert q['warranty_table'] == 3 and q['warranty'] == 3 and q['warranty_custom'] is None  # gói cấp 2: bảo hành 3 tháng
    with pytest.raises(BusinessError) as e:
        ri.apply(db, disp, t.id, t.version, {}, warranty='5')
    assert e.value.code == 'WARRANTY_REQUIRED'
    db.rollback()
    # Qua web như anh bấm: chọn 6 tháng khi Duyệt giá.
    csrf = service_login(client, disp)
    db.refresh(t)
    r = client.post(f'/kiem-tra-may/{t.id}/ap-gia', data={'csrf': csrf, 'version': str(t.version), 'bao_hanh': '6'},
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    t, row = db.get(ServiceTicket, t.id), ri.inspection(db, t.id)
    assert (row.custom_warranty, row.warranty_months) == (6, 6)
    step = db.scalar(select(ServiceStep).where(ServiceStep.ticket_id == t.id, ServiceStep.action == 'quote_custom_warranty'))
    assert step.note == 'Bảo hành riêng 6 tháng (gói 3 tháng)'
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'data-rq-warranty-badge' in page and 'Bảo hành riêng · 6 tháng' in page
    # Bản khách, tin Zalo: 6 tháng.
    v = rq.create(db, disp, t.id, t.version)
    assert rq.warranty_text(v.snapshot) == '6 tháng kể từ ngày trả máy' and v.snapshot['chosen']['warranty'] == 6
    assert 'Bảo hành 6 tháng' in rq.suggested_text(db, v)[0]
    rq.mark_sent(db, disp, v.id, 'COPY')
    db.refresh(t)
    rq.agree(db, disp, v.id, t.version, 'PA1', 'ZALO', 'Anh Nam', service_tickets._now().date() + timedelta(days=1))
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    assert t.quote_approved_at
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'data-xg-warranty-locked' in page and 'Theo báo giá đã duyệt' in page
    # Sửa xong: không chọn lại, form gửi 1 tháng vẫn ghi 6 tháng theo báo giá.
    photo(db, p, t, 'SAU', who='dispatcher')
    svc.workshop_repaired(db, disp, t.id, t.version, repaired_by=p['technician'].id, mode='FULL', reason='', fault='Rách bao lụa',
                          fix='Thay bao lụa', warranty_months=1, warranty_scope='')
    db.expire_all()
    assert db.get(ServiceTicket, t.id).warranty_months == 6


def test_equal_to_package_clears_and_level_change_drops(db, workshop, make_user):
    p, t, fuser, opts = _setup(db, workshop, make_user)
    disp = p['dispatcher']
    ri.apply(db, disp, t.id, t.version, {}, warranty='0')
    db.expire_all()
    row = ri.inspection(db, t.id)
    assert (row.custom_warranty, row.warranty_months) == (0, 0)  # không bảo hành vẫn là một lựa chọn riêng
    t = db.get(ServiceTicket, t.id)
    ri.apply(db, disp, t.id, t.version, {})  # không gửi ô bảo hành: giữ nguyên
    db.expire_all()
    assert ri.inspection(db, t.id).custom_warranty == 0
    t = db.get(ServiceTicket, t.id)
    ri.apply(db, disp, t.id, t.version, {}, warranty='3')  # bằng theo gói = về theo gói
    db.expire_all()
    row = ri.inspection(db, t.id)
    assert (row.custom_warranty, row.warranty_months) == (None, 3)
    step = db.scalars(select(ServiceStep).where(ServiceStep.ticket_id == t.id, ServiceStep.action == 'quote_custom_warranty')
                      .order_by(ServiceStep.id.desc())).first()
    assert step.note == 'Bảo hành về theo gói (3 tháng)'
    t = db.get(ServiceTicket, t.id)
    ri.apply(db, disp, t.id, t.version, {}, warranty='12')
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy']))  # thợ lưu lại y hệt: giữ
    db.expire_all()
    assert ri.inspection(db, t.id).custom_warranty == 12
    t = db.get(ServiceTicket, t.id)
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy', 'Thay rulo ép']))  # đổi cấp: bỏ
    db.expire_all()
    assert ri.inspection(db, t.id).custom_warranty is None


def test_only_quoters_set_warranty(db, workshop, make_user):
    p, t, fuser, opts = _setup(db, workshop, make_user)
    kho = make_user('bh.kho', 'dispatcher', 'warehouse')
    for u in (kho, p['technician']):
        with pytest.raises(BusinessError) as e:
            ri.apply(db, u, t.id, t.version, {}, warranty='12')
        assert e.value.status == 403
        db.rollback()
    assert ri.inspection(db, t.id).custom_warranty is None


def test_custom_price_typed_with_vat(client, db, workshop, make_user):
    p, t, fuser, opts = _setup(db, workshop, make_user)
    sid = ri.inspection(db, t.id).items[0].service_id
    disp = p['dispatcher']
    # Gõ 540.000 đã gồm VAT → lưu 500.000 chưa VAT, tổng khách đúng 540.000.
    csrf = service_login(client, disp)
    db.refresh(t)
    r = client.post(f'/kiem-tra-may/{t.id}/ap-gia', data={'csrf': csrf, 'version': str(t.version), 'gia_gom_vat': '1',
                                                         f'gia_{sid}': '540.000', f'ly_do_gia_{sid}': 'Khách quen'}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    t, row = db.get(ServiceTicket, t.id), ri.inspection(db, t.id)
    assert row.items[0].custom_price == 500000
    assert ri.quote(db, t, row)['total'] == 540000
    # Số lẻ: 790.000 gồm VAT vẫn ra đúng 790.000.
    ri.apply(db, disp, t.id, t.version, {}, {}, {sid: '790.000'}, {sid: 'Khách quen'}, gross=True)
    db.expire_all()
    t, row = db.get(ServiceTicket, t.id), ri.inspection(db, t.id)
    assert ri.quote(db, t, row)['total'] == 790000 and row.items[0].custom_gross == 790000
    assert sum(x.amount + x.vat_amount for x in t.lines if x.kind == 'PHI') == 790000  # dòng phí trên phiếu cũng đúng 790.000
    # Gồm VAT bằng giá bảng (450.000 + VAT = 486.000) = về giá bảng, không cần lý do.
    ri.apply(db, disp, t.id, t.version, {}, {}, {sid: '486.000'}, {sid: ''}, gross=True)
    db.expire_all()
    assert ri.inspection(db, t.id).items[0].custom_price is None
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'name="gia_gom_vat"' in page and 'Đã gồm VAT' in page
