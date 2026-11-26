"""Sửa ngày phiếu dịch vụ (TUẤN duyệt mẫu R2BcBQtqyfJGfcHGZAxgXd 26/11/2026)."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import service_dates, service_tickets, service_warranty
from app.errors import BusinessError
from app.models import AuditLog, CommissionMonth, ServiceStep, ServiceTicket
from test_my_work import people, purchase_people, NOW, ticket  # noqa: F401
from test_service_warranty import previous
from test_web import csrf_of, service_login


def fail(code, fn, *args):
    with pytest.raises(BusinessError) as exc:
        fn(*args)
    assert exc.value.code == code


def done_ticket(db, p, days=3):
    stamp = NOW - timedelta(days=days)
    return previous(db, p, days=days, created_at=stamp - timedelta(hours=2), status='DA_DONG')


def local(value):
    return value.astimezone(service_tickets.service_sla.TZ)


def test_director_moves_three_dates_keeps_time_and_records_history(db, people):
    p = people; t = done_ticket(db, p)
    old_created, old_priced = local(t.created_at), local(t.priced_at)
    old_done, _ = service_warranty.completion(db, t)
    t = service_dates.fix_dates(db, p['director'], t.id, t.version, date(2026, 10, 13), date(2026, 10, 14),
                                date(2026, 10, 14), 'Nhập phiếu muộn')
    db.expire_all(); t = db.get(ServiceTicket, t.id)
    assert local(t.created_at).date() == date(2026, 10, 13) and local(t.created_at).time() == old_created.time()
    assert local(t.priced_at).date() == date(2026, 10, 14) and local(t.priced_at).time() == old_priced.time()
    stamp, _ = service_warranty.completion(db, t)
    assert local(stamp).date() == date(2026, 10, 14) and local(stamp).time() == local(old_done).time()
    step = db.scalar(select(ServiceStep).where(ServiceStep.ticket_id == t.id, ServiceStep.action == 'fix_dates'))
    assert 'Ngày nhận 15/10/2026 → 13/10/2026' in step.note and 'Ngày hoàn thành 15/10/2026 → 14/10/2026' in step.note
    assert 'Lý do: Nhập phiếu muộn' in step.note
    assert db.scalar(select(AuditLog).where(AuditLog.action == 'service_ticket.fix_dates')).reason == 'Nhập phiếu muộn'


def test_only_director_reason_required_no_future_not_before_received(db, people):
    p = people; t = done_ticket(db, p)
    fail('FORBIDDEN', service_dates.fix_dates, db, p['dispatcher'], t.id, t.version, date(2026, 10, 13), None, None, 'Lý do')
    fail('REASON_REQUIRED', service_dates.fix_dates, db, p['director'], t.id, t.version, date(2026, 10, 13), None, None, ' ')
    fail('FUTURE_DAY', service_dates.fix_dates, db, p['director'], t.id, t.version, None, None, date(2026, 10, 19), 'Lý do')
    fail('BAD_DATE', service_dates.fix_dates, db, p['director'], t.id, t.version, None, None, date(2026, 10, 1), 'Lý do')
    fail('NO_CHANGE', service_dates.fix_dates, db, p['director'], t.id, t.version, local(t.created_at).date(), None, None, 'Lý do')


def test_open_ticket_has_no_done_or_priced_date(db, people):
    p = people; t = ticket(db, p, 'DA_GIAO')
    fail('BAD_FORM', service_dates.fix_dates, db, p['director'], t.id, t.version, None, date(2026, 10, 18), None, 'Lý do')
    t = service_dates.fix_dates(db, p['director'], t.id, t.version, date(2026, 10, 17), None, None, 'Lý do')
    assert local(t.created_at).date() == date(2026, 10, 17)


def test_locked_commission_month_blocks_received_and_done_not_priced(db, people):
    p = people; t = previous(db, p, days=20, created_at=NOW - timedelta(days=21), status='DA_DONG')  # 27–28/09
    db.add(CommissionMonth(month=date(2026, 9, 1), locked_by=p['director'].id, locked_at=NOW, total=0)); db.commit()
    fail('MONTH_LOCKED', service_dates.fix_dates, db, p['director'], t.id, t.version, None, date(2026, 10, 1), None, 'Lý do')
    fail('MONTH_LOCKED', service_dates.fix_dates, db, p['director'], t.id, t.version, date(2026, 9, 26), None, None, 'Lý do')
    t = service_dates.fix_dates(db, p['director'], t.id, t.version, None, None, date(2026, 10, 1), 'Chốt phí muộn')
    assert local(t.priced_at).date() == date(2026, 10, 1)


def test_cancelled_ticket_blocked(db, people):
    p = people; t = previous(db, p, days=3, status='DA_HUY')
    fail('BAD_STATUS', service_dates.fix_dates, db, p['director'], t.id, t.version, date(2026, 10, 14), None, None, 'Lý do')
    assert not service_dates.can_fix(p['director'], t)


def test_completing_again_clears_manual_done_date(db, people):
    p = people; t = ticket(db, p, 'HOAN_THANH')
    t = service_dates.fix_dates(db, p['director'], t.id, t.version, date(2026, 10, 16), date(2026, 10, 17), None, 'Lý do')
    assert t.done_at is not None and local(service_warranty.completion(db, t)[0]).date() == date(2026, 10, 17)
    t = service_tickets.transition(db, p['dispatcher'], t.id, 'DANG_LAM', t.version, 'Mở lại')
    service_tickets.transition(db, p['dispatcher'], t.id, 'HOAN_THANH', t.version, 'Sửa lại')
    db.expire_all(); assert db.get(ServiceTicket, t.id).done_at is None


def test_web_button_director_only_and_post(client, db, people):
    p = people; t = done_ticket(db, p)
    service_login(client, p['dispatcher'])
    assert 'service-fix-dates' not in client.get(f'/dich-vu/{t.id}').text
    service_login(client, p['director'])
    html = client.get(f'/dich-vu/{t.id}').text
    assert 'Sửa ngày phiếu' in html and 'name="ngay_xong"' in html and 'name="ngay_chot"' in html
    r = client.post(f'/dich-vu/{t.id}/fix-dates', data={'csrf': csrf_of(html), 'version': t.version, 'ngay_nhan': '2026-10-13',
                    'ngay_xong': '', 'ngay_chot': '', 'reason': 'Nhập phiếu muộn'}, follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all(); assert local(db.get(ServiceTicket, t.id).created_at).date() == date(2026, 10, 13)
    html = client.get(f'/dich-vu/{t.id}').text
    assert 'Ngày nhận</dt><dd>13/10/2026' in html and 'Sửa ngày phiếu' in html
    r = client.post(f'/dich-vu/{t.id}/fix-dates', data={'csrf': csrf_of(html), 'version': db.get(ServiceTicket, t.id).version, 'ngay_nhan': '2026-10-20',
                    'reason': 'Sai'}, follow_redirects=False)
    assert r.status_code in (400, 409, 422) and 'không được sau hôm nay' in r.text
