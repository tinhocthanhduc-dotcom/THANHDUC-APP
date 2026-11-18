"""Đợt 7 tối ưu thao tác (TUẤN duyệt mẫu 18/11/2026): Tổng quan gọn, 5 số + Việc cần anh trên đầu, khối khác gập."""
import re
from datetime import datetime

from app import dashboard as svc, service_tickets
from app.schemas import ServiceTicketIn
from test_dashboard import people  # noqa: F401
from test_purchase_orders import purchase_people  # noqa: F401
from test_purchase_reports import report_people  # noqa: F401
from test_supplier_debts import m3_people  # noqa: F401
from test_web import service_login


def _top(page):
    return page[:page.index('Bên dưới · bấm để mở')]


def test_director_top_has_five_and_need_box_rest_folded(client, db, people):
    p = people
    service_tickets.create(db, p['sales'], ServiceTicketIn(customer_code='KTD-000001', kind='SUA_TAI_CHO', symptom='Kẹt giấy'))
    service_login(client, p['director'])
    page = client.get('/tong-quan').text
    top = _top(page)
    assert 'Việc cần anh' in top and 'data-need="chua-giao"' in top and 'href="/dieu-phoi"' in top
    # Hàng ô thứ hai, Ai đang làm gì, Đơn trong ngày, Việc cần làm tiếp, Việc đang diễn ra: không còn ở trên đầu.
    for text in ('Đang có việc lúc này', 'id="lv-people-title"', 'id="dn-title"', 'id="lv-next-title"', 'id="lv-lanes-title"'):
        assert text not in top and text in page
    folds = re.findall(r'<details class="lv-fold card" data-fold="([a-z-]+)"', page)
    assert folds[:5] == ['hom-nay', 'ai-lam', 'don', 'lam-tiep', 'dien-ra'] and {'theo-doi', 'tien'} <= set(folds)
    assert not re.search(r'data-fold="[a-z-]+"[^>]* open', page)
    assert 'tong-quan-gon.js' in page and 'id="theo-doi"' in page


def test_finance_sees_lam_ngay_without_approvals_and_calm_when_empty(client, db, people):
    p = people
    service_login(client, p['finance'])
    top = _top(client.get('/tong-quan').text)
    assert 'Việc cần làm ngay' in top and 'Việc cần anh' not in top and 'data-need="duyet"' not in top
    assert '✓ Không có việc cần làm ngay lúc này.' in top
