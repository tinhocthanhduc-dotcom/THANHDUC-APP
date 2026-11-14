"""Gửi báo giá sửa chữa cho khách (đợt 4, mẫu M5–M7 14/11/2026).

Điều kiện xong: mỗi lần gửi là bản chụp phần cho khách (không linh kiện, giá vốn); giá phải khớp dòng tiền phiếu;
lần mới thay lần cũ, link cũ không hiện giá; link hết hạn / thu hồi / quá lần mở không hiện giá; tin sai số bị chặn;
khách đồng ý chọn PA khác thì áp lại đúng giá đã gửi rồi Khách đồng ý; chưa đồng ý → báo lại kỹ thuật; không sửa đóng lần chờ.
"""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import repair_inspection as ri
from app import repair_quotes as rq
from app import service_tickets
from app import service_workshop as svc
from app.errors import BusinessError
from app.models import AuditLog, RepairInspection, RepairQuoteVersion
from test_kiem_tra_may import book, form
from test_mv1_workshop import onsite, photo  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_my_work import people, purchase_people  # noqa: F401
from test_xg1_workshop import holding, workshop  # noqa: F401


def err(code, fn, *a, **kw):
    with pytest.raises(BusinessError) as e:
        fn(*a, **kw)
    assert e.value.code == code
    return e.value


def ready(db, p, make_user, parts=(('TD-0001', 1),)):
    boss = make_user('rq.boss', 'director')
    fuser, feed, opts = book(db, boss)
    p0 = p['workshop']
    t = p0[3]
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy'], parts=parts))
    ri.apply(db, p['dispatcher'], t.id, t.version, {})
    db.refresh(t)
    return t, fuser, boss


@pytest.fixture
def quoted(db, workshop, make_user):
    p, a, b, t, eq = workshop
    p = dict(p, workshop=workshop)
    t, fuser, boss = ready(db, p, make_user)
    return p, t, fuser, boss


def tomorrow():
    return service_tickets._now().date() + timedelta(days=1)


def test_snapshot_has_only_customer_part_and_versions_replace(db, quoted):
    p, t, fuser, boss = quoted
    disp = p['dispatcher']
    err('FORBIDDEN', rq.create, db, p['technician'], t.id, t.version)
    db.rollback()
    v1 = rq.create(db, disp, t.id, t.version)
    snap = v1.snapshot
    assert v1.no == 1 and v1.status == 'CHO' and len(v1.token) >= 24 and t.number not in v1.token
    assert snap['chosen'] == dict(subtotal=450000, vat=36000, total=486000, warranty=3)
    # Có giá chính hãng: bản khách có phương án khác để khách chọn.
    assert snap['alt_key'] == 'PA2' and snap['alt']['total'] == 648000
    raw = str(snap)
    assert 'TD-0001' not in raw and 'Thay bao lụa' not in raw and 'cost' not in raw and 'margin' not in raw
    assert '486.000đ' in v1.text and rq.link(v1) in v1.text and 'Thay bao lụa' not in v1.text
    # Lần 2 thay lần 1: link lần 1 không hiện giá nữa.
    v2 = rq.create(db, disp, t.id, t.version)
    db.refresh(v1)
    assert (v1.status, v2.no, v2.status) == ('THAY_THE', 2, 'CHO')
    assert rq.public(db, v1.token)[1] == 'lan_moi' and rq.public(db, v2.token)[1] == 'ok'
    assert db.get(RepairQuoteVersion, v2.id).opened_count == 1
    assert db.scalar(select(AuditLog).where(AuditLog.action == 'repair_quote.create'))


def test_create_requires_applied_and_matching_ticket_price(db, workshop, make_user):
    p, a, b, t, eq = workshop
    boss = make_user('rq.boss', 'director')
    fuser, feed, opts = book(db, boss)
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy']))
    err('NOT_APPLIED', rq.create, db, p['dispatcher'], t.id, t.version)
    db.rollback()
    ri.apply(db, p['dispatcher'], t.id, t.version, {})
    db.refresh(t)
    # Giám đốc đổi giá bảng giá sau khi áp: không gửi giá lệch dòng tiền phiếu.
    from app.models import RepairPrice
    db.query(RepairPrice).filter_by(service_id=fuser.id, level=2).update(dict(price=500000))
    db.commit()
    err('STALE_QUOTE', rq.create, db, p['dispatcher'], t.id, t.version)


