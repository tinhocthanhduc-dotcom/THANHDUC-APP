"""Máy đã bị từ chối bảo hành: ghi vào thiết bị, lần sau không tự miễn phí, cảnh báo đỏ (TUẤN chọn phương án 1, 19/11/2026)."""
from datetime import timedelta

from sqlalchemy import select

from app import service_charges, service_tickets as tickets, service_warranty as svc
from app.models import AuditLog, Equipment
from app.schemas import ServiceCharges
from test_my_work import NOW, people, purchase_people  # noqa: F401
from test_web import service_people  # noqa: F401
from test_service_warranty import fail, machine, previous, warranty
from test_web import service_login

def _working(db, p, t):
    tickets.assign(db, p['dispatcher'], t.id, p['technician'].id, t.version)
    for status in ('DA_NHAN', 'DA_HEN', 'DANG_LAM'):
        tickets.transition(db, p['technician'], t.id, status, t.version,
                           appointment_at=tickets._now() + timedelta(days=1) if status == 'DA_HEN' else None)
    return t


FEE = dict(kind='PHI', description='Khách làm rơi máy', qty=1, unit_price=300000)


def _reject(db, p, t, void=True, reason='Máy rơi vỡ, chập nguồn'):
    body = ServiceCharges(version=t.version, billing='THU_TIEN', billing_reason=reason, void_machine_warranty=void, lines=[FEE])
    return service_charges.set_charges(db, p['dispatcher'], t.id, body)


def test_void_marks_machine_and_next_warranty_is_not_free(db, people):
    p = people
    eq = machine(db, p, until=NOW.date() + timedelta(days=200))
    t = warranty(db, p, serial=eq.serial)
    assert t.warranty_basis == 'MAY_CON_HAN' and t.billing == 'MIEN_PHI'
    _reject(db, p, t)
    db.refresh(eq)
    assert eq.warranty_void_on == svc.today() and eq.warranty_void_reason == 'Máy rơi vỡ, chập nguồn' and eq.warranty_void_ticket_id == t.id
    assert eq.warranty_until == NOW.date() + timedelta(days=200)  # hạn cũ giữ nguyên, chỉ thêm dấu
    assert db.scalar(select(AuditLog.reason).where(AuditLog.action == 'equipment.warranty_void')) == 'Máy rơi vỡ, chập nguồn'
    nxt = warranty(db, p, serial=eq.serial)
    assert nxt.warranty_basis == 'HET_HAN' and nxt.billing is None and nxt.free_reason == ''
    assert svc.info(db, nxt)['void'].startswith('Máy đã bị từ chối bảo hành ngày')
    # Điều phối vẫn cho bảo hành được: chọn Miễn phí kèm lý do.
    service_charges.set_charges(db, p['dispatcher'], nxt.id, ServiceCharges(version=nxt.version, billing='MIEN_PHI', free_reason='GĐ đồng ý bảo hành lần này'))
    assert nxt.billing == 'MIEN_PHI'


def test_without_tick_machine_stays_in_warranty(db, people):
    p = people
    eq = machine(db, p, until=NOW.date() + timedelta(days=200))
    t = warranty(db, p, serial=eq.serial)
    _reject(db, p, t, void=False)
    db.refresh(eq)
    assert eq.warranty_void_on is None
    assert warranty(db, p, serial=eq.serial).warranty_basis == 'MAY_CON_HAN'


def test_void_needs_override_and_serial(db, people):
    p = people
    parent = previous(db, p)
    t = warranty(db, p, parent)  # còn hạn theo phiếu trước nhưng không có serial
    fail('SERIAL_REQUIRED', _reject, db, p, t)
    eq = machine(db, p, until=NOW.date() - timedelta(days=5))
    expired = warranty(db, p, serial=eq.serial)
    fail('WARRANTY_MISMATCH', _reject, db, p, expired)


def test_repair_before_void_loses_warranty_repair_after_keeps_it(db, people):
    p = people
    eq = machine(db, p)
    old = previous(db, p, 20, serial=eq.serial)
    t = warranty(db, p, old, serial=eq.serial)
    assert t.warranty_basis == 'PHIEU_TRUOC'
    _reject(db, p, t)
    assert warranty(db, p, old, serial=eq.serial).warranty_basis == 'HET_HAN'
    row = db.get(Equipment, eq.serial)
    row.warranty_void_on = svc.today() - timedelta(days=30); db.commit()
    later = previous(db, p, 10, serial=eq.serial)
    assert warranty(db, p, later, serial=eq.serial).warranty_basis == 'PHIEU_TRUOC'


def test_web_tick_warning_and_clear(client, db, people):
    p = people
    eq = machine(db, p, until=NOW.date() + timedelta(days=200))
    t = _working(db, p, warranty(db, p, serial=eq.serial))
    csrf = service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{t.id}')
    assert 'name="void_machine_warranty"' in page.text
    data = {'csrf': csrf, 'version': str(t.version), 'billing': 'THU_TIEN', 'free_reason': '', 'billing_reason': 'Rách tem bảo hành',
            'void_machine_warranty': '1', 'line_kind': ['PHI'], 'line_description': ['Công sửa'], 'line_product': [''],
            'line_qty': ['1'], 'line_price': ['200.000'], 'vat_0': '10'}
    r = client.post(f'/dich-vu/{t.id}/tien/luu', data=data, follow_redirects=False)
    assert r.status_code in (302, 303), r.text
    db.expire_all()
    assert db.get(Equipment, eq.serial).warranty_void_reason == 'Rách tem bảo hành'
    nxt = _working(db, p, warranty(db, p, serial=eq.serial))
    page = client.get(f'/dich-vu/{nxt.id}')
    assert 'data-warranty-void' in page.text and 'Rách tem bảo hành' in page.text
    assert 'value="THU_TIEN"  checked' in page.text
    intake = client.get('/dich-vu/moi', params={'customer_code': 'KTD-000001'})
    assert 'Đã từ chối BH' in intake.text
    device = client.get('/thiet-bi/' + eq.serial)
    assert 'Gỡ dấu từ chối bảo hành' in device.text
    row = db.get(Equipment, eq.serial)
    r = client.post('/thiet-bi/go-tu-choi-bh', data={'csrf': csrf, 'serial': eq.serial, 'version': str(row.version)}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Equipment, eq.serial).warranty_void_on is None
    assert warranty(db, p, serial=eq.serial).warranty_basis == 'MAY_CON_HAN'


def test_technician_cannot_clear(client, db, people):
    p = people
    eq = machine(db, p)
    csrf = service_login(client, p['technician'])
    r = client.post('/thiet-bi/go-tu-choi-bh', data={'csrf': csrf, 'serial': eq.serial, 'version': '1'}, follow_redirects=False)
    assert r.status_code == 403
