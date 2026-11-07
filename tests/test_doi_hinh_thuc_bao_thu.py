"""Đổi hình thức báo thu Tiền mặt ⇄ Chuyển khoản một bước (TUẤN duyệt 07/11/2026)."""
import pytest

from app import cash_posting, sales_receipts as svc
from app.errors import BusinessError
from app.models import AuditLog, CashDoc, SalesReceipt
from test_receipt_cash import fails, order, report, t3_people, ticket  # noqa: F401  (fixture)
from test_web import csrf_of, service_login


def _fresh(db, *rows):
    db.expire_all()
    return [db.get(SalesReceipt, r.id) for r in rows]


def test_pending_cash_switched_by_reporter_keeps_debt_and_clears_held(db, t3_people, configured_finance):
    p = t3_people; t = ticket(db, p)
    tech = p['technician']
    r = svc.report_for_ticket(db, tech, t.id, svc.SalesReceiptReport(version=t.version, amount=500000, method='TIEN_MAT'))
    before = svc.balance(t)
    assert svc.held_by(db, tech)['total'] == 500000
    for who in ('sales', 'dispatcher'):  # không phải người lập, không phải kế toán
        fails('FORBIDDEN', svc.switch_method, db, p[who], r.id, r.version, 'CHUYEN_KHOAN', 'Chọn nhầm')
    fails('SAME_METHOD', svc.switch_method, db, tech, r.id, r.version, 'TIEN_MAT', 'Chọn nhầm')
    fails('REASON_REQUIRED', svc.switch_method, db, tech, r.id, r.version, 'CHUYEN_KHOAN', '  ')
    fails('SELF_CONFIRM', svc.switch_method, db, tech, r.id, r.version, 'CHUYEN_KHOAN', 'Chọn nhầm', True)
    fails('VERSION_CONFLICT', svc.switch_method, db, tech, r.id, r.version + 1, 'CHUYEN_KHOAN', 'Chọn nhầm')
    new = svc.switch_method(db, tech, r.id, r.version, 'CHUYEN_KHOAN', 'Chọn nhầm hình thức')
    old, new = _fresh(db, r, new)
    assert old.status == 'TU_CHOI' and old.replaced_by_id == new.id and 'Tiền mặt → Chuyển khoản' in old.reason
    assert (new.status, new.method, new.amount, new.received_at, new.reported_by, new.ticket_id) == \
        ('CHO_XAC_NHAN', 'CHUYEN_KHOAN', 500000, old.received_at, tech.id, t.id)
    assert new.cash_doc_id is None and db.query(CashDoc).count() == 0
    db.expire_all()
    assert svc.balance(t) == before  # công nợ, đã thu, chờ xác nhận không đổi
    assert svc.held_by(db, tech)['total'] == 0
    fails('BAD_STATUS', svc.switch_method, db, tech, old.id, old.version, 'CHUYEN_KHOAN', 'Lần hai')
    assert db.query(AuditLog).filter(AuditLog.action == 'sales_receipt.switch', AuditLog.entity_id == str(old.id)).count() == 1


def test_finance_switch_and_confirm_now_posts_to_bank(db, t3_people, configured_finance):
    p = t3_people; o = order(db, p); r = report(db, p, o, 600000)
    assert svc.can_confirm_now(p['finance'], r)
    new = svc.switch_method(db, p['finance'], r.id, r.version, 'CHUYEN_KHOAN', 'Khách đổi ý', True)
    old, new = _fresh(db, r, new)
    assert old.status == 'TU_CHOI' and new.status == 'DA_XAC_NHAN' and new.reported_by == p['sales'].id
    pt = db.get(CashDoc, new.cash_doc_id)
    assert pt.status == 'DA_GHI' and pt.fund_code == 'TK-ACB' and pt.amount == 600000 and pt.source_id == str(new.id)
    assert cash_posting.balance(db, p['finance'], 'TK-ACB') == 600000 and cash_posting.balance(db, p['finance'], 'QUY-TM') == 0
    assert svc.balance(db.get(type(o), o.id))['paid'] == 600000


def test_confirmed_switch_director_only_moves_cash_to_bank(db, t3_people, configured_finance):
    p = t3_people; o = order(db, p); r = report(db, p, o, 600000)
    svc.confirm(db, p['finance'], r.id, r.version)
    db.expire_all(); r = db.get(SalesReceipt, r.id); old_pt = r.cash_doc_id
    assert cash_posting.balance(db, p['finance'], 'QUY-TM') == 600000
    for who in ('sales', 'finance'):
        fails('FORBIDDEN', svc.switch_method, db, p[who], r.id, r.version, 'CHUYEN_KHOAN', 'Chọn nhầm')
    new = svc.switch_method(db, p['director'], r.id, r.version, 'CHUYEN_KHOAN', 'Chọn nhầm hình thức')
    old, new = _fresh(db, r, new)
    assert old.status == 'DA_HUY' and old.replaced_by_id == new.id and old.cash_doc_id == old_pt
    assert db.get(CashDoc, old_pt).status == 'DA_HUY'
    assert new.status == 'DA_XAC_NHAN' and new.received_at == old.received_at
    assert db.get(CashDoc, new.cash_doc_id).fund_code == 'TK-ACB'
    assert cash_posting.balance(db, p['finance'], 'QUY-TM') == 0
    assert cash_posting.balance(db, p['finance'], 'TK-ACB') == 600000
    assert svc.balance(db.get(type(o), o.id))['paid'] == 600000
    # đổi ngược lại được trên phiếu mới
    back = svc.switch_method(db, p['director'], new.id, new.version, 'TIEN_MAT', 'Khác: kiểm lại sao kê')
    db.expire_all()
    assert db.get(SalesReceipt, back.id).method == 'TIEN_MAT'
    assert cash_posting.balance(db, p['finance'], 'QUY-TM') == 600000 and cash_posting.balance(db, p['finance'], 'TK-ACB') == 0
    assert svc.balance(db.get(type(o), o.id))['paid'] == 600000


