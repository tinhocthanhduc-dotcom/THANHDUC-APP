"""Đợt 4 (TUẤN duyệt 18/11/2026): phiếu dịch vụ văn phòng có khung tóm tắt, khung Bước hiện tại khi chờ thợ, mục khác gập."""
from test_my_work import people, purchase_people, ticket  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_web import service_login


def test_office_ticket_summary_current_step_and_folds(client, db, people):
    p = people
    working = ticket(db, p, 'DANG_LAM')
    new = ticket(db, p, 'MOI')
    service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{working.id}').text
    assert 'Tóm tắt phiếu' in page and 'Khách báo lỗi' in page and 'Máy bị kẹt giấy' in page
    assert 'data-svc-now' in page and 'thợ đang làm tại khách' in page and 'data-wait-tech' in page
    # Nút chính của thợ vẫn có, nhưng nằm trong "Làm thay thợ"; việc phụ gập trong "Việc khác".
    assert page.index('data-act-for-tech') < page.index('data-next-primary') and 'data-more-actions' in page
    assert 'id="svc-money"' in page and 'Tiền và vật tư · chưa lưu' in page
    # 18/11: nút Lưu tiền phải thấy ngay trong khung tiền, không chỉ nằm trong "Làm thay thợ".
    assert page.index('id="svc-money"') < page.index('data-charge-save')
    page = client.get(f'/dich-vu/{new.id}').text
    assert 'data-svc-now' not in page and 'data-act-for-tech' not in page and 'id="service-assign"' in page


def test_office_money_fold_shows_saved_total_and_warehouse_dropdown(client, db, held):
    """TUẤN 19/11/2026: tiêu đề gập hiện tổng đã lưu (trước chốt t.total còn trống → 0 đ); Kho xuất là ô xổ xuống."""
    from test_technician_stock import charges
    p, _a, _b = held
    t = ticket(db, p, 'DANG_LAM')
    charges(db, p, t, qty=3, warehouse='K58', who='dispatcher')
    assert t.total is None
    service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'Tiền và vật tư · 6.000 đ' in page and 'Tiền và vật tư · 0 đ' not in page
    assert '<select name="warehouse_0" data-charge-warehouse required>' in page
    assert '<option value="K58" selected>' in page
