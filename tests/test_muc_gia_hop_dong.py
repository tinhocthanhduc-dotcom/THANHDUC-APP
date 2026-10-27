"""TUẤN 27/10/2026: Mực định kỳ giá theo hợp đồng ghi lúc tạo phiếu; thợ thấy sẵn, mặc định Chưa thu, mỗi phiếu ghi nợ riêng."""
from app import sales_receipts, service_tickets as svc
from app.models import PartRequest, ServiceTicket
from test_my_work import people, purchase_people, ticket, NOW  # noqa: F401
from test_technician_stock import held, balance, error  # noqa: F401
from test_chay_thu_luong_tho import ink, ink_form  # noqa: F401
from test_muc_nhan_don import form
from test_ux1e_gon_man_tho import post
from test_web import service_login


def test_dispatcher_records_contract_price_as_fee_line(client, db, held):  # noqa: F811
    p, a, b = held
    csrf = service_login(client, p['dispatcher'])
    assert 'name="ink_price"' in client.get('/dich-vu/moi').text
    r = client.post('/dich-vu/moi', data=form(csrf, ink_price='350.000', assignee_id=str(p['technician'].id)), follow_redirects=False)
    assert r.status_code == 303, r.text
    t = db.query(ServiceTicket).one()
    [line] = t.lines
    assert line.kind == 'PHI' and line.description == svc.INK_FEE_DESCRIPTION and line.qty == 1
    assert line.amount + line.vat_amount == 350000 and t.billing == 'THU_TIEN'


def test_bad_price_saves_nothing_and_other_kind_ignores_price(client, db, held):  # noqa: F811
    p, a, b = held
    csrf = service_login(client, p['dispatcher'])
    r = client.post('/dich-vu/moi', data=form(csrf, ink_price='35,5'), follow_redirects=False)
    assert r.status_code == 400 and 'data-error-code="BAD_AMOUNT"' in r.text and 'value="35,5"' in r.text
    assert db.query(ServiceTicket).count() == 0 and db.query(PartRequest).count() == 0
    r = client.post('/dich-vu/moi', data=form(csrf, kind='SUA_TAI_CHO', symptom='Kẹt giấy', ink_price='350.000'), follow_redirects=False)
    assert r.status_code == 303
    assert db.query(ServiceTicket).one().lines == []


def test_technician_sees_price_unpaid_by_default_and_ticket_keeps_debt(client, db, ink):  # noqa: F811
    p, own, t = ink
    svc._ink(db, p['dispatcher'], t, [], 350000); db.commit(); db.refresh(t)
    service_login(client, p['technician'])
    html = client.get(f'/tho/phieu/{t.id}').text
    assert 'value="350.000"' in html and 'cuối tháng văn phòng xuất 1 hóa đơn chung' in html
    assert 'name="collection_mode" value="" checked' in html
    r = post(client, t, ink_form(client, p, t, collection_mode='', customer_amount='350.000'))
    assert r.status_code == 303, r.text
    db.refresh(t)
    assert t.status in ('HOAN_THANH', 'DA_DONG')
    assert sales_receipts.balance(t)['remaining'] == 350000
