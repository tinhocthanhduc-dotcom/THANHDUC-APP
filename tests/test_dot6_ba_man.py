"""Đợt 6: Việc của tôi theo phân hệ, Máy đang giữ đủ thông tin, Dịch vụ cùng khuôn danh sách. Chỉ giao diện."""
from datetime import timedelta

from app import equipment_custody as svc, service_tickets
from app.schemas import ServiceTicketIn
from test_equipment_custody import handover, machine, receive, ticket  # noqa: F401
from test_my_work import NOW, people, purchase_people  # noqa: F401
from test_web import service_login


def test_held_list_shows_customer_fault_place_next_step_and_filters(client, db, machine, monkeypatch):
    p, eq = machine
    t = ticket(db, p)
    monkeypatch.setattr(svc, '_now', lambda: NOW - timedelta(days=9))
    receive(db, p, eq, t)
    monkeypatch.setattr(svc, '_now', lambda: NOW)
    handover(db, p, eq, t)
    service_login(client, p['warehouse'])
    page = client.get('/may-dang-giu').text
    row = page[page.index('data-held-serial="AB123"'):page.index('</tr>', page.index('data-held-serial="AB123"'))]
    assert t.number in row and 'Máy cần mang về sửa' in row and 'Thợ giữ' in row and '9 ngày' in row
    # Bàn giao đang chờ kho nhận: bước tiếp là kho nhận máy, nút dẫn thẳng tới màn giữ máy.
    assert 'Nhận máy vào kho kỹ thuật' in row and f'href="/giu-may/{t.id}"' in row and 'Nhận máy ›' in row
    assert 'lt-alert' in row and 'class="page-head"' in page and 'class="tiles keep-phone"' in page
    assert '<span class="t-label">Chờ nhận</span><span class="t-n">1</span>' in page
    assert '<span class="t-label">Quá hạn giữ</span><span class="t-n">1</span>' in page
    assert 'AB123' in client.get('/may-dang-giu?xem=cho-nhan').text
    assert 'AB123' in client.get('/may-dang-giu?q=ab123').text and 'AB123' not in client.get('/may-dang-giu?q=khong-co').text
    assert 'AB123' not in client.get('/may-dang-giu?location=KHO_KY_THUAT').text
    assert client.get('/may-dang-giu?location=XYZ&xem=abc').status_code == 200


def test_my_work_groups_by_area_and_pulls_overdue_first(client, db, machine):
    p, eq = machine
    late = service_tickets.create(db, p['dispatcher'], ServiceTicketIn(customer_code='KTD-000001', serial='AB123', kind='MANG_VE', symptom='In mờ'))
    late.due_at = NOW - timedelta(hours=3)
    db.commit()
    service_login(client, p['dispatcher'])
    page = client.get('/viec-cua-toi').text
    first = page[page.index('class="card work-first"'):page.index('</section>', page.index('class="card work-first"'))]
    assert late.number in first and 'class="work-item work-overdue"' in first and 'data-work-group' not in first
    assert 'class="card work-area wk-sv"' in page and 'Quá hạn · làm trước' in page
    assert 'href="/viec-cua-toi?khu=late"' in page and 'href="/viec-cua-toi?khu=sv"' in page
    only_late = client.get('/viec-cua-toi?khu=late').text
    assert 'work-first' in only_late and 'class="card work-area' not in only_late
    assert 'work-first' not in client.get('/viec-cua-toi?khu=sv').text
    assert client.get('/viec-cua-toi?khu=xyz').status_code == 200


def test_service_list_uses_list_frame_without_pink_rows(client, db, machine):
    p, eq = machine
    t = ticket(db, p)
    service_login(client, p['dispatcher'])
    page = client.get('/dich-vu?assignee_id=chua&loai=MANG_VE').text
    assert 'class="filter-chip"' in page and 'Loại: Mang về' in page and 'Không có phiếu phù hợp.' in page
    page = client.get('/dich-vu').text
    assert 'class="page-head"' in page and 'class="card list-frame"' in page and 'class="list-table lt-cards svl-table"' in page
    assert f'data-href="/dich-vu/{t.id}"' in page and 'svl-row' not in page
