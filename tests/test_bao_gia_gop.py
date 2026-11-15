"""Báo giá gộp nhiều máy một khách (TUẤN duyệt mẫu 15/11/2026).

Điều kiện xong: gộp ≥2 phiếu cùng khách đã áp giá thành BGG-yy-nnnnn; mỗi phiếu có lần báo giá riêng (giá đúng dòng tiền phiếu);
một link /bgg chỉ phần cho khách; khách trả lời từng máy → đúng Khách đồng ý / Chưa đồng ý của từng phiếu; điều phối mới làm được;
khách khác hoặc máy chưa áp giá bị chặn, không tạo nửa chừng.
"""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import equipment, equipment_custody as custody
from app import price_book as pb
from app import repair_inspection as ri
from app import repair_quote_groups as rg
from app import repair_quotes as rq
from app import service_tickets
from app import service_workshop as svc
from app.models import RepairInspection, RepairQuoteGroup, RepairQuoteVersion, ServiceTicket
from app.schemas import CustodyHandover, EquipmentIn
from test_gui_bao_gia_sua import err, ready, tomorrow
from test_kiem_tra_may import form
from test_mv1_workshop import intake, onsite, photo  # noqa: F401
from test_my_work import people, purchase_people, ticket  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_xg1_workshop import holding, workshop  # noqa: F401


def second(db, p, serial='MV1-MAY2'):
    """Máy thứ hai của cùng khách về xưởng (giống fixture workshop)."""
    t = ticket(db, p, 'DANG_LAM')
    eq = equipment.create(db, p['dispatcher'], EquipmentIn(serial=serial, product_code='TD-0001', customer_code=t.customer_code))
    t.serial = eq.serial
    db.commit()
    photo(db, p, t)
    svc.bring(db, p['technician'], t.id, t.version, intake(eq), [])
    svc.hand_to_workshop(db, p['technician'], t.id, t.version, CustodyHandover(version=eq.version, location='KHO_KY_THUAT'))
    hand = custody.pending(db, eq.serial)
    custody.confirm(db, p['warehouse'], hand.id, hand.version)
    db.refresh(t)
    return t


@pytest.fixture
def pair(db, workshop, make_user):
    p, a, b, t1, eq = workshop
    p = dict(p, workshop=workshop)
    t1, fuser, boss = ready(db, p, make_user)
    t2 = second(db, p)
    opts = {o.name: o.id for o in pb.options(db, fuser.id)}
    ri.save(db, p['technician'], t2.id, form(fuser, opts, ['Thay bao lụa sấy']))
    ri.apply(db, p['dispatcher'], t2.id, t2.version, {})
    db.refresh(t2)
    return p, t1, t2, fuser


def picks(*ts):
    return {t.id: t.version for t in ts}


def test_group_creates_one_version_per_ticket_and_one_link(db, pair):
    p, t1, t2, fuser = pair
    disp = p['dispatcher']
    err('FORBIDDEN', rg.create, db, p['technician'], t1.customer_code, picks(t1, t2))
    db.rollback()
    err('PICK_TWO', rg.create, db, disp, t1.customer_code, picks(t1))
    db.rollback()
    old = rq.create(db, disp, t1.id, t1.version)  # lần riêng đang chờ: gộp sẽ thay
    g = rg.create(db, disp, t1.customer_code, picks(t1, t2))
    assert g.number.startswith('BGG-') and len(g.token) >= 24
    members = rg.members(db, g)
    assert [v.ticket_id for v in members] == sorted([t1.id, t2.id]) and all(v.status == 'CHO' and v.group_id == g.id for v in members)
    db.refresh(old)
    assert old.status == 'THAY_THE'
    # Tổng nhóm = cộng đúng bản chụp từng phiếu (486.000 mỗi máy), phương án chính hãng 648.000 mỗi máy.
    tot = rg._totals([v.snapshot for v in members])
    assert tot['chosen'] == dict(subtotal=900000, vat=72000, total=972000) and tot['pa2'] == 1296000 and tot['pa1'] is None
    assert '972.000đ' in g.text and rg.link(g) in g.text and t1.number in g.text and 'TD-0001' not in g.text
    # Tiền phiếu không đổi.
    assert [x.unit_price for x in db.get(ServiceTicket, t1.id).lines] == [450000]
    assert rg.for_ticket(db, t2.id).id == g.id


