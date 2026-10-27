"""Công nợ khách hàng: màn khuôn 3 cho kế toán, giám đốc; cùng số với thẻ Công nợ trang khách. Chỉ giao diện, không luật tiền mới."""
from datetime import date, timedelta

from sqlalchemy import select

from app import customers, finance_reports, sales_receipts
from app.models import Customer, CustomerPhone, SalesReceipt
from app.schemas import CustomerPatch, SalesReceiptReport
from test_lg3_credit_limit import confirmed, paid
from test_stock_reservations import people, purchase_people
from test_web import service_login

PAGE = '/cong-no-khach-hang'


def setup(db, p):
    """KTD-000001 nợ 70 (đã thu 30), báo thu 20 chờ; KTD-000002 nợ 500, không cho nợ."""
    second = Customer(code='KTD-000002', name='Trường Lê Quý Đôn', kind='CONG_TY', search_text='ktd-000002 truong le quy don')
    db.add(second); db.add(CustomerPhone(customer_code='KTD-000002', phone='0909000002')); db.commit()
    first = db.get(Customer, 'KTD-000001')
    customers.update(db, p['finance'], first.code, CustomerPatch(version=first.version, phones=['0900000001'], allow_debt=True, credit_limit=1000))
    from app import sales_orders
    from app.schemas import SalesOrderIn
    o1 = confirmed(db, p, 100)
    sales_orders.deliver(db, p['warehouse'], o1.id, o1.version)  # phải thu ghi khi giao, không phải khi chốt
    db.refresh(o1)
    paid(db, p, o1, 30)
    db.refresh(o1)
    paid(db, p, o1, 20, confirm=False)
    o2 = sales_orders.create(db, p['sales'], SalesOrderIn(customer_code='KTD-000002', payment_term='TRA_NGAY',
                             lines=[dict(kind='KHAC', description='Công lắp đặt', qty=1, unit_price=500, vat_rate=0)]))
    o2 = sales_orders.confirm(db, p['sales'], o2.id, o2.version)
    sales_orders.deliver(db, p['warehouse'], o2.id, o2.version)
    return o1, o2


def test_finance_sees_tiles_rows_doc_links_last_receipt_and_totals(client, db, people, configured_finance):
    p = people
    o1, o2 = setup(db, p)
    service_login(client, p['finance'])
    page = client.get(PAGE).text
    assert 'Công nợ khách hàng' in page and 'data-debt-row="KTD-000001"' in page and 'data-debt-row="KTD-000002"' in page
    # Ô đếm: 2 khách chưa trả 550 (sổ nợ 570 trừ 20 đang ở nhân viên); KTD-000002 không cho nợ nên vượt hạn mức.
    assert '2 khách' in page and '550 đ · phải đi thu' in page and '>20<' in page
    assert 'Còn nợ <b>570</b> đ = khách chưa trả <b>550</b> + đang ở nhân viên <b>20</b> đ' in page
    assert f'href="/don-ban/{o1.id}#thu-tien"' in page and f'href="/don-ban/{o2.id}#thu-tien"' in page
    # Nút thu tiền: Báo thu mở đúng chứng từ còn nợ; Xác nhận thu chỉ khi có báo thu chờ.
    assert page.count('Báo thu ›') == 2 and f'class="btn btn-new cnk-sm cnk-pay" href="/don-ban/{o1.id}#thu-tien"' in page
    assert page.count('Xác nhận thu ›') == 1
    assert 'Thu 30' in page and 'Chưa thu lần nào' in page and 'Không cho nợ' in page and 'Còn 930' in page
    assert 'href="/bao-cao/tai-chinh?khach=KTD-000002#chung-tu"' in page and 'href="/khach-hang/KTD-000001"' in page
    # Nợ nhiều nhất lên trước.
    assert page.index('data-debt-row="KTD-000002"') < page.index('data-debt-row="KTD-000001"')
    assert client.get(PAGE + '?xep=ten').text.index('data-debt-row="KTD-000001"') < client.get(PAGE + '?xep=ten').text.index('data-debt-row="KTD-000002"')
    # Cùng số với báo cáo tài chính.
    totals = finance_reports.debt_totals(db, p['finance'])
    assert totals['remaining'] == 570 and totals['pending'] == 20


def test_tabs_and_filters_narrow_rows_and_totals(client, db, people, configured_finance):
    p = people
    setup(db, p)
    service_login(client, p['finance'])
    over = client.get(PAGE + '?tab=vuot').text
    assert 'data-debt-row="KTD-000002"' in over and 'data-debt-row="KTD-000001"' not in over and 'Còn nợ <b>500</b> đ' in over
    pending = client.get(PAGE + '?tab=cho').text
    assert 'data-debt-row="KTD-000001"' in pending and 'data-debt-row="KTD-000002"' not in pending
    old = client.get(PAGE + '?tab=qua-han').text
    assert 'data-debt-row=' not in old and 'Không có khách nào trong nhóm này.' in old and 'Xóa lọc' in old
    assert 'data-debt-row="KTD-000002"' in client.get(PAGE + '?q=le quy').text
    assert 'data-debt-row="KTD-000001"' not in client.get(PAGE + '?q=le quy').text
    assert 'data-debt-row="KTD-000002"' in client.get(PAGE + '?q=0909000').text
    by_kind = client.get(PAGE + '?loai=CONG_TY').text
    assert 'data-debt-row="KTD-000002"' in by_kind and 'data-debt-row="KTD-000001"' not in by_kind and 'Công ty' in by_kind
    nothing = client.get(PAGE + '?q=khongcoai').text
    assert 'data-debt-row=' not in nothing and 'Xóa lọc' in nothing


