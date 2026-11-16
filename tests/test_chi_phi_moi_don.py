"""Chi phí phát sinh trên mọi đơn (TUẤN duyệt 16/11/2026, mẫu 3jpHcVYJarVVHsxfm461sD)."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import (approval_limits, attachments, cash_docs, cash_posting, commissions, finance_catalogs, finance_mappings, profit_report,
                 purchase_reports, sales_orders, service_charges, service_expenses as se, service_tickets)
from app.config import get_settings
from app.errors import BusinessError
from app.models import CashCategory, CashDoc, Fund, SalesOrder, ServiceExpense
from app.schemas import CashCategoryIn, CashDocIn, FundIn, SalesOrderIn, ServiceCharges
from test_my_work import NOW, DAY, people, purchase_people, ticket
from test_stock_reservations import order, stock
from test_technician_stock import held
from test_web import csrf_of, d5_image, service_login


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(se, '_now', lambda: NOW)


def fail(code, fn, *args, **kwargs):
    with pytest.raises(BusinessError) as exc:
        fn(*args, **kwargs)
    assert exc.value.code == code, exc.value.message
    return exc.value


def money_setup(db, p):
    finance_catalogs.create(db, p['director'], Fund, FundIn(code='TM', name='Tiền mặt', kind='TIEN_MAT'))
    # Danh mục để mặc định "Tính chi phí": phiếu chi chi phí phát sinh vẫn không được trừ lần hai ở Lãi ròng.
    finance_catalogs.create(db, p['director'], CashCategory, CashCategoryIn(code='CPPS', name='Chi phí phát sinh', direction='CHI'))
    finance_mappings.set_mapping(db, p['director'], 'DANH_MUC_CHI_PHI_THO', category_code='CPPS', version=1)
    doc = cash_docs.insert(db, p['director'], CashDocIn(kind='PT', doc_date=DAY, fund_code='TM', is_opening=True, amount=10_000_000))
    cash_posting.post_in_tx(db, p['director'], doc.id, doc.version); db.commit()


def on_order(db, p, o, who='sales', amount='110', **kw):
    return se.create(db, p[who], None, kw.pop('kind', 'GUI_HANG'), kw.pop('description', 'Gửi Viettel Post'), kw.pop('place', 'Viettel Post'),
                     amount, None, order_id=o.id, **kw)


def approve(db, user, row):
    db.refresh(row)
    return se.decide(db, user, row.id, row.version, True)


def gh_doc(db, p, o):
    gh = sales_orders.deliveries(db, o.id)[0]
    return next(d for d in purchase_reports.gross_margin(db, p['finance'], DAY, DAY)['documents'] if d['kind'] == 'GH' and d['id'] == gh.id)


def test_vat_cost_is_ex_vat():
    assert (se.net_cost(110, 10), se.net_cost(1_080_000, 8), se.net_cost(350_000, 0), se.net_cost(1, 10)) == (100, 1_000_000, 350_000, 1)


def test_order_expense_adds_ex_vat_cost_to_delivery_margin_not_customer_price(db, people):
    p = people; stock(db, p, 10); o = order(db, p, 4)
    total = o.total
    row = on_order(db, p, o, vat_rate=10)  # đơn chưa giao: tính vào lần giao đầu tiên
    assert (row.amount, row.cost, row.vat_rate, row.delivery_id, row.status) == (110, 100, 10, None, 'CHO_DUYET')
    pending = on_order(db, p, o, amount='50', kind='CHO_MAY', description='Grab chở máy')
    approve(db, p['finance'], row)
    sales_orders.deliver(db, p['warehouse'], o.id, o.version, [dict(order_line_no=1, qty=4)])
    doc = gh_doc(db, p, o)
    # Doanh thu 800, giá vốn hàng 400 + chi phí phát sinh đã duyệt 100 (khoản chờ duyệt chưa tính).
    assert (doc['revenue'], doc['cost'], doc['profit']) == (800, 500, 300)
    db.refresh(o)
    assert o.total == total  # giá báo khách / công nợ khách không đổi
    profit = se.order_profit(db, p['finance'], o)
    assert (profit['revenue'], profit['goods'], profit['expenses'], profit['profit'], profit['pending']) == (800, 400, 100, 300, pending.cost)
    assert se.order_profit(db, p['sales'], o) is None


def test_order_permissions_status_and_partner(db, people):
    p = people; stock(db, p, 10)
    draft = order(db, p, 2, confirm=False)
    fail('BAD_STATUS', on_order, db, p, draft)
    db.rollback()
    o = order(db, p, 2)
    fail('FORBIDDEN', on_order, db, p, o, who='other_sales')
    db.rollback()
    fail('FORBIDDEN', on_order, db, p, o, who='technician')  # thợ không giao đơn này
    db.rollback()
    fail('PARTNER_REQUIRED', on_order, db, p, o, payer='DOI_TAC', place='')
    fail('BAD_VAT', on_order, db, p, o, vat_rate=5)
    assert on_order(db, p, o, who='warehouse').technician_id == p['warehouse'].id
    db.get(SalesOrder, o.id).delivery_assignee_id = p['technician'].id; db.commit()
    assert on_order(db, p, o, who='technician').technician_id == p['technician'].id


def test_late_rule_after_seven_days(db, people, monkeypatch):
    p = people; stock(db, p, 10); o = order(db, p, 2)
    sales_orders.deliver(db, p['warehouse'], o.id, o.version, [dict(order_line_no=1, qty=2)])
    monkeypatch.setattr(se, '_now', lambda: NOW + timedelta(days=8))
    fail('LATE', on_order, db, p, o)
    db.rollback()
    fail('NOTE_REQUIRED', on_order, db, p, o, who='finance')
    db.rollback()
    row = on_order(db, p, o, who='finance', note='Hóa đơn gửi hàng về trễ')
    assert row.note == 'Hóa đơn gửi hàng về trễ' and row.delivery_id == sales_orders.deliveries(db, o.id)[0].id


def test_finance_approves_within_limit_director_above_and_not_own(db, people):
    p = people; stock(db, p, 10); o = order(db, p, 2)
    approval_limits.save(db, p['director'], {**{k: str(v) for k, v in approval_limits.DEFAULTS.items()}, 'CHI_PHI': '100'})
    small, big = on_order(db, p, o, amount='110', vat_rate=10), on_order(db, p, o, amount='150')
    own = on_order(db, p, o, who='finance', amount='20')
    assert approve(db, p['finance'], small).status == 'DA_DUYET'  # giá vốn 100 = hạn mức
    fail('TREN_HAN_MUC', approve, db, p['finance'], big)
    db.rollback()
    fail('FORBIDDEN', approve, db, p['finance'], own)
    db.rollback()
    assert approve(db, p['director'], big).status == 'DA_DUYET'
    assert approve(db, p['director'], own).status == 'DA_DUYET'
    listing = {r['row'].id: r for r in se.listing(db, p['finance'], 'DA_DUYET')}
    assert listing[small.id]['href'] == f'/don-ban/{o.id}' and listing[small.id]['ticket'] == o.number


def test_pay_groups_by_payee_and_net_profit_never_counts_twice(db, people):
    p = people; stock(db, p, 10); o = order(db, p, 2); money_setup(db, p)
    mine = on_order(db, p, o, amount='110', vat_rate=10)
    partner = on_order(db, p, o, amount='1.080', vat_rate=8, kind='DOI_TAC', description='Sửa main', place='Minh Khoa', payer='DOI_TAC')
    for r in (mine, partner):
        approve(db, p['finance'], r)
    fail('MIXED_TECHNICIANS', se.pay, db, p['finance'], [mine.id, partner.id], 'TM', DAY)
    db.rollback()
    doc = se.pay(db, p['finance'], [partner.id], 'TM', DAY)
    assert (doc.amount, doc.partner_name, doc.source_type) == (1080, 'Minh Khoa', 'SERVICE_EXPENSE')
    assert doc.description.startswith('Trả đối tác Minh Khoa') and o.number in doc.description
    se.pay(db, p['finance'], [mine.id], 'TM', DAY)
    assert {r.status for r in db.scalars(select(ServiceExpense))} == {'DA_HOAN'}
    # Phiếu chi danh mục "Tính chi phí" nhưng sinh từ chi phí phát sinh: không vào chi phí hoạt động.
    assert db.get(CashCategory, 'CPPS').pl_kind == 'CHI_PHI'
    assert profit_report.operating_expenses(db, DAY, DAY)['amount'] == 0


def test_ticket_expense_lowers_technician_commission_base(db, held, monkeypatch):
    p, a, b = held
    t = ticket(db, p, 'DANG_LAM')
    row = se.create(db, p['technician'], t.id, 'LINH_KIEN', 'Bánh răng', 'Tân Bình', '216.000', None, vat_rate=8)
    assert row.cost == 200000
    approve(db, p['finance'], row)
    db.refresh(t)
    attachments.add(db, p['technician'], 'service_ticket', str(t.id), 'SAU', 'sau.png', d5_image())
    service_charges.set_charges(db, p['technician'], t.id, ServiceCharges(version=t.version, billing='THU_TIEN',
        lines=[dict(kind='PHI', description='Công sửa', qty=1, unit_price=500000, vat_rate=0)]))
    db.refresh(t); service_tickets.transition(db, p['technician'], t.id, 'HOAN_THANH', t.version)
    doc = next(d for d in purchase_reports.gross_margin(db, p['finance'], DAY, DAY)['documents'] if d['kind'] == 'DV')
    assert (doc['revenue'], doc['cost']) == (500000, 200000)
    db.refresh(t)
    assert se.ticket_profit(db, p['finance'], t)['expenses'] == 200000
    monkeypatch.setenv('TD_COMMISSION_START', '2026-01-01'); get_settings.cache_clear()
    try:
        commissions.add_rule(db, p['director'], dict(target='THO', pct=2, fixed_amount=0, effective_from='2026-01-01', reason='Thử chi phí phát sinh'))
        from sqlalchemy import text
        month = t.created_at.astimezone(commissions.TZ).date().replace(day=1)
        query, params = commissions._draft_query(month)
        query = query.replace('from earned where entitled<>previous or', 'from earned where true or')  # phiếu chưa thu tiền: vẫn xem nền
        rows = [x for x in db.execute(text(query), params).mappings() if x['ticket_id'] == t.id]
        assert len(rows) == 1 and rows[0]['base'] == 300000  # gói 500.000 − chi phí 200.000
    finally:
        get_settings.cache_clear()


def test_technician_can_record_from_acceptance_not_before(db, held):
    p, a, b = held
    t = ticket(db, p, 'DA_GIAO')
    fail('BAD_STATUS', se.create, db, p['technician'], t.id, 'GUI_XE', '', '', '5.000')
    db.rollback()
    t2 = ticket(db, p, 'DA_HEN')
    assert se.create(db, p['technician'], t2.id, 'GUI_XE', '', '', '5.000').status == 'CHO_DUYET'
    office = se.create(db, p['dispatcher'], t.id, 'DOI_TAC', 'Sửa main', 'Minh Khoa', '500.000', None, payer='DOI_TAC')
    assert office.technician_id == p['dispatcher'].id


def test_order_page_shows_internal_box_and_profit_only_to_finance(client, db, people):
    p = people; stock(db, p, 10); o = order(db, p, 2)
    service_login(client, p['sales'])
    html = client.get(f'/don-ban/{o.id}').text
    assert 'Chi phí phát sinh nội bộ' in html and 'khách không thấy' in html and 'data-order-profit' not in html
    r = client.post(f'/don-ban/{o.id}/chi-phi/them', data=dict(csrf=csrf_of(html), kind='GUI_HANG', description='Gửi hàng', amount=''), follow_redirects=False)
    assert r.status_code == 400 and 'Nhập số tiền' in r.text and 'value="Gửi hàng"' in r.text
    r = client.post(f'/don-ban/{o.id}/chi-phi/them', data=dict(csrf=csrf_of(html), kind='GUI_HANG', description='Gửi hàng', amount='66.000', vat_rate='10',
                                                         payer='NV_UNG'), files={'photo': ('hd.png', d5_image(), 'image/png')}, follow_redirects=False)
    assert r.status_code == 303
    row = db.scalar(select(ServiceExpense))
    assert (row.order_id, row.amount, row.cost) == (o.id, 66000, 60000)
    assert 'vốn 60.000' in client.get(f'/don-ban/{o.id}').text
    service_login(client, p['finance'])
    html = client.get(f'/don-ban/{o.id}').text
    assert 'data-order-profit' in html and 'Lãi đơn' in html
    html = client.get('/chi-phi-tho').text
    assert 'Gửi hàng' in html and o.number in html and 'Giá vốn 60.000 · HĐ 10%' in html


def test_dispatcher_adds_on_ticket_page_and_technician_delivery_screen(client, db, held):
    p, a, b = held
    t = ticket(db, p, 'DANG_LAM')
    service_login(client, p['dispatcher'])
    html = client.get(f'/dich-vu/{t.id}').text
    assert f'action="/dich-vu/{t.id}/chi-phi/them"' in html
    r = client.post(f'/dich-vu/{t.id}/chi-phi/them', data=dict(csrf=csrf_of(html), kind='DOI_TAC', description='Sửa main', place='', amount='500.000',
                                                             payer='DOI_TAC'), follow_redirects=False)
    assert r.status_code == 400 and 'ghi tên đối tác' in r.text
    r = client.post(f'/dich-vu/{t.id}/chi-phi/them', data=dict(csrf=csrf_of(html), kind='DOI_TAC', description='Sửa main', place='Minh Khoa',
                                                             amount='500.000', payer='DOI_TAC'), follow_redirects=False)
    assert r.status_code == 303
    row = db.scalar(select(ServiceExpense))
    assert (row.ticket_id, row.payer, row.place) == (t.id, 'DOI_TAC', 'Minh Khoa')
    # Thợ không thấy khoản văn phòng ghi trên màn thợ.
    service_login(client, p['technician'])
    assert 'Sửa main' not in client.get(f'/tho/phieu/{t.id}').text
    # Màn giao đơn của thợ có form chi phí phát sinh.
    o = sales_orders.create(db, p['sales'], SalesOrderIn(customer_code='KTD-000001', lines=[
        dict(kind='KHAC', description='Công lắp đặt', qty=1, unit_price=1000, vat_rate=0)]))
    sales_orders.confirm(db, p['sales'], o.id, o.version)
    o = db.get(SalesOrder, o.id); o.fulfillment = 'THO_GIAO'; o.delivery_assignee_id = p['technician'].id; db.commit()
    html = client.get(f'/giao-don/{o.id}?return_to=tho').text
    assert 'id="delivery-expense"' in html
    r = client.post(f'/giao-don/{o.id}/chi-phi/them', data=dict(csrf=csrf_of(html), kind='GUI_XE', amount='5.000', return_to='tho'), follow_redirects=False)
    assert r.status_code == 303
    assert db.scalar(select(ServiceExpense).where(ServiceExpense.order_id == o.id)).technician_id == p['technician'].id
    assert 'Gửi xe' in client.get(f'/giao-don/{o.id}?return_to=tho').text