def test_link_expired_revoked_and_open_limit(db, quoted, monkeypatch):
    p, t, fuser, boss = quoted
    v = rq.create(db, p['dispatcher'], t.id, t.version)
    assert rq.public(db, 'ngan')[1] == 'khong_co' and rq.public(db, 'x' * 30)[1] == 'khong_co'
    monkeypatch.setattr(rq, 'MAX_OPENS', 2)
    assert [rq.public(db, v.token)[1] for _ in range(3)] == ['ok', 'ok', 'qua_lan']
    monkeypatch.setattr(rq, '_today', lambda: v.valid_until + timedelta(days=1))
    assert rq.public(db, v.token)[1] == 'het_han'
    err('EXPIRED', rq.agree, db, p['dispatcher'], v.id, t.version, 'PA1', 'ZALO', 'Anh Nam', v.valid_until + timedelta(days=2))
    db.rollback()
    monkeypatch.undo()
    rq.revoke(db, p['dispatcher'], v.id)
    assert rq.public(db, v.token)[1] == 'thu_hoi'


def test_send_text_numbers_checked(db, quoted):
    p, t, fuser, boss = quoted
    v = rq.create(db, p['dispatcher'], t.id, t.version)
    err('TEXT_NUMBERS', rq.mark_sent, db, p['dispatcher'], v.id, 'COPY', v.text.replace('486.000', '480.000'))
    db.rollback()
    err('NOT_LINKED', rq.send_bot, db, p['dispatcher'], v.id)
    db.rollback()
    v = rq.mark_sent(db, p['dispatcher'], v.id, 'COPY', v.text + '\nCảm ơn anh.')
    assert v.sent_via == 'COPY' and v.sent_at and v.text.endswith('Cảm ơn anh.')
    assert not rq.overdue(v)
    v.sent_at = rq._now() - timedelta(days=3)
    db.commit()
    rows = ri.waiting_rows(db)
    assert rows[0]['late'] and rows[0]['stage'].startswith('Chờ khách trả lời · 3 ngày')


def test_agree_alternative_reapplies_exact_price_and_approves(db, quoted):
    p, t, fuser, boss = quoted
    disp = p['dispatcher']
    v = rq.create(db, disp, t.id, t.version)
    rq.mark_sent(db, disp, v.id, 'COPY')
    err('PERSON_REQUIRED', rq.agree, db, disp, v.id, t.version, 'PA2', 'ZALO', '', tomorrow())
    db.rollback()
    v = rq.agree(db, disp, v.id, t.version, 'PA2', 'ZALO', 'Anh Nam kế toán', tomorrow())
    db.refresh(t)
    assert (v.status, v.reply_choice, v.reply_person) == ('DONG_Y', 'PA2', 'Anh Nam kế toán')
    assert t.quote_approved_at and t.promised_return_on == tomorrow()
    assert [x.unit_price for x in t.lines] == [600000]


def test_agree_with_changed_price_is_refused(db, quoted):
    p, t, fuser, boss = quoted
    v = rq.create(db, p['dispatcher'], t.id, t.version)
    from app.models import RepairPrice
    db.query(RepairPrice).filter_by(service_id=fuser.id, level=2).update(dict(price_genuine=700000))
    db.commit()
    err('PRICE_CHANGED', rq.agree, db, p['dispatcher'], v.id, t.version, 'PA2', 'ZALO', 'Anh Nam', tomorrow())
    db.rollback()
    db.refresh(t)
    assert [x.unit_price for x in t.lines] == [450000] and t.quote_approved_at is None


