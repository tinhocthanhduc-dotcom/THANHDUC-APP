"""Giá riêng phiếu khi Duyệt giá (TUẤN duyệt mẫu G296xR5EspFChNAWzbvvTy 26/11/2026, phiếu DV-26-00164).

Điều kiện xong: thợ chỉ chọn gói/cấp; GĐ/kinh doanh gõ Giá chốt (chưa VAT) thay giá bảng, khác giá bảng bắt buộc lý do;
dòng phí trên phiếu, bản in, link khách dùng giá chốt; trống hoặc bằng giá bảng = bỏ giá riêng; thợ đổi cấp thì bỏ;
lịch sử phiếu ghi giá riêng; người không có quyền báo giá không gửi được giá.
"""
import pytest
from sqlalchemy import select

from app import repair_inspection as ri
from app import repair_quotes as rq
from app.errors import BusinessError
from app.models import ServiceStep, ServiceTicket
from test_gui_bao_gia_sua import tomorrow
from test_kiem_tra_may import book, form
from test_mv1_workshop import onsite, photo  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_my_work import people, purchase_people  # noqa: F401
from test_web import service_login
from test_xg1_workshop import holding, workshop  # noqa: F401


def _inspected(db, p, make_user):
    boss = make_user('gr.boss', 'director')
    fuser, feed, opts = book(db, boss)
    t = p['workshop'][3]
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy']))
    db.refresh(t)
    return t, fuser, opts, boss


def _fee(t, row):
    return [x.unit_price for x in t.lines if x.kind == 'PHI' and x.description in (row.applied_lines or [])]


def test_custom_price_applies_everywhere(client, db, workshop, make_user):
    p, a, b, t, eq = workshop
    p = dict(p, workshop=workshop)
    t, fuser, opts, boss = _inspected(db, p, make_user)
    row = ri.inspection(db, t.id)
    sid = row.items[0].service_id
    table = ri.quote(db, t, row)['lines'][0]['pick']['price']
    assert table and table != 700000
    disp = p['dispatcher']
    # Khác giá bảng mà thiếu lý do: chặn.
    with pytest.raises(BusinessError) as e:
        ri.apply(db, disp, t.id, t.version, {}, {}, {sid: '700.000'}, {sid: ''})
    assert e.value.code == 'REASON_REQUIRED'
    db.rollback()
    with pytest.raises(BusinessError) as e:
        ri.apply(db, disp, t.id, t.version, {}, {}, {sid: '0'}, {sid: 'Miễn'})
    assert e.value.code == 'BAD_PRICE'
    db.rollback()
    # Qua web như anh bấm: dòng phí trên phiếu = 700.000, lịch sử ghi giá riêng.
    csrf = service_login(client, disp)
    db.refresh(t)
    r = client.post(f'/kiem-tra-may/{t.id}/ap-gia', data={'csrf': csrf, 'version': str(t.version), f'gia_{sid}': '700.000',
                                                         f'ly_do_gia_{sid}': 'Khách quen, đã báo miệng'}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    t, row = db.get(ServiceTicket, t.id), ri.inspection(db, t.id)
    assert (row.items[0].custom_price, row.items[0].custom_reason) == (700000, 'Khách quen, đã báo miệng')
    assert _fee(t, row) == [700000] and row.applied_at
    step = db.scalar(select(ServiceStep).where(ServiceStep.ticket_id == t.id, ServiceStep.action == 'quote_custom_price'))
    assert 'giá riêng 700.000' in step.note and f'bảng {table:,}'.replace(',', '.') in step.note and 'Khách quen' in step.note
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'data-rq-custom-badge' in page and 'value="700.000"' in page and '756.000' in page
    # Báo giá lần 1: bản khách chỉ một giá 700.000 + VAT, in ra đúng.
    v = rq.create(db, disp, t.id, t.version)
    assert v.snapshot['chosen']['total'] == 756000 and v.snapshot['lines'][0]['PA2'] is None
    pr = client.get(f'/kiem-tra-may/bao-gia/{v.id}/in').text
    assert '756.000' in pr and '700.000' in pr
    rq.mark_sent(db, disp, v.id, 'COPY')
    db.refresh(t)
    rq.agree(db, disp, v.id, t.version, 'PA1', 'ZALO', 'Anh Nam', tomorrow())
    db.expire_all()
    assert db.get(ServiceTicket, t.id).quote_approved_at


def test_blank_or_table_price_clears_and_level_change_drops(db, workshop, make_user):
    p, a, b, t, eq = workshop
    p = dict(p, workshop=workshop)
    t, fuser, opts, boss = _inspected(db, p, make_user)
    row = ri.inspection(db, t.id)
    sid = row.items[0].service_id
    table = ri.quote(db, t, row)['lines'][0]['pick']['price']
    disp = p['dispatcher']
    ri.apply(db, disp, t.id, t.version, {}, {}, {sid: '900000'}, {sid: 'Giảm cho khách'})
    db.refresh(t)
    # Bằng giá bảng = bỏ giá riêng, phiếu về giá bảng.
    ri.apply(db, disp, t.id, t.version, {}, {}, {sid: f'{table:,}'.replace(',', '.')}, {sid: 'x'})
    db.expire_all()
    t, row = db.get(ServiceTicket, t.id), ri.inspection(db, t.id)
    assert row.items[0].custom_price is None and row.items[0].custom_reason == '' and _fee(t, row) == [table]
    # Gõ lại, rồi để trống = về giá bảng.
    ri.apply(db, disp, t.id, t.version, {}, {}, {sid: '900.000'}, {sid: 'Giảm cho khách'})
    db.refresh(t)
    ri.apply(db, disp, t.id, t.version, {}, {}, {sid: ''}, {sid: ''})
    db.expire_all()
    assert ri.inspection(db, t.id).items[0].custom_price is None
    # Không gửi ô giá (không đổi) thì giữ giá riêng; thợ lưu lại y hệt cũng giữ; đổi cấp thì bỏ.
    t = db.get(ServiceTicket, t.id)
    ri.apply(db, disp, t.id, t.version, {}, {}, {sid: '900.000'}, {sid: 'Giảm cho khách'})
    db.refresh(t)
    ri.apply(db, disp, t.id, t.version, {}, {})
    db.expire_all()
    assert ri.inspection(db, t.id).items[0].custom_price == 900000
    t = db.get(ServiceTicket, t.id)
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy']))
    db.expire_all()
    assert ri.inspection(db, t.id).items[0].custom_price == 900000
    t = db.get(ServiceTicket, t.id)
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy', 'Thay rulo ép']))
    db.expire_all()
    assert all(it.custom_price is None for it in ri.inspection(db, t.id).items)  # thợ đổi lựa chọn: giá riêng bỏ


def test_only_quoters_set_price(db, workshop, make_user):
    p, a, b, t, eq = workshop
    p = dict(p, workshop=workshop)
    t, fuser, opts, boss = _inspected(db, p, make_user)
    sid = ri.inspection(db, t.id).items[0].service_id
    kho = make_user('gr.kho', 'dispatcher', 'warehouse')
    for u in (kho, p['technician']):
        with pytest.raises(BusinessError) as e:
            ri.apply(db, u, t.id, t.version, {}, {}, {sid: '700.000'}, {sid: 'Tự giảm'})
        assert e.value.status == 403
        db.rollback()
    assert ri.inspection(db, t.id).items[0].custom_price is None
