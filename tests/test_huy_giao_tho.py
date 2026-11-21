"""Hủy giao thợ trên phiếu dịch vụ (TUẤN duyệt mẫu 21/11/2026)."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import equipment_custody as custody, nhan_vien_ao as nva, notifications, service_tickets, service_unassign as su, service_workshop as ws
from app.errors import BusinessError
from app.models import EquipmentCustody, ServiceStep, ServiceTicket
from test_my_work import people, purchase_people, ticket  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_doi_tho_hang_theo import supplied
from test_mv1_workshop import onsite, photo  # noqa: F401
from test_xg1_workshop import holding, workshop, quote, approve, repaired  # noqa: F401
from test_web import service_login
from web_forms import Forms


def error(code, fn, *a, **kw):
    with pytest.raises(BusinessError) as e:
        fn(*a, **kw)
    assert e.value.code == code
    return e.value


def fresh(db, t):
    db.expire_all()
    return db.get(ServiceTicket, t.id)


@pytest.mark.parametrize('status', ['DA_GIAO', 'DA_NHAN', 'DA_HEN'])
def test_unassign_returns_ticket_to_new_and_logs_reason(db, people, status):
    p = people
    t = ticket(db, p, status)
    t, old_id, label = su.unassign(db, p['dispatcher'], t.id, t.version, 'GIAO_NHAM', 'để Thạnh sắp lại')
    t = fresh(db, t)
    assert (t.status, t.assignee_id, t.self_claim_until, old_id, label) == ('MOI', None, None, p['technician'].id, 'Giao nhầm thợ')
    step = t.steps[-1]
    assert step.action == 'unassign' and step.from_status == status and step.to_status == 'MOI'
    assert step.note == f"Hủy giao thợ: {p['technician'].full_name} · Giao nhầm thợ. để Thạnh sắp lại"
    assert su.previous_assignee(db, step) == p['technician'].id
    # Thợ cũ có chuông báo; người bấm thì không.
    bells = [x for x in notifications.items(db, p['technician']) if x['id'].startswith('ticket_unassigned:')]
    assert len(bells) == 1 and t.number in bells[0]['text']
    assert not [x for x in notifications.items(db, p['other_tech']) if x['id'].startswith('ticket_unassigned:')]
    # Giao lại tay như phiếu mới.
    service_tickets.assign(db, p['dispatcher'], t.id, p['other_tech'].id, t.version)
    assert fresh(db, t).status == 'DA_GIAO'


def test_virtual_dispatcher_skips_unassigned_ticket(db, people):
    p = people
    t = ticket(db, p, 'DA_GIAO')
    other = ticket(db, p, 'MOI')
    su.unassign(db, p['dispatcher'], t.id, None, 'THO_BAN')
    c = nva.cfg(nva.rows(db)['DIEU_PHOI'])
    ids = {x.id for x in nva._candidates(db, c, datetime.now(UTC) + timedelta(days=2))}
    assert other.id in ids and t.id not in ids


def test_unassign_rules(db, people):
    p = people
    t = ticket(db, p, 'DA_HEN')
    error('FORBIDDEN', su.unassign, db, p['technician'], t.id, t.version, 'GIAO_NHAM')
    error('REASON_REQUIRED', su.unassign, db, p['dispatcher'], t.id, t.version, '')
    error('NOTE_REQUIRED', su.unassign, db, p['dispatcher'], t.id, t.version, 'KHAC', ' ')
    error('VERSION_CONFLICT', su.unassign, db, p['dispatcher'], t.id, t.version - 1, 'GIAO_NHAM')
    service_tickets.transition(db, p['technician'], t.id, 'DANG_LAM', t.version)
    e = error('CANNOT_UNASSIGN', su.unassign, db, p['dispatcher'], t.id, t.version, 'GIAO_NHAM')
    assert 'đã bắt đầu làm' in e.message and e.status == 409
    assert fresh(db, t).assignee_id == p['technician'].id
    new = ticket(db, p, 'MOI')
    error('CANNOT_UNASSIGN', su.unassign, db, p['dispatcher'], new.id, new.version, 'GIAO_NHAM')


def test_blocked_while_technician_holds_ticket_parts(db, held):
    p, a, b = held
    t = ticket(db, p, 'DA_HEN')
    service_tickets.transition(db, p['technician'], t.id, 'DANG_LAM', t.version)
    supplied(db, p, t)
    # Phiếu quay về Đã hẹn (hẹn lại) nhưng vật tư của phiếu vẫn ở kho thợ.
    t.status = 'DA_HEN'; db.commit()
    e = error('CANNOT_UNASSIGN', su.unassign, db, p['dispatcher'], t.id, t.version, 'GIAO_NHAM')
    assert 'TD-0001 ×2' in e.message and 'Giao thợ khác' in e.message
    t = fresh(db, t)
    assert t.status == 'DA_HEN' and t.assignee_id == p['technician'].id


def test_return_trip_goes_back_to_ready_and_cancels_machine_handover(client, db, workshop):
    p, a, b, t, eq = workshop
    quote(db, p, t); approve(db, p, t); photo(db, p, t, 'SAU', who='dispatcher'); repaired(db, p, t)
    ws.schedule_return(db, p['dispatcher'], t.id, t.version, service_tickets._now() + timedelta(days=1), None, p['technician'].id, 'Giao trả máy')
    hand = custody.pending(db, eq.serial)
    assert t.status == 'DA_HEN' and hand.holder_id == p['technician'].id
    su.unassign(db, p['dispatcher'], t.id, t.version, 'KHACH_DOI')
    t = fresh(db, t)
    assert (t.status, t.hold_reason) == ('TAM_DUNG', 'SUA_XONG_CHO_TRA')
    assert db.get(EquipmentCustody, hand.id).status == 'DA_HUY' and custody.pending(db, eq.serial) is None
    assert db.get(type(eq), eq.serial).custody_location == 'KHO_KY_THUAT'
    # Màn thợ Hôm nay không còn hiện phiếu (máy ở Kho kỹ thuật chờ hẹn trả), không tính việc đang mở.
    from app import service_self_claim
    service_login(client, p['technician'])
    assert t.number not in client.get('/tho').text
    assert service_self_claim.open_jobs(db, p['technician'].id) == 0
    # Hẹn trả lại cho thợ khác như bình thường.
    ws.schedule_return(db, p['dispatcher'], t.id, t.version, service_tickets._now() + timedelta(days=2), None, p['other_tech'].id, 'Hẹn lại')
    t = fresh(db, t)
    assert t.assignee_id == p['other_tech'].id and custody.pending(db, eq.serial).holder_id == p['other_tech'].id


def test_blocked_when_technician_already_holds_machine(db, workshop):
    p, a, b, t, eq = workshop
    quote(db, p, t); approve(db, p, t); photo(db, p, t, 'SAU', who='dispatcher'); repaired(db, p, t)
    ws.schedule_return(db, p['dispatcher'], t.id, t.version, service_tickets._now() + timedelta(days=1), None, p['technician'].id, 'Giao trả máy')
    hand = custody.pending(db, eq.serial)
    custody.confirm(db, p['technician'], hand.id, hand.version)
    e = error('CANNOT_UNASSIGN', su.unassign, db, p['dispatcher'], t.id, t.version, 'KHACH_DOI')
    assert eq.serial in e.message and 'Kho kỹ thuật' in e.message


def test_detail_button_posts_and_shows_toast(client, db, people):
    p = people
    t = ticket(db, p, 'DA_HEN')
    service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{t.id}')
    assert page.status_code == 200 and 'data-unassign' in page.text and 'Xác nhận hủy giao' in page.text
    form = next(f for f in Forms(page.text, page.url).forms if f['path'].endswith('/huy-giao-tho'))['fields']
    form.update(ly_do='GIAO_NHAM')
    r = client.post(f'/dich-vu/{t.id}/huy-giao-tho', data=form, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].endswith('huy_giao=1')
    assert fresh(db, t).status == 'MOI'
    page = client.get(r.headers['location'])
    assert 'Đã hủy giao thợ' in page.text and 'data-unassign' not in page.text


def test_detail_shows_block_reason_and_error_reopens_form(client, db, people):
    p = people
    t = ticket(db, p, 'DA_HEN')
    service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{t.id}')
    form = next(f for f in Forms(page.text, page.url).forms if f['path'].endswith('/huy-giao-tho'))['fields']
    form.update(ly_do='KHAC', note='')
    r = client.post(f'/dich-vu/{t.id}/huy-giao-tho', data=form)
    assert r.status_code == 400 and 'Chọn Khác thì ghi rõ lý do' in r.text and 'data-unassign open' in r.text
    service_tickets.transition(db, p['technician'], t.id, 'DANG_LAM', fresh(db, t).version)
    page = client.get(f'/dich-vu/{t.id}')
    assert 'data-unassign-block' in page.text and 'đã bắt đầu làm' in page.text