def test_group_refuses_other_customer_or_not_applied_atomically(db, pair, make_user):
    p, t1, t2, fuser = pair
    disp = p['dispatcher']
    row = db.get(RepairInspection, t2.id)
    row.applied_at = None
    db.commit()
    err('NOT_APPLIED', rg.create, db, disp, t1.customer_code, picks(t1, t2))
    db.rollback()
    assert db.scalar(select(RepairQuoteGroup)) is None and db.scalar(select(RepairQuoteVersion)) is None
    ri.apply(db, disp, t2.id, db.get(ServiceTicket, t2.id).version, {})
    t2 = db.get(ServiceTicket, t2.id)
    from app.models import Customer
    other = db.scalar(select(Customer).where(Customer.code != t1.customer_code))
    if other is not None:
        err('OTHER_CUSTOMER', rg.create, db, disp, other.code, picks(t1, t2))
        db.rollback()
    err('VERSION_CONFLICT', rg.create, db, disp, t1.customer_code, {t1.id: t1.version - 1, t2.id: t2.version})
    db.rollback()
    assert db.scalar(select(RepairQuoteVersion)) is None


def test_public_link_send_and_revoke(db, pair, monkeypatch):
    p, t1, t2, fuser = pair
    disp = p['dispatcher']
    g = rg.create(db, disp, t1.customer_code, picks(t1, t2))
    assert rg.public(db, 'ngan')[1] == 'khong_co'
    gg, state, rows = rg.public(db, g.token)
    assert state == 'ok' and len(rows) == 2 and db.get(RepairQuoteGroup, g.id).opened_count == 1
    assert all(v.opened_count == 1 for v in rg.members(db, g))
    err('TEXT_NUMBERS', rg.mark_sent, db, disp, g.id, g.text.replace('972.000', '900.000'))
    db.rollback()
    err('NOT_LINKED', rg.send_bot, db, disp, g.id)
    db.rollback()
    rg.mark_sent(db, disp, g.id)
    assert all(v.sent_at and v.sent_via == 'COPY' for v in rg.members(db, g))
    assert ri.waiting_rows(db)[0]['stage'].startswith('Chờ khách trả lời')
    monkeypatch.setattr(rq, '_today', lambda: g.valid_until + timedelta(days=1))
    assert rg.public(db, g.token)[1] == 'het_han'
    monkeypatch.undo()
    rg.revoke(db, disp, g.id)
    assert rg.public(db, g.token)[1] == 'thu_hoi' and all(v.status == 'THU_HOI' for v in rg.members(db, g))


def test_reply_per_machine_uses_each_ticket_flow(db, pair):
    p, t1, t2, fuser = pair
    disp = p['dispatcher']
    g = rg.create(db, disp, t1.customer_code, picks(t1, t2))
    t1, t2 = db.get(ServiceTicket, t1.id), db.get(ServiceTicket, t2.id)
    vers = picks(t1, t2)
    err('NO_ANSWER', rg.reply, db, disp, g.id, {t1.id: '', t2.id: ''}, vers, 'ZALO', 'Chị Hương', tomorrow())
    err('REASON_REQUIRED', rg.reply, db, disp, g.id, {t1.id: 'PA2', t2.id: 'CHUA'}, vers, 'ZALO', 'Chị Hương', tomorrow())
    err('PERSON_REQUIRED', rg.reply, db, disp, g.id, {t1.id: 'PA2', t2.id: ''}, vers, 'ZALO', '', tomorrow())
    err('BAD_APPOINTMENT', rg.reply, db, disp, g.id, {t1.id: 'PA1'}, vers, 'ZALO', 'Chị Hương', service_tickets._now().date() - timedelta(days=1))
    db.rollback()
    done = rg.reply(db, disp, g.id, {t1.id: 'PA2', t2.id: 'CHUA'}, vers, 'ZALO', 'Chị Hương', tomorrow(), {t2.id: 'Giá cao'})
    assert done == dict(agreed=[t1.number], not_yet=[t2.number])
    db.expire_all()
    a, b = db.get(ServiceTicket, t1.id), db.get(ServiceTicket, t2.id)
    # Máy 1: khách chọn PA2 → áp lại giá chính hãng đúng bản đã gửi rồi Khách đồng ý.
    assert a.quote_approved_at and a.promised_return_on == tomorrow() and [x.unit_price for x in a.lines] == [600000]
    # Máy 2: về Chờ kiểm tra lại, thợ thấy lý do; chưa đồng ý.
    assert b.quote_approved_at is None and db.get(RepairInspection, t2.id).recheck_reason.startswith('Báo giá lần 1: Giá cao')
    assert db.get(RepairQuoteGroup, g.id).status == 'XONG'
    err('BAD_STATUS', rg.reply, db, disp, g.id, {t1.id: 'PA1'}, vers, 'ZALO', 'Chị Hương', tomorrow())


