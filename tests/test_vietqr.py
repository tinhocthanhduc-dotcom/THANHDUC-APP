"""QR chuyển khoản VietQR (đợt 1, TUẤN duyệt 08/11/2026): chuỗi NAPAS, mã nội dung, trang /qr, khuôn in, màn Quỹ."""
import pytest

from app import finance_catalogs, quotes, tin_gui_khach, vietqr
from app.errors import BusinessError
from app.models import Fund
from app.schemas import FundPatch, QuoteIn
from test_receipt_cash import order, t3_people, ticket  # noqa: F401 (fixture + helper)
from test_web import csrf_of, service_login

ACCOUNT = dict(bank_bin='970416', account_name='Cty TNHH TMDV Tin Học Thành Đức')


def configure(db, people):
    fund = db.get(Fund, 'TK-ACB')
    return finance_catalogs.update(db, people['director'], Fund, 'TK-ACB', FundPatch(version=fund.version, **ACCOUNT))


def test_payload_crc_and_fields():
    assert vietqr.crc16(b'123456789') == 0x29B1
    data = vietqr.payload('970436', '0071000123456', 1576800, 'TD DB2600123')
    assert data.startswith('000201010212')  # QR động
    assert '0010A000000727' in data and '0006970436' in data and '01130071000123456' in data and '0208QRIBFTTA' in data
    assert '5303704' in data and '54071576800' in data and '5802VN' in data and '62160812TD DB2600123' in data
    assert data[-8:-4] == '6304' and data[-4:] == f'{vietqr.crc16(data[:-4].encode()):04X}'
    static = vietqr.payload('970436', '0071000123456')
    assert '010211' in static and '54' + '07' not in static
    for bad in (dict(bank_bin='9704', account_no='1'), dict(bank_bin='970436', account_no=''), dict(bank_bin='970436', account_no='1', amount=500)):
        with pytest.raises(ValueError):
            vietqr.payload(**bad)
    svg = vietqr.svg(data)
    assert svg.startswith('<svg') and 'viewBox' in svg and '<?xml' not in svg


def test_content_code_round_trip_and_bank_variants():
    assert vietqr.content_code('DB-26-00123') == 'TD DB2600123'
    assert vietqr.content_code('KTD-000123') == 'TD KTD000123'
    assert vietqr.ascii_upper('Công ty TNHH TMDV Tin Học Thành Đức') == 'CONG TY TNHH TMDV TIN HOC THANH DUC'
    for text, expected in [('TD DB2600123', ('DB', 'DB-26-00123')), ('td db2600123 ck tien hang', ('DB', 'DB-26-00123')),
                           ('CK TD-DV-26-00045', ('DV', 'DV-26-00045')), ('TDBT2600078', ('BT', 'BT-26-00078')),
                           ('TD KTD000123', ('KTD', 'KTD-000123')), ('thanh toan don hang', None), ('', None)]:
        assert vietqr.parse_content(text) == expected


def test_company_account_requires_mapped_bank_fund_with_bin_and_name(db, t3_people, configured_finance):
    p = t3_people
    assert vietqr.company_account(db) is None  # TK-ACB chưa có BIN, chủ TK
    assert vietqr.for_document(db, 'DB-26-00001', 500000) is None
    assert tin_gui_khach.bank_line(db) == ''
    configure(db, p)
    account = vietqr.company_account(db)
    assert account['bank_bin'] == '970416' and account['bank_name'] == 'ACB' and account['account_no'] == '123456789'
    assert account['account_name'] == 'CTY TNHH TMDV TIN HOC THANH DUC'
    q = vietqr.for_document(db, 'DV-26-00045', 702000)
    assert q['content'] == 'TD DV2600045' and q['amount'] == 702000 and '<svg' in q['svg'] and 'ACB 123456789' in q['line']
    assert vietqr.for_document(db, 'DV-26-00045', 500) is None  # dưới 1.000 đ không vẽ
    assert tin_gui_khach.bank_line(db) == 'ACB 123456789 · CTY TNHH TMDV TIN HOC THANH DUC'
    # BIN sai hoặc đặt cho quỹ tiền mặt bị chặn.
    from pydantic import ValidationError
    with pytest.raises(ValidationError):  # BIN phải đúng 6 số ngay từ schema
        FundPatch(version=1, bank_bin='12345')
    cash = db.get(Fund, 'QUY-TM')
    with pytest.raises(BusinessError) as exc:
        finance_catalogs.update(db, p['director'], Fund, 'QUY-TM', FundPatch(version=cash.version, bank_bin='970416'))
    assert exc.value.code == 'BAD_BANK_BIN'


