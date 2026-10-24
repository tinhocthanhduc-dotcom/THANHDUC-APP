"""Đợt 5 · dịch vụ: phiếu khung 2 cột (Bước tiếp + chọn thợ ở cột phải), Điều phối cùng khuôn danh sách."""
from datetime import UTC, datetime, timedelta

from test_web import new_service, service_login, service_people  # noqa: F401


def test_new_ticket_assign_sits_in_next_box_and_overdue_is_a_badge(client, db, service_people):
    t = new_service(db, service_people)
    t.due_at = datetime.now(UTC) - timedelta(hours=2)
    db.commit()
    service_login(client, service_people['dispatcher'])
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'class="doc-grid"' in page and 'class="doc-side"' in page
    box = page[page.index('svc-next'):page.rindex('</aside>')]
    assert 'Bước tiếp · điều phối' in box and 'id="service-assign"' in box and 'svc-techs' in box
    assert service_people['technician'].full_name in box and 'việc đang mở' in box
    # Quá hạn: nhãn đỏ trên đầu và dòng hạn chữ đỏ, không tô đỏ cả khối.
    assert 'b-huy">Quá hạn' in page and 'service-overdue' not in page and 'quá hạn</dd>' in page


def test_dispatch_rows_show_customer_and_next_step(client, db, service_people):
    t = new_service(db, service_people)
    t.due_at = datetime.now(UTC) - timedelta(hours=1)
    db.commit()
    service_login(client, service_people['dispatcher'])
    page = client.get('/dieu-phoi').text
    assert 'Bước tiếp · ai làm' in page and t.number in page and 'href="/dich-vu/moi"' in page
    row = page[page.index(f'>{t.number}<'):]
    row = row[:row.index('</tr>')]
    assert 'Giao thợ' in row and 'điều phối' in row and 'Chưa giao' in row
    new = client.get('/dieu-phoi?view=new').text
    assert t.number in new and 'aria-current="true"' in new
    assert t.number not in client.get('/dieu-phoi?view=workshop').text
