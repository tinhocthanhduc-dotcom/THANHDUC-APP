"""Thu chung cả chỗ (TUẤN duyệt mẫu 13/11/2026): khách trả một lần cho nhiều máy cùng chỗ; mỗi phiếu một báo thu đúng số,
thiếu thì trừ phiếu gốc trước, chờ kế toán xác nhận."""
from app import sales_receipts as svc
from app.models import SalesReceipt
from app.schemas import SalesReceiptReport
from test_receipt_cash import fails, t3_people, ticket  # noqa: F401  (fixture)
from test_web import service_login


def family(db, p):
    root = ticket(db, p, receivable=450000, total=450000, subtotal=450000)
    child = ticket(db, p, receivable=850000, total=850000, subtotal=850000, split_from_id=root.id)
    todo = ticket(db, p, status='DANG_LAM', receivable=None, total=None, subtotal=None, priced_at=None, split_from_id=root.id)
    return root, child, todo


def test_full_payment_one_receipt_per_ticket(db, t3_people):
    p = t3_people; root, child, todo = family(db, p)
    rows = svc.family_due(db, p['technician'], child)
    assert [(r['t'].id, r['due'], r['done']) for r in rows] == [(root.id, 450000, True), (child.id, 850000, True), (todo.id, 0, False)]
    out = svc.report_for_family(db, p['technician'], child.id, 1300000, 'TIEN_MAT')
    assert [(r.ticket_id, r.amount, r.status) for r in out] == [(root.id, 450000, 'CHO_XAC_NHAN'), (child.id, 850000, 'CHO_XAC_NHAN')]
    assert all(r.note.startswith(svc.FAMILY_TAG + ': ' + root.number) and r.reported_by == p['technician'].id for r in out)
    fails('NOTHING_DUE', svc.report_for_family, db, p['technician'], root.id, 1, 'TIEN_MAT')


def test_partial_takes_root_first(db, t3_people):
    p = t3_people; root, child, _ = family(db, p)
    out = svc.report_for_family(db, p['technician'], root.id, 1000000, 'CHUYEN_KHOAN', 'khách hẹn trả nốt')
    assert [(r.ticket_id, r.amount, r.method) for r in out] == [(root.id, 450000, 'CHUYEN_KHOAN'), (child.id, 550000, 'CHUYEN_KHOAN')]
    assert out[0].note.endswith('khách hẹn trả nốt')
    assert [r['due'] for r in svc.family_due(db, p['technician'], root)][:2] == [0, 300000]


def test_existing_pending_reduces_due_and_overpay_blocked(db, t3_people):
    p = t3_people; root, child, _ = family(db, p)
    svc.report_for_ticket(db, p['technician'], root.id, SalesReceiptReport(version=root.version, amount=200000, method='TIEN_MAT'))
    n = db.query(SalesReceipt).count()
    fails('OVER_PAYMENT', svc.report_for_family, db, p['technician'], root.id, 1100001, 'TIEN_MAT')
    fails('BAD_AMOUNT', svc.report_for_family, db, p['technician'], root.id, 0, 'TIEN_MAT')
    assert db.query(SalesReceipt).count() == n
    out = svc.report_for_family(db, p['technician'], root.id, 1100000, 'TIEN_MAT')
    assert [r.amount for r in out] == [250000, 850000]


def test_other_technician_forbidden(db, t3_people, make_user):
    p = t3_people; root, _, _ = family(db, p)
    other = make_user('tho-khac', 'technician')
    fails('FORBIDDEN', svc.report_for_family, db, other, root.id, 1000, 'TIEN_MAT')


def test_web_page_and_post(db, t3_people, client):
    p = t3_people; root, child, todo = family(db, p)
    csrf = service_login(client, p['technician'])
    page = client.get(f'/tho/phieu/{child.id}/thu-chung')
    assert page.status_code == 200 and '1.300.000' in page.text and 'Chưa hoàn thành' in page.text
    bad = client.post(f'/tho/phieu/{child.id}/thu-chung', data=dict(csrf=csrf, mode='PARTIAL', amount='1.300.000', method='TIEN_MAT'))
    assert bad.status_code == 400 and 'Thu một phần phải nhỏ hơn' in bad.text
    ok = client.post(f'/tho/phieu/{child.id}/thu-chung', data=dict(csrf=csrf, mode='FULL', method='TIEN_MAT'), follow_redirects=False)
    assert ok.status_code == 303 and ok.headers['location'] == f'/tho/phieu/{child.id}?da_luu=1&thu_chung=2'
    assert sorted(r.amount for r in db.query(SalesReceipt)) == [450000, 850000]
