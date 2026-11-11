"""Hạn mức duyệt · tạm ứng (TUẤN duyệt 11/11/2026): kế toán duyệt tạm ứng tới hạn mức, trên mức về giám đốc."""
from app import payroll
from app.models import SalaryAdvance
from test_han_muc_duyet import fail
from test_ns5_payroll import money, p  # noqa: F401
from test_tk_tai_khoan import web_login


def test_finance_decides_advances_within_limit(client, db, p, money):
    small = payroll.request_advance(db, p['technician'], 1_500_000, 'Sửa xe')
    big = payroll.request_advance(db, p['sales'], 2_500_000, 'Việc gia đình')
    rows = {r['item'].id: r for r in payroll.advances(db, p['finance'])}
    assert rows[small.id]['can_decide'] and big.id not in rows
    assert payroll.pending_count(db, p['finance']) == 1 and payroll.pending_count(db, p['director']) == 2
    fail('TREN_HAN_MUC', payroll.decide_advance, db, p['finance'], big.id, big.version)
    csrf = web_login(client, p['finance'])
    page = client.get('/tam-ung').text
    assert 'Trong hạn mức kế toán duyệt' in page and 'Việc gia đình' not in page
    assert client.post(f'/tam-ung/{big.id}/duyet', data=dict(csrf=csrf, version=big.version)).status_code == 403
    assert client.post(f'/tam-ung/{small.id}/duyet', data=dict(csrf=csrf, version=small.version)).status_code == 200
    row = db.get(SalaryAdvance, small.id, populate_existing=True)
    assert row.status == 'DA_DUYET' and row.decided_by == p['finance'].id
    payroll.decide_advance(db, p['director'], big.id, big.version)
    assert db.get(SalaryAdvance, big.id, populate_existing=True).status == 'DA_DUYET'
