"""UX5b: xin nhiều linh kiện một lần trong phiếu; phiếu thợ báo khi kho đã cấp đủ."""
import pytest
from sqlalchemy import func, select

from app import part_requests as svc
from app.errors import BusinessError
from app.models import AuditLog, PartRequest, ServiceStep
from app.schemas import PartRequestIn
from test_my_work import people, purchase_people, ticket  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_web import service_login


def post(client, csrf, t, codes, qtys, reason='Thay drum và gạt'):
    return client.post(f'/dich-vu/{t.id}/yeu-cau-linh-kien', data=dict(csrf=csrf, version=t.version, return_to='tho',
                       product_code=codes, qty=qtys, reason=reason), follow_redirects=False)


def test_many_parts_for_ticket_in_one_submit(client, db, held):
    p, a, b = held; t = ticket(db, p, 'DANG_LAM')
    csrf = service_login(client, p['technician'])
    # Dòng cuối là ô tìm trống khi có JS bị bỏ qua.
    r = post(client, csrf, t, ['TD-0001', 'TD-0002', ''], ['2', '1', '1'])
    assert r.status_code == 303 and r.headers['location'].startswith(f'/tho/phieu/{t.id}')
    rows = list(db.scalars(select(PartRequest).order_by(PartRequest.id)))
    assert [(x.product_code, int(x.qty), x.ticket_id, x.status) for x in rows] == [('TD-0001', 2, t.id, 'CHO_CAP'), ('TD-0002', 1, t.id, 'CHO_CAP')]
    db.refresh(t)
    assert t.status == 'CHO_LINH_KIEN'
    # Chỉ một bước chuyển trạng thái cho cả lần gửi; mỗi yêu cầu một dòng nhật ký.
    assert db.scalar(select(func.count()).select_from(ServiceStep).where(ServiceStep.ticket_id == t.id, ServiceStep.to_status == 'CHO_LINH_KIEN')) == 1
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == 'part_request.create')) == 2
    page = client.get(f'/tho/phieu/{t.id}').text
    assert 'tho-part-row' in page and 'data-parts-ready' not in page
    # Đang chờ linh kiện vẫn xin thêm được.
    assert 'data-part-multi' in page


def test_one_bad_code_creates_nothing_and_keeps_lines(client, db, held):
    p, a, b = held; t = ticket(db, p, 'DANG_LAM')
    csrf = service_login(client, p['technician'])
    r = post(client, csrf, t, ['TD-0001', 'KHONG-CO'], ['2', '1'])
    assert r.status_code == 400 and 'KHONG-CO' in r.text and 'không tồn tại' in r.text and 'tho-app' in r.text
    assert 'value="TD-0001"' in r.text and 'value="KHONG-CO"' in r.text
    assert db.scalar(select(PartRequest)) is None
    db.refresh(t); assert t.status == 'DANG_LAM'


def test_duplicate_and_limits(db, held):
    p, a, b = held; t = ticket(db, p, 'DANG_LAM')
    body = PartRequestIn(version=t.version, product_code='TD-0001', qty=1, reason='x')
    for bodies, code in (([body, body], 'DUPLICATE_PRODUCT'), ([], 'LINES_REQUIRED'),
                         ([body.model_copy(update={'product_code': f'TD-{i:04d}'}) for i in range(21)], 'TOO_MANY_LINES')):
        with pytest.raises(BusinessError) as e:
            svc.create_many(db, p['technician'], t.id, t.version, bodies)
        assert e.value.code == code
    assert db.scalar(select(PartRequest)) is None


def test_ready_banner_when_all_supplied(client, db, held):
    p, a, b = held; t = ticket(db, p, 'DANG_LAM')
    rows = svc.create_many(db, p['technician'], t.id, t.version, [PartRequestIn(version=t.version, product_code='TD-0001', qty=1, reason='Thay drum')])
    svc.supply(db, p['warehouse'], rows[0].id, 'K58', rows[0].version)
    service_login(client, p['technician'])
    page = client.get(f'/tho/phieu/{t.id}').text
    assert 'data-parts-ready' in page and 'Đã cấp' in page
