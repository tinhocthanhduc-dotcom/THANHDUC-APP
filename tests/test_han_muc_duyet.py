"""Hạn mức duyệt (TUẤN duyệt mẫu 11/11/2026): kế toán duyệt thay giám đốc trong hạn mức; trên mức, phiếu của mình
hoặc việc không giao vẫn về giám đốc. Không nới kiểm tra quyền/tiền/kho cũ."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app import approval_limits as limits_svc
from app import attendance, morning_report
from app import pending_dispositions as dispositions
from app.errors import BusinessError
from app.models import ApprovalLimit, AuditLog, PendingDisposition
from app.service_sla import TZ
from test_cxl2a_dispositions import lot, make  # noqa: F401
from test_my_work import people, purchase_people  # noqa: F401
from test_ns2_attendance import clock, p as ns2_people, req  # noqa: F401
from test_tk_tai_khoan import web_login


def fail(code, fn, *args, **kwargs):
    with pytest.raises(BusinessError) as exc:
        fn(*args, **kwargs)
    assert exc.value.code == code, str(exc.value)
    return exc.value


def test_defaults_save_audit_and_director_only(client, db, make_user):
    boss, kt = make_user('hm.boss', 'director'), make_user('hm.kt', 'finance')
    assert limits_svc.limits(db) == {'BO_SUNG': 1, 'NGHI': 2, 'TAM_UNG': 2_000_000, 'HUY_HANG': 500_000}
    fail('FORBIDDEN', limits_svc.save, db, kt, dict(BO_SUNG=1, NGHI=5, TAM_UNG=9_000_000, HUY_HANG=0))
    fail('BAD_LIMIT', limits_svc.save, db, boss, dict(BO_SUNG=1, NGHI=2, TAM_UNG='-5', HUY_HANG=0))
    limits_svc.save(db, boss, dict(BO_SUNG='0', NGHI='1', TAM_UNG='3.000.000', HUY_HANG='0'))
    assert limits_svc.limits(db) == {'BO_SUNG': 0, 'NGHI': 1, 'TAM_UNG': 3_000_000, 'HUY_HANG': 0}
    log = db.scalar(select(AuditLog).where(AuditLog.action == 'approval_limits.save'))
    assert log.user_id == boss.id and log.after['TAM_UNG'] == 3_000_000 and log.before['TAM_UNG'] == 2_000_000
    csrf = web_login(client, kt)
    assert client.get('/han-muc-duyet').status_code == 403
    assert client.post('/han-muc-duyet', data=dict(csrf=csrf, BO_SUNG='1', NGHI='2', TAM_UNG='1', HUY_HANG='1')).status_code == 403
    csrf = web_login(client, boss)
    page = client.get('/han-muc-duyet').text
    assert 'Hạn mức duyệt' in page and 'value="3.000.000"' in page and 'Tới 1 ngày' in page
    response = client.post('/han-muc-duyet', data=dict(csrf=csrf, BO_SUNG='1', NGHI='2', TAM_UNG='2.500.000', HUY_HANG='400.000'))
    assert response.status_code == 200 and db.get(ApprovalLimit, 'TAM_UNG', populate_existing=True).value == 2_500_000
    bad = client.post('/han-muc-duyet', data=dict(csrf=csrf, BO_SUNG='1', NGHI='2', TAM_UNG='abc', HUY_HANG='0'))
    assert bad.status_code == 422 or 'nhập số nguyên' in bad.text
    assert db.get(ApprovalLimit, 'HUY_HANG', populate_existing=True).value == 400_000


def test_finance_approves_attendance_within_limit_never_own(client, db, ns2_people, clock):
    p = ns2_people
    punch = req(db, p, 'BO_SUNG_VAO', punch_at='2026-11-02T08:00')
    short = req(db, p, 'NGHI_PHEP', from_date='2026-11-05', to_date='2026-11-06', part='CA_NGAY')
    long = req(db, p, 'NGHI_PHEP', from_date='2026-11-09', to_date='2026-11-11', part='CA_NGAY')
    assert [r['item'].id for r in attendance.requests(db, p['finance'], True)] == [short.id, punch.id]
    assert attendance.pending_count(db, p['finance']) == 2 and attendance.pending_count(db, p['director']) == 3
    fail('TREN_HAN_MUC', attendance.approve, db, p['finance'], long.id, long.version)
    attendance.approve(db, p['finance'], punch.id, punch.version)
    assert punch.status == 'DA_DUYET' and punch.decided_by == p['finance'].id
    csrf = web_login(client, p['finance'])
    page = client.get('/cham-cong/duyet').text
    assert f'id="cc-{short.id}"' in page and f'id="cc-{long.id}"' not in page
    assert client.post(f'/cham-cong/duyet/{long.id}/duyet', data=dict(csrf=csrf, version=long.version)).status_code == 403
    assert client.post(f'/cham-cong/duyet/{short.id}/tu-choi', data=dict(csrf=csrf, version=short.version, note='Trùng lịch')).status_code == 200
    assert db.get(type(short), short.id, populate_existing=True).status == 'TU_CHOI'
    attendance.approve(db, p['director'], long.id, long.version)  # giám đốc vẫn duyệt mọi phiếu
    limits_svc.save(db, p['director'], dict(BO_SUNG=0, NGHI=0, TAM_UNG=0, HUY_HANG=0))
    assert client.get('/cham-cong/duyet').status_code == 403 and attendance.pending_count(db, p['finance']) == 0


def test_finance_own_attendance_request_goes_to_director(db, ns2_people, clock):
    p = ns2_people
    from app import employees
    employees.create(db, p['director'], p['finance'].id, dict(position='Kế toán', start_date='2026-01-01'))
    own = attendance.request(db, p['finance'], 'BO_SUNG_RA', reason='Quên bấm', punch_at='2026-11-02T17:30')
    assert attendance.requests(db, p['finance'], True) == []
    fail('FORBIDDEN', attendance.approve, db, p['finance'], own.id, own.version)
    assert [r for r in limits_svc.queue(db) if r['id'] == own.id][0]['delegable'] is False


def test_disposal_by_cost_and_morning_report_lines(client, db, lot, monkeypatch):
    p, item = lot  # giá vốn 100/cái
    limits_svc.save(db, p['director'], dict(BO_SUNG=1, NGHI=2, TAM_UNG=2_000_000, HUY_HANG=300))
    small = make(db, p, item, 'HUY', 3, reason='Hỏng')        # vốn 300
    big = make(db, p, item, 'HUY', 4, reason='Vỡ cả hộp')      # vốn 400
    fail('TREN_HAN_MUC', dispositions.approve, db, p['finance'], big.id, big.version)
    csrf = web_login(client, p['finance'])
    page = client.get('/cho-xu-ly/duyet-huy').text
    assert f'xl-{small.id}' in page and f'xl-{big.id}' not in page
    assert client.post(f'/cho-xu-ly/xl/{small.id}/duyet', data=dict(csrf=csrf, version=small.version)).status_code == 200
    done = db.get(PendingDisposition, small.id, populate_existing=True)
    assert done.status == 'DA_GHI' and done.decided_by == p['finance'].id and done.cost_value == 300
    # Báo cáo sáng hôm sau: phiếu trên mức chờ giám đốc + kế toán đã duyệt hôm qua.
    today = datetime.now(TZ).date() + timedelta(days=1)
    monkeypatch.setattr(morning_report, 'period', lambda session, day: (day - timedelta(days=1), day - timedelta(days=1)))
    d = morning_report.build(db, p['director'], today)
    assert [r['id'] for r in d['boss_waiting']] == [big.id] and d['delegated']['disposal'] == 1 and d['delegated']['disposal_amount'] == 300
    text = '\n'.join(morning_report.approval_lines(d, 'https://x'))
    assert 'CHỜ ANH DUYỆT · 1' in text and 'Hủy 4 ' in text and '400đ' in text
    assert 'Kế toán đã duyệt hôm qua: 1 hủy hàng 300đ' in text
    assert morning_report.build(db, p['finance'], today)['boss_waiting'] == []
