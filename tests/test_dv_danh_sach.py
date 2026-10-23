"""Danh sách dịch vụ: ô số, nhóm trạng thái, lọc loại/thợ và phân trang (nguyên tắc 23/10/2026)."""
import re
from datetime import UTC, datetime, timedelta

from app import service_tickets as svc
from app.models import ServiceTicket
from app.web import paging
from test_web import new_service, service_login, service_people  # noqa: F401


def numbers(html):
    return re.findall(r'<b class="code">(DV-[\d-]+)</b>', html)


def many(db, people, n):
    return [new_service(db, people) for _ in range(n)]


def test_paging_build_clamps_and_keeps_filters():
    p = paging.build('9', '50', 120, dict(q='kẹt', nhom='lam', trang='3', phan='1'))
    assert (p['page'], p['size'], p['last'], p['start'], p['end']) == (3, 50, 3, 101, 120)
    assert p['numbers'] == [1, 2, 3]
    assert p['href'](2) == '?q=k%E1%BA%B9t&nhom=lam&so=50&trang=2' and p['href'](1) == '?q=k%E1%BA%B9t&nhom=lam&so=50'
    p = paging.build('x', '7', 0, {})
    assert (p['page'], p['size'], p['last'], p['start'], p['end']) == (1, 25, 1, 0, 0)
    assert paging.build('6', '', 300, {})['numbers'] == [1, None, 5, 6, 7, None, 12]


def test_pages_do_not_overlap_and_total_counts_all(client, db, service_people):
    tickets = many(db, service_people, 30)
    service_login(client, service_people['dispatcher'])
    first = client.get('/dich-vu').text
    second = client.get('/dich-vu?trang=2').text
    one, two = numbers(first), numbers(second)
    assert len(one) == 25 and len(two) == 5 and not set(one) & set(two)
    assert set(one) | set(two) == {t.number for t in tickets}
    assert 'Hiện <b>1–25</b> / 30 phiếu' in first and 'Hiện <b>26–30</b> / 30 phiếu' in second
    assert 'href="?trang=2"' in first and 'data-pager-next' in first and 'data-pager-next' not in second
    # Trang quá lớn mở trang cuối; số dòng/trang lạ về 25.
    assert numbers(client.get('/dich-vu?trang=99').text) == two
    assert len(numbers(client.get('/dich-vu?so=50').text)) == 30
    assert len(numbers(client.get('/dich-vu?so=7').text)) == 25
    # Tìm trực tiếp (phan=1) trả về trang 1 kèm thanh phân trang, không cả trang.
    part = client.get('/dich-vu?phan=1&trang=2').text
    assert '<html' not in part and 'Hiện <b>26–30</b>' in part


def test_groups_tiles_kind_and_unassigned_filters(client, db, service_people):
    p = service_people
    fresh, assigned, waiting, done = many(db, p, 4)
    svc.assign(db, p['dispatcher'], assigned.id, p['technician'].id, assigned.version)
    svc.assign(db, p['dispatcher'], waiting.id, p['technician'].id, waiting.version)
    svc.assign(db, p['dispatcher'], done.id, p['other'].id, done.version)
    db.refresh(waiting)
    db.query(ServiceTicket).filter_by(id=waiting.id).update(dict(status='CHO_LINH_KIEN'))
    db.query(ServiceTicket).filter_by(id=done.id).update(dict(status='DA_DONG', kind=None))
    db.query(ServiceTicket).filter_by(id=fresh.id).update(dict(priority='KHAN', due_at=datetime.now(UTC) - timedelta(hours=1)))
    db.commit()
    service_login(client, p['dispatcher'])
    page = client.get('/dich-vu').text
    assert '<span>Chưa giao thợ</span><strong>1</strong><small>1 phiếu Khẩn</small>' in page
    assert '<span>Quá hạn</span><strong>1</strong>' in page and 'href="/dich-vu?overdue=1"' in page
    assert re.search(r'Đang xử lý <span>1</span>', page) and re.search(r'Hoàn thành <span>1</span>', page)
    assert numbers(client.get('/dich-vu?nhom=moi').text) == [fresh.number]
    assert numbers(client.get('/dich-vu?nhom=lam').text) == [assigned.number]
    assert numbers(client.get('/dich-vu?nhom=cho').text) == [waiting.number]
    assert numbers(client.get('/dich-vu?loai=chua').text) == [done.number]
    assert done.number not in numbers(client.get('/dich-vu?loai=SUA_TAI_CHO').text)
    assert numbers(client.get('/dich-vu?assignee_id=chua').text) == [fresh.number]
    assert set(numbers(client.get(f'/dich-vu?assignee_id={p["technician"].id}').text)) == {assigned.number, waiting.number}
    # Tab đếm theo bộ lọc đang chọn, link tab giữ bộ lọc khác.
    page = client.get('/dich-vu?loai=chua').text
    assert re.search(r'Tất cả <span>1</span>', page) and 'href="/dich-vu?loai=chua&amp;nhom=xong"' in page
    # Nhóm lạ, loại lạ bị bỏ qua, không lỗi.
    assert len(numbers(client.get('/dich-vu?nhom=abc&loai=XYZ').text)) == 4
    assert 'Giao thợ</span>' in client.get('/dich-vu?nhom=moi').text


def test_technician_sees_only_own_with_paging(client, db, service_people):
    p = service_people
    mine = many(db, p, 27)
    for t in mine:
        svc.assign(db, p['dispatcher'], t.id, p['technician'].id, t.version)
    other = new_service(db, p)
    service_login(client, p['technician'])
    page = client.get('/dich-vu').text
    assert '<h1>Việc của tôi</h1>' in page and 'name="assignee_id"' not in page
    assert 'Hiện <b>1–25</b> / 27 phiếu' in page and other.number not in page
    assert other.number not in client.get('/dich-vu?assignee_id=chua&trang=2').text
