"""Đơn mua nhập giá đã gồm VAT (TUẤN 28/10/2026): tổng đúng bằng SL × giá nhập, giá vốn lưu chưa VAT."""
from decimal import Decimal

from sqlalchemy import select

from app import purchase_orders as orders, purchase_receipts as receipts, purchase_returns as returns, supplier_debts
from app.models import PurchaseOrder, StockBalance
from app.schemas import PurchaseOrderUpdate
from test_purchase_orders import purchase_people, body, DAY
from test_purchase_receipts import placed
from test_dot3_form_tao import purchase_form
from test_web import csrf_of, service_login

LINE = dict(product_code='TD-0001', warehouse_code='K58', qty=20, unit_cost=33000, vat_rate=8)


def test_vat_included_total_equals_entered_and_cost_stays_ex_vat(db, purchase_people):
    u = purchase_people['warehouse']
    row = orders.create(db, u, body(price_includes_vat=True, lines=[LINE]))
    line = row.lines[0]
    assert (line.entered_price, line.unit_cost, line.amount, line.vat_amount) == (33000, 30556, 611111, 48889)
    assert (row.subtotal, row.vat_total, row.total) == (611111, 48889, 660000)
    assert orders.snapshot(row)['price_includes_vat'] is True
    # Đặt hàng kiểm tra lại dòng theo giá nhập gốc, tiền không đổi.
    orders.order(db, u, row.id, row.version)
    assert (row.status, row.total, row.lines[0].unit_cost) == ('DA_DAT', 660000, 30556)


def test_two_lines_mixed_vat_and_update_back_to_ex_vat(db, purchase_people):
    u = purchase_people['warehouse']
    row = orders.create(db, u, body(price_includes_vat=True, lines=[LINE, dict(LINE, product_code='TD-0002', qty=3, unit_cost=110000, vat_rate=10)]))
    assert row.total == 660000 + 330000 and row.lines[1].amount == 300000 and row.lines[1].vat_amount == 30000
    row = orders.update(db, u, row.id, PurchaseOrderUpdate(version=row.version, **body(lines=[LINE]).model_dump()))
    assert row.price_includes_vat is False and row.lines[0].entered_price is None
    assert (row.lines[0].unit_cost, row.subtotal, row.vat_total, row.total) == (33000, 660000, 52800, 712800)


def test_old_body_without_flag_is_ex_vat(db, purchase_people):
    row = orders.create(db, purchase_people['warehouse'], body())
    assert row.price_includes_vat is False and row.total == 162000 and row.lines[0].entered_price is None


def test_partial_receipts_split_by_amount_and_sum_to_order(db, purchase_people):
    p = purchase_people
    row = placed(db, p, price_includes_vat=True, lines=[LINE])
    first = receipts.receive(db, p['warehouse'], row.id, row.version, DAY, [dict(order_line_id=row.lines[0].id, qty=7)])[0]
    assert (first.amount, first.vat_amount, first.total) == (213889, 17111, 231000)
    assert db.get(StockBalance, ('K58', 'TD-0001')).value == 213889
    second = receipts.receive(db, p['warehouse'], row.id, row.version, DAY, [dict(order_line_id=row.lines[0].id, qty=13)])[0]
    assert (second.amount, second.vat_amount) == (397222, 31778)
    assert first.total + second.total == row.total == 660000
    assert db.get(StockBalance, ('K58', 'TD-0001')).value == 611111
    assert receipts.payable_by_order(db, p['finance'], row.id) == 660000


def test_partial_return_uses_receipt_amount_share(db, purchase_people):
    p = purchase_people
    row = placed(db, p, price_includes_vat=True, lines=[LINE])
    receipt = receipts.receive(db, p['warehouse'], row.id, row.version, DAY, [dict(order_line_id=row.lines[0].id, qty=20)])[0]
    back = returns.create(db, p['warehouse'], receipt_id=receipt.id, version=row.version, return_date=DAY, reason='Hàng lỗi',
                          lines=[dict(receipt_line_id=receipt.lines[0].id, qty=Decimal(5))])
    assert (back.amount, back.vat_amount, back.total) == (152778, 12222, 165000)
    assert supplier_debts.order_balance(db, p['finance'], row.id)['remaining'] == 495000


def test_web_form_saves_vat_included_and_shows_entered_price(client, db, purchase_people):
    service_login(client, purchase_people['warehouse'])
    page = client.get('/don-mua/moi').text
    assert 'name="price_includes_vat" value="1"' in page and 'data-po-price-head>Giá chưa VAT' in page
    r = client.post('/don-mua/moi', data=purchase_form(csrf_of(page), qty='20', unit_cost='33.000', price_includes_vat='1', then=''), follow_redirects=False)
    assert r.status_code == 303
    order = db.scalar(select(PurchaseOrder))
    assert (order.price_includes_vat, order.total, order.lines[0].unit_cost) == (True, 660000, 30556)
    edit = client.get(r.headers['location'].split('?')[0]).text
    assert 'value="33.000"' in edit and 'data-po-price-head>Giá gồm VAT' in edit
    assert 'name="price_includes_vat" value="1" checked' in edit