def test_not_yet_sends_back_to_technician_and_decline_closes(db, quoted):
    p, t, fuser, boss = quoted
    disp = p['dispatcher']
    v = rq.create(db, disp, t.id, t.version)
    err('REASON_REQUIRED', rq.not_yet, db, disp, v.id, 'Khác')
    db.rollback()
    rq.not_yet(db, disp, v.id, 'Giá cao', 350000, 'DIEN_THOAI')
    row = db.get(RepairInspection, t.id)
    assert row.applied_at is None and row.recheck_reason == 'Báo giá lần 1: Giá cao · khách muốn khoảng 350.000đ'
    assert ri.waiting_rows(db)[0]['stage'] == 'Khách chưa đồng ý · kiểm tra lại'
    err('NOT_APPLIED', rq.create, db, disp, t.id, t.version)
    db.rollback()
    ri.apply(db, disp, t.id, t.version, {})
    db.refresh(t)
    assert db.get(RepairInspection, t.id).recheck_reason == ''
    v2 = rq.create(db, disp, t.id, t.version)
    assert v2.no == 2
    svc.quote_decline(db, disp, t.id, t.version, 'FULL', 100000, 'Khách không sửa')
    db.refresh(v2)
    assert v2.status == 'KHONG_SUA'


def test_web_send_public_print_and_replies(client, db, quoted):
    from test_web import service_login
    p, t, fuser, boss = quoted
    csrf = service_login(client, p['technician'])
    assert client.post(f'/kiem-tra-may/{t.id}/gui', data={'csrf': csrf, 'version': str(t.version)}, follow_redirects=False).status_code == 403
    client.cookies.clear()
    csrf = service_login(client, p['dispatcher'])
    r = client.post(f'/kiem-tra-may/{t.id}/gui', data={'csrf': csrf, 'version': str(t.version)}, follow_redirects=False)
    assert r.status_code == 303 and 'bao_gia_lan=1' in r.headers['location']
    v = db.scalar(select(RepairQuoteVersion).where(RepairQuoteVersion.ticket_id == t.id))
    page = client.get(f'/dich-vu/{t.id}?bao_gia_lan=1').text
    assert rq.link(v) in page and 'Khách trả lời' in page and 'Lưu và gửi báo giá lần 2' not in page
    pr = client.get(f'/kiem-tra-may/bao-gia/{v.id}/in')
    assert pr.status_code == 200 and 'BÁO GIÁ SỬA CHỮA' in pr.text and '486.000' in pr.text and 'TD-0001' not in pr.text
    r = client.post(f'/kiem-tra-may/bao-gia/{v.id}/da-gui', data={'csrf': csrf, 'text': v.text}, follow_redirects=False)
    assert r.status_code == 303
    # Khách mở link: không đăng nhập, chỉ phần cho khách.
    client.cookies.clear()
    pub = client.get(f'/bg/{v.token}')
    assert pub.status_code == 200 and '486.000' in pub.text and 'TD-0001' not in pub.text and 'vốn' not in pub.text
    assert pub.headers['cache-control'] == 'no-store' and 'noindex' in pub.headers['x-robots-tag']
    assert client.get('/bg/' + 'x' * 30).status_code == 404
    csrf = service_login(client, p['dispatcher'])
    r = client.post(f'/kiem-tra-may/bao-gia/{v.id}/chua-dong-y', data={'csrf': csrf, 'reason': 'Giá cao', 'via': 'ZALO', 'wanted_price': '350.000'},
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(RepairInspection, t.id).recheck_reason.startswith('Báo giá lần 1: Giá cao')
    client.cookies.clear()
    service_login(client, p['technician'])
    assert 'Khách chưa đồng ý, kiểm tra lại' in client.get(f'/kiem-tra-may/{t.id}').text
    db.refresh(v)
    client.cookies.clear()
    assert client.get(f'/bg/{v.token}').status_code == 410


def test_web_agree_sets_return_date(client, db, quoted):
    from test_web import service_login
    p, t, fuser, boss = quoted
    v = rq.create(db, p['dispatcher'], t.id, t.version)
    db.refresh(t)
    csrf = service_login(client, p['dispatcher'])
    data = {'csrf': csrf, 'version': str(t.version), 'pa': 'PA1', 'via': 'ZALO', 'person': '', 'promised_return_on': tomorrow().isoformat()}
    r = client.post(f'/kiem-tra-may/bao-gia/{v.id}/dong-y', data=data)
    assert r.status_code == 400 and 'PERSON_REQUIRED' in r.text
    r = client.post(f'/kiem-tra-may/bao-gia/{v.id}/dong-y', data=dict(data, person='Chị Lan'), follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(RepairQuoteVersion, v.id).status == 'DONG_Y' and db.get(service_tickets.ServiceTicket, t.id).quote_approved_at
