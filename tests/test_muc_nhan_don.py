"""Mẫu MucNhanDon TUẤN duyệt 27/10/2026: Mực định kỳ, điều phối chọn mực ngay lúc nhận đơn, kho cấp sau khi giao thợ."""
from datetime import timedelta

from sqlalchemy import select

from app import part_requests, service_tickets as svc
from app.models import PartRequest, ServiceTicket, StockDoc
from test_my_work import people, purchase_people, NOW  # noqa: F401
from test_technician_stock import held, balance, error  # noqa: F401
from test_web import service_login


def form(csrf, **kw):
    data = dict(csrf=csrf, customer_code='KTD-000001', symptom='', priority='THUONG', kind='MUC_DINH_KY',
                appointment_choice='CHIEU:' + (NOW + timedelta(days=1)).date().isoformat(), ink_code=['TD-0001'], ink_qty=['3'])
    data.update(kw)
    return data


def test_create_with_ink_waits_for_technician_then_supplies_to_his_warehouse(client, db, held):  # noqa: F811
    p, a, b = held
    csrf = service_login(client, p['dispatcher'])
    page = client.get('/dich-vu/moi').text
    assert 'data-ink-section' in page and 'Mực giao cho thợ mang đi' in page
    assert 'data-stock="K' in client.get('/dich-vu/moi/tim-muc?q=may').text
    r = client.post('/dich-vu/moi', data=form(csrf), follow_redirects=False)
    assert r.status_code == 303, r.text
    t = db.query(ServiceTicket).one()
    assert t.status == 'MOI' and t.kind == 'MUC_DINH_KY' and t.symptom == 'Giao mực định kỳ'
    row = db.scalar(select(PartRequest))
    assert row.ticket_id == t.id and row.status == 'CHO_CAP' and row.qty == 3 and row.requester_id == p['dispatcher'].id
    service_login(client, p['warehouse'])
    assert 'data-wait-assign' in client.get('/yeu-cau-linh-kien').text
    docs = db.query(StockDoc).count()
    error('NOT_ASSIGNED', part_requests.supply, db, p['warehouse'], row.id, 'K58', row.version)
    assert db.query(StockDoc).count() == docs
    db.refresh(t)
    svc.assign(db, p['dispatcher'], t.id, p['technician'].id, t.version)
    db.refresh(row)
    part_requests.supply(db, p['warehouse'], row.id, 'K58', row.version)
    db.refresh(row); db.refresh(t)
    assert row.status == 'DA_CAP' and row.to_warehouse_code == 'K501' and t.status == 'DA_GIAO'
    assert balance(db, 'K501') == 3


def test_create_and_assign_atomic_and_bad_product_saves_nothing(client, db, held):  # noqa: F811
    p, a, b = held
    csrf = service_login(client, p['dispatcher'])
    r = client.post('/dich-vu/moi', data=form(csrf, ink_code=['TD-0001', 'KHONG-CO'], ink_qty=['1', '1']), follow_redirects=False)
    assert r.status_code == 400 and 'data-error-code="BAD_PRODUCT"' in r.text and 'TD-0001' in r.text
    assert db.query(ServiceTicket).count() == 0 and db.query(PartRequest).count() == 0
    r = client.post('/dich-vu/moi', data=form(csrf, assignee_id=str(p['technician'].id), ink_qty=['0']), follow_redirects=False)
    assert r.status_code == 400 and 'data-error-code="BAD_QTY"' in r.text
    assert db.query(ServiceTicket).count() == 0
    r = client.post('/dich-vu/moi', data=form(csrf, assignee_id=str(p['technician'].id)), follow_redirects=False)
    assert r.status_code == 303
    t = db.query(ServiceTicket).one()
    assert t.assignee_id == p['technician'].id and db.query(PartRequest).one().status == 'CHO_CAP'


def test_other_kind_ignores_ink_lines_and_dispatcher_can_add_ink_before_assign(client, db, held):  # noqa: F811
    p, a, b = held
    csrf = service_login(client, p['dispatcher'])
    r = client.post('/dich-vu/moi', data=form(csrf, kind='SUA_TAI_CHO', symptom='Kẹt giấy'), follow_redirects=False)
    assert r.status_code == 303 and db.query(PartRequest).count() == 0
    r = client.post('/dich-vu/moi', data=form(csrf, ink_code=[], ink_qty=[]), follow_redirects=False)
    assert r.status_code == 303 and db.query(PartRequest).count() == 0
    t = db.scalar(select(ServiceTicket).where(ServiceTicket.kind == 'MUC_DINH_KY'))
    html = client.get(f'/dich-vu/{t.id}').text
    assert 'id="part-create"' in html and 'Mực định kỳ: giao cho thợ mang đi' in html
