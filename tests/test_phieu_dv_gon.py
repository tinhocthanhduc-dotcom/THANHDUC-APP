"""Đợt 4 (TUẤN duyệt 18/11/2026): phiếu dịch vụ văn phòng có khung tóm tắt, khung Bước hiện tại khi chờ thợ, mục khác gập."""
from test_my_work import people, purchase_people, ticket  # noqa: F401
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
    page = client.get(f'/dich-vu/{new.id}').text
    assert 'data-svc-now' not in page and 'data-act-for-tech' not in page and 'id="service-assign"' in page
