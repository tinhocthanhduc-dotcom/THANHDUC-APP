"""Báo cáo chạy thử luồng thợ 27/10/2026: Mực định kỳ không ảnh, bảo hành theo loại, trả máy từ DA_HEN một transaction."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import attachments, part_requests, service_tickets, technician_work, service_workshop as ws, equipment_custody as custody
from app.errors import BusinessError
from app.models import PartRequest, SalesReceipt, ServiceTicket, ServiceVisit, StockDoc
from app.schemas import PartRequestIn
from test_my_work import people, purchase_people, ticket, NOW  # noqa: F401
from test_technician_stock import held, transfer, balance  # noqa: F401
from test_mv1_workshop import onsite, photo, intake  # noqa: F401
from test_xg1_workshop import holding, workshop, quote, approve, repaired  # noqa: F401
from test_ux1e_gon_man_tho import data, post
from test_web import service_login


@pytest.fixture
def ink(db, held):  # noqa: F811
    # Phiếu Mực định kỳ đang làm, KHÔNG có ảnh nào (khác fixture job vốn đã có ảnh SAU, BIÊN BẢN).
    p, own, _ = held
    t = ticket(db, p, 'DANG_LAM')
    t.kind = 'MUC_DINH_KY'; db.commit()
    assert not attachments.has_visible(db, 'service_ticket', t.id, 'SAU')
    return p, own, t


def ink_form(client, p, t, **kw):
    form = data(client, p, t, material_product=[], material_qty=[], fault_found='', fix_done='', **kw)
    form.pop('repair_result', None)
    return form


@pytest.mark.parametrize('mode', ['FULL', 'PARTIAL', '', 'FREE'])
def test_muc_dinh_ky_without_any_photo_completes(client, db, ink, mode):
    p, own, t = ink
    extra = dict(collection_mode=mode, customer_amount='200.000')
    if mode == 'PARTIAL': extra['partial_amount'] = '100.000'
    if mode == 'FREE': extra.update(customer_amount='0', free_reason='Theo hợp đồng')
    r = post(client, t, ink_form(client, p, t, **extra))
    assert r.status_code == 303, r.text
    db.refresh(t)
    assert t.status in ('HOAN_THANH', 'DA_DONG') and t.fix_done == 'Đã giao mực định kỳ' and t.warranty_months == 0


def test_muc_dinh_ky_with_issued_ink_and_no_photo(client, db, ink):
    p, own, t = ink
    req = part_requests.create(db, p['dispatcher'], t.id, PartRequestIn(version=t.version, product_code='TD-0001', qty=2, reason='Mực định kỳ'))
    part_requests.supply(db, p['warehouse'], req.id, 'K58', req.version)
    db.refresh(t)
    assert t.status == 'DANG_LAM' and balance(db, own.code) == 2
    form = ink_form(client, p, t, collection_mode='FULL', customer_amount='200.000')
    r = post(client, t, form | dict(material_product=['TD-0001'], material_qty=['2']))
    assert r.status_code == 303, r.text
    db.refresh(t)
    assert t.status in ('HOAN_THANH', 'DA_DONG')


def test_repair_still_requires_after_photo(client, db, held):  # noqa: F811
    p, own, _ = held
    t = ticket(db, p, 'DANG_LAM')
    with pytest.raises(BusinessError) as e:
        technician_work.complete(db, p['technician'], t.id, t.version, '100000', 'FULL', '', [], fault='Kẹt', fix='Đã sửa xong')
    assert e.value.code == 'PHOTO_REQUIRED'
    db.refresh(t); assert t.status == 'DANG_LAM'


def test_muc_dinh_ky_ignores_old_draft_warranty(client, db, ink):
    p, own, t = ink
    r = post(client, t, ink_form(client, p, t, collection_mode='FULL', customer_amount='200.000', warranty_months='1'))
    assert r.status_code == 303, r.text
    db.refresh(t)
    assert t.warranty_months == 0


@pytest.fixture
def returning(db, workshop):  # noqa: F811
    p, a, b, t, eq = workshop
    quote(db, p, t); approve(db, p, t); photo(db, p, t, 'SAU', who='dispatcher'); repaired(db, p, t)
    ws.schedule_return(db, p['dispatcher'], t.id, t.version, service_tickets._now() + timedelta(days=1), None, p['technician'].id, 'Giao trả máy')
    hand = custody.pending(db, eq.serial)
    if hand: custody.confirm(db, p['technician'], hand.id, hand.version)
    db.refresh(t)
    assert t.status == 'DA_HEN'
    photo(db, p, t, 'TRA_MAY'); photo(db, p, t, 'BIEN_BAN')
    return p, t, eq


def test_return_from_appointment_bad_partial_rolls_back_arrival(client, db, returning):
    p, t, eq = returning
    before = (t.status, t.version, t.hold_reason)
    visits = db.scalar(select(func.count()).select_from(ServiceVisit).where(ServiceVisit.ticket_id == t.id))
    csrf = service_login(client, p['technician'])
    r = client.post(f'/tho/phieu/{t.id}/hoan-thanh', data=dict(csrf=csrf, version=t.version, customer_amount=str(t.receivable),
        collection_mode='PARTIAL', partial_amount='999.999', method='TIEN_MAT', gps_lat='10.77', gps_lng='106.70', gps_accuracy='12'), follow_redirects=False)
    assert r.status_code in (400, 409) and 'BAD_PARTIAL_AMOUNT' in r.text
    db.expire_all(); t = db.get(ServiceTicket, t.id)
    assert (t.status, t.version, t.hold_reason) == before
    assert db.scalar(select(func.count()).select_from(ServiceVisit).where(ServiceVisit.ticket_id == t.id)) == visits
    assert db.scalar(select(func.count()).select_from(SalesReceipt).where(SalesReceipt.ticket_id == t.id)) == 0


def test_return_from_appointment_valid_completes_with_arrival(db, returning):
    p, t, eq = returning
    technician_work.complete(db, p['technician'], t.id, t.version, t.receivable, 'FULL', '', [], return_machine=intake(eq),
                             arrive=dict(note='Đến khách trả máy', lat='10.77', lng='106.70', accuracy='12'))
    db.refresh(t)
    assert t.status == 'DA_DONG' and eq.custody_location == 'TAI_KHACH'
    visit = db.scalar(select(ServiceVisit).where(ServiceVisit.ticket_id == t.id).order_by(ServiceVisit.id.desc()))
    assert visit is not None and visit.arrive_lat is not None