def test_web_pick_create_public_print_and_reply(client, db, pair):
    from test_web import service_login
    p, t1, t2, fuser = pair
    csrf = service_login(client, p['technician'])
    assert client.get(f'/bao-gia-gop/moi?khach={t1.customer_code}').status_code == 403
    page = client.get('/kiem-tra-may').text
    assert '2 máy cùng khách' in page and 'Gộp báo giá' not in page
    client.cookies.clear()
    csrf = service_login(client, p['dispatcher'])
    page = client.get('/kiem-tra-may').text
    assert 'Gộp báo giá 2 máy' in page
    page = client.get(f'/bao-gia-gop/moi?khach={t1.customer_code}').text
    assert t1.number in page and t2.number in page and '972.000' in page
    data = {'csrf': csrf, 'khach': t1.customer_code, 'phieu': [str(t1.id), str(t2.id)],
            f'v_{t1.id}': str(t1.version), f'v_{t2.id}': str(t2.version)}
    r = client.post('/bao-gia-gop/moi', data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    g = db.scalar(select(RepairQuoteGroup))
    page = client.get(r.headers['location']).text
    assert g.number in page and rg.link(g) in page and 'Ghi trả lời vào từng phiếu' in page
    assert g.number in client.get(f'/dich-vu/{t1.id}').text
    pr = client.get(f'/bao-gia-gop/{g.id}/in')
    assert pr.status_code == 200 and '972.000' in pr.text and 'TD-0001' not in pr.text
    t1, t2 = db.get(ServiceTicket, t1.id), db.get(ServiceTicket, t2.id)
    r = client.post(f'/bao-gia-gop/{g.id}/tra-loi', data={'csrf': csrf, f'tl_{t1.id}': 'PA1', f'v_{t1.id}': str(t1.version),
                                                         f'tl_{t2.id}': 'PA1', f'v_{t2.id}': str(t2.version), 'via': 'ZALO', 'person': '',
                                                         'promised_return_on': tomorrow().isoformat()})
    assert r.status_code == 400 and 'PERSON_REQUIRED' in r.text
    client.cookies.clear()
    pub = client.get(f'/bgg/{g.token}')
    assert pub.status_code == 200 and '972.000' in pub.text and t2.number in pub.text and 'TD-0001' not in pub.text and 'vốn' not in pub.text
    assert pub.headers['cache-control'] == 'no-store' and 'noindex' in pub.headers['x-robots-tag']
    assert client.get('/bgg/' + 'x' * 30).status_code == 404
    csrf = service_login(client, p['dispatcher'])
    r = client.post(f'/bao-gia-gop/{g.id}/tra-loi', data={'csrf': csrf, f'tl_{t1.id}': 'PA1', f'v_{t1.id}': str(t1.version),
                                                         f'tl_{t2.id}': 'PA1', f'v_{t2.id}': str(t2.version), 'via': 'ZALO',
                                                         'person': 'Chị Hương', 'promised_return_on': tomorrow().isoformat()},
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert all(db.get(ServiceTicket, x).quote_approved_at for x in (t1.id, t2.id))
    client.cookies.clear()
    assert client.get(f'/bgg/{g.token}').status_code == 200  # máy đã đồng ý vẫn xem lại được
