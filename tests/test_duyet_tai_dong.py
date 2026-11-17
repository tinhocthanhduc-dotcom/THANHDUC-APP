"""Đợt 2 tối ưu thao tác (TUẤN duyệt mẫu 17/11/2026): Duyệt/Từ chối ngay tại dòng trang Chờ anh duyệt.

Nút tại dòng gọi đúng hàm nghiệp vụ trang gốc: quyền, phiên bản, lý do bắt buộc giữ nguyên.
"""
from app import director_approvals, payroll
from app.models import SalaryAdvance
from test_ns5_payroll import p  # noqa: F401
from test_tk_tai_khoan import web_login


def _status(db, ident):
    return db.get(SalaryAdvance, ident, populate_existing=True).status


def test_page_shows_inline_buttons_and_approves(client, db, p):
    row = payroll.request_advance(db, p['technician'], 2_500_000, 'Sửa xe')
    item = next(r for r in director_approvals.rows(db) if r['kind'] == 'TAM_UNG')
    assert item['id'] == row.id and item['version'] == row.version and item['inline']
    csrf = web_login(client, p['director'])
    page = client.get('/cho-anh-duyet').text
    assert f'action="/cho-anh-duyet/TAM_UNG/{row.id}/duyet"' in page and f'action="/cho-anh-duyet/TAM_UNG/{row.id}/tu-choi"' in page
    assert 'data-ap-approve' in page and 'duyet-tai-dong.js' in page
    r = client.post(f'/cho-anh-duyet/TAM_UNG/{row.id}/duyet', data=dict(csrf=csrf, version=row.version, nhom='tien'), follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/cho-anh-duyet?nhom=tien&da_luu=duyet'
    saved = db.get(SalaryAdvance, row.id, populate_existing=True)
    assert saved.status == 'DA_DUYET' and saved.decided_by == p['director'].id
    # Bấm lại bằng phiên bản cũ: không duyệt lần hai, báo lỗi ngay trên trang.
    r = client.post(f'/cho-anh-duyet/TAM_UNG/{row.id}/duyet', data=dict(csrf=csrf, version=row.version))
    assert r.status_code >= 400 and 'alert bad' in r.text


def test_reject_needs_reason(client, db, p):
    row = payroll.request_advance(db, p['sales'], 1_000_000, 'Việc gia đình')
    csrf = web_login(client, p['director'])
    r = client.post(f'/cho-anh-duyet/TAM_UNG/{row.id}/tu-choi', data=dict(csrf=csrf, version=row.version, reason=''))
    assert r.status_code == 400 and 'alert bad' in r.text and 'ap-rejecting' in r.text
    assert _status(db, row.id) == 'CHO_DUYET'
    r = client.post(f'/cho-anh-duyet/TAM_UNG/{row.id}/tu-choi', data=dict(csrf=csrf, version=row.version, reason='Tháng này đã ứng'),
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].endswith('da_luu=tu-choi')
    assert _status(db, row.id) == 'TU_CHOI'


def test_only_director_page_and_stock_count_not_inline(client, db, p):
    row = payroll.request_advance(db, p['technician'], 500_000, 'Sinh hoạt')
    csrf = web_login(client, p['warehouse'])
    assert client.post(f'/cho-anh-duyet/TAM_UNG/{row.id}/duyet', data=dict(csrf=csrf, version=row.version)).status_code == 403
    assert _status(db, row.id) == 'CHO_DUYET'
    csrf = web_login(client, p['director'])
    assert client.post('/cho-anh-duyet/KIEM_KE/1/duyet', data=dict(csrf=csrf, version=1)).status_code == 404
    assert client.post(f'/cho-anh-duyet/TAM_UNG/{row.id}/xoa', data=dict(csrf=csrf, version=row.version)).status_code == 404
