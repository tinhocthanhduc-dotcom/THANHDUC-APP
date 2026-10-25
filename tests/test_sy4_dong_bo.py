"""SY4 (báo cáo đồng bộ 25/10/2026, F5): đổi loại dịch vụ lưu tại chỗ, không tải lại trang, không mất bản nháp."""
import re
from app.models import ServiceTicket
from test_my_work import people, purchase_people, ticket, NOW  # noqa: F401
from test_technician_stock import held, transfer, balance  # noqa: F401
from test_ux1b_hoan_thanh_gop import job  # noqa: F401
from test_ks1_service_kind import unclassified  # noqa: F401
from test_mv1_workshop import onsite, photo, intake, snapshot  # noqa: F401
from test_web import service_login


def test_inline_kind_save_returns_json_and_keeps_page(client, db, unclassified):
    p, _, t = unclassified; csrf = service_login(client, p['technician'])
    page = client.get(f'/tho/phieu/{t.id}').text
    # Không còn autosubmit; form đổi loại tại chỗ, có dòng trạng thái và script riêng.
    assert 'data-autosubmit' not in page.split('id="loai-dich-vu"')[1].split('</section>')[0]
    assert 'data-kind-inline' in page and 'data-kind-status' in page and 'service-kind.js' in page
    before = t.version
    r = client.post(f'/dich-vu/{t.id}/kind', data=dict(csrf=csrf, return_to='tho', kind='MUC_DINH_KY', version=t.version), headers={'X-Inline': '1'})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data['ok'] and data['kind'] == 'MUC_DINH_KY' and data['label'] == 'Mực định kỳ' and data['version'] == before + 1
    assert re.fullmatch(r'[0-9a-f-]{36}', data['form_key'])
    db.expire_all(); assert db.get(ServiceTicket, t.id).kind == 'MUC_DINH_KY'
    # Version cũ: JSON 409 kèm mã, không đổi gì, không trang lỗi HTML.
    r = client.post(f'/dich-vu/{t.id}/kind', data=dict(csrf=csrf, kind='MANG_VE', version=before), headers={'X-Inline': '1'})
    assert r.status_code == 409 and r.json()['code'] == 'VERSION_CONFLICT' and '<html' not in r.text
    db.expire_all(); assert db.get(ServiceTicket, t.id).kind == 'MUC_DINH_KY'
    # Người không có quyền: JSON 403, không ghi gì.
    csrf2 = service_login(client, p['other_tech'])
    r = client.post(f'/dich-vu/{t.id}/kind', data=dict(csrf=csrf2, kind='MANG_VE', version=before + 1), headers={'X-Inline': '1'})
    assert r.status_code == 403 and r.json()['code'] == 'FORBIDDEN'
    db.expire_all(); assert db.get(ServiceTicket, t.id).kind == 'MUC_DINH_KY'


def test_noscript_submit_still_redirects(client, db, unclassified):
    p, _, t = unclassified; csrf = service_login(client, p['technician'])
    r = client.post(f'/dich-vu/{t.id}/kind', data=dict(csrf=csrf, return_to='tho', kind='SUA_TAI_CHO', version=t.version), follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == f'/tho/phieu/{t.id}?da_luu=1'