def test_old_debt_flagged_after_thirty_days(client, db, people, configured_finance, monkeypatch):
    p = people
    setup(db, p)
    from app.web import customer_debts
    monkeypatch.setattr(customer_debts, '_today', lambda: date.today() + timedelta(days=40))
    service_login(client, p['finance'])
    page = client.get(PAGE + '?tab=qua-han').text
    assert 'data-debt-row="KTD-000001"' in page and 'data-debt-row="KTD-000002"' in page and 'class="lt-alert"' in page


def test_empty_state_and_only_finance_or_director(client, db, people):
    p = people
    service_login(client, p['finance'])
    page = client.get(PAGE).text
    assert 'Không có khách nào còn nợ' in page and 'href="/bao-thu"' in page and '0 khách' in page
    service_login(client, p['director'])
    assert client.get(PAGE).status_code == 200
    for role in ('sales', 'warehouse', 'technician'):
        service_login(client, p[role])
        r = client.get(PAGE)
        assert r.status_code == 403, role
        assert 'href="/cong-no-khach-hang"' not in client.get('/viec-cua-toi').text
    service_login(client, p['finance'])
    assert 'href="/cong-no-khach-hang"' in client.get('/viec-cua-toi').text


def test_last_receipts_picks_latest_confirmed_only(db, people, configured_finance):
    p = people
    o1, _ = setup(db, p)
    assert finance_reports.last_receipts(db, p['finance']) == {'KTD-000001': (date.today(), 30)}
    confirmed_rows = db.scalars(select(SalesReceipt).where(SalesReceipt.status == 'DA_XAC_NHAN')).all()
    assert len(confirmed_rows) == 1


def test_splits_customer_unpaid_from_money_held_by_staff(client, db, people, configured_finance):
    """Báo thu chờ xác nhận là tiền đã ở nhân viên: tách khỏi phần khách chưa trả, ghi rõ ai giữ; số nợ trên sổ không đổi."""
    p = people
    o1, o2 = setup(db, p)
    db.refresh(o2)
    # KTD-000002 đưa đủ 500 bằng chuyển khoản, chưa đối soát: khách hết nợ thật nhưng sổ vẫn 500.
    sales_receipts.report(db, p['sales'], o2.id, SalesReceiptReport(version=o2.version, method='CHUYEN_KHOAN', amount=500))
    service_login(client, p['finance'])
    page = client.get(PAGE).text
    holder = f"Tiền mặt ở {p['sales'].full_name}"
    assert f'{holder} · 0 ngày 20' in page and 'Chuyển khoản chờ đối soát 500' in page
    assert 'Còn nợ trên sổ 70' in page and 'Còn nợ trên sổ 500' in page and 'Khách đã trả đủ' in page
    assert 'Còn nợ <b>570</b> đ = khách chưa trả <b>50</b> + đang ở nhân viên <b>520</b> đ' in page
    # Báo thu chỉ còn ở khách chưa trả hết; Xác nhận thu ở cả hai.
    assert page.count('Báo thu ›') == 1 and page.count('Xác nhận thu ›') == 2
    unpaid = client.get(PAGE + '?tab=chua-tra').text
    assert 'data-debt-row="KTD-000001"' in unpaid and 'data-debt-row="KTD-000002"' not in unpaid
    held = client.get(PAGE + '?tab=o-nv').text
    assert 'data-holders' in held and 'data-debt-row="KTD-000001"' in held and 'data-debt-row="KTD-000002"' in held
    assert client.get(PAGE + '?tab=cho').text.count('data-debt-row=') == 2  # link cũ vẫn mở đúng tab
    only_ck = client.get(PAGE + '?tab=o-nv&giu=ck').text
    assert 'data-debt-row="KTD-000002"' in only_ck and 'data-debt-row="KTD-000001"' not in only_ck
    only_cash = client.get(PAGE + f"?giu={p['sales'].id}").text
    assert 'data-debt-row="KTD-000001"' in only_cash and 'data-debt-row="KTD-000002"' not in only_cash
    # Sổ nợ không đổi: cùng số với báo cáo tài chính.
    totals = finance_reports.debt_totals(db, p['finance'])
    assert totals['remaining'] == 570 and totals['pending'] == 520


def test_debt_paid_to_staff_is_not_customer_overdue(client, db, people, configured_finance, monkeypatch):
    p = people
    _, o2 = setup(db, p)
    db.refresh(o2)
    sales_receipts.report(db, p['sales'], o2.id, SalesReceiptReport(version=o2.version, method='TIEN_MAT', amount=500))
    from app.web import customer_debts
    monkeypatch.setattr(customer_debts, '_today', lambda: date.today() + timedelta(days=40))
    service_login(client, p['finance'])
    old = client.get(PAGE + '?tab=qua-han').text
    assert 'data-debt-row="KTD-000001"' in old and 'data-debt-row="KTD-000002"' not in old
