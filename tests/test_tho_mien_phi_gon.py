"""TUẤN 25/10: thợ chỉ bấm Miễn phí, không còn 4 ô lý do và ô ghi lý do."""
import re

from app.models import SalesReceipt
from test_ux1b_hoan_thanh_gop import job, form, post  # noqa: F401
from test_web import service_login
from test_technician_stock import held  # noqa: F401
from test_my_work import people, purchase_people  # noqa: F401


def test_free_has_no_reason_chips_and_completes_with_default_reason(client, db, job):
    p, _, t = job
    service_login(client, p['technician'])
    page = client.get(f'/tho/phieu/{t.id}').text
    assert 'Giám đốc cho miễn' not in page and 'Lý do miễn phí' not in page
    assert 'type="hidden" form="service-complete" name="free_reason"' in page
    # Ô ẩn nằm ngoài <form> (gắn bằng form=), trình duyệt vẫn gửi kèm.
    reason = re.search(r'name="free_reason" value="([^"]*)"', page).group(1)
    assert reason == 'Thợ chọn miễn phí'
    data = form(client, p, t); data['free_reason'] = reason
    data.update(billing='MIEN_PHI', amount='0')
    assert post(client, t, data).status_code == 303
    db.expire_all(); db.refresh(t)
    assert t.status == 'DA_DONG' and t.receivable == 0
    assert t.free_reason == 'Thợ chọn miễn phí'
    assert db.query(SalesReceipt).count() == 0
