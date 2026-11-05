"""Mẫu MucGiaoTho TUẤN duyệt 05/11/2026: danh sách mực/linh kiện của phiếu như dòng đơn bán, một nút Lưu danh sách."""
from decimal import Decimal

from sqlalchemy import select

from app import part_requests, service_tickets as svc
from app.schemas import PartRequestIn
from app.models import AuditLog, PartRequest, Product, ServiceTicket, StockDoc
from test_my_work import people, purchase_people, ticket  # noqa: F401
from test_technician_stock import held, error  # noqa: F401
from test_muc_nhan_don import form
from test_web import service_login


def _ink_ticket(client, db, p, **kw):
    db.add(Product(code='TD-0003', name='Hộp mực 35A', search_text='hop muc 35a')); db.commit()
    csrf = service_login(client, p['dispatcher'])
    r = client.post('/dich-vu/moi', data=form(csrf, ink_code=['TD-0001', 'TD-0002'], ink_qty=['3', '2'], **kw), follow_redirects=False)
    assert r.status_code == 303, r.text
    t = db.scalar(select(ServiceTicket).where(ServiceTicket.kind == 'MUC_DINH_KY'))
    rows = list(db.scalars(select(PartRequest).order_by(PartRequest.id)))
    return csrf, t, rows


def test_page_shows_compact_table_and_save_edits_removes_adds(client, db, held):  # noqa: F811
    p, _, _ = held
    csrf, t, (a, b) = _ink_ticket(client, db, p)
    html = client.get(f'/dich-vu/{t.id}').text
    assert 'action="/dich-vu/%d/linh-kien/luu"' % t.id in html and 'Lưu danh sách' in html
    assert f'id="yc-{a.id}"' in html and 'name="line_qty"' in html and 'data-pl-remove' in html
    assert 'Lý do hủy' not in html and 'K58 còn 10' in html
    assert 'data-stock="' in client.get(f'/dich-vu/{t.id}/tim-linh-kien?q=may').text
    r = client.post(f'/dich-vu/{t.id}/linh-kien/luu', data=dict(csrf=csrf, version=t.version,
        line_id=[a.id, b.id], line_version=[a.version, b.version], line_qty=['5', '2'], remove=[str(b.id)],
        add_code=['TD-0003'], add_qty=['4']), follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    rows = {r.product_code: r for r in db.scalars(select(PartRequest))}
    assert rows['TD-0001'].qty == 5 and rows['TD-0001'].status == 'CHO_CAP'
    assert rows['TD-0002'].status == 'DA_HUY' and rows['TD-0002'].decision_note == 'Bỏ khỏi danh sách mực'
    assert rows['TD-0003'].qty == 4 and rows['TD-0003'].status == 'CHO_CAP' and rows['TD-0003'].reason == part_requests.INK_REASON
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.entity == 'part_request')))
    assert {'part_request.update_qty', 'part_request.cancel', 'part_request.create'} <= actions
    html = client.get(f'/dich-vu/{t.id}').text
    assert 'Đã hủy (1)' in html and f'id="yc-{b.id}"' in html


def test_supplied_line_locked_and_one_bad_line_saves_nothing(client, db, held):  # noqa: F811
    p, _, _ = held
    csrf, t, (a, b) = _ink_ticket(client, db, p, assignee_id=str(p['technician'].id))
    part_requests.supply(db, p['warehouse'], a.id, 'K58', a.version)
    db.expire_all(); a = db.get(PartRequest, a.id); b = db.get(PartRequest, b.id); t = db.get(ServiceTicket, t.id)
    html = client.get(f'/dich-vu/{t.id}').text
    assert f'name="line_id" value="{a.id}"' not in html and f'name="line_id" value="{b.id}"' in html
    docs = db.query(StockDoc).count()
    r = client.post(f'/dich-vu/{t.id}/linh-kien/luu', data=dict(csrf=csrf, version=t.version,
        line_id=[b.id, a.id], line_version=[b.version, a.version], line_qty=['6', '1']))
    assert r.status_code == 409 and 'data-error-code="BAD_STATUS"' in r.text
    db.expire_all()
    assert db.get(PartRequest, b.id).qty == 2 and db.get(PartRequest, a.id).qty == 3 and db.query(StockDoc).count() == docs
    r = client.post(f'/dich-vu/{t.id}/linh-kien/luu', data=dict(csrf=csrf, version=t.version,
        line_id=[b.id], line_version=[b.version], line_qty=['0']))
    assert r.status_code == 400 and 'data-error-code="BAD_QTY"' in r.text
    r = client.post(f'/dich-vu/{t.id}/linh-kien/luu', data=dict(csrf=csrf, version=t.version, add_code=['TD-0002'], add_qty=['1']))
    assert r.status_code == 400 and 'data-error-code="DUPLICATE_PRODUCT"' in r.text
    error('VERSION_CONFLICT', part_requests.save_list, db, p['dispatcher'], t.id, t.version, [(b.id, b.version + 1, Decimal(3))], [], [])
    assert db.get(PartRequest, b.id).qty == 2


def test_permissions_and_repair_ticket_cannot_add_from_list(client, db, held):  # noqa: F811
    p, _, _ = held
    t = ticket(db, p, 'DANG_LAM')
    row = part_requests.create(db, p['technician'], t.id, PartRequestIn(version=t.version, product_code='TD-0001', qty=2, reason='Thay drum'))
    db.refresh(t)
    error('BAD_LINES', part_requests.save_list, db, p['technician'], t.id, t.version, [], [], [('TD-0002', Decimal(1))])
    error('FORBIDDEN', part_requests.save_list, db, p['other_tech'], t.id, t.version, [(row.id, row.version, Decimal(3))], [], [])
    part_requests.save_list(db, p['technician'], t.id, t.version, [(row.id, row.version, Decimal(3))], [], [])
    db.refresh(row)
    assert row.qty == 3
    part_requests.save_list(db, p['dispatcher'], t.id, t.version, [], [(row.id, row.version)], [])
    db.refresh(row)
    assert row.status == 'DA_HUY' and row.decision_note == 'Bỏ khỏi danh sách linh kiện'
    db.refresh(t)
    svc_status = db.get(ServiceTicket, t.id).status
    assert svc_status == 'CHO_LINH_KIEN'