def test_fund_form_saves_bin_and_name_and_shows_static_qr(client, db, t3_people, configured_finance):
    p = t3_people
    csrf = service_login(client, p['finance'])
    page = client.get('/quy/TK-ACB').text
    assert 'name="bank_bin"' in page and 'ACB · 970416' in page and 'data-static-qr' not in page
    r = client.post('/quy/TK-ACB', data=dict(csrf=csrf, version='1', name='TK-ACB', kind='NGAN_HANG', bank_name='', account_no='123456789',
                                            bank_bin='970416', account_name='Cty TNHH TMDV Tin Học Thành Đức', note='', active='1'), follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    fund = db.get(Fund, 'TK-ACB')
    assert (fund.bank_bin, fund.account_name, fund.bank_name) == ('970416', 'CTY TNHH TMDV TIN HOC THANH DUC', 'ACB')
    page = client.get('/quy/TK-ACB').text
    assert 'data-static-qr' in page and 'href="/qr/quy"' in page
    static = client.get('/qr/quy')
    assert static.status_code == 200 and 'Khách tự nhập số tiền' in static.text and '123456789' in static.text


def test_qr_page_for_ticket_caps_amount_and_hides_others(client, db, t3_people, configured_finance):
    p = t3_people
    configure(db, p)
    t = ticket(db, p)
    service_login(client, p['technician'])
    page = client.get(f'/qr/dich-vu/{t.id}')
    assert page.status_code == 200, page.text
    html = page.text
    assert '500.000 đ' in html and f'TD {t.number.replace("-", "")}' in html and '<svg' in html and 'còn phải thu' in html
    assert f'href="/tho/phieu/{t.id}#thu-tien"' in html and 'data-copy="ACB 123456789' in html
    # Hạ số tiền được; nâng quá số còn phải thu thì về trần và báo lỗi; dưới 1.000 đ cũng báo.
    lower = client.get(f'/qr/dich-vu/{t.id}?so=200.000').text
    assert '200.000 đ' in lower and 'data-error-code' not in lower
    over = client.get(f'/qr/dich-vu/{t.id}?so=600.000').text
    assert 'không được quá số còn phải thu 500.000' in over and '>500.000 đ<' in over
    assert 'tối thiểu 1.000' in client.get(f'/qr/dich-vu/{t.id}?so=500').text
    assert 'chỉ gồm chữ số' in client.get(f'/qr/dich-vu/{t.id}?so=abc').text
    svg = client.get(f'/qr/dich-vu/{t.id}.svg?so=200.000')
    assert svg.status_code == 200 and svg.headers['content-type'].startswith('image/svg+xml') and svg.text.startswith('<svg')
    # Thợ khác không thấy phiếu (404 như phiếu không tồn tại); thợ của phiếu thấy nút QR trên /tho.
    tho = client.get(f'/tho/phieu/{t.id}').text
    assert 'data-qr-link' in tho and f'href="/qr/dich-vu/{t.id}"' in tho
    # Kinh doanh (người tạo phiếu) mở được QR của phiếu và về trang dịch vụ văn phòng; thợ khác không thấy phiếu.
    service_login(client, p['sales'])
    office = client.get(f'/qr/dich-vu/{t.id}')
    assert office.status_code == 200 and f'href="/dich-vu/{t.id}#thu-tien"' in office.text


def test_qr_page_for_order_technician_only_when_assigned_and_hidden_when_unconfigured(client, db, t3_people, configured_finance, make_user):
    p = t3_people
    o = order(db, p)
    service_login(client, p['sales'])
    # Chưa cấu hình BIN: trang báo chưa in được, chi tiết đơn không có nút QR.
    page = client.get(f'/qr/don-ban/{o.id}')
    assert page.status_code == 200 and 'Chưa in được QR' in page.text and 'data-vietqr' not in page.text
    assert 'data-qr-link' not in client.get(f'/don-ban/{o.id}').text
    configure(db, p)
    page = client.get(f'/qr/don-ban/{o.id}').text
    assert '1.000.000 đ' in page and f'TD {o.number.replace("-", "")}' in page and f'href="/don-ban/{o.id}#thu-tien"' in page
    detail = client.get(f'/don-ban/{o.id}').text
    assert f'href="/qr/don-ban/{o.id}"' in detail and 'QR chuyển khoản · 1.000.000 đ' in detail
    # Thợ không được giao đơn: 403; thợ được giao đơn: mở được, về trang giao đơn của thợ.
    stranger = make_user('t3.tho2', 'technician')
    service_login(client, stranger)
    assert client.get(f'/qr/don-ban/{o.id}').status_code == 403
    o.delivery_assignee_id = stranger.id
    db.commit()
    page = client.get(f'/qr/don-ban/{o.id}')
    assert page.status_code == 200 and f'href="/tho/giao-don/{o.id}#thu-tien"' in page.text
    assert client.get('/qr/khac/1').status_code == 404


def test_quote_print_shows_qr_only_when_configured(client, db, t3_people, configured_finance):
    p = t3_people
    q = quotes.create(db, p['sales'], QuoteIn(customer_code='KTD-000001', lines=[dict(description='Máy in', qty=1, unit_price=2000000, vat_rate=0)]))
    service_login(client, p['sales'])
    html = client.get(f'/bao-gia/{q.id}/in').text
    assert 'Quét để chuyển khoản' not in html
    configure(db, p)
    html = client.get(f'/bao-gia/{q.id}/in').text
    assert 'Quét để chuyển khoản' in html and f'TD {q.number.replace("-", "")}' in html and '2.000.000 đ' in html and 'ACB · 123456789' in html
