"""Công nợ NCC khuôn 3 (27/10/2026): danh sách ô số/tab/tìm + trang một NCC khung 2 cột. Chỉ đọc lại số của supplier_debts."""
from datetime import timedelta

import pytest

from app import supplier_debts
from app.web import supplier_debts as page_mod
from test_ncc1_supplier_queue import people, group
from test_ncc2_fifo_aging import receipt
from test_purchase_orders import purchase_people, DAY
from test_supplier_debts import m3_people, pay
from test_web import service_login


@pytest.fixture
def ncc(db, people, monkeypatch):
    monkeypatch.setattr(page_mod, '_today', lambda: DAY)
    p = people
    old_order, _ = receipt(db, p, 60, 1000000)
    new_order, _ = receipt(db, p, 5, 500000)
    return p, old_order, new_order


def test_list_fifo_docs_since_and_buttons_match_balance_and_ncc2(client, db, ncc):
    p, old_order, new_order = ncc
    pay(db, p, 600000)
    service_login(client, p['finance'])
    html = client.get('/cong-no-ncc').text
    remaining = supplier_debts.balance(db, p['finance'], 'NCC-0001')['remaining']
    assert remaining == 900000 and 'data-remaining>900.000<' in html
    # Nợ từ = phiếu cũ nhất còn nợ, giống thẻ NCC2 ở Việc của tôi.
    since = DAY - timedelta(days=60)
    assert group(db, p)['rows'][0]['oldest_debt_date'] == since
    assert since.strftime('%d/%m/%Y') in html and 'class="lt-alert"' in html
    assert f'href="/don-mua/{old_order.id}">{old_order.number}<' in html and f'href="/don-mua/{new_order.id}">{new_order.number}<' in html
    assert 'href="/cong-no-ncc/NCC-0001#tra-tien">Trả tiền ›' in html and 'href="/cong-no-ncc/NCC-0001#so-no">Sổ nợ ›' in html
    assert 'Tìm</button>' not in html and 'Quá 30 ngày <span>1</span>' in html
    # Tìm theo số đơn mua; tab Đã trả trước trống.
    assert 'NCC-0001' in client.get('/cong-no-ncc', params={'q': new_order.number}).text
    assert 'data-debt-row' not in client.get('/cong-no-ncc', params={'q': 'khong co ncc nay'}).text
    assert 'data-debt-row' not in client.get('/cong-no-ncc?tab=tra-truoc').text


def test_old_debt_paid_off_is_not_overdue_and_detail_lists_open_orders(client, db, ncc):
    p, old_order, new_order = ncc
    pay(db, p, 1000000)
    service_login(client, p['finance'])
    html = client.get('/cong-no-ncc?tab=qua-han').text
    assert 'data-debt-row' not in html
    page = client.get('/cong-no-ncc/NCC-0001')
    assert page.status_code == 200 and '<header class="page-head">' in page.text
    text = page.text
    assert f'?don={new_order.id}#tra-tien' in text and f'?don={old_order.id}#tra-tien' not in text
    assert 'id="tra-tien"' in text and 'id="so-no"' in text and 'Bước tiếp · kế toán' in text
    assert 'data-supplier-remaining>500.000 <small>đồng</small><' in text and 'data-fill-value="500.000"' in text
    assert 'data-supplier-opening' not in text


def test_permissions_unchanged(client, db, ncc):
    p, _, _ = ncc
    for role in ('sales', 'warehouse', 'technician'):
        service_login(client, p[role])
        assert client.get('/cong-no-ncc').status_code == 403
        assert client.get('/cong-no-ncc/NCC-0001').status_code == 403
    service_login(client, p['director'])
    html = client.get('/cong-no-ncc').text
    assert 'Nhập nợ đầu kỳ NCC' in html
    assert 'data-supplier-opening' in client.get('/cong-no-ncc/NCC-0001').text