def test_post_failure_rolls_back_whole_switch(db, t3_people, configured_finance, monkeypatch):
    p = t3_people; o = order(db, p); r = report(db, p, o, 600000)
    svc.confirm(db, p['finance'], r.id, r.version)
    db.expire_all(); r = db.get(SalesReceipt, r.id)
    receipts, docs, audits = db.query(SalesReceipt).count(), db.query(CashDoc).count(), db.query(AuditLog).count()
    def boom(*a, **k):
        raise BusinessError('TEST_FAILURE', 'Lỗi thử')
    monkeypatch.setattr(svc, '_post_cash', boom)
    fails('TEST_FAILURE', svc.switch_method, db, p['director'], r.id, r.version, 'CHUYEN_KHOAN', 'Chọn nhầm')
    db.expire_all(); r = db.get(SalesReceipt, r.id)
    assert r.status == 'DA_XAC_NHAN' and r.replaced_by_id is None
    assert (db.query(SalesReceipt).count(), db.query(CashDoc).count(), db.query(AuditLog).count()) == (receipts, docs, audits)
    assert cash_posting.balance(db, p['finance'], 'QUY-TM') == 600000


def test_web_switch_from_queue_and_permissions(client, db, t3_people, configured_finance):
    p = t3_people; o = order(db, p); r = report(db, p, o, 600000)
    csrf = service_login(client, p['finance'])
    page = client.get(f'/bao-thu/{r.id}')
    assert page.status_code == 200 and f'data-receipt-switch="{r.id}"' in page.text and 'name="confirm_now"' in page.text
    bad = client.post(f'/doi-hinh-thuc-bao-thu/{r.id}', data=dict(csrf=csrf, version=1, origin='queue', method='CHUYEN_KHOAN', reason_choice='KHAC', reason_other=''))
    assert bad.status_code == 400 and 'lý do đổi hình thức' in bad.text
    ok = client.post(f'/doi-hinh-thuc-bao-thu/{r.id}', data=dict(csrf=csrf, version=1, origin='queue', method='CHUYEN_KHOAN',
                                                       reason_choice='Chọn nhầm hình thức', confirm_now='1'), follow_redirects=False)
    assert ok.status_code == 303
    db.expire_all(); old = db.get(SalesReceipt, r.id); new = db.get(SalesReceipt, old.replaced_by_id)
    assert old.status == 'TU_CHOI' and new.status == 'DA_XAC_NHAN' and new.method == 'CHUYEN_KHOAN'
    # BT đã xác nhận: kế toán không thấy nút, giám đốc thấy
    page = client.get(f'/bao-thu/{new.id}')
    assert f'data-receipt-switch="{new.id}"' not in page.text and f'Thay cho {old.number}' in page.text
    assert client.post(f'/doi-hinh-thuc-bao-thu/{new.id}', data=dict(csrf=csrf, version=new.version, origin='queue', method='TIEN_MAT',
                                                            reason_choice='Chọn nhầm hình thức')).status_code == 403
    csrf = service_login(client, p['director'])
    assert f'data-receipt-switch="{new.id}"' in client.get(f'/bao-thu/{new.id}').text


def test_web_technician_switches_own_pending_on_ticket(client, db, t3_people, configured_finance):
    p = t3_people; t = ticket(db, p); tech = p['technician']
    r = svc.report_for_ticket(db, tech, t.id, svc.SalesReceiptReport(version=t.version, amount=500000, method='TIEN_MAT'))
    csrf = service_login(client, tech)
    page = client.get(f'/tho/phieu/{t.id}')
    assert page.status_code == 200 and f'data-receipt-switch="{r.id}"' in page.text and 'name="confirm_now"' not in page.text
    res = client.post(f'/doi-hinh-thuc-bao-thu/{r.id}', data=dict(csrf=csrf, version=1, origin='ticket', return_to='tho', method='CHUYEN_KHOAN',
                                                        reason_choice='Chọn nhầm hình thức'), follow_redirects=False)
    db.expire_all(); old = db.get(SalesReceipt, r.id)
    assert res.status_code == 303 and res.headers['location'] == f'/tho/phieu/{t.id}?da_luu=1#bt-{old.replaced_by_id}'
    assert client.get('/tien-toi-giu').text.count('data-held-receipt=') == 0
