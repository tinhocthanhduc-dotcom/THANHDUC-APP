import re

import pytest

from app.models import Customer, Product

from conftest import PASSWORD


def csrf_of(html: str) -> str:
    return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


@pytest.fixture
def web_login(client, make_user):
    def _login(username: str, *roles: str) -> str:
        client.cookies.clear()
        make_user(username, *roles)
        r = client.post("/dang-nhap", data={"username": username, "password": PASSWORD, "next": "/khach-hang"})
        assert r.status_code == 200, r.text
        return csrf_of(r.text)

    return _login


def customer_form(csrf: str, **kw) -> dict:
    data = {"csrf": csrf, "kind": "CA_NHAN", "name": "Nguyễn Văn An", "phones": "0901 234 567", "tax_code": "", "address": "", "email": "", "note": ""}
    data.update(kw)
    return data


def test_chua_dang_nhap_chuyen_ve_trang_dang_nhap(client):
    r = client.get("/khach-hang?q=an", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/dang-nhap?next=%2Fkhach-hang%3Fq%3Dan"


def test_dang_nhap_sai_va_dung(client, make_user):
    make_user("le.tan", "reception")
    r = client.post("/dang-nhap", data={"username": "le.tan", "password": "sai-mat-khau", "next": "/hang-hoa"})
    assert r.status_code == 401 and "Sai tên đăng nhập" in r.text
    r = client.post("/dang-nhap", data={"username": "Le.Tan ", "password": PASSWORD, "next": "/hang-hoa"}, follow_redirects=False)
    assert r.headers["location"] == "/dich-vu"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie


def test_khong_chuyen_huong_ra_trang_ngoai(client, make_user):
    make_user("le.tan", "reception")
    r = client.post("/dang-nhap", data={"username": "le.tan", "password": PASSWORD, "next": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/dich-vu"


def test_dang_xuat(client, web_login):
    csrf = web_login("ban.hang", "sales")
    r = client.post("/dang-xuat", data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/khach-hang", follow_redirects=False).status_code == 303


def test_tao_khach_va_tim(client, web_login, db):
    csrf = web_login("ban.hang", "sales")
    r = client.post("/khach-hang/moi", data=customer_form(csrf), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/khach-hang/KTD-000001?da_luu=1")
    page = client.get(r.headers["location"]).text
    assert "Đã lưu." in page and "0901234567" in page
    rows = client.get("/khach-hang?q=nguyen van&phan=1").text
    assert "KTD-000001" in rows and "<html" not in rows
    assert "KTD-000001" in client.get("/khach-hang?q=234567").text


def test_form_thieu_csrf_bi_chan(client, web_login, db):
    web_login("ban.hang", "sales")
    r = client.post("/khach-hang/moi", data=customer_form("0" * 32))
    assert r.status_code == 403 and "Trang đã cũ" in r.text
    assert db.query(Customer).count() == 0


def test_loi_nghiep_vu_hien_lai_form(client, web_login):
    csrf = web_login("ban.hang", "sales")
    r = client.post("/khach-hang/moi", data=customer_form(csrf, phones=""))
    assert r.status_code == 400
    assert "cần ít nhất một số điện thoại" in r.text and 'value="Nguyễn Văn An"' in r.text
    r = client.post("/khach-hang/moi", data=customer_form(csrf, name="  "))
    assert "Tên không được để trống" in r.text


def test_trung_so_dien_thoai_can_xac_nhan(client, web_login):
    csrf = web_login("ban.hang", "sales")
    client.post("/khach-hang/moi", data=customer_form(csrf, kind="CONG_TY", name="Công ty A", tax_code="0301234567"))
    second = customer_form(csrf, kind="CONG_TY", name="Công ty B", tax_code="0309999999")
    r = client.post("/khach-hang/moi", data=second)
    assert r.status_code == 409 and 'name="confirm_separate"' in r.text and "KTD-000001" in r.text
    r = client.post("/khach-hang/moi", data={**second, "confirm_separate": "1", "confirm_reason": "Hai công ty cùng kế toán"})
    assert "Dùng chung số điện thoại với" in r.text and "KTD-000001" in r.text


def test_ban_hang_sua_khach_khong_dong_vao_cong_no(client, web_login, db):
    csrf = web_login("ke.toan", "finance")
    client.post("/khach-hang/moi", data=customer_form(csrf, allow_debt="1", credit_limit="5.000.000"))
    c = db.get(Customer, "KTD-000001")
    assert c.allow_debt and c.credit_limit == 5_000_000

    csrf = web_login("ban.hang", "sales")
    page = client.get("/khach-hang/KTD-000001").text
    assert 'name="credit_limit"' not in page and "5.000.000" in page
    r = client.post("/khach-hang/KTD-000001", data=customer_form(csrf, name="Nguyễn Văn An (Q.7)", version="1"), follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    c = db.get(Customer, "KTD-000001")
    assert c.name == "Nguyễn Văn An (Q.7)" and c.credit_limit == 5_000_000


def test_sua_giu_so_nguoi_lien_he_rieng(client, web_login, db):
    csrf = web_login("ban.hang", "sales")
    data = customer_form(csrf, kind="CONG_TY", name="Công ty C", tax_code="0301234567", phones="02838123456")
    data.update(contact_name=["Chị Lan", ""], contact_phones=["0912 345 678", ""], contact_note=["Kế toán", ""])
    client.post("/khach-hang/moi", data=data)
    page = client.get("/khach-hang/KTD-000001").text
    assert 'name="phones" value="02838123456"' in page
    assert 'value="0912345678"' in page
    client.post("/khach-hang/KTD-000001", data={**data, "phones": "02838123456", "contact_phones": ["0912345678", ""], "version": "1"})
    db.expire_all()
    own = {p.phone: p.is_contact for p in db.get(Customer, "KTD-000001").phones}
    assert own == {"02838123456": False, "0912345678": True}


def test_hai_nguoi_cung_sua(client, web_login):
    csrf = web_login("ban.hang", "sales")
    client.post("/khach-hang/moi", data=customer_form(csrf))
    client.post("/khach-hang/KTD-000001", data=customer_form(csrf, name="Tên mới", version="1"))
    r = client.post("/khach-hang/KTD-000001", data=customer_form(csrf, name="Tên cũ hơn", version="1"))
    assert r.status_code == 409 and "Người khác vừa sửa" in r.text
    assert 'name="version" value="1"' in r.text


def test_ky_thuat_khong_vao_khach_hang(client, web_login):
    csrf = web_login("ban.hang", "sales")
    client.post("/khach-hang/moi", data=customer_form(csrf))
    csrf = web_login("ky.thuat", "technician")
    page = client.get("/khach-hang/KTD-000001")
    assert page.status_code == 403 and "Bạn không có quyền vào trang này" in page.text
    r = client.post("/khach-hang/KTD-000001", data=customer_form(csrf, version="1"))
    assert r.status_code == 403 and "không có quyền" in r.text


def test_khong_tim_thay_hien_trang_loi(client, web_login):
    web_login("ban.hang", "sales")
    r = client.get("/khach-hang/KTD-999999")
    assert r.status_code == 404 and "Không tìm thấy khách" in r.text and "<html" in r.text


def test_hang_hoa_chi_kho_tao(client, web_login, db):
    csrf = web_login("ban.hang", "sales")
    assert client.get("/hang-hoa/moi").status_code == 403
    csrf = web_login("thu.kho", "warehouse")
    assert 'value="TD-0001"' in client.get("/hang-hoa/moi").text
    data = {"csrf": csrf, "code": "td-0124", "name": "Mực HP 107A", "group_name": "Mực", "unit": "hộp", "sale_price": "350.000"}
    r = client.post("/hang-hoa/moi", data=data, follow_redirects=False)
    assert r.headers["location"] == "/hang-hoa/TD-0124?da_luu=1"
    assert db.get(Product, "TD-0124").sale_price == 350_000
    assert 'value="TD-0125"' in client.get("/hang-hoa/moi").text
    assert "Mã hàng đã có" in client.post("/hang-hoa/moi", data=data).text
    assert "dạng TD-0001" in client.post("/hang-hoa/moi", data={**data, "code": "MUC-1"}).text


def test_hang_hoa_sua_va_ngung_ban(client, web_login, db):
    csrf = web_login("thu.kho", "warehouse")
    client.post("/hang-hoa/moi", data={"csrf": csrf, "code": "TD-0001", "name": "Drum Brother", "unit": "cái", "sale_price": "0"})
    r = client.post("/hang-hoa/TD-0001", data={"csrf": csrf, "version": "1", "name": "Drum Brother DR-2385", "unit": "cái", "sale_price": "1.250.000", "active": "1"})
    assert "Đã lưu." in r.text and "1.250.000" in r.text
    assert "TD-0001" in client.get("/hang-hoa?q=dr-2385&phan=1").text
    client.post("/hang-hoa/TD-0001", data={"csrf": csrf, "version": "2", "name": "Drum Brother DR-2385", "unit": "cái", "sale_price": "1.250.000"})
    db.expire_all()
    assert db.get(Product, "TD-0001").active is False
    assert "TD-0001" not in client.get("/hang-hoa?phan=1").text
    assert "TD-0001" in client.get("/hang-hoa?tat_ca=1&phan=1").text


def test_api_van_tra_json(client):
    r = client.get("/customers")
    assert r.status_code == 401 and r.json()["error"] == "UNAUTHENTICATED"


@pytest.mark.parametrize('path,data,code', [('/kho', {'code': 'K58', 'name': 'Kho 58'}, 'K58'), ('/nha-cung-cap', {'name': 'Minh Phát', 'tax_code': '0301234567', 'phones': '0901 234 567'}, 'NCC-0001')])
def test_catalog_web_create_update_search_and_conflict(client, web_login, path, data, code):
    csrf = web_login('thu.kho', 'warehouse')
    assert client.get(path + '/moi').status_code == 200
    assert client.post(path + '/moi', data={**data, 'csrf': 'bad'}).status_code == 403
    r = client.post(path + '/moi', data={**data, 'csrf': csrf, 'active': '1'}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == path + '/' + code + '?da_luu=1'
    assert 'Đã lưu.' in client.get(r.headers['location']).text
    assert code in client.get(path + '?q=minh%20phat&phan=1').text
    assert '<html' not in client.get(path + '?phan=1').text
    form = {**data, 'csrf': csrf, 'version': '1', 'name': 'Tên mới'}
    assert client.post(path + '/' + code, data=form, follow_redirects=False).status_code == 303
    r = client.post(path + '/' + code, data=form)
    assert r.status_code == 409 and 'Người khác vừa sửa' in r.text
    assert 'name="version" value="1"' in r.text
    assert 'Ngừng sử dụng' in client.get(path).text
    csrf = web_login('ban.hang', 'sales')
    assert client.get(path + '/' + code).status_code == 403
    assert client.get(path + '/moi').status_code == 403
    assert client.post(path + '/' + code, data={**form, 'csrf': csrf}).status_code == 403
    assert client.post(path + '/moi', data={**data, 'csrf': csrf}).status_code == 403


def test_supplier_web_errors_preserve_form(client, web_login):
    csrf = web_login('thu.kho', 'warehouse')
    data = {'csrf': csrf, 'name': 'Minh Phát', 'tax_code': '0301234567', 'active': '1'}
    client.post('/nha-cung-cap/moi', data=data)
    r = client.post('/nha-cung-cap/moi', data=data)
    assert r.status_code == 409 and 'MST đã thuộc' in r.text and 'value="Minh Phát"' in r.text
    r = client.post('/nha-cung-cap/moi', data={**data, 'name': ''})
    assert r.status_code == 400 and 'Tên không được để trống' in r.text


@pytest.fixture
def stock_catalog(db):
    from app.models import Supplier, Warehouse
    db.add_all([Warehouse(code='K58', name='Kho 58'), Warehouse(code='K145', name='Kho 145'),
                Supplier(code='NCC-0001', name='Minh Phát'),
                Product(code='TD-0001', name='Mực in', search_text='td-0001 muc in'),
                Product(code='TD-0002', name='Drum', search_text='td-0002 drum')])
    db.commit()


def stock_form(csrf, **kw):
    data = dict(csrf=csrf, kind='PN', warehouse_code='K58', doc_date='2026-10-17', supplier_code='NCC-0001',
                reason='Nhập mua hàng', note='Giữ ghi chú', product_code=['TD-0001'], qty=['2'],
                unit_cost=['120.000'], serial=['SN-01'], line_note=['Dòng đầu'])
    data.update(kw)
    return data


def stock_create_web(client, csrf, **kw):
    r = client.post('/phieu-kho/moi', data=stock_form(csrf, **kw), follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(re.search(r'/phieu-kho/(\d+)', r.headers['location']).group(1))


def test_k6_receipt_form_parses_money(client, web_login, stock_catalog, db):
    from app.models import StockDoc
    csrf = web_login('thu.kho', 'warehouse')
    doc_id = stock_create_web(client, csrf)
    doc = db.get(StockDoc, doc_id)
    assert doc.lines[0].unit_cost == 120000
    assert doc.lines[0].qty == 2 and doc.status == 'NHAP'
    page = client.get(f'/phieu-kho/{doc_id}')
    assert 'value="120.000"' in page.text and 'Mực in' in page.text


def test_k6_post_saves_draft_then_updates_balance_and_card(client, web_login, stock_catalog, db):
    from app.models import StockBalance, StockDoc, StockLedger
    csrf = web_login('thu.kho', 'warehouse')
    doc_id = stock_create_web(client, csrf)
    r = client.post(f'/phieu-kho/{doc_id}/post', data=stock_form(csrf, version='1', qty=['3']), follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    assert db.get(StockDoc, doc_id).lines[0].qty == 3
    bal = db.get(StockBalance, ('K58', 'TD-0001'))
    assert bal.qty == 3 and bal.value == 360000
    page = client.get('/ton-kho').text
    assert '3.000' in page and '360.000' in page and '120.000' in page
    page = client.get('/the-kho/K58/TD-0001').text
    assert page.count('class="card stock-ledger-row"') == 1
    assert f'href="/phieu-kho/{doc_id}"' in page
    assert db.query(StockLedger).count() == 1


def test_k6_negative_stock_preserves_form_and_balance(client, web_login, stock_catalog, db):
    from app.models import StockBalance, StockDoc
    csrf = web_login('thu.kho', 'warehouse')
    stock_create_web(client, csrf, action='post')
    doc_id = stock_create_web(client, csrf, kind='PX', supplier_code='', unit_cost=[''], qty=['5'])
    r = client.post(f'/phieu-kho/{doc_id}/post', data=stock_form(csrf, version='1', kind='PX', supplier_code='',
                    unit_cost=[''], qty=['5'], note='Giao gấp cho khách'), follow_redirects=False)
    assert r.status_code == 409
    assert 'Không đủ tồn cho TD-0001' in r.text and 'tồn hiện có 2.000' in r.text and 'số cần 5.000' in r.text
    assert 'value="5"' in r.text and 'Giao gấp cho khách' in r.text and 'value="SN-01"' in r.text
    assert 'name="version" value="2"' in r.text
    db.expire_all()
    bal = db.get(StockBalance, ('K58', 'TD-0001'))
    assert bal.qty == 2 and bal.value == 240000
    assert db.get(StockDoc, doc_id).status == 'NHAP'


def test_k6_void_needs_reason_then_reverses_balance(client, web_login, stock_catalog, db):
    from app.models import StockBalance, StockDoc
    csrf = web_login('thu.kho', 'warehouse')
    doc_id = stock_create_web(client, csrf, action='post')
    doc = db.get(StockDoc, doc_id)
    r = client.post(f'/phieu-kho/{doc_id}/void', data={'csrf': csrf, 'version': doc.version, 'reason': ''})
    assert r.status_code == 400 and 'phải ghi lý do' in r.text
    assert 'data-confirm-void' in r.text
    assert db.get(StockBalance, ('K58', 'TD-0001')).qty == 2
    r = client.post(f'/phieu-kho/{doc_id}/void', data={'csrf': csrf, 'version': doc.version, 'reason': 'Nhập sai'}, follow_redirects=False)
    assert r.status_code == 303, r.text
    db.expire_all()
    bal = db.get(StockBalance, ('K58', 'TD-0001'))
    assert bal.qty == 0 and bal.value == 0
    assert db.get(StockDoc, doc_id).status == 'DA_HUY'


def test_k6_balance_search_without_accents(client, web_login, stock_catalog):
    csrf = web_login('thu.kho', 'warehouse')
    stock_create_web(client, csrf, action='post')
    page = client.get('/ton-kho?q=muc+in').text
    assert 'Mực in' in page and 'TD-0001' in page
    assert 'Mực in' in client.get('/ton-kho?q=TD-0001&phan=1').text
    assert 'Mực in' not in client.get('/ton-kho?q=Drum&phan=1').text


def test_k6_sales_read_only(client, web_login, stock_catalog):
    csrf = web_login('thu.kho', 'warehouse')
    doc_id = stock_create_web(client, csrf)
    csrf = web_login('ban.hang', 'sales')
    for path in ['/phieu-kho', '/phieu-kho/moi', f'/phieu-kho/{doc_id}']:
        assert client.get(path).status_code == 403
    for path in ['/ton-kho', '/the-kho/K58/TD-0001']:
        r = client.get(path)
        assert r.status_code == 200, r.text
        assert '>Ghi sổ<' not in r.text and '>Lưu nháp<' not in r.text and '>Hủy nháp<' not in r.text
    assert client.post('/phieu-kho/moi', data=stock_form(csrf)).status_code == 403
    for path in [f'/phieu-kho/{doc_id}', f'/phieu-kho/{doc_id}/post', f'/phieu-kho/{doc_id}/cancel', f'/phieu-kho/{doc_id}/void']:
        assert client.post(path, data=stock_form(csrf, version=1)).status_code == 403


@pytest.mark.parametrize('action', ['', '/post', '/cancel', '/void'])
def test_k6_csrf_required(client, web_login, stock_catalog, action):
    csrf = web_login('thu.kho', 'warehouse')
    doc_id = stock_create_web(client, csrf)
    data = stock_form(csrf, version=1)
    data.pop('csrf')
    assert client.post(f'/phieu-kho/{doc_id}' + action, data=data).status_code == 403
    assert client.post('/phieu-kho/moi', data=data).status_code == 403


def test_k6_picker_filters_and_draft_cancel(client, web_login, stock_catalog):
    csrf = web_login('thu.kho', 'warehouse')
    rows = client.get('/phieu-kho/tim-hang?q=muc+in').text
    assert 'data-pick-product="TD-0001"' in rows and 'Mực in' in rows and 'TD-0002' not in rows
    doc_id = stock_create_web(client, csrf)
    page = client.get('/phieu-kho?loai=PX&kho=K58&trang_thai=NHAP&tu=2026-01-01&den=2026-12-31').text
    assert f'href="/phieu-kho/{doc_id}"' not in page
    page = client.get('/phieu-kho?loai=PN&kho=K58&trang_thai=NHAP&tu=2026-01-01&den=2026-12-31').text
    assert f'href="/phieu-kho/{doc_id}"' in page
    assert client.post(f'/phieu-kho/{doc_id}/cancel', data={'csrf': csrf, 'version': 1, 'reason': ''}).status_code == 400
    r = client.post(f'/phieu-kho/{doc_id}/cancel', data={'csrf': csrf, 'version': 1, 'reason': 'Sai phiếu'})
    assert 'Đã hủy' in r.text and 'Sai phiếu' in r.text


def test_k6_validation_and_stale_version_keep_input(client, web_login, stock_catalog):
    csrf = web_login('thu.kho', 'warehouse')
    r = client.post('/phieu-kho/moi', data=stock_form(csrf, unit_cost=['-120.000']))
    assert r.status_code == 400 and 'value="-120.000"' in r.text
    doc_id = stock_create_web(client, csrf)
    url = f'/phieu-kho/{doc_id}'
    assert client.post(url, data=stock_form(csrf, version=1)).status_code == 200
    r = client.post(url, data=stock_form(csrf, version=1, note='Chưa được lưu'))
    assert r.status_code == 409 and 'Chưa được lưu' in r.text and 'name="version" value="1"' in r.text


OPENING_CSV = b'MA_HANG,SO_LUONG,DON_GIA\nTD-0001,12,120000\nTD-0002,2,50000\n'


def opening_upload(client, csrf, payload=OPENING_CSV, **kw):
    fields = {'csrf': csrf, 'warehouse_code': 'K58', 'doc_date': '2026-12-31', **kw}
    return client.post('/ton-dau-ky', data=fields, files={'file': ('opening.csv', payload, 'text/csv')}, follow_redirects=False)


def test_opening_web_two_rows_create_draft_without_changing_stock(client, web_login, stock_catalog, db):
    from app.models import StockBalance, StockDoc, StockLedger
    csrf = web_login('thu.kho', 'warehouse')
    r = opening_upload(client, csrf)
    assert r.status_code == 303, r.text
    page = client.get(r.headers['location'])
    doc = db.query(StockDoc).one()
    assert doc.kind == 'KK' and doc.status == 'NHAP' and len(doc.lines) == 2
    assert [x.unit_cost for x in doc.lines] == [120000, 50000]
    assert doc.number in page.text and f'href="/phieu-kho/{doc.id}"' in page.text
    assert 'Kiểm tra rồi bấm Ghi sổ từng phiếu' in page.text
    assert db.query(StockBalance).count() == 0 and db.query(StockLedger).count() == 0


def test_opening_web_errors_all_shown_and_choices_retained(client, web_login, stock_catalog, db):
    from app.models import StockDoc
    csrf = web_login('thu.kho', 'warehouse')
    payload = b'MA_HANG,SO_LUONG,DON_GIA\nTD-9999,1,100\nTD-0001,,120000\n'
    r = opening_upload(client, csrf, payload, warehouse_code='K145', doc_date='2026-10-17')
    assert r.status_code == 400
    assert 'Mã không có trong danh mục' in r.text and 'Số lượng sai' in r.text
    assert '<td>2</td><td>TD-9999</td>' in r.text and '<td>3</td><td>TD-0001</td>' in r.text
    assert 'value="K145" checked' in r.text and 'value="2026-10-17"' in r.text
    assert db.query(StockDoc).count() == 0


def test_opening_web_duplicate_import_links_existing_docs(client, web_login, stock_catalog, db):
    from app.models import StockDoc
    csrf = web_login('thu.kho', 'warehouse')
    assert opening_upload(client, csrf).status_code == 303
    r = opening_upload(client, csrf)
    doc = db.query(StockDoc).one()
    assert r.status_code == 409 and 'Kho này đã có phiếu kiểm kê đầu kỳ.' in r.text
    assert f'href="/phieu-kho/{doc.id}"' in r.text and doc.number in r.text
    assert db.query(StockDoc).count() == 1


def test_opening_web_bom_and_semicolon(client, web_login, stock_catalog, db):
    from app.models import StockDoc
    from decimal import Decimal
    csrf = web_login('thu.kho', 'warehouse')
    payload = 'MA_HANG;SO_LUONG;DON_GIA\r\nTD-0001;2,5;120.000\r\n'.encode('utf-8-sig')
    r = opening_upload(client, csrf, payload)
    assert r.status_code == 303, r.text
    doc = db.query(StockDoc).one()
    assert doc.lines[0].qty == Decimal('2.500') and doc.lines[0].unit_cost == 120000


def test_opening_web_sales_denied_and_csrf_enforced(client, web_login, stock_catalog, db):
    from app.models import StockDoc
    csrf = web_login('ban.hang', 'sales')
    r = client.get('/ton-dau-ky')
    assert r.status_code == 403 and 'Bạn không có quyền vào trang này' in r.text
    assert opening_upload(client, csrf).status_code == 403
    csrf = web_login('thu.kho', 'warehouse')
    assert client.post('/ton-dau-ky', data={'warehouse_code': 'K58', 'doc_date': '2026-12-31'},
                       files={'file': ('opening.csv', OPENING_CSV, 'text/csv')}).status_code == 403
    assert db.query(StockDoc).count() == 0


def test_opening_web_sample_csv(client, web_login):
    import csv
    from io import StringIO
    web_login('thu.kho', 'warehouse')
    r = client.get('/ton-dau-ky/mau.csv')
    assert r.status_code == 200 and r.headers['content-type'].startswith('text/csv')
    assert 'attachment' in r.headers['content-disposition']
    rows = list(csv.reader(StringIO(r.content.decode('utf-8-sig'))))
    assert rows[0] == ['MA_HANG', 'SO_LUONG', 'DON_GIA'] and len(rows) == 2 and len(rows[1]) == 3


@pytest.mark.parametrize('payload,message,status', [
    (b'x' * (2 * 1024 * 1024 + 1), 'không được lớn hơn 2 MB', 413),
    (b'\xff\xfe', 'UTF-8', 400),
    (b'wrong,columns\n1,2\n', 'MA_HANG', 400),
    (b'MA_HANG,SO_LUONG,DON_GIA\n', 'File không có dòng hàng nào.', 400),
    (b'MA_HANG,SO_LUONG,DON_GIA\nTD-0001,1,100,extra\n', 'thừa cột', 400),
])
def test_opening_web_file_validation(client, web_login, stock_catalog, db, payload, message, status):
    from app.models import StockDoc
    csrf = web_login('thu.kho', 'warehouse')
    r = opening_upload(client, csrf, payload)
    assert r.status_code == status and message in r.text
    assert db.query(StockDoc).count() == 0


def test_opening_web_missing_file_date_and_warehouse(client, web_login, stock_catalog):
    csrf = web_login('thu.kho', 'warehouse')
    r = client.post('/ton-dau-ky', data={'csrf': csrf, 'warehouse_code': 'K58', 'doc_date': '2026-12-31'})
    assert r.status_code == 400 and 'Hãy chọn file CSV' in r.text
    r = opening_upload(client, csrf, doc_date='bad-date')
    assert r.status_code == 400 and 'Ngày phiếu chưa hợp lệ' in r.text
    r = opening_upload(client, csrf, warehouse_code='K404')
    assert r.status_code == 400 and 'Kho không tồn tại.' in r.text
    r = client.get('/ton-dau-ky')
    assert 'value="2026-12-31"' in r.text


def test_opening_web_all_error_labels(client, web_login, stock_catalog, db):
    from app.models import StockDoc
    db.add(Product(code='TD-0003', name='Ngừng bán', active=False))
    db.commit()
    csrf = web_login('thu.kho', 'warehouse')
    payload = b'MA_HANG,SO_LUONG,DON_GIA\nTD-0001,1,100\nTD-0001,1,100\n,1,100\nTD-0002,1,bad\nTD-0003,1,100\n'
    r = opening_upload(client, csrf, payload)
    assert r.status_code == 400
    assert 'Mã trùng (dòng đầu: 2)' in r.text and 'Thiếu mã hàng' in r.text
    assert 'Đơn giá sai' in r.text and 'Mã đã ngừng bán' in r.text
    assert db.query(StockDoc).count() == 0


def test_opening_web_result_lists_multiple_drafts(client, web_login, stock_catalog, monkeypatch):
    from app import stock_opening
    monkeypatch.setattr(stock_opening, 'MAX_LINES', 1)
    csrf = web_login('giam.doc', 'director')
    r = opening_upload(client, csrf)
    assert r.status_code == 303
    page = client.get(r.headers['location'])
    assert 'KK-26-00001' in page.text and 'KK-26-00002' in page.text
    assert 'Đã tạo 2 phiếu KK nháp' in page.text


def test_opening_web_nonempty_warehouse_message(client, web_login, stock_catalog, db):
    from app.models import StockDoc
    csrf = web_login('thu.kho', 'warehouse')
    doc_id = stock_create_web(client, csrf, action='post')
    doc = db.get(StockDoc, doc_id)
    r = client.post(f'/phieu-kho/{doc_id}/void', data={'csrf': csrf, 'version': doc.version, 'reason': 'Hủy thử'})
    assert r.status_code == 200
    r = opening_upload(client, csrf)
    assert r.status_code == 409 and 'Kho đã có phát sinh nhập xuất, không nhập tồn đầu kỳ được nữa.' in r.text


def set_min_level(client, csrf, code='TD-0001', qty='5', version='0', warehouse='K58'):
    return client.post(f'/the-kho/{warehouse}/{code}', data={'csrf': csrf, 'min_qty': qty, 'version': version}, follow_redirects=False)


def test_k7_low_stock_shortage(client, web_login, stock_catalog):
    csrf = web_login('thu.kho', 'warehouse')
    stock_create_web(client, csrf, action='post', qty=['3'])
    assert set_min_level(client, csrf).status_code == 303
    page = client.get('/ton-kho?canh-bao=1').text
    assert 'TD-0001' in page and 'Mực in' in page
    assert '<dt>Số thiếu</dt><dd>2.000</dd>' in page
    assert '<dt>Mức tối thiểu</dt><dd>5.000</dd>' in page


def test_k7_stock_above_minimum_not_alerted(client, web_login, stock_catalog):
    csrf = web_login('thu.kho', 'warehouse')
    stock_create_web(client, csrf, action='post', qty=['10'])
    assert set_min_level(client, csrf).status_code == 303
    assert 'TD-0001' not in client.get('/ton-kho?canh-bao=1').text
    assert 'TD-0001' in client.get('/ton-kho').text


def test_k7_never_received_zero_stock_first(client, web_login, stock_catalog, db):
    from app.models import StockBalance
    csrf = web_login('thu.kho', 'warehouse')
    stock_create_web(client, csrf, action='post', qty=['3'])
    assert set_min_level(client, csrf).status_code == 303
    assert set_min_level(client, csrf, code='TD-0002', qty='2').status_code == 303
    page = client.get('/ton-kho?canh-bao=1').text
    assert page.index('TD-0002') < page.index('TD-0001')
    assert '<dt>Số thiếu</dt><dd>2.000</dd>' in page
    assert db.get(StockBalance, ('K58', 'TD-0002')) is None
    # Đọc cảnh báo không tạo dòng tồn giả cho mã chưa nhập.
    assert 'TD-0002' not in client.get('/ton-kho').text


def test_k7_zero_disables_alert_without_delete(client, web_login, stock_catalog, db):
    from app.models import StockMinLevel
    csrf = web_login('thu.kho', 'warehouse')
    assert set_min_level(client, csrf).status_code == 303
    assert set_min_level(client, csrf, qty='0', version='1').status_code == 303
    assert 'TD-0001' not in client.get('/ton-kho?canh-bao=1').text
    row = db.get(StockMinLevel, ('K58', 'TD-0001'))
    assert row.min_qty == 0 and row.version == 2


def test_k7_old_version_preserves_entered_qty(client, web_login, stock_catalog, db):
    from app.models import StockMinLevel
    csrf = web_login('thu.kho', 'warehouse')
    assert set_min_level(client, csrf).status_code == 303
    assert set_min_level(client, csrf, qty='6', version='1').status_code == 303
    r = set_min_level(client, csrf, qty='7,5', version='1')
    assert r.status_code == 409 and 'Người khác vừa sửa mức tồn tối thiểu' in r.text
    assert 'name="min_qty" value="7,5"' in r.text and 'name="version" value="1"' in r.text
    row = db.get(StockMinLevel, ('K58', 'TD-0001'))
    assert row.min_qty == 6 and row.version == 2


def test_k7_sales_and_csrf(client, web_login, stock_catalog, db):
    from app.models import StockMinLevel
    csrf = web_login('ban.hang', 'sales')
    assert client.get('/ton-kho?canh-bao=1').status_code == 403
    for path in ['/ton-kho', '/the-kho/K58/TD-0001']:
        r = client.get(path)
        assert r.status_code == 200 and '>Lưu<' not in r.text
    assert set_min_level(client, csrf).status_code == 403
    csrf = web_login('thu.kho', 'warehouse')
    assert client.post('/the-kho/K58/TD-0001', data={'min_qty': '5', 'version': '0'}).status_code == 403
    assert db.query(StockMinLevel).count() == 0


def test_k7_audit_create_and_update(client, web_login, stock_catalog, db):
    from app.models import AuditLog, StockMinLevel
    csrf = web_login('giam.doc', 'director')
    assert set_min_level(client, csrf).status_code == 303
    logs = db.query(AuditLog).filter_by(entity='stock_min_level').order_by(AuditLog.id).all()
    assert len(logs) == 1 and logs[0].action == 'stock_min_level.create'
    assert logs[0].before is None and logs[0].after['min_qty'] == '5'
    assert logs[0].entity_id == 'K58/TD-0001'
    assert set_min_level(client, csrf, qty='6', version='1').status_code == 303
    logs = db.query(AuditLog).filter_by(entity='stock_min_level').order_by(AuditLog.id).all()
    assert len(logs) == 2 and logs[1].action == 'stock_min_level.update'
    assert logs[1].before['min_qty'] == '5.000' and logs[1].after['min_qty'] == '6'
    row = db.get(StockMinLevel, ('K58', 'TD-0001'))
    assert row.updated_by == logs[1].user_id and row.updated_at is not None


@pytest.mark.parametrize('qty', ['-1', '1.2345', 'NaN', 'Infinity', '999999999999', ''])
def test_k7_bad_minimum_preserves_input(client, web_login, stock_catalog, qty):
    csrf = web_login('thu.kho', 'warehouse')
    r = set_min_level(client, csrf, qty=qty)
    assert r.status_code == 400 and 'Mức tồn tối thiểu chưa hợp lệ' in r.text
    assert f'name="min_qty" value="{qty}"' in r.text


def test_k7_filter_warehouse_search_and_threshold_equality(client, web_login, stock_catalog):
    csrf = web_login('thu.kho', 'warehouse')
    stock_create_web(client, csrf, action='post', qty=['5'])
    assert set_min_level(client, csrf).status_code == 303
    assert set_min_level(client, csrf, warehouse='K145', code='TD-0002', qty='2').status_code == 303
    page = client.get('/ton-kho?canh-bao=1&kho=K58&q=muc+in&phan=1').text
    assert 'TD-0001' in page and 'TD-0002' not in page
    assert '<dt>Số thiếu</dt><dd>0.000</dd>' in page and '<html' not in page
    page = client.get('/ton-kho?canh-bao=1&kho=K145').text
    assert 'TD-0002' in page and 'TD-0001' not in page


def test_k7_initial_version_conflict_and_missing_version(client, web_login, stock_catalog, db):
    from app.models import AuditLog
    csrf = web_login('thu.kho', 'warehouse')
    assert set_min_level(client, csrf, version='1').status_code == 409
    assert set_min_level(client, csrf).status_code == 303
    assert set_min_level(client, csrf, qty='9', version='0').status_code == 409
    assert client.post('/the-kho/K58/TD-0001', data={'csrf': csrf, 'min_qty': '5'}).status_code == 400
    assert db.query(AuditLog).filter_by(entity='stock_min_level').count() == 1


@pytest.mark.parametrize('creating', [True, False])
def test_k7_concurrent_writes_have_one_winner(client, web_login, stock_catalog, engine, db, monkeypatch, creating):
    import threading
    from sqlalchemy.orm import Session
    from app import stock_min_levels as svc
    from app.errors import BusinessError
    from app.models import AuditLog, StockMinLevel, User
    from app.schemas import StockMinLevelIn
    csrf = web_login('thu.kho', 'warehouse')
    if not creating:
        assert set_min_level(client, csrf).status_code == 303
    user_id = db.query(User).filter_by(username='thu.kho').one().id
    barrier = threading.Barrier(2)
    if creating:
        original = Session.get
        def get(session, model, *args, **kw):
            row = original(session, model, *args, **kw)
            if model is StockMinLevel and row is None:
                barrier.wait(timeout=10)
            return row
        monkeypatch.setattr(Session, 'get', get)
    else:
        original = svc.snapshot
        def snapshot(level):
            if level.version == 1:
                barrier.wait(timeout=10)
            return original(level)
        monkeypatch.setattr(svc, 'snapshot', snapshot)
    results = []
    def write(value):
        with Session(engine, expire_on_commit=False) as session:
            user = session.get(User, user_id)
            try:
                svc.set_level(session, user, 'K58', 'TD-0001', StockMinLevelIn(min_qty=value, version=0 if creating else 1))
                results.append('OK')
            except BusinessError as exc:
                results.append(exc.code)
    threads = [threading.Thread(target=write, args=(value,)) for value in (6, 7)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert sorted(results) == ['OK', 'VERSION_CONFLICT']
    assert db.query(StockMinLevel).count() == 1
    assert db.query(AuditLog).filter_by(entity='stock_min_level').count() == (1 if creating else 2)


@pytest.fixture
def d1_catalog(db):
    db.add_all([Customer(code='KTD-000001', name='Minh Phát', search_text='ktd-000001 minh phat'),
                Customer(code='KTD-000002', name='Hoàng Anh', search_text='ktd-000002 hoang anh'),
                Product(code='TD-0001', name='Máy in', search_text='td-0001 may in'),
                Product(code='TD-0002', name='Máy photo', search_text='td-0002 may photo')])
    db.commit()


def site_form(csrf, **kw):
    return dict(csrf=csrf, name='Chi nhánh 58', address='58 Lê Lợi', contact_name='Chị Lan',
                contact_phone='0901234567', note='Ghi chú địa điểm', active='1', **kw)


def device_form(csrf, **kw):
    data = dict(csrf=csrf, serial='ab 123', product_code='TD-0001', customer_code='KTD-000001', site_id='',
                warranty_until='2027-12-31', condition='MOI', note='Thiết bị quầy 1', active='1')
    data.update(kw)
    return data


def test_d1_two_sites_on_customer_page(client, web_login, d1_catalog, db):
    from app.models import CustomerSite
    csrf = web_login('dieu.phoi', 'dispatcher')
    for name in ('Chi nhánh 58', 'Chi nhánh 145'):
        data = site_form(csrf); data['name'] = name
        r = client.post('/khach-hang/KTD-000001/dia-diem/moi', data=data, follow_redirects=False)
        assert r.status_code == 303, r.text
    page = client.get('/khach-hang/KTD-000001').text
    assert '<h2>Địa điểm</h2>' in page and '<h3>Chi nhánh 58' in page and '<h3>Chi nhánh 145' in page
    assert 'Chi nhánh 58' not in client.get('/khach-hang/KTD-000002').text
    assert db.query(CustomerSite).count() == 2


def test_d1_serial_normalized_and_duplicate_keeps_form(client, web_login, d1_catalog, db):
    from app.models import Equipment
    csrf = web_login('ky.thuat', 'technician')
    r = client.post('/thiet-bi/moi', data=device_form(csrf), follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].startswith('/thiet-bi/AB123')
    row = db.get(Equipment, 'AB123')
    assert row.serial == 'AB123' and row.active
    r = client.post('/thiet-bi/moi', data=device_form(csrf, serial='AB123', note='Giữ dữ liệu trùng'))
    assert r.status_code == 409 and 'Số sê-ri đã có' in r.text
    assert 'value="AB123"' in r.text and 'Giữ dữ liệu trùng' in r.text and 'value="2027-12-31"' in r.text
    assert db.query(Equipment).count() == 1


def test_d1_wrong_customer_site_rejected(client, web_login, d1_catalog, db):
    from app.models import CustomerSite, Equipment
    csrf = web_login('dieu.phoi', 'dispatcher')
    client.post('/khach-hang/KTD-000002/dia-diem/moi', data=site_form(csrf))
    site = db.query(CustomerSite).one()
    r = client.post('/thiet-bi/moi', data=device_form(csrf, site_id=site.id))
    assert r.status_code == 400 and 'Địa điểm phải thuộc đúng khách hàng' in r.text
    assert db.query(Equipment).count() == 0
    client.post('/thiet-bi/moi', data=device_form(csrf, customer_code='KTD-000002', site_id=site.id))
    r = client.post('/thiet-bi/AB123', data=device_form(csrf, version=1, customer_code='KTD-000001', site_id=site.id))
    assert r.status_code == 400
    db.expire_all()
    assert db.get(Equipment, 'AB123').customer_code == 'KTD-000002'


def test_d1_find_without_accents_and_compact_serial(client, web_login, d1_catalog):
    csrf = web_login('dieu.phoi', 'dispatcher')
    client.post('/thiet-bi/moi', data=device_form(csrf))
    for q in ('may in', 'minh phat', 'ab 123'):
        page = client.get('/thiet-bi', params={'q': q, 'phan': 1})
        assert page.status_code == 200 and 'AB123' in page.text and 'Máy in' in page.text
        assert '<html' not in page.text
    assert 'AB123' not in client.get('/thiet-bi?q=100%25&phan=1').text


def test_d1_stale_version_keeps_equipment_and_site_input(client, web_login, d1_catalog, db):
    from app.models import CustomerSite, Equipment
    csrf = web_login('dieu.phoi', 'dispatcher')
    client.post('/thiet-bi/moi', data=device_form(csrf))
    assert client.post('/thiet-bi/AB123', data=device_form(csrf, version=1, note='Đã lưu mới')).status_code == 200
    r = client.post('/thiet-bi/AB123', data=device_form(csrf, version=1, warranty_until='2030-01-02', note='Không được lưu'))
    assert r.status_code == 409 and 'Người khác vừa sửa thiết bị' in r.text
    assert 'value="2030-01-02"' in r.text and 'Không được lưu' in r.text and 'name="version" value="1"' in r.text
    db.expire_all()
    assert db.get(Equipment, 'AB123').note == 'Đã lưu mới'
    client.post('/khach-hang/KTD-000001/dia-diem/moi', data=site_form(csrf))
    site = db.query(CustomerSite).one()
    url = f'/khach-hang/KTD-000001/dia-diem/{site.id}'
    data = site_form(csrf); data.update(version=1, contact_phone='0902222222')
    assert client.post(url, data=data).status_code == 200
    data.update(contact_phone='0903333333')
    r = client.post(url, data=data)
    assert r.status_code == 409 and 'Người khác vừa sửa địa điểm' in r.text and 'value="0903333333"' in r.text
    assert f'action="{url}"' in r.text


def test_d1_sales_read_only_and_csrf(client, web_login, d1_catalog, db):
    from app.models import CustomerSite, Equipment
    csrf = web_login('dieu.phoi', 'dispatcher')
    client.post('/khach-hang/KTD-000001/dia-diem/moi', data=site_form(csrf))
    site = db.query(CustomerSite).one()
    client.post('/thiet-bi/moi', data=device_form(csrf))
    csrf = web_login('ban.hang', 'sales')
    for path in ['/thiet-bi', '/thiet-bi/moi', '/thiet-bi/AB123', '/khach-hang/KTD-000001']:
        page = client.get(path)
        assert page.status_code == 200
        assert '>Lưu thiết bị<' not in page.text and '>Lưu địa điểm<' not in page.text and '>Thêm địa điểm<' not in page.text
    for path, data in [('/thiet-bi/moi', device_form(csrf)), ('/thiet-bi/AB123', device_form(csrf, version=1)),
                       ('/khach-hang/KTD-000001/dia-diem/moi', site_form(csrf)),
                       (f'/khach-hang/KTD-000001/dia-diem/{site.id}', {**site_form(csrf), 'version': 1})]:
        assert client.post(path, data=data).status_code == 403
    csrf = web_login('ky.thuat', 'technician')
    for path, data in [('/thiet-bi/moi', device_form(csrf)), ('/thiet-bi/AB123', device_form(csrf, version=1)),
                       ('/khach-hang/KTD-000001/dia-diem/moi', site_form(csrf)),
                       (f'/khach-hang/KTD-000001/dia-diem/{site.id}', {**site_form(csrf), 'version': 1})]:
        data.pop('csrf')
        assert client.post(path, data=data).status_code == 403
    assert db.query(Equipment).count() == 1


def test_d1_audit_and_soft_deactivation(client, web_login, d1_catalog, db):
    from app.models import AuditLog, CustomerSite, Equipment
    csrf = web_login('giam.doc', 'director')
    client.post('/khach-hang/KTD-000001/dia-diem/moi', data=site_form(csrf))
    site = db.query(CustomerSite).one()
    data = site_form(csrf); data.pop('active'); data.update(version=1)
    r = client.post(f'/khach-hang/KTD-000001/dia-diem/{site.id}', data=data)
    assert r.status_code == 200
    client.post('/thiet-bi/moi', data=device_form(csrf))
    data = device_form(csrf, version=1); data.pop('active')
    assert client.post('/thiet-bi/AB123', data=data).status_code == 200
    db.expire_all()
    assert db.query(CustomerSite).count() == 1 and not db.get(CustomerSite, site.id).active
    assert db.query(Equipment).count() == 1 and not db.get(Equipment, 'AB123').active
    logs = db.query(AuditLog).filter(AuditLog.entity.in_(['customer_site', 'equipment'])).order_by(AuditLog.id).all()
    assert [x.action for x in logs] == ['customer_site.create', 'customer_site.update', 'equipment.create', 'equipment.update']
    assert logs[0].before is None and logs[1].before['active'] and not logs[1].after['active']
    assert logs[2].before is None and logs[3].before['active'] and not logs[3].after['active']
    assert 'Ngừng dùng' in client.get('/thiet-bi').text
    assert client.delete('/thiet-bi/AB123').status_code == 405


def test_d1_picker_sites_scoped_and_optional_ownership(client, web_login, d1_catalog, db):
    from app.models import CustomerSite, Equipment
    csrf = web_login('dieu.phoi', 'dispatcher')
    client.post('/khach-hang/KTD-000001/dia-diem/moi', data=site_form(csrf))
    data = site_form(csrf); data['name'] = 'Khách thứ hai'
    client.post('/khach-hang/KTD-000002/dia-diem/moi', data=data)
    r = client.get('/thiet-bi/dia-diem?customer_code=KTD-000001')
    assert 'Chi nhánh 58' in r.text and 'Khách thứ hai' not in r.text
    assert 'Máy in' in client.get('/thiet-bi/tim-hang?q=may+in').text
    assert 'Minh Phát' in client.get('/thiet-bi/tim-khach?q=minh+phat').text
    r = client.post('/thiet-bi/moi', data=device_form(csrf, serial=' ab\t12\u00a03 ', customer_code='', warranty_until=''))
    assert r.status_code == 200, r.text
    row = db.get(Equipment, 'AB123')
    assert row.customer_code is None and row.site_id is None and row.warranty_until is None
    site = db.query(CustomerSite).filter_by(customer_code='KTD-000002').one()
    assert client.post('/khach-hang/KTD-000001/dia-diem/' + str(site.id), data={**site_form(csrf), 'version': 1}).status_code == 404


def test_d1_invalid_fields_keep_form(client, web_login, d1_catalog):
    csrf = web_login('dieu.phoi', 'dispatcher')
    for kw in ({'serial': '   '}, {'site_id': 'not-an-id'}, {'condition': 'X'}, {'warranty_until': 'bad'}):
        r = client.post('/thiet-bi/moi', data=device_form(csrf, **kw))
        assert r.status_code == 400
    r = client.post('/khach-hang/KTD-000001/dia-diem/moi', data={**site_form(csrf), 'name': ''})
    assert r.status_code == 400 and 'Tên địa điểm chưa hợp lệ' in r.text


@pytest.mark.parametrize('entity', ['site', 'equipment'])
def test_d1_concurrent_update_one_winner(client, web_login, d1_catalog, db, engine, monkeypatch, entity):
    import threading
    from sqlalchemy.orm import Session
    from app import customer_sites, equipment
    from app.errors import BusinessError
    from app.models import CustomerSite, User, AuditLog
    from app.schemas import CustomerSitePatch, EquipmentPatch
    csrf = web_login('dieu.phoi', 'dispatcher')
    client.post('/khach-hang/KTD-000001/dia-diem/moi', data=site_form(csrf))
    site_id = db.query(CustomerSite).one().id
    client.post('/thiet-bi/moi', data=device_form(csrf))
    user_id = db.query(User).filter_by(username='dieu.phoi').one().id
    svc = customer_sites if entity == 'site' else equipment
    original = svc.snapshot
    barrier = threading.Barrier(2)
    def snapshot(row):
        if row.version == 1:
            barrier.wait(timeout=10)
        return original(row)
    monkeypatch.setattr(svc, 'snapshot', snapshot)
    results = []
    def write(note):
        with Session(engine, expire_on_commit=False) as session:
            user = session.get(User, user_id)
            try:
                if entity == 'site':
                    svc.update(session, user, site_id, CustomerSitePatch(version=1, note=note))
                else:
                    svc.update(session, user, 'AB123', EquipmentPatch(version=1, note=note))
                results.append('OK')
            except BusinessError as exc:
                results.append(exc.code)
    threads = [threading.Thread(target=write, args=(note,)) for note in ('A', 'B')]
    for t in threads: t.start()
    for t in threads: t.join(timeout=15)
    assert sorted(results) == ['OK', 'VERSION_CONFLICT']
    action = 'customer_site.update' if entity == 'site' else 'equipment.update'
    assert db.query(AuditLog).filter_by(action=action).count() == 1


def test_d1_concurrent_duplicate_serial(client, web_login, d1_catalog, db, engine):
    import threading
    from sqlalchemy.orm import Session
    from app import equipment
    from app.errors import BusinessError
    from app.models import AuditLog, Equipment, User
    from app.schemas import EquipmentIn
    web_login('dieu.phoi', 'dispatcher')
    user_id = db.query(User).filter_by(username='dieu.phoi').one().id
    results = []
    def create(serial):
        with Session(engine, expire_on_commit=False) as session:
            user = session.get(User, user_id)
            try:
                equipment.create(session, user, EquipmentIn(serial=serial, product_code='TD-0001'))
                results.append('OK')
            except BusinessError as exc:
                results.append(exc.code)
    threads = [threading.Thread(target=create, args=(serial,)) for serial in ('ab 123', 'AB123', ' ab123 ')]
    for t in threads: t.start()
    for t in threads: t.join(timeout=15)
    assert sorted(results) == ['DUPLICATE_SERIAL', 'DUPLICATE_SERIAL', 'OK']
    assert db.query(Equipment).count() == 1
    assert db.query(AuditLog).filter_by(action='equipment.create').count() == 1


# D3 — màn hình chỉ gọi lõi phiếu dịch vụ, dùng cookie và CSRF như các trang khác.
@pytest.fixture
def service_people(db, make_user):
    from app.models import CustomerSite, Equipment
    db.add_all([
        Customer(code="KTD-000001", name="Minh Phát", search_text="minh phat"),
        Customer(code="KTD-000002", name="Khách khác", search_text="khach khac"),
        Product(code="TD-0001", name="Máy in", search_text="may in"),
    ])
    db.commit()
    people = {role: make_user('dv.' + role, role) for role in ('sales', 'dispatcher', 'technician', 'warehouse', 'director')}
    people['other'] = make_user('dv.tho.b', 'technician')
    site = CustomerSite(customer_code='KTD-000001', name='Văn phòng', created_by=people['dispatcher'].id)
    other_site = CustomerSite(customer_code='KTD-000002', name='Địa điểm khách khác', created_by=people['dispatcher'].id)
    db.add_all([site, other_site])
    db.flush()
    db.add_all([
        Equipment(serial='AB123', product_code='TD-0001', customer_code='KTD-000001', site_id=site.id,
                  search_text='ab123 may in minh phat', created_by=people['dispatcher'].id),
        Equipment(serial='OTHER456', product_code='TD-0001', customer_code='KTD-000002',
                  search_text='other456', created_by=people['dispatcher'].id),
    ])
    db.commit()
    return people


def service_login(client, user):
    client.cookies.clear()
    response = client.post('/dang-nhap', data={'username': user.username, 'password': PASSWORD, 'next': '/dich-vu'})
    assert response.status_code == 200, response.text
    return csrf_of(response.text)


def service_form(csrf, **kw):
    return {'csrf': csrf, 'customer_code': 'KTD-000001', 'site_id': '', 'serial': '',
            'kind': 'SUA_TAI_CHO', 'priority': 'THUONG', 'symptom': 'Máy in bị kẹt giấy', **kw}


def new_service(db, people):
    from app import service_tickets
    from app.schemas import ServiceTicketIn
    return service_tickets.create(db, people['sales'], ServiceTicketIn(**{k: v for k, v in service_form('x').items()
                                                                       if k not in ('csrf', 'site_id', 'serial')}))


def service_action(client, ticket, csrf, action='transition', **kw):
    return client.post(f'/dich-vu/{ticket.id}/{action}', data={'csrf': csrf, 'version': str(ticket.version), **kw})


def test_d3_reception_create_and_customer_choices(client, db, service_people, monkeypatch, make_user):
    from datetime import UTC, datetime
    from app import service_tickets as svc
    from app.models import ServiceTicket
    monkeypatch.setattr(svc, '_now', lambda: datetime(2026, 10, 17, 6, tzinfo=UTC))
    csrf = service_login(client, make_user('dv.reception', 'reception'))
    page = client.get('/dich-vu/moi').text
    assert 'data-live=".service-options"' in page and 'href="/khach-hang/moi"' in page
    assert '<select' not in page
    choices = client.get('/dich-vu/chon-khach?customer_code=KTD-000001').text
    assert 'Văn phòng' in choices and 'AB123' in choices
    assert 'OTHER456' not in choices and 'Địa điểm khách khác' not in choices
    assert 'Minh Phát' in client.get('/dich-vu/tim-khach?q=minh phat').text
    response = client.post('/dich-vu/moi', data=service_form(csrf, serial='AB123'))
    assert response.status_code == 200, response.text
    ticket = db.query(ServiceTicket).one()
    assert re.fullmatch(r'DV-26-\d{5}', ticket.number) and ticket.status == 'MOI'
    assert ticket.number in response.text and '<h2>Mới' in response.text
    assert 'Giao thợ</button>' not in response.text
    assert ticket.number in client.get('/dich-vu?q=minh phat&phan=1').text
    assert ticket.number in client.get('/dich-vu?q=ab123&phan=1').text
    assert '<html' not in client.get('/dich-vu?phan=1').text


def test_d3_dispatcher_assignment_open_counts(client, db, service_people):
    from app import service_tickets as svc
    existing = new_service(db, service_people)
    svc.assign(db, service_people['dispatcher'], existing.id, service_people['technician'].id, existing.version)
    ticket = new_service(db, service_people)
    csrf = service_login(client, service_people['dispatcher'])
    page = client.get(f'/dich-vu/{ticket.id}').text
    assert 'Dv.Technician · 1 việc đang mở' in page
    assert 'Dv.Tho.B · 0 việc đang mở' in page
    response = service_action(client, ticket, csrf, 'assign', assignee_id=service_people['other'].id, note='Giao anh B')
    assert response.status_code == 200, response.text
    db.refresh(ticket)
    assert ticket.status == 'DA_GIAO' and ticket.assignee_id == service_people['other'].id
    assert 'Giao anh B' in response.text and 'Lịch sử bước' in response.text


def test_d3_technician_flow_and_photo_required(client, db, service_people, tmp_path, monkeypatch):
    import io
    from PIL import Image
    from app import attachments, service_tickets as svc
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), 'files_dir', str(tmp_path))
    tech = service_people['technician']
    ticket = new_service(db, service_people)
    svc.assign(db, service_people['dispatcher'], ticket.id, tech.id, ticket.version)
    csrf = service_login(client, tech)
    for target, extra in [
        ('DA_NHAN', {}),
        ('DA_HEN', {'appointment_at': '2026-10-20T09:30', 'note': 'Khách hẹn buổi sáng'}),
        ('DANG_LAM', {}),
        ('CHO_LINH_KIEN', {'note': 'Chờ linh kiện thay thế'}),
        ('DANG_LAM', {}),
    ]:
        response = service_action(client, ticket, csrf, to_status=target, **extra)
        assert response.status_code == 200, response.text
        db.refresh(ticket)
        assert ticket.status == target
        if target == 'DA_HEN':
            assert ticket.appointment_at.isoformat() == '2026-10-20T02:30:00+00:00'
            assert '20/10/2026 09:30' in response.text
        if target == 'CHO_LINH_KIEN':
            assert '>Tiếp tục làm</button>' in response.text
    response = service_action(client, ticket, csrf, to_status='HOAN_THANH', note='Đã sửa xong máy')
    assert response.status_code == 400 and 'Cần chụp ảnh sau sửa trước khi hoàn thành' in response.text
    assert 'Đã sửa xong máy</textarea>' in response.text
    db.refresh(ticket)
    assert ticket.status == 'DANG_LAM'
    image = io.BytesIO()
    Image.new('RGB', (8, 8), 'white').save(image, format='PNG')
    attachments.add(db, tech, 'service_ticket', str(ticket.id), 'SAU', 'sau.png', image.getvalue())
    # D6: phải chọn cách thu trước khi hoàn thành (màn hình tiền làm ở bước sau).
    from app import service_charges
    from app.schemas import ServiceCharges
    service_charges.set_charges(db, tech, ticket.id, ServiceCharges(billing='MIEN_PHI', free_reason='Bảo hành', lines=[]))
    db.refresh(ticket)
    response = service_action(client, ticket, csrf, to_status='HOAN_THANH', note='Đã sửa xong máy')
    assert response.status_code == 200, response.text
    db.refresh(ticket)
    assert ticket.status == 'HOAN_THANH' and ticket.due_at is None
    assert 'Chờ linh kiện thay thế' in response.text and 'Khách hẹn buổi sáng' in response.text
    assert '/transition' not in response.text and '/assign' not in response.text


def test_d3_technician_cannot_see_other_tickets(client, db, service_people):
    from app import service_tickets as svc
    ticket = new_service(db, service_people)
    svc.assign(db, service_people['dispatcher'], ticket.id, service_people['technician'].id, ticket.version)
    csrf = service_login(client, service_people['other'])
    assert client.get(f'/dich-vu/{ticket.id}').status_code == 404
    page = client.get('/dich-vu').text
    assert '<h1>Việc của tôi</h1>' in page and ticket.number not in page
    assert ticket.number not in client.get('/dich-vu?q=minh phat&phan=1').text
    assert service_action(client, ticket, csrf, to_status='DA_NHAN').status_code == 404


def test_d3_reason_required_and_invalid_appointment_keep_form(client, db, service_people):
    from app import service_tickets as svc
    ticket = new_service(db, service_people)
    svc.assign(db, service_people['dispatcher'], ticket.id, service_people['technician'].id, ticket.version)
    svc.transition(db, service_people['technician'], ticket.id, 'DA_NHAN', ticket.version)
    csrf = service_login(client, service_people['technician'])
    response = service_action(client, ticket, csrf, to_status='KHONG_LAM_DUOC', note='x')
    assert response.status_code == 400 and 'Phải ghi lý do' in response.text
    assert 'x</textarea>' in response.text and f'name="version" value="{ticket.version}"' in response.text
    response = service_action(client, ticket, csrf, to_status='DA_HEN', appointment_at='sai-ngay', note='Xin giữ ghi chú')
    assert response.status_code == 400 and 'Ngày giờ hẹn chưa hợp lệ' in response.text
    assert 'value="sai-ngay"' in response.text and 'Xin giữ ghi chú</textarea>' in response.text
    response = service_action(client, ticket, csrf, to_status='DA_HEN', appointment_at='', note='Giữ lời hẹn')
    assert response.status_code == 400 and 'Phải chọn giờ hẹn khách' in response.text
    assert 'Giữ lời hẹn</textarea>' in response.text
    db.refresh(ticket)
    assert ticket.status == 'DA_NHAN'


def test_d3_old_version_keeps_form(client, db, service_people):
    from app import service_tickets as svc
    ticket = new_service(db, service_people)
    old = ticket.version
    svc.assign(db, service_people['dispatcher'], ticket.id, service_people['technician'].id, old)
    csrf = service_login(client, service_people['dispatcher'])
    response = service_action(client, ticket, csrf, 'assign', version=str(old),
                              assignee_id=service_people['other'].id, note='Ghi chú chưa được lưu')
    assert response.status_code == 409 and 'Người khác vừa cập nhật phiếu' in response.text
    assert 'Ghi chú chưa được lưu</textarea>' in response.text
    assert f'name="version" value="{old}"' in response.text
    assert re.search(r'name="assignee_id" value="' + str(service_people['other'].id) + '" checked', response.text)
    db.refresh(ticket)
    assert ticket.assignee_id == service_people['technician'].id


def test_d3_overdue_and_other_filters(client, db, service_people):
    from datetime import UTC, datetime, timedelta
    from app import service_tickets as svc
    old = new_service(db, service_people)
    timely = new_service(db, service_people)
    closed = new_service(db, service_people)
    svc.transition(db, service_people['dispatcher'], closed.id, 'DA_HUY', closed.version, 'Khách báo hủy')
    svc.assign(db, service_people['dispatcher'], timely.id, service_people['technician'].id, timely.version)
    old.due_at = datetime.now(UTC) - timedelta(hours=1)
    closed.due_at = old.due_at
    db.commit()
    service_login(client, service_people['dispatcher'])
    page = client.get('/dich-vu?overdue=1&phan=1').text
    assert old.number in page and 'service-overdue' in page
    assert timely.number not in page and closed.number not in page
    assert timely.number in client.get('/dich-vu?status=DA_GIAO&phan=1').text
    assert old.number not in client.get('/dich-vu?status=DA_GIAO&phan=1').text
    page = client.get(f'/dich-vu?assignee_id={service_people["technician"].id}&phan=1').text
    assert timely.number in page and old.number not in page


def test_d3_csrf_and_forbidden_posts(client, db, service_people):
    from app import service_tickets as svc
    ticket = new_service(db, service_people)
    csrf = service_login(client, service_people['warehouse'])
    assert client.post('/dich-vu/moi', data=service_form(csrf)).status_code == 403
    assert service_action(client, ticket, csrf, 'assign', assignee_id=service_people['technician'].id).status_code == 403
    assert service_action(client, ticket, csrf, to_status='DA_HUY', note='Không được phép').status_code == 403
    csrf = service_login(client, service_people['sales'])
    page = client.get(f'/dich-vu/{ticket.id}').text
    assert '/assign' not in page and '/transition' not in page and '/transfer-request' not in page
    assert service_action(client, ticket, csrf, 'assign', assignee_id=service_people['technician'].id).status_code == 403
    assert client.post('/dich-vu/moi', data=service_form('')).status_code == 403
    assert service_action(client, ticket, '', 'assign', assignee_id=service_people['technician'].id).status_code == 403
    assert service_action(client, ticket, '', to_status='DA_HUY', note='Khách hủy phiếu').status_code == 403
    # Giao thật để thợ thấy phiếu, rồi kiểm quyền hủy vẫn do lõi quyết định.
    svc.assign(db, service_people['dispatcher'], ticket.id, service_people['technician'].id, ticket.version)
    csrf = service_login(client, service_people['technician'])
    assert service_action(client, ticket, csrf, to_status='DA_HUY', note='Thợ xin hủy').status_code == 403
    assert client.post('/dich-vu/moi', data=service_form(csrf)).status_code == 403


def test_d3_transfer_request_reject_and_cancel(client, db, service_people):
    from app import service_tickets as svc
    ticket = new_service(db, service_people)
    svc.assign(db, service_people['dispatcher'], ticket.id, service_people['technician'].id, ticket.version)
    csrf = service_login(client, service_people['technician'])
    response = service_action(client, ticket, csrf, 'transfer-request', reason='Đang ở xa khách')
    assert response.status_code == 200 and 'Đang ở xa khách' in response.text
    db.refresh(ticket)
    assert ticket.transfer_request == 'Đang ở xa khách'
    csrf = service_login(client, service_people['dispatcher'])
    assert '>Từ chối chuyển</button>' in client.get(f'/dich-vu/{ticket.id}').text
    response = service_action(client, ticket, csrf, 'transfer-reject', reason='Chưa có người thay')
    assert response.status_code == 200 and 'Chưa có người thay' in response.text
    db.refresh(ticket)
    assert not ticket.transfer_request
    response = service_action(client, ticket, csrf, to_status='DA_HUY', note='')
    assert response.status_code == 400 and 'Phải ghi lý do' in response.text
    response = service_action(client, ticket, csrf, to_status='DA_HUY', note='Khách hẹn việc khác')
    assert response.status_code == 200, response.text
    db.refresh(ticket)
    assert ticket.status == 'DA_HUY' and ticket.status_reason == 'Khách hẹn việc khác'


def test_d3_create_errors_keep_form_and_version_is_required(client, db, service_people):
    csrf = service_login(client, service_people['dispatcher'])
    data = service_form(csrf, customer_code='KTD-999999', symptom='Giữ nguyên mô tả lỗi', kind='BAO_HANH', priority='KHAN')
    response = client.post('/dich-vu/moi', data=data)
    assert response.status_code == 400 and 'không tồn tại' in response.text
    assert 'Giữ nguyên mô tả lỗi</textarea>' in response.text and 'value="KTD-999999"' in response.text
    assert 'value="BAO_HANH" checked' in response.text and 'value="KHAN" checked' in response.text
    response = client.post('/dich-vu/moi', data={**data, 'customer_code': 'KTD-000001', 'symptom': 'x'})
    assert response.status_code == 400 and 'Mô tả lỗi chưa hợp lệ' in response.text
    ticket = new_service(db, service_people)
    csrf = service_login(client, service_people['dispatcher'])
    response = service_action(client, ticket, csrf, 'assign', version='', assignee_id=service_people['technician'].id, note='Vẫn còn ghi chú')
    assert response.status_code == 400 and 'Thiếu phiên bản phiếu' in response.text
    assert 'Vẫn còn ghi chú</textarea>' in response.text and 'name="version" value=""' in response.text


def test_d3_closed_after_loading_keeps_attempted_form(client, db, service_people):
    from app import service_tickets as svc
    ticket = new_service(db, service_people)
    old_version = ticket.version
    svc.transition(db, service_people['dispatcher'], ticket.id, 'DA_HUY', old_version, 'Khách đã hủy việc')
    csrf = service_login(client, service_people['dispatcher'])
    response = service_action(client, ticket, csrf, 'assign', version=old_version,
                              assignee_id=service_people['technician'].id, note='Ghi chú đang soạn')
    assert response.status_code == 409 and 'Người khác vừa cập nhật phiếu' in response.text
    assert 'Ghi chú đang soạn</textarea>' in response.text
    assert 'Dữ liệu vừa gửi chưa được lưu' in response.text
    assert '/assign' not in response.text


@pytest.fixture
def d5_ticket(db, service_people):
    from datetime import UTC, datetime
    from app import service_tickets as svc
    from app.models import CustomerSite
    from app.schemas import ServiceTicketIn
    site = db.query(CustomerSite).filter_by(customer_code='KTD-000001').one()
    t = svc.create(db, service_people['sales'], ServiceTicketIn(customer_code='KTD-000001', site_id=site.id,
                   serial='AB123', kind='SUA_TAI_CHO', symptom='Máy in kẹt giấy'))
    tech = service_people['technician']
    svc.assign(db, service_people['dispatcher'], t.id, tech.id, t.version)
    svc.transition(db, tech, t.id, 'DA_NHAN', t.version)
    svc.transition(db, tech, t.id, 'DA_HEN', t.version, appointment_at=datetime(2026, 10, 20, 2, 30, tzinfo=UTC))
    t = svc.transition(db, tech, t.id, 'DANG_LAM', t.version, 'Đang kiểm tra máy')
    # D6: chọn cách thu trước để các test hoàn thành chỉ còn kiểm ảnh.
    from app import service_charges
    from app.schemas import ServiceCharges
    return service_charges.set_charges(db, tech, t.id, ServiceCharges(billing='MIEN_PHI', free_reason='Bảo hành', lines=[]))


def d5_image():
    import io
    from PIL import Image
    image = io.BytesIO()
    Image.new('RGB', (12, 16), 'green').save(image, format='PNG')
    return image.getvalue()


def d5_upload(client, ticket, csrf, tag='SAU', filename='sau.png', data=None):
    return client.post(f'/dich-vu/{ticket.id}/anh/tai', data={'csrf': csrf, 'tag': tag},
                       files={'file': (filename, d5_image() if data is None else data, 'image/png')})


def test_d5_upload_after_photo_then_complete(client, db, service_people, d5_ticket):
    from app.models import Attachment
    csrf = service_login(client, service_people['technician'])
    response = service_action(client, d5_ticket, csrf, to_status='HOAN_THANH', note='Đã thay bánh kéo giấy')
    assert response.status_code == 400 and 'Cần chụp ảnh sau sửa' in response.text
    response = d5_upload(client, d5_ticket, csrf)
    assert response.status_code == 200, response.text
    a = db.query(Attachment).one()
    assert a.tag == 'SAU' and a.entity_id == str(d5_ticket.id)
    assert f'src="/attachments/{a.id}/file"' in response.text
    # D5 có hai camera ảnh sửa; KT4 thêm camera nhận máy ở khối Máy khách riêng.
    repair_photos = response.text.split('<section id="anh"', 1)[1].split('</section>', 1)[0]
    assert repair_photos.count('capture="environment"') == 2
    assert 'Ảnh trước' in response.text and 'Ảnh sau sửa' in response.text
    assert 'Ẩn ảnh / tệp</summary>' not in response.text
    file_response = client.get(f'/attachments/{a.id}/file')
    assert file_response.status_code == 200 and file_response.headers['content-type'] == 'image/jpeg'
    assert file_response.headers['cache-control'] == 'private, no-store'
    response = service_action(client, d5_ticket, csrf, to_status='HOAN_THANH', note='Đã thay bánh kéo giấy')
    assert response.status_code == 200, response.text
    db.refresh(d5_ticket)
    assert d5_ticket.status == 'HOAN_THANH'


def test_d5_other_technician_cannot_upload_or_print(client, db, service_people, d5_ticket):
    from app.models import Attachment
    csrf = service_login(client, service_people['technician'])
    d5_upload(client, d5_ticket, csrf)
    a = db.query(Attachment).one()
    csrf = service_login(client, service_people['other'])
    assert d5_upload(client, d5_ticket, csrf).status_code == 404
    assert client.get(f'/dich-vu/{d5_ticket.id}/bien-ban').status_code == 404
    assert client.get(f'/attachments/{a.id}/file').status_code == 404
    assert db.query(Attachment).count() == 1


def test_d5_hide_requires_dispatcher_and_reason(client, db, service_people, d5_ticket):
    from app.models import Attachment, AuditLog
    csrf = service_login(client, service_people['technician'])
    d5_upload(client, d5_ticket, csrf)
    a = db.query(Attachment).one()
    path = f'/dich-vu/{d5_ticket.id}/anh/{a.id}/an'
    # Người chụp ảnh cũng không có nút ẩn ở D5, và POST bị chặn.
    assert client.post(path, data={'csrf': csrf, 'version': a.version, 'reason': 'Ảnh chụp sai'}).status_code == 403
    csrf = service_login(client, service_people['dispatcher'])
    response = client.post(path, data={'csrf': csrf, 'version': a.version + 1, 'reason': 'Giữ lý do đang nhập'})
    assert response.status_code == 409 and 'Giữ lý do đang nhập</textarea>' in response.text
    response = client.post(path, data={'csrf': csrf, 'version': a.version, 'reason': ''})
    assert response.status_code == 400 and 'Ẩn tệp phải ghi lý do' in response.text
    response = client.post(path, data={'csrf': csrf, 'version': a.version, 'reason': 'x'})
    assert response.status_code == 400 and 'x</textarea>' in response.text
    response = client.post(path, data={'csrf': csrf, 'version': a.version, 'reason': 'Ảnh tải nhầm máy'})
    assert response.status_code == 200, response.text
    assert f'/attachments/{a.id}/file' not in response.text
    assert f'/attachments/{a.id}/file' not in client.get(f'/dich-vu/{d5_ticket.id}/bien-ban').text
    assert client.get(f'/attachments/{a.id}/file').status_code == 404
    db.refresh(a)
    assert a.hidden and a.hidden_reason == 'Ảnh tải nhầm máy'
    assert db.query(Attachment).count() == 1
    assert db.query(AuditLog).filter_by(action='attachment.hide').count() == 1
    # Giám đốc cũng không thấy ảnh đã ẩn trong gallery / bản in mặc định.
    service_login(client, service_people['director'])
    assert f'/attachments/{a.id}/file' not in client.get(f'/dich-vu/{d5_ticket.id}').text


def test_d5_report_contains_ticket_serial_and_visible_after_photos(client, db, service_people, d5_ticket):
    from app.models import Attachment
    csrf = service_login(client, service_people['technician'])
    d5_upload(client, d5_ticket, csrf, tag='TRUOC', filename='truoc.png')
    before = db.query(Attachment).one()
    d5_upload(client, d5_ticket, csrf)
    after = db.query(Attachment).filter_by(tag='SAU').one()
    service_action(client, d5_ticket, csrf, to_status='HOAN_THANH', note='Đã thay bánh kéo giấy và chạy thử')
    report = client.get(f'/dich-vu/{d5_ticket.id}/bien-ban')
    assert report.status_code == 200, report.text
    for value in [d5_ticket.number, 'AB123', 'Máy in', 'Minh Phát', 'Văn phòng', 'Máy in kẹt giấy',
                  'Đã thay bánh kéo giấy và chạy thử', 'Dv.Technician', 'Đại diện khách hàng', 'Ngày giờ hoàn thành']:
        assert value in report.text
    assert f'src="/attachments/{after.id}/file"' in report.text
    assert f'/attachments/{before.id}/file' not in report.text
    assert 'window.print()' in report.text and 'Phiếu chưa hoàn thành' not in report.text
    assert 'size: A4' in client.get('/static/app.css').text


def test_d5_csrf_upload_and_hide(client, db, service_people, d5_ticket):
    from app.models import Attachment
    csrf = service_login(client, service_people['dispatcher'])
    assert d5_upload(client, d5_ticket, '').status_code == 403
    assert db.query(Attachment).count() == 0
    d5_upload(client, d5_ticket, csrf)
    a = db.query(Attachment).one()
    response = client.post(f'/dich-vu/{d5_ticket.id}/anh/{a.id}/an', data={'version': a.version, 'reason': 'Ảnh sai máy'})
    assert response.status_code == 403
    db.refresh(a)
    assert not a.hidden


def test_d5_other_files_validation_and_cookie_auth_scope(client, db, service_people, d5_ticket):
    from app.models import Attachment
    csrf = service_login(client, service_people['dispatcher'])
    response = d5_upload(client, d5_ticket, csrf, filename='anh.pdf', data=b'%PDF-1.7\nexample')
    assert response.status_code == 400 and 'phải là ảnh' in response.text
    assert d5_upload(client, d5_ticket, csrf, data=b'bad data').status_code == 400
    assert d5_upload(client, d5_ticket, csrf, data=b'').status_code == 400
    assert d5_upload(client, d5_ticket, csrf, tag='SAI').status_code == 400
    assert db.query(Attachment).count() == 0
    response = d5_upload(client, d5_ticket, csrf, tag='KHAC', filename='bien-ban.pdf', data=b'%PDF-1.7\nexample')
    assert response.status_code == 200, response.text
    a = db.query(Attachment).one()
    assert client.get(f'/attachments/{a.id}/file').headers['content-type'] == 'application/pdf'
    # Cookie chỉ bổ sung cho GET đọc file, không cho phép ghi trực tiếp qua API thiếu CSRF.
    assert client.post('/attachments', data={'entity': 'service_ticket', 'entity_id': str(d5_ticket.id), 'tag': 'SAU'},
                       files={'file': ('sau.png', d5_image(), 'image/png')}).status_code == 401
    client.cookies.clear()
    assert client.get(f'/attachments/{a.id}/file').status_code == 401


def test_d5_cannot_hide_attachment_from_other_ticket(client, db, service_people, d5_ticket):
    from app.models import Attachment
    other = new_service(db, service_people)
    csrf = service_login(client, service_people['dispatcher'])
    d5_upload(client, other, csrf)
    a = db.query(Attachment).one()
    response = client.post(f'/dich-vu/{d5_ticket.id}/anh/{a.id}/an', data={'csrf': csrf, 'version': a.version, 'reason': 'Không đúng phiếu'})
    assert response.status_code == 404
    db.refresh(a)
    assert not a.hidden


# D6 UI — ghi tiền qua service_charges, ghi/xuất/trả tồn qua lõi hiện có.
@pytest.fixture
def charge_work(db, service_people):
    from datetime import UTC, datetime
    from app import attachments, service_tickets as svc
    t = new_service(db, service_people)
    tech = service_people['technician']
    svc.assign(db, service_people['dispatcher'], t.id, tech.id, t.version)
    svc.transition(db, tech, t.id, 'DA_NHAN', t.version)
    svc.transition(db, tech, t.id, 'DA_HEN', t.version, appointment_at=datetime.now(UTC))
    svc.transition(db, tech, t.id, 'DANG_LAM', t.version)
    attachments.add(db, tech, 'service_ticket', str(t.id), 'SAU', 'sau.png', d5_image())
    return t


@pytest.fixture
def charge_stock(db, service_people):
    from datetime import date
    from app import stock_docs, stock_posting
    from app.models import Supplier, Warehouse
    from app.schemas import StockDocIn
    db.add_all([Supplier(code='NCC-0001', name='Nhà cung cấp'), Warehouse(code='K58', name='Kho 58'), Warehouse(code='K145', name='Kho 145')])
    db.get(Product, 'TD-0001').sale_price = 120000
    db.commit()
    doc = stock_docs.create(db, service_people['warehouse'], StockDocIn(kind='PN', warehouse_code='K58',
               supplier_code='NCC-0001', doc_date=date(2026, 10, 17), lines=[{'product_code':'TD-0001','qty':'5','unit_cost':50000}]))
    stock_posting.post(db, service_people['warehouse'], doc.id, doc.version)


def charge_form(csrf, ticket, *, material=False, **kw):
    return {'csrf':csrf, 'version':str(ticket.version), 'billing':'THU_TIEN', 'free_reason':'',
            'line_kind':['VAT_TU' if material else 'PHI'], 'line_description':['Vật tư thay thế' if material else 'Công sửa máy'],
            'line_product':['TD-0001' if material else ''], 'warehouse_0':'K58' if material else '',
            'line_qty':['1'], 'line_price':['120.000'], 'vat_0':'10', **kw}


def save_charge(client, ticket, data):
    return client.post(f'/dich-vu/{ticket.id}/tien/luu', data=data)


def test_d6_ui_money_and_vat_are_computed_by_core(client, db, service_people, charge_work, charge_stock):
    csrf = service_login(client, service_people['technician'])
    response = save_charge(client, charge_work, charge_form(csrf, charge_work, line_qty=['1.5']))
    assert response.status_code == 200, response.text
    db.refresh(charge_work)
    assert charge_work.lines[0].unit_price == 120000
    assert charge_work.lines[0].amount == 180000 and charge_work.lines[0].vat_amount == 18000
    assert charge_work.total is None  # Tổng chỉ được chốt lúc hoàn thành.
    assert '198.000 đồng' in response.text and 'value="120.000"' in response.text
    assert 'data-live=".charge-options"' in client.get(f'/dich-vu/{charge_work.id}').text or 'charge-template-VAT_TU' in response.text
    picked = client.get('/dich-vu/tim-vat-tu?q=may in').text
    assert 'TD-0001' in picked and 'data-price="120000"' in picked
    assert 'type="number"' not in response.text


def test_d6_ui_billing_and_empty_charges_errors_keep_data(client, db, service_people, charge_work):
    csrf = service_login(client, service_people['technician'])
    response = service_action(client, charge_work, csrf, to_status='HOAN_THANH', note='Giữ ghi chú nghiệm thu')
    assert response.status_code == 400 and 'Chọn Có thu tiền hoặc Miễn phí' in response.text
    assert 'Giữ ghi chú nghiệm thu</textarea>' in response.text
    response = save_charge(client, charge_work, charge_form(csrf, charge_work, billing=''))
    assert response.status_code == 400 and 'value="120.000"' in response.text
    assert 'value="Công sửa máy"' in response.text
    save_charge(client, charge_work, {'csrf':csrf,'version':charge_work.version,'billing':'THU_TIEN'})
    db.refresh(charge_work)
    response = service_action(client, charge_work, csrf, to_status='HOAN_THANH', note='Chưa nhập dòng phí')
    assert response.status_code == 400 and 'chưa có dòng phí hoặc vật tư nào có giá' in response.text
    db.refresh(charge_work)
    assert charge_work.status == 'DANG_LAM'


def test_d6_ui_material_shortage_keeps_stock_and_working_ticket(client, db, service_people, charge_work, charge_stock):
    from app.models import StockBalance, StockDoc
    csrf = service_login(client, service_people['technician'])
    assert save_charge(client, charge_work, charge_form(csrf, charge_work, material=True, line_qty=['6'])).status_code == 200
    db.refresh(charge_work)
    response = service_action(client, charge_work, csrf, to_status='HOAN_THANH', note='Giữ ghi chú vật tư thiếu')
    assert response.status_code == 409 and 'Không đủ tồn cho TD-0001' in response.text
    assert 'tồn hiện có 5.000' in response.text and 'số cần 6.000' in response.text
    assert 'Giữ ghi chú vật tư thiếu</textarea>' in response.text
    assert 'value="6"' in response.text or 'value="6.000"' in response.text
    assert 'value="120.000"' in response.text
    db.expire_all()
    assert charge_work.status == 'DANG_LAM' and charge_work.total is None
    assert db.get(StockBalance, ('K58','TD-0001')).qty == 5
    assert db.query(StockDoc).filter_by(kind='PX').count() == 0


def test_d6_ui_complete_shows_generated_px_and_reopen_returns_stock(client, db, service_people, charge_work, charge_stock):
    from app.models import StockBalance, StockDoc
    csrf = service_login(client, service_people['technician'])
    save_charge(client, charge_work, charge_form(csrf, charge_work, material=True, line_qty=['2']))
    db.refresh(charge_work)
    response = service_action(client, charge_work, csrf, to_status='HOAN_THANH', note='Đã thay vật tư')
    assert response.status_code == 200, response.text
    db.expire_all()
    doc = db.query(StockDoc).filter_by(kind='PX').one()
    assert doc.status == 'DA_GHI' and f'href="/phieu-kho/{doc.id}"' in response.text and doc.number in response.text
    assert db.get(StockBalance, ('K58','TD-0001')).qty == 3
    assert charge_work.total == 264000 and charge_work.receivable == 264000
    assert '>Lưu tiền</button>' not in response.text
    assert '<fieldset disabled>' in response.text
    response = save_charge(client, charge_work, charge_form(csrf, charge_work, material=True, line_price=['999.000']))
    assert response.status_code == 409 and 'data-error-code="PRICE_LOCKED"' in response.text
    assert 'value="999.000"' in response.text
    csrf = service_login(client, service_people['dispatcher'])
    page = client.get(f'/dich-vu/{charge_work.id}').text
    assert '>Mở lại</button>' in page and '>Đóng hồ sơ</button>' in page
    response = service_action(client, charge_work, csrf, to_status='DANG_LAM', note='')
    assert response.status_code == 400 and 'Phải ghi lý do' in response.text
    response = service_action(client, charge_work, csrf, to_status='DANG_LAM', note='Khách yêu cầu kiểm tra thêm')
    assert response.status_code == 200, response.text
    db.expire_all()
    assert charge_work.status == 'DANG_LAM' and charge_work.total is None
    assert db.get(StockBalance, ('K58','TD-0001')).qty == 5
    assert doc.status == 'DA_HUY' and '>Lưu tiền</button>' in response.text


def test_d6_ui_receivables_roles_dates_total_and_csv(client, db, make_user, service_people, charge_work):
    from app import service_tickets as svc
    csrf = service_login(client, service_people['technician'])
    save_charge(client, charge_work, charge_form(csrf, charge_work))
    db.refresh(charge_work)
    service_action(client, charge_work, csrf, to_status='HOAN_THANH', note='Xong việc có thu')
    db.refresh(charge_work)
    # Phiếu miễn phí thứ hai hoàn thành cùng ngày, vẫn không có trong cần thu.
    free = new_service(db, service_people)
    svc.assign(db, service_people['dispatcher'], free.id, service_people['technician'].id, free.version)
    svc.transition(db, service_people['technician'], free.id, 'DA_NHAN', free.version)
    save_charge(client, free, charge_form(csrf, free, billing='MIEN_PHI', free_reason='Bảo hành miễn phí'))
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo
    from app import attachments
    db.refresh(free)
    svc.transition(db, service_people['technician'], free.id, 'DA_HEN', free.version, appointment_at=datetime.now(UTC))
    svc.transition(db, service_people['technician'], free.id, 'DANG_LAM', free.version)
    attachments.add(db, service_people['technician'], 'service_ticket', str(free.id), 'SAU', 'sau.png', d5_image())
    svc.transition(db, service_people['technician'], free.id, 'HOAN_THANH', free.version)
    finance = make_user('dv.ke.toan', 'finance')
    service_login(client, finance)
    response = client.get('/dich-vu/can-thu')
    assert response.status_code == 200, response.text
    assert charge_work.number in response.text and free.number not in response.text
    assert 'Tổng cộng: 132.000 đồng' in response.text
    date = charge_work.priced_at.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat()
    assert charge_work.number in client.get(f'/dich-vu/can-thu?from={date}&to={date}').text
    assert charge_work.number not in client.get('/dich-vu/can-thu?from=2000-01-01&to=2000-01-02').text
    import csv, io
    download = client.get('/dich-vu/can-thu.csv')
    rows = list(csv.reader(io.StringIO(download.content.decode('utf-8-sig'))))
    assert rows[0] == ['Số phiếu','Khách hàng','Ngày hoàn thành','Số phải thu','Đã thu','Chờ xác nhận','Còn nợ']
    assert rows[1][0] == charge_work.number and rows[-1] == ['Tổng cộng','','','','','','132000']
    for role in ['technician','sales']:
        service_login(client, service_people[role])
        assert client.get('/dich-vu/can-thu').status_code == 403
        assert client.get('/dich-vu/can-thu.csv').status_code == 403
    service_login(client, service_people['director'])
    assert client.get('/dich-vu/can-thu').status_code == 200


def test_d6_ui_csrf_and_permissions(client, db, service_people, charge_work):
    csrf = service_login(client, service_people['technician'])
    assert save_charge(client, charge_work, charge_form('', charge_work)).status_code == 403
    assert not charge_work.lines
    csrf = service_login(client, service_people['other'])
    assert save_charge(client, charge_work, charge_form(csrf, charge_work)).status_code == 404
    csrf = service_login(client, service_people['sales'])
    assert '>Lưu tiền</button>' not in client.get(f'/dich-vu/{charge_work.id}').text
    assert save_charge(client, charge_work, charge_form(csrf, charge_work)).status_code == 403


def test_d6_ui_stale_version_and_invalid_money_keep_rows(client, db, service_people, charge_work):
    csrf = service_login(client, service_people['technician'])
    old = charge_work.version
    save_charge(client, charge_work, charge_form(csrf, charge_work))
    db.refresh(charge_work)
    response = save_charge(client, charge_work, charge_form(csrf, charge_work, version=old, line_price=['321.000'], line_description=['Đang nhập dở']))
    assert response.status_code == 409 and 'VERSION_CONFLICT' in response.text
    assert 'value="321.000"' in response.text and 'value="Đang nhập dở"' in response.text
    response = save_charge(client, charge_work, charge_form(csrf, charge_work, line_qty=['0']))
    assert response.status_code == 400 and 'Số lượng chưa hợp lệ' in response.text and 'value="0"' in response.text
    response = save_charge(client, charge_work, charge_form(csrf, charge_work, line_price=['120.00']))
    assert response.status_code == 400 and 'Đơn giá dòng 1 chưa hợp lệ' in response.text and 'value="120.00"' in response.text
    response = save_charge(client, charge_work, charge_form(csrf, charge_work, billing='MIEN_PHI', free_reason='x'))
    assert response.status_code == 400 and 'Miễn phí phải ghi lý do' in response.text and 'x</textarea>' in response.text
    assert client.get('/dich-vu/can-thu?from=sai').status_code == 403  # Thợ bị chặn trước khi lọc ngày.


def test_d6_ui_dispatcher_closes_completed_ticket(client, db, service_people, charge_work):
    csrf = service_login(client, service_people['technician'])
    save_charge(client, charge_work, charge_form(csrf, charge_work))
    db.refresh(charge_work)
    service_action(client, charge_work, csrf, to_status='HOAN_THANH')
    db.refresh(charge_work)
    assert service_action(client, charge_work, csrf, to_status='DA_DONG').status_code == 403
    csrf = service_login(client, service_people['dispatcher'])
    response = service_action(client, charge_work, csrf, to_status='DA_DONG')
    assert response.status_code == 200, response.text
    db.refresh(charge_work)
    assert charge_work.status == 'DA_DONG' and '>Lưu tiền</button>' not in response.text
    assert '/transition' not in response.text


def test_d6_ui_mixed_lines_keep_per_row_vat_and_warehouse(client, db, service_people, charge_work, charge_stock):
    csrf = service_login(client, service_people['technician'])
    data = charge_form(csrf, charge_work, line_kind=['PHI','VAT_TU'], line_description=['Công sửa','Mực thử'],
                       line_product=['','TD-0001'], line_qty=['1.5','0.001'], line_price=['333','500'],
                       vat_0='10', vat_1='0', warehouse_0='', warehouse_1='K58')
    response = save_charge(client, charge_work, data)
    assert response.status_code == 200, response.text
    db.refresh(charge_work)
    assert [(line.amount,line.vat_amount) for line in charge_work.lines] == [(500,50),(1,0)]
    assert charge_work.lines[0].warehouse_code is None and charge_work.lines[1].warehouse_code == 'K58'
    assert '551 đồng' in response.text


# D7 — dữ liệu lịch sử cố định để kiểm tra thống kê, ranh giới ngày VN và hạn cũ.
def d7_clock(value):
    from datetime import datetime
    return datetime.fromisoformat(value)


def d7_ticket(db, people, number, events, *, created='2026-10-10T08:00:00+07:00', due=None, status=None):
    from app.models import AuditLog, ServiceStep, ServiceTicket
    worker = people['technician'].id
    ticket = ServiceTicket(number=number, customer_code='KTD-000001', kind='SUA_TAI_CHO', priority='THUONG',
                           symptom='Kiểm tra máy', status=status or events[-1][1], assignee_id=worker,
                           created_at=d7_clock(created), created_by=people['sales'].id,
                           due_at=d7_clock(due) if due else None)
    db.add(ticket)
    db.flush()
    previous = None
    for action, state, at, next_due, assignee in events:
        actor = people['dispatcher'].id if action in ('create', 'assign', 'reassign') else assignee
        before = previous
        after = {'status': state, 'assignee_id': assignee, 'due_at': next_due}
        db.add(ServiceStep(ticket_id=ticket.id, action=action, from_status=before['status'] if before else None,
                           to_status=state, assignee_id=assignee, actor_id=actor, at=d7_clock(at), note='Bước cố định'))
        db.add(AuditLog(entity='service_ticket', entity_id=str(ticket.id), action='service_ticket.' + action,
                        user_id=actor, at=d7_clock(at), before=before, after=after))
        previous = after
    if previous:
        ticket.assignee_id = previous['assignee_id']
    db.commit()
    return ticket


def d7_events(worker, *, late=False, finish='HOAN_THANH', completed='2026-10-10T11:00:00+07:00'):
    return [
        ('create', 'MOI', '2026-10-10T08:00:00+07:00', '2026-10-10T10:00:00+07:00', None),
        ('assign', 'DA_GIAO', '2026-10-10T09:00:00+07:00', '2026-10-10T09:15:00+07:00', worker),
        ('transition', 'DA_NHAN', '2026-10-10T09:30:00+07:00' if late else '2026-10-10T09:10:00+07:00', '2026-10-10T18:00:00+07:00', worker),
        ('transition', 'DA_HEN', '2026-10-10T09:40:00+07:00', '2026-10-10T10:00:00+07:00', worker),
        ('transition', 'DANG_LAM', '2026-10-10T10:00:00+07:00', '2026-10-10T18:00:00+07:00', worker),
        ('transition', finish, completed, None, worker),
    ]


@pytest.fixture
def d7_history(db, service_people):
    worker = service_people['technician'].id
    first = d7_ticket(db, service_people, 'DV-26-00001', d7_events(worker), created='2026-10-09T23:30:00+07:00')
    second = d7_ticket(db, service_people, 'DV-26-00002', d7_events(worker, late=True, completed='2026-10-10T13:00:00+07:00'))
    failed = d7_ticket(db, service_people, 'DV-26-00003', d7_events(worker, finish='KHONG_LAM_DUOC'))
    # Đóng đúng 00:00 ngày sau (17:00 UTC ngày trước) phải nằm ngoài khoảng chỉ ngày 10.
    outside = d7_ticket(db, service_people, 'DV-26-00004', d7_events(worker, completed='2026-10-11T00:00:00+07:00'),
                        created='2026-10-11T00:00:00+07:00')
    return first, second, failed, outside


def d7_csv(client, query='from=2026-10-10&to=2026-10-10'):
    import csv
    import io
    response = client.get('/dieu-phoi/bao-cao.csv?' + query)
    assert response.status_code == 200, response.text
    return list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))


def test_d7_overdue_excludes_closed_and_orders_oldest(client, db, service_people, monkeypatch):
    from app import service_tickets as svc
    from app.web import dispatch
    now = d7_clock('2026-10-10T12:00:00+07:00')
    monkeypatch.setattr(dispatch, '_now', lambda: now)
    monkeypatch.setattr(svc, '_now', lambda: now)
    later = d7_ticket(db, service_people, 'DV-26-00001', [], due='2026-10-10T11:00:00+07:00', status='DANG_LAM')
    older = d7_ticket(db, service_people, 'DV-26-00002', [], due='2026-10-10T08:45:00+07:00', status='DA_HEN')
    closed = d7_ticket(db, service_people, 'DV-26-00003', [], due='2026-10-09T08:00:00+07:00', status='HOAN_THANH')
    waiting = d7_ticket(db, service_people, 'DV-26-00004', [], status='CHO_LINH_KIEN')
    service_login(client, service_people['dispatcher'])
    response = client.get('/dieu-phoi')
    assert response.status_code == 200, response.text
    assert response.text.index(older.number) < response.text.index(later.number)
    assert closed.number not in response.text and waiting.number not in response.text
    assert 'trễ 3 giờ 15 phút' in response.text and 'trễ 1 giờ 0 phút' in response.text
    assert '<strong>3</strong><span>Đơn đang mở' in response.text
    assert '<strong>2</strong><span>Đơn quá hạn' in response.text
    assert '<strong>1</strong><span>Chờ linh kiện' in response.text
    assert waiting.number in client.get('/dieu-phoi?view=parts').text
    assert waiting.number in client.get('/dieu-phoi?view=open').text
    assert closed.number not in client.get('/dieu-phoi?view=open').text


def test_d7_dashboard_not_truncated_by_search_limit(client, db, service_people, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), 'search_limit', 1)
    for i in range(7):
        d7_ticket(db, service_people, f'DV-26-{i+1:05}', [], due='2026-01-01T00:00:00+07:00', status='DA_NHAN')
    service_login(client, service_people['dispatcher'])
    response = client.get('/dieu-phoi')
    assert '<strong>7</strong><span>Đơn quá hạn' in response.text
    assert response.text.count('class="code">DV-26-') == 7


def test_d7_report_counts_average_and_historical_lateness(client, db, service_people, d7_history):
    service_login(client, service_people['dispatcher'])
    response = client.get('/dieu-phoi/bao-cao?from=2026-10-10&to=2026-10-10')
    assert response.status_code == 200, response.text
    assert '180.0 phút' in response.text and '33.3%' in response.text
    rows = d7_csv(client)
    assert rows[1] == ['Dv.Technician', '2', '1', '180.0', '33.3', '0']
    daily_index = rows.index(['Ngày', 'Đơn nhận', 'Đơn hoàn thành'])
    assert rows[daily_index + 1] == ['2026-10-10', '2', '2']
    # Phiếu hoàn thành không còn due_at; vẫn phát hiện hạn nhận cũ bị vượt ở lịch sử.
    assert all(t.due_at is None for t in d7_history)


@pytest.mark.parametrize('role', ['technician', 'sales', 'warehouse'])
def test_d7_forbidden_roles(client, service_people, role):
    service_login(client, service_people[role])
    for path in ['/dieu-phoi', '/dieu-phoi/bao-cao', '/dieu-phoi/bao-cao.csv']:
        assert client.get(path).status_code == 403
    assert 'href="/dieu-phoi"' not in client.get('/dich-vu').text


def test_d7_csv_headers_and_vietnam_date_defaults(client, service_people, d7_history, monkeypatch):
    from app.web import dispatch
    monkeypatch.setattr(dispatch, '_now', lambda: d7_clock('2026-10-10T18:00:00+00:00'))  # VN đã ngày 11.
    service_login(client, service_people['director'])
    response = client.get('/dieu-phoi/bao-cao')
    assert 'name="from" value="2026-10-05"' in response.text
    assert 'name="to" value="2026-10-11"' in response.text
    rows = d7_csv(client)
    assert rows[0] == ['Thợ', 'Đơn hoàn thành', 'Không làm được', 'Thời gian trung bình (phút)', 'Tỷ lệ trễ (%)', 'Chưa xác định trễ']
    assert ['Ngày', 'Đơn nhận', 'Đơn hoàn thành'] in rows
    response = client.get('/dieu-phoi/bao-cao.csv?from=2026-10-10&to=2026-10-10')
    assert response.content.startswith(b'\xef\xbb\xbf')
    assert response.headers['content-type'].startswith('text/csv')
    assert 'attachment;' in response.headers['content-disposition']


def test_d7_reassignment_uses_latest_assignment_for_completing_technician(client, db, service_people):
    a, b = service_people['technician'].id, service_people['other'].id
    events = d7_events(a)
    events = events[:3] + [
        ('reassign', 'DA_GIAO', '2026-10-10T10:30:00+07:00', '2026-10-10T10:45:00+07:00', b),
        ('transition', 'DA_NHAN', '2026-10-10T10:40:00+07:00', '2026-10-10T18:00:00+07:00', b),
        ('transition', 'DA_HEN', '2026-10-10T10:41:00+07:00', '2026-10-10T11:00:00+07:00', b),
        ('transition', 'DANG_LAM', '2026-10-10T11:00:00+07:00', '2026-10-10T18:00:00+07:00', b),
        ('transition', 'HOAN_THANH', '2026-10-10T11:30:00+07:00', None, b),
    ]
    d7_ticket(db, service_people, 'DV-26-00001', events)
    service_login(client, service_people['dispatcher'])
    assert d7_csv(client)[1] == ['Dv.Tho.B', '1', '0', '60.0', '0.0', '0']


def test_d7_missing_audit_not_assumed_on_time_and_bad_dates(client, db, service_people):
    from app.models import ServiceStep, ServiceTicket
    t = ServiceTicket(number='DV-26-00001', customer_code='KTD-000001', kind='SUA_TAI_CHO', symptom='Máy hỏng',
                      status='HOAN_THANH', assignee_id=service_people['technician'].id, created_by=service_people['sales'].id)
    db.add(t); db.flush()
    db.add(ServiceStep(ticket_id=t.id, action='transition', from_status='DANG_LAM', to_status='HOAN_THANH',
                       actor_id=t.assignee_id, assignee_id=t.assignee_id, at=d7_clock('2026-10-10T12:00:00+07:00')))
    db.commit()
    service_login(client, service_people['dispatcher'])
    assert d7_csv(client)[1] == ['Dv.Technician', '1', '0', '', '', '1']
    response = client.get('/dieu-phoi/bao-cao?from=2026-10-11&to=2026-10-10')
    assert response.status_code == 400 and 'Khoảng ngày chưa hợp lệ' in response.text
    assert 'value="2026-10-11"' in response.text
    assert client.get('/dieu-phoi/bao-cao.csv?from=sai&to=2026-10-10').status_code == 400


def test_d7_read_only_transactions_and_csv_formula_escape(client, db, engine, service_people, d7_history):
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from app.main import app
    from app.db import get_session
    service_people['technician'].full_name = '=HYPERLINK("example")'
    db.commit()
    service_login(client, service_people['dispatcher'])
    def read_only_session():
        with Session(engine) as s:
            s.execute(text('SET TRANSACTION READ ONLY'))
            yield s
    app.dependency_overrides[get_session] = read_only_session
    try:
        assert client.get('/dieu-phoi').status_code == 200
        assert client.get('/dieu-phoi/bao-cao?from=2026-10-10&to=2026-10-10').status_code == 200
        assert d7_csv(client)[1][0].startswith("'=HYPERLINK")
    finally:
        app.dependency_overrides.pop(get_session)


def test_d7_lateness_matches_real_core_audit_transactions(client, db, service_people, d5_ticket):
    from datetime import UTC, datetime, timedelta
    from app import attachments, service_tickets as svc
    tech = service_people['technician']
    # Hạn hiện tại bị vượt; lần chuyển thật ghi snapshot hạn cũ và bước trong cùng transaction.
    d5_ticket.due_at = datetime.now(UTC) - timedelta(hours=2)
    db.commit()
    attachments.add(db, tech, 'service_ticket', str(d5_ticket.id), 'SAU', 'sau.png', d5_image())
    svc.transition(db, tech, d5_ticket.id, 'HOAN_THANH', d5_ticket.version, 'Đã sửa máy')
    service_login(client, service_people['dispatcher'])
    rows = d7_csv(client, '')
    assert rows[1][1:3] == ['1', '0']
    assert rows[1][4:] == ['100.0', '0']


def test_d7_reschedule_uses_original_appointment_deadline(client, db, service_people):
    worker = service_people['technician'].id
    events = d7_events(worker)[:4] + [
        ('reschedule', 'DA_HEN', '2026-10-10T10:30:00+07:00', '2026-10-10T12:00:00+07:00', worker),
        ('transition', 'DANG_LAM', '2026-10-10T11:00:00+07:00', '2026-10-10T18:00:00+07:00', worker),
        ('transition', 'HOAN_THANH', '2026-10-10T12:00:00+07:00', None, worker),
    ]
    t = d7_ticket(db, service_people, 'DV-26-00001', events)
    t.appointment_at = d7_clock('2026-10-10T12:00:00+07:00')
    db.commit()
    service_login(client, service_people['dispatcher'])
    # Dời hẹn lúc 10:30 đã trễ hạn hẹn cũ 10:00, dù lịch hẹn cuối được dời sang 12:00.
    assert d7_csv(client)[1][4:] == ['100.0', '0']


@pytest.mark.parametrize('final_status', ['HOAN_THANH', 'DA_DONG', 'DANG_LAM', 'DA_HUY'])
def test_d7_reopened_ticket_not_counted_twice_or_after_cancellation(client, db, service_people, final_status):
    worker = service_people['technician'].id
    events = d7_events(worker) + [
        ('reopen', 'DANG_LAM', '2026-10-10T11:30:00+07:00', '2026-10-10T18:00:00+07:00', worker),
        ('transition', 'HOAN_THANH', '2026-10-10T13:00:00+07:00', None, worker),
    ]
    if final_status != 'HOAN_THANH':
        action = {'DA_DONG': 'close', 'DANG_LAM': 'reopen', 'DA_HUY': 'cancel'}[final_status]
        events.append((action, final_status, '2026-10-10T14:00:00+07:00', None, worker))
    d7_ticket(db, service_people, 'DV-26-00001', events)
    service_login(client, service_people['dispatcher'])
    rows = d7_csv(client)
    daily_index = rows.index(['Ngày', 'Đơn nhận', 'Đơn hoàn thành'])
    if final_status in ('HOAN_THANH', 'DA_DONG'):
        assert rows[1] == ['Dv.Technician', '1', '0', '240.0', '0.0', '0']
        assert rows[daily_index + 1] == ['2026-10-10', '1', '1']
    else:
        assert rows[1] == []
        assert rows[daily_index + 1] == ['2026-10-10', '1', '0']


@pytest.fixture
def b1_catalog(db, monkeypatch):
    from datetime import date
    from app import quotes
    monkeypatch.setattr(quotes, '_today', lambda: date(2026, 10, 17))
    db.add_all([
        Customer(code='KTD-000001', name='Minh Phát', search_text='minh phat'),
        Product(code='TD-0001', name='Máy in', unit='cái', sale_price=120000, search_text='td-0001 may in'),
    ])
    db.commit()


def b1_form(csrf, **changes):
    data = dict(csrf=csrf, customer_code='KTD-000001', contact_name='Chị Lan',
                valid_until='2026-10-24', note='Giao tận nơi', product_code='',
                description='Công lắp đặt', qty='1.5', unit='lần', unit_price='333', vat_0='10')
    data.update(changes)
    return data


def test_b1_web_create_send_print(client, web_login, db, b1_catalog):
    from app.models import Quote
    csrf = web_login('b1.sales', 'sales')
    assert client.get('/bao-gia/moi').status_code == 200
    r = client.post('/bao-gia/moi', data=b1_form(csrf, product_code='TD-0001', description='', qty='2', unit_price='120.000'), follow_redirects=False)
    assert r.status_code == 303
    q = db.query(Quote).one()
    assert q.number == 'BG-26-00001' and q.total == 264000
    assert q.lines[0].unit_price == 120000
    page = client.get(f'/bao-gia/{q.id}')
    assert '264.000' in page.text and 'Lưu báo giá' in page.text
    r = client.post(f'/bao-gia/{q.id}/send', data=dict(csrf=csrf,version=q.version))
    assert r.status_code == 200 and 'Đã gửi' in r.text and 'Lưu báo giá' not in r.text
    printed = client.get(f'/bao-gia/{q.id}/in')
    assert printed.status_code == 200
    for expected in ('BG-26-00001', 'Hai trăm sáu mươi bốn nghìn đồng', '0313015734', '0888 304 668', 'Đường Mã Lò', 'Máy in'):
        assert expected in printed.text
    assert 'BG-26-00001' in client.get('/bao-gia?q=minh phat&phan=1').text
    assert 'Minh Phát' in client.get('/bao-gia/tim-khach?q=minh phat').text
    assert 'Máy in' in client.get('/bao-gia/tim-hang?q=may in').text


def test_b1_web_stale_and_validation_preserve_input(client, web_login, db, b1_catalog):
    from app.models import Quote
    csrf = web_login('b1.sales', 'sales')
    r = client.post('/bao-gia/moi', data=b1_form(csrf, qty='-1', note='Giữ ghi chú'))
    assert r.status_code == 400 and 'Giữ ghi chú' in r.text and 'value="-1"' in r.text
    assert db.query(Quote).count() == 0
    client.post('/bao-gia/moi', data=b1_form(csrf))
    q = db.query(Quote).one()
    version = q.version
    assert client.post(f'/bao-gia/{q.id}', data=b1_form(csrf, version=version, unit_price='120.000')).status_code == 200
    r = client.post(f'/bao-gia/{q.id}', data=b1_form(csrf, version=version, unit_price='999.000', qty='7.5', note='Giữ nguyên'))
    assert r.status_code == 409 and 'VERSION_CONFLICT' in r.text
    assert '999.000' in r.text and 'value="7.5"' in r.text and 'Giữ nguyên' in r.text
    db.expire_all()
    assert db.get(Quote,q.id).lines[0].unit_price == 120000


def test_b1_web_revision_cancel_empty_and_expired(client, web_login, db, b1_catalog):
    from app.models import Quote
    csrf = web_login('b1.sales', 'sales')
    client.post('/bao-gia/moi', data=b1_form(csrf, valid_until='2026-10-01'))
    q = db.query(Quote).one()
    r = client.post(f'/bao-gia/{q.id}/cancel', data=dict(csrf=csrf,version=q.version,reason=' '))
    assert r.status_code == 400 and 'REASON_REQUIRED' in r.text
    client.post(f'/bao-gia/{q.id}/send', data=dict(csrf=csrf,version=q.version))
    assert 'Hết hạn' in client.get('/bao-gia').text
    db.expire_all()
    client.post(f'/bao-gia/{q.id}/revise', data=dict(csrf=csrf,version=q.version))
    db.expire_all()
    old = db.get(Quote,q.id)
    assert old.status == 'DA_HUY' and old.replaced_by_id
    assert f'/bao-gia/{old.replaced_by_id}' in client.get(f'/bao-gia/{old.id}').text
    new = db.get(Quote,old.replaced_by_id)
    r = client.post(f'/bao-gia/{new.id}/cancel', data=dict(csrf=csrf,version=new.version,reason='Khách đổi kế hoạch'))
    assert r.status_code == 200 and 'Khách đổi kế hoạch' in r.text
    r = client.post('/bao-gia/moi', data=dict(csrf=csrf,customer_code='KTD-000001',valid_until='2026-10-24'))
    assert r.status_code == 200
    empty = db.query(Quote).order_by(Quote.id.desc()).first()
    r = client.post(f'/bao-gia/{empty.id}/send', data=dict(csrf=csrf,version=empty.version))
    assert r.status_code == 400 and 'EMPTY_LINES' in r.text


def test_b1_web_roles_and_csrf(client, web_login, db, b1_catalog):
    from app.models import Quote
    csrf = web_login('b1.sales', 'sales')
    client.post('/bao-gia/moi', data=b1_form(csrf))
    q = db.query(Quote).one()
    assert client.post('/bao-gia/moi',data=b1_form('')).status_code == 403
    assert client.post(f'/bao-gia/{q.id}/send',data=dict(version=q.version)).status_code == 403
    csrf = web_login('b1.finance', 'finance')
    for path in ('/bao-gia', f'/bao-gia/{q.id}', f'/bao-gia/{q.id}/in'):
        r = client.get(path)
        assert r.status_code == 200 and 'Lưu báo giá' not in r.text
        assert f'action="/bao-gia/{q.id}/send"' not in r.text
    assert client.get('/bao-gia/moi').status_code == 403
    assert client.post('/bao-gia/moi',data=b1_form(csrf)).status_code == 403
    assert client.post(f'/bao-gia/{q.id}/send',data=dict(csrf=csrf,version=q.version)).status_code == 403
    csrf = web_login('b1.tech', 'technician')
    assert client.get('/bao-gia').status_code == 403
    assert client.post('/bao-gia/moi',data=b1_form(csrf)).status_code == 403


@pytest.fixture
def b3_catalog(db, b1_catalog, make_user):
    from datetime import date
    from app import stock_docs, stock_posting
    from app.models import Supplier, Warehouse
    from app.schemas import StockDocIn
    db.get(Customer,'KTD-000001').address='58 Mã Lò'
    db.add_all([Warehouse(code='K58',name='Kho 58'),Warehouse(code='K145',name='Kho 145'),
                Supplier(code='NCC-0001',name='Nhà cung cấp')])
    db.commit()
    user=make_user('b3.seed','warehouse')
    doc=stock_docs.create(db,user,StockDocIn(kind='PN',warehouse_code='K58',doc_date=date(2026,10,17),supplier_code='NCC-0001',
              lines=[dict(product_code='TD-0001',qty=5,unit_cost=80000)]))
    stock_posting.post(db,user,doc.id,doc.version)


def b3_form(csrf, **changes):
    data=dict(csrf=csrf,customer_code='KTD-000001',contact_name='Chị Lan',delivery_address='',note='Giao buổi sáng',
              product_code=['TD-0001',''],description=['Máy in','Công lắp đặt'],qty=['2','1.5'],unit=['cái','lần'],
              unit_price=['120.000','333'],kind_0='HANG',kind_1='KHAC',warehouse_0='K58',warehouse_1='',vat_0='10',vat_1='10')
    data.update(changes)
    return data


def b3_create(client,db,csrf,**changes):
    from app.models import SalesOrder
    r=client.post('/don-ban/moi',data=b3_form(csrf,**changes),follow_redirects=False)
    assert r.status_code==303,r.text
    db.expire_all()
    return db.query(SalesOrder).order_by(SalesOrder.id.desc()).first()


def b3_action(client,db,csrf,order,action,**data):
    db.expire_all()
    return client.post(f'/don-ban/{order.id}/{action}',data=dict(csrf=csrf,version=order.version,**data))


def b3_quote(db):
    from app import quotes
    from app.models import User
    from app.schemas import QuoteIn
    user=db.query(User).filter_by(username='b3.sales').one()
    q=quotes.create(db,user,QuoteIn(customer_code='KTD-000001',contact_name='Chị Lan',note='Theo báo giá',lines=[
        dict(product_code='TD-0001',qty=2,unit_price=120000,vat_rate=10),
        dict(description='Công',qty='1.5',unit_price=333,unit='lần',vat_rate=10)]))
    quotes.send(db,user,q.id,q.version)
    quotes.accept(db,user,q.id,q.version)
    return q


def test_b3_sales_creates_confirms_delivers_two_lines(client,db,web_login,b3_catalog):
    from decimal import Decimal
    from app.models import StockBalance,StockDoc,StockLedger
    csrf=web_login('b3.sales','sales')
    assert client.get('/don-ban/moi').status_code==200
    order=b3_create(client,db,csrf)
    assert order.total==264550 and order.lines[0].unit_price==120000 and order.delivery_address=='58 Mã Lò'
    assert 'Đơn bán' in client.get('/don-ban').text
    assert b3_action(client,db,csrf,order,'confirm').status_code==200
    response=b3_action(client,db,csrf,order,'deliver')
    assert response.status_code==200 and 'Đã giao' in response.text
    db.expire_all()
    px=db.query(StockDoc).filter_by(kind='PX').one()
    assert order.status=='DA_GIAO' and px.status=='DA_GHI'
    assert px.number in response.text and f'href="/phieu-kho/{px.id}"' in response.text
    assert db.get(StockBalance,('K58','TD-0001')).qty==Decimal(3)
    assert db.query(StockLedger).filter_by(doc_id=px.id).count()==1
    assert order.number in client.get('/don-ban?q=minh phat&status=DA_GIAO&phan=1').text
    assert order.number not in client.get('/don-ban?from=2000-01-01&to=2000-01-02&phan=1').text
    assert order.number not in client.get('/don-ban?status=NHAP&phan=1').text


def test_b3_create_from_accepted_quote_and_link(client,db,web_login,b3_catalog):
    from app.models import SalesOrder
    csrf=web_login('b3.sales','sales')
    q=b3_quote(db)
    page=client.get(f'/bao-gia/{q.id}')
    assert f'href="/don-ban/tu-bao-gia/{q.id}"' in page.text and 'Tạo đơn bán' in page.text
    page=client.get(f'/don-ban/tu-bao-gia/{q.id}')
    assert page.status_code==200 and '58 Mã Lò' in page.text and 'warehouse_1' in page.text
    form=dict(csrf=csrf,version=q.version,warehouse_1='K58',contact_name='Anh Sơn',delivery_address='145 Mã Lò',note='Chọn kho giao')
    r=client.post(f'/don-ban/tu-bao-gia/{q.id}',data=form,follow_redirects=False)
    assert r.status_code==303
    db.expire_all()
    order=db.query(SalesOrder).one()
    assert order.quote_id==q.id and order.total==q.total and order.delivery_address=='145 Mã Lò'
    assert order.lines[0].warehouse_code=='K58' and order.lines[1].kind=='KHAC'
    page=client.get(f'/bao-gia/{q.id}')
    assert f'href="/don-ban/{order.id}"' in page.text and order.number in page.text and 'Tạo đơn bán' not in page.text
    repeated=client.post(f'/don-ban/tu-bao-gia/{q.id}',data=form)
    assert repeated.status_code==409 and 'QUOTE_USED' in repeated.text and order.number in repeated.text
    assert 'Chọn kho giao' in repeated.text
    b3_action(client,db,csrf,order,'cancel',reason='Thay cách giao')
    assert 'Tạo đơn bán' in client.get(f'/bao-gia/{q.id}').text
    # Thiếu các trường tùy chọn trong POST vẫn dùng mặc định nguồn/địa chỉ khách.
    r=client.post(f'/don-ban/tu-bao-gia/{q.id}',data=dict(csrf=csrf,version=q.version,warehouse_1='K58'))
    assert r.status_code==200
    db.expire_all()
    latest=db.query(SalesOrder).order_by(SalesOrder.id.desc()).first()
    assert latest.contact_name=='Chị Lan' and latest.delivery_address=='58 Mã Lò'


def test_b3_shortage_shows_product_warehouse_and_preserves_order(client,db,web_login,b3_catalog):
    from decimal import Decimal
    from app.models import StockBalance,StockDoc
    csrf=web_login('b3.sales','sales')
    order=b3_create(client,db,csrf,qty=['6','1.5'])
    b3_action(client,db,csrf,order,'confirm')
    response=b3_action(client,db,csrf,order,'deliver')
    assert response.status_code==409 and 'NEGATIVE_STOCK' in response.text
    assert 'Không đủ tồn' in response.text and 'Mã hàng TD-0001, kho K58: tồn 5, cần 6' in response.text
    db.expire_all()
    assert order.status=='DA_CHOT' and order.lines[0].qty==Decimal(6)
    assert db.get(StockBalance,('K58','TD-0001')).qty==Decimal(5)
    assert db.query(StockDoc).filter_by(kind='PX').count()==0
    assert '120.000' in response.text and 'Giao buổi sáng' in response.text


def test_b3_cancel_delivered_requires_reason_and_returns_stock(client,db,web_login,b3_catalog):
    from decimal import Decimal
    from app.models import StockBalance,StockDoc
    csrf=web_login('b3.sales','sales')
    order=b3_create(client,db,csrf)
    b3_action(client,db,csrf,order,'confirm')
    page=b3_action(client,db,csrf,order,'deliver')
    assert 'data-order-cancel' in page.text and 'Hủy sẽ trả hàng về kho' in page.text
    response=b3_action(client,db,csrf,order,'cancel')
    assert response.status_code==400 and 'REASON_REQUIRED' in response.text
    db.expire_all()
    assert order.status=='DA_GIAO'
    response=b3_action(client,db,csrf,order,'cancel',reason='Khách trả hàng')
    assert response.status_code==200 and 'Đã hủy' in response.text and 'Khách trả hàng' in response.text
    db.expire_all()
    assert order.status=='DA_HUY' and order.receivable==0
    assert db.get(StockBalance,('K58','TD-0001')).qty==Decimal(5)
    assert db.query(StockDoc).filter_by(kind='PX').one().status=='DA_HUY'


def test_b3_warehouse_can_deliver_without_edit_and_technician_forbidden(client,db,web_login,b3_catalog):
    csrf=web_login('b3.sales','sales')
    order=b3_create(client,db,csrf)
    b3_action(client,db,csrf,order,'confirm')
    csrf=web_login('b3.warehouse','warehouse')
    page=client.get(f'/don-ban/{order.id}')
    assert page.status_code==200 and '>Giao hàng</button>' in page.text
    assert '>Lưu đơn</button>' not in page.text and '>Chốt đơn</button>' not in page.text and '>Hủy đơn</button>' not in page.text
    assert client.post('/don-ban/moi',data=b3_form(csrf)).status_code==403
    assert client.post(f'/don-ban/{order.id}',data=b3_form(csrf,version=order.version)).status_code==403
    assert b3_action(client,db,csrf,order,'confirm').status_code==403
    assert b3_action(client,db,csrf,order,'cancel',reason='Không có quyền').status_code==403
    assert b3_action(client,db,csrf,order,'deliver').status_code==200
    csrf=web_login('b3.technician','technician')
    for path in ('/don-ban',f'/don-ban/{order.id}',f'/don-ban/{order.id}/in-giao','/don-ban/tim-hang?q=may in','/don-ban/tu-bao-gia/1'):
        assert client.get(path).status_code==403
    assert b3_action(client,db,csrf,order,'deliver').status_code==403
    assert client.post('/don-ban/moi',data=b3_form(csrf)).status_code==403


def test_b3_all_posts_require_csrf(client,db,web_login,b3_catalog):
    csrf=web_login('b3.sales','sales')
    order=b3_create(client,db,csrf)
    q=b3_quote(db)
    posts=[('/don-ban/moi',b3_form('')), (f'/don-ban/{order.id}',b3_form('',version=order.version)),
           (f'/don-ban/{order.id}/confirm',dict(version=order.version)),
           (f'/don-ban/{order.id}/deliver',dict(version=order.version)),
           (f'/don-ban/{order.id}/cancel',dict(version=order.version,reason='Khách hủy')),
           (f'/don-ban/tu-bao-gia/{q.id}',dict(version=q.version,warehouse_1='K58'))]
    for path,data in posts:
        r=client.post(path,data=data)
        assert r.status_code==403 and 'Trang đã cũ' in r.text
    db.expire_all()
    assert order.status=='NHAP'


def test_b3_print_delivery_money_words_company_and_signatures(client,db,web_login,b3_catalog):
    csrf=web_login('b3.sales','sales')
    order=b3_create(client,db,csrf)
    b3_action(client,db,csrf,order,'confirm')
    page=b3_action(client,db,csrf,order,'deliver')
    assert f'/don-ban/{order.id}/in-giao' in page.text
    r=client.get(f'/don-ban/{order.id}/in-giao')
    assert r.status_code==200
    for expected in (order.number,'PHIẾU GIAO HÀNG','Hai trăm sáu mươi bốn nghìn năm trăm năm mươi đồng',
                     'Người giao','Người nhận','58 Mã Lò','0313015734','0888 304 668','1,5'):
        assert expected in r.text


def test_b3_money_inputs_use_text_and_touch_choices(client,db,web_login,b3_catalog):
    csrf=web_login('b3.sales','sales')
    order=b3_create(client,db,csrf)
    page=client.get(f'/don-ban/{order.id}')
    inputs=re.findall(r'<input\b[^>]*name="unit_price"[^>]*>',page.text)
    assert len(inputs)>=2
    assert all('data-money' in tag and 'type="number"' not in tag and 'inputmode="numeric"' in tag for tag in inputs)
    assert 'value="120.000"' in page.text and 'data-live=".order-options"' in page.text
    assert 'data-order-kind' in page.text and 'data-order-warehouse' in page.text and 'data-order-vat' in page.text
    assert '<select' not in page.text
    assert 'Máy in' in client.get('/don-ban/tim-hang?q=may in').text
    assert 'data-address="58 Mã Lò"' in client.get('/don-ban/tim-khach?q=minh phat').text


def test_b3_stale_version_keeps_form_data(client,db,web_login,b3_catalog):
    csrf=web_login('b3.sales','sales')
    order=b3_create(client,db,csrf)
    version=order.version
    assert client.post(f'/don-ban/{order.id}',data=b3_form(csrf,version=version)).status_code==200
    r=client.post(f'/don-ban/{order.id}',data=b3_form(csrf,version=version,qty=['7,5','1.5'],unit_price=['999.000','333'],
        delivery_address='Địa chỉ vừa nhập',note='Giữ dòng của tôi',warehouse_0='K145'))
    assert r.status_code==409 and 'VERSION_CONFLICT' in r.text
    assert 'value="999.000"' in r.text and 'value="7,5"' in r.text and 'Giữ dòng của tôi' in r.text
    assert 'Địa chỉ vừa nhập' in r.text
    db.expire_all()
    assert order.lines[0].unit_price==120000 and order.lines[0].warehouse_code=='K58'
    assert b3_action(client,db,csrf,order,'confirm').status_code==200
    assert client.post(f'/don-ban/{order.id}',data=b3_form(csrf,version=order.version)).status_code==409


def test_b3_draft_missing_warehouse_and_invalid_input_preserved(client,db,web_login,b3_catalog):
    from app.models import SalesOrder
    csrf=web_login('b3.sales','sales')
    invalid=client.post('/don-ban/moi',data=b3_form(csrf,qty=['0','1.5'],delivery_address='Giao tại đây'))
    assert invalid.status_code==400 and 'Số lượng' in invalid.text and 'Giao tại đây' in invalid.text and 'value="0"' in invalid.text
    assert db.query(SalesOrder).count()==0
    order=b3_create(client,db,csrf,warehouse_0='')
    r=b3_action(client,db,csrf,order,'confirm')
    assert r.status_code==400 and 'WAREHOUSE_REQUIRED' in r.text and 'chọn kho xuất' in r.text
    assert client.post(f'/don-ban/{order.id}',data=b3_form(csrf,version=order.version,delivery_address='Giao tại đây')).status_code==200
    assert b3_action(client,db,csrf,order,'confirm').status_code==200


def test_b3_from_quote_stale_preserves_fields(client,db,web_login,b3_catalog):
    csrf=web_login('b3.sales','sales')
    q=b3_quote(db)
    r=client.post(f'/don-ban/tu-bao-gia/{q.id}',data=dict(csrf=csrf,version=1,warehouse_1='K145',contact_name='Chị Hương',
            delivery_address='Giao ở địa điểm mới',note='Giữ yêu cầu'))
    assert r.status_code==409 and 'VERSION_CONFLICT' in r.text
    assert 'Chị Hương' in r.text and 'Giao ở địa điểm mới' in r.text and 'Giữ yêu cầu' in r.text
    assert re.search(r'name="warehouse_1" value="K145" checked',r.text)
    assert 'name="version" value="1"' in r.text


def test_b3_finance_read_only_and_date_errors(client,db,web_login,b3_catalog):
    csrf=web_login('b3.sales','sales')
    order=b3_create(client,db,csrf)
    csrf=web_login('b3.finance','finance')
    for path in ('/don-ban',f'/don-ban/{order.id}',f'/don-ban/{order.id}/in-giao'):
        page=client.get(path)
        assert page.status_code==200 and '>Lưu đơn</button>' not in page.text and '>Chốt đơn</button>' not in page.text
    assert client.get('/don-ban/moi').status_code==403
    assert client.post('/don-ban/moi',data=b3_form(csrf)).status_code==403
    assert b3_action(client,db,csrf,order,'deliver').status_code==403
    r=client.get('/don-ban?from=2026-10-18&to=2026-10-17')
    assert r.status_code==400 and 'Ngày bắt đầu' in r.text and 'value="2026-10-18"' in r.text
    assert client.get('/don-ban?from=sai&phan=1').status_code==400


@pytest.fixture
def b4_order(client,db,web_login,b3_catalog,configured_finance):
    csrf=web_login('b4.sales','sales')
    order=b3_create(client,db,csrf,qty=['2','1'],unit_price=['500.000','0'],vat_0='0',vat_1='0')
    assert b3_action(client,db,csrf,order,'confirm').status_code==200
    db.expire_all()
    assert order.receivable==1000000
    return order,csrf


def b4_report(client,db,order,csrf,amount='600.000',**changes):
    db.expire_all()
    data=dict(csrf=csrf,version=order.version,amount=amount,method='TIEN_MAT',received_at='2026-10-18',note='Đã nhận tiền')
    data.update(changes)
    return client.post(f'/don-ban/{order.id}/bao-thu',data=data)


def test_b4_web_report_confirm_fully_paid_disappears(client,db,make_user,b4_order):
    from app.models import SalesReceipt
    order,csrf=b4_order
    page=client.get(f'/don-ban/{order.id}')
    assert 'name="amount" value="1.000.000"' in page.text
    assert 'data-receipt-report' in page.text and 'type="number"' not in re.search(r'<input[^>]*name="amount"[^>]*>',page.text).group()
    assert order.number in client.get('/don-ban/can-thu').text
    r=b4_report(client,db,order,csrf)
    assert r.status_code==200 and 'BT-26-00001' in r.text and 'name="amount" value="400.000"' in r.text
    assert b4_report(client,db,order,csrf,'400.000',method='CHUYEN_KHOAN').status_code==200
    r=b4_report(client,db,order,csrf,'1',note='Giữ báo thu thừa')
    assert r.status_code==409 and 'OVER_PAYMENT' in r.text and 'Giữ báo thu thừa' in r.text
    finance=make_user('b4.finance','finance')
    csrf=service_login(client,finance)
    page=client.get('/bao-thu')
    assert page.status_code==200 and page.text.index('BT-26-00001')<page.text.index('BT-26-00002')
    receipts=db.query(SalesReceipt).order_by(SalesReceipt.id).all()
    for receipt in receipts:
        r=client.post(f'/bao-thu/{receipt.id}/confirm',data=dict(csrf=csrf,version=receipt.version,origin='queue'))
        assert r.status_code==200
    assert order.number not in client.get('/don-ban/can-thu').text
    page=client.get(f'/don-ban/{order.id}')
    assert 'data-balance="paid">1.000.000 đồng' in page.text
    assert 'data-balance="pending">0 đồng' in page.text and 'data-balance="remaining">0 đồng' in page.text


def test_b4_web_reject_reason_releases_capacity_and_filters(client,db,make_user,b4_order):
    from app.models import SalesReceipt,User
    order,csrf=b4_order
    b4_report(client,db,order,csrf,'600.000',received_at='2026-10-17')
    b4_report(client,db,order,csrf,'400.000',method='CHUYEN_KHOAN')
    finance=make_user('b4.finance','finance')
    csrf=service_login(client,finance)
    receipts=db.query(SalesReceipt).order_by(SalesReceipt.id).all()
    page=client.get('/bao-thu?method=CHUYEN_KHOAN&from=2026-10-18&to=2026-10-18')
    assert receipts[0].number not in page.text and receipts[1].number in page.text
    r=client.post(f'/bao-thu/{receipts[0].id}/reject',data=dict(csrf=csrf,version=1,origin='queue',reason=' '))
    assert r.status_code==400 and 'REASON_REQUIRED' in r.text
    r=client.post(f'/bao-thu/{receipts[0].id}/reject',data=dict(csrf=csrf,version=1,origin='queue',reason='Chưa nhận được tiền'))
    assert r.status_code==200 and receipts[0].number not in r.text
    csrf=service_login(client,db.query(User).filter_by(username='b4.sales').one())
    page=client.get(f'/don-ban/{order.id}')
    assert 'name="amount" value="600.000"' in page.text and 'Chưa nhận được tiền' in page.text
    assert b4_report(client,db,order,csrf,'600.000').status_code==200
    assert client.get('/bao-thu').status_code==403


def test_b4_web_has_receipts_blocks_cancel_until_void_and_reject(client,db,make_user,b4_order):
    from app.models import SalesReceipt,User,StockBalance
    order,csrf=b4_order
    assert b3_action(client,db,csrf,order,'deliver').status_code==200
    b4_report(client,db,order,csrf,'600.000')
    b4_report(client,db,order,csrf,'400.000')
    blocked=b3_action(client,db,csrf,order,'cancel',reason='Khách hủy đơn')
    assert blocked.status_code==409 and 'HAS_RECEIPTS' in blocked.text and 'Tài chính phải từ chối hoặc hủy báo thu trước' in blocked.text
    db.expire_all()
    assert db.get(StockBalance,('K58','TD-0001')).qty==3
    receipts=db.query(SalesReceipt).order_by(SalesReceipt.id).all()
    finance=make_user('b4.finance','finance');csrf=service_login(client,finance)
    assert client.post(f'/bao-thu/{receipts[0].id}/confirm',data=dict(csrf=csrf,version=1)).status_code==200
    assert client.post(f'/bao-thu/{receipts[1].id}/reject',data=dict(csrf=csrf,version=1,reason='Chưa nhận')).status_code==200
    assert client.post(f'/bao-thu/{receipts[0].id}/void',data=dict(csrf=csrf,version=2,reason='Trả lại')).status_code==403
    director=make_user('b4.director','director');csrf=service_login(client,director)
    page=client.get(f'/don-ban/{order.id}')
    assert 'Hủy báo thu' in page.text
    r=client.post(f'/bao-thu/{receipts[0].id}/void',data=dict(csrf=csrf,version=2,reason='',origin='order'))
    assert r.status_code==400 and 'REASON_REQUIRED' in r.text
    r=client.post(f'/bao-thu/{receipts[0].id}/void',data=dict(csrf=csrf,version=2,reason='Trả lại khách',origin='order'))
    assert r.status_code==200 and 'data-balance="remaining">1.000.000 đồng' in r.text
    csrf=service_login(client,db.query(User).filter_by(username='b4.sales').one())
    assert b3_action(client,db,csrf,order,'cancel',reason='Khách hủy đơn').status_code==200
    db.expire_all()
    assert order.status=='DA_HUY'
    assert db.get(StockBalance,('K58','TD-0001')).qty==5


def test_b4_web_self_confirmation_and_roles(client,db,make_user,b4_order):
    from app.models import SalesReceipt
    order,csrf=b4_order
    dual=make_user('b4.dual','sales','finance');csrf=service_login(client,dual)
    b4_report(client,db,order,csrf,'100.000')
    receipt=db.query(SalesReceipt).one()
    page=client.get('/bao-thu')
    assert f'action="/bao-thu/{receipt.id}/confirm"' not in page.text
    r=client.post(f'/bao-thu/{receipt.id}/confirm',data=dict(csrf=csrf,version=1))
    assert r.status_code==403 and 'SELF_CONFIRM' in r.text
    for role in ('warehouse','technician'):
        csrf=service_login(client,make_user('b4.'+role,role))
        assert client.get('/bao-thu').status_code==403
        assert client.get('/don-ban/can-thu').status_code==403
        assert b4_report(client,db,order,csrf,'1').status_code==403
        assert client.post(f'/bao-thu/{receipt.id}/confirm',data=dict(csrf=csrf,version=1)).status_code==403
    director=make_user('b4.director','director');csrf=service_login(client,director)
    assert b4_report(client,db,order,csrf,'100.000').status_code==200
    receipt=db.query(SalesReceipt).order_by(SalesReceipt.id.desc()).first()
    assert client.post(f'/bao-thu/{receipt.id}/confirm',data=dict(csrf=csrf,version=1)).status_code==200


def test_b4_web_csrf_and_versions_preserve_form(client,db,make_user,b4_order):
    from app.models import SalesReceipt
    order,csrf=b4_order
    r=b4_report(client,db,order,csrf,'600.000',version=1,note='Giữ số đã nhập')
    assert r.status_code==409 and 'VERSION_CONFLICT' in r.text and 'Giữ số đã nhập' in r.text and 'value="600.000"' in r.text
    assert db.query(SalesReceipt).count()==0
    assert b4_report(client,db,order,'','600.000').status_code==403
    b4_report(client,db,order,csrf,'600.000')
    receipt=db.query(SalesReceipt).one()
    csrf=service_login(client,make_user('b4.director','director'))
    for action in ('confirm','reject','void'):
        r=client.post(f'/bao-thu/{receipt.id}/{action}',data=dict(version=receipt.version,reason='Thiếu CSRF'))
        assert r.status_code==403 and 'Trang đã cũ' in r.text
    client.post(f'/bao-thu/{receipt.id}/confirm',data=dict(csrf=csrf,version=1))
    stale=client.post(f'/bao-thu/{receipt.id}/reject',data=dict(csrf=csrf,version=1,reason='Giữ lý do vừa nhập',origin='queue'))
    assert stale.status_code==409 and 'VERSION_CONFLICT' in stale.text and 'Giữ lý do vừa nhập' in stale.text


def test_b4_web_invalid_money_dates_and_finance_read_only(client,db,make_user,b4_order):
    order,csrf=b4_order
    for amount in ('-1','1,5','1.00','0','999999999999999999999999'):
        r=b4_report(client,db,order,csrf,amount,note='Kiểm tra số tiền')
        assert r.status_code==400 and 'Kiểm tra số tiền' in r.text
    csrf=service_login(client,make_user('b4.finance','finance'))
    page=client.get(f'/don-ban/{order.id}')
    assert 'data-receipt-report' not in page.text and '>Báo thu</a>' in page.text
    assert b4_report(client,db,order,csrf,'1').status_code==403
    r=client.get('/bao-thu?from=2026-10-19&to=2026-10-18')
    assert r.status_code==400 and 'Ngày bắt đầu' in r.text and 'value="2026-10-19"' in r.text
    assert client.get('/bao-thu?method=SAI').status_code==400


from test_sales_reports import report_people


@pytest.fixture
def b5_data(db,report_people):
    from datetime import datetime
    from test_sales_reports import order,quote
    p=report_people
    a=order(db,p,1,subtotal=1000000,vat_total=100000,total=1100000,receivable=1100000)
    b=order(db,p,2,created_by=p['b'].id,customer_code='KTD-000002',subtotal=2000000,vat_total=160000,
            total=2160000,receivable=2160000,delivered_at=datetime.fromisoformat('2026-10-02T00:30:00+07:00'))
    q=quote(db,p,1);a.quote_id=q.id;db.commit()
    return p,a,b


def test_b5_page_sections_presets_daily_totals_and_debt_details(client,db,b5_data):
    p,a,b=b5_data
    service_login(client,p['finance'])
    page=client.get('/bao-cao/ban-hang')
    assert page.status_code==200
    for expected in ('Doanh thu theo ngày','Doanh thu theo nhân viên','Top 20 khách hàng','Tuổi nợ hiện tại',
                     'Tỷ lệ báo giá thành đơn','3.260.000','Cộng','name="from" value="2026-10-01"',
                     'name="to" value="2026-10-31"','Tháng trước','Hôm nay','Tháng này'):
        assert expected in page.text
    assert 'không giới hạn theo khoảng doanh thu' in page.text
    detail=client.get('/bao-cao/ban-hang?from=2026-10-01&to=2026-10-31&nhom=8_30')
    assert detail.status_code==200 and a.number in detail.text and b.number in detail.text
    assert f'href="/don-ban/{a.id}#thu-tien"' in detail.text
    assert 'Tải CSV danh sách' in detail.text
    today=client.get('/bao-cao/ban-hang?period=today')
    assert 'name="from" value="2026-10-18"' in today.text and 'name="to" value="2026-10-18"' in today.text
    previous=client.get('/bao-cao/ban-hang?period=last_month')
    assert 'name="from" value="2026-09-01"' in previous.text and 'name="to" value="2026-09-30"' in previous.text
    assert '3.260.000' in previous.text  # Tuổi nợ vẫn hiện đầy đủ nợ cũ, không phụ thuộc kỳ doanh thu.


def test_b5_sales_scope_in_page_every_csv_and_age_detail(client,db,b5_data):
    p,a,b=b5_data
    service_login(client,p['a'])
    page=client.get('/bao-cao/ban-hang?nhom=8_30')
    assert page.status_code==200 and a.number in page.text and b.number not in page.text
    assert p['b'].full_name not in page.text and '2.160.000' not in page.text
    for table in ('ngay','nhan-vien','khach-hang','tuoi-no','don-no','bao-gia'):
        r=client.get(f'/bao-cao/ban-hang/{table}.csv?nhom=8_30')
        assert r.status_code==200 and b.number not in r.text and p['b'].full_name not in r.text
        assert '2160000' not in r.text
    service_login(client,p['finance'])
    page=client.get('/bao-cao/ban-hang?nhom=8_30')
    assert a.number in page.text and b.number in page.text and p['b'].full_name in page.text


def test_b5_csv_bom_headers_exact_money_and_formula_escape(client,db,b5_data):
    import csv,io
    p,a,b=b5_data
    service_login(client,p['finance'])
    headers={
        'ngay':['Ngày','Số đơn','Trước VAT','VAT','Tổng'],
        'nhan-vien':['Mã nhân viên','Nhân viên','Số đơn','Trước VAT','VAT','Tổng'],
        'khach-hang':['Mã khách','Khách hàng','Số đơn','Trước VAT','VAT','Tổng'],
        'tuoi-no':['Nhóm tuổi nợ','Số đơn','Còn nợ','Chờ xác nhận'],
        'don-no':['Số đơn','Khách hàng','Nhân viên','Ngày chốt','Số ngày','Phải thu','Đã thu','Chờ xác nhận','Còn nợ'],
        'bao-gia':['Số báo giá','Có đơn chưa hủy','Tỷ lệ (%)'],
    }
    for table,expected in headers.items():
        r=client.get(f'/bao-cao/ban-hang/{table}.csv?nhom=8_30')
        assert r.status_code==200 and r.content.startswith(b'\xef\xbb\xbf')
        rows=list(csv.reader(io.StringIO(r.content.decode('utf-8-sig'))))
        assert rows[0]==expected
        assert 'attachment;' in r.headers['content-disposition'] and r.headers['cache-control']=='private, no-store'
        if table=='ngay':
            assert rows[-1]==['Cộng','2','3000000','260000','3260000']
    p['a'].full_name='=HYPERLINK("test")';db.get(Customer,'KTD-000001').name='@SUM(1)';db.commit()
    r=client.get('/bao-cao/ban-hang/nhan-vien.csv')
    assert "'=HYPERLINK" in r.text
    assert "'@SUM(1)" in client.get('/bao-cao/ban-hang/khach-hang.csv').text


def test_b5_errors_and_technician_forbidden(client,db,b5_data,make_user):
    p,a,b=b5_data
    service_login(client,p['finance'])
    r=client.get('/bao-cao/ban-hang?from=2026-10-31&to=2026-10-01')
    assert r.status_code==400 and 'Ngày bắt đầu' in r.text and 'value="2026-10-31"' in r.text
    assert client.get('/bao-cao/ban-hang?from=sai').status_code==400
    assert client.get('/bao-cao/ban-hang?nhom=sai').status_code==400
    assert client.get('/bao-cao/ban-hang/don-no.csv?nhom=sai').status_code==400
    assert client.get('/bao-cao/ban-hang/sai.csv').status_code==404
    service_login(client,make_user('report.tech.web','technician'))
    for path in ('/bao-cao/ban-hang','/bao-cao/ban-hang?nhom=8_30','/bao-cao/ban-hang/ngay.csv','/bao-cao/ban-hang/don-no.csv?nhom=8_30'):
        assert client.get(path).status_code==403
    assert '>Báo cáo bán hàng</a>' not in client.get('/khach-hang').text


def test_b5_web_all_sections_and_csv_use_read_only_transaction(client,db,engine,b5_data):
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from app.db import get_session
    from app.main import app
    p,a,b=b5_data
    service_login(client,p['finance'])
    def read_only():
        with Session(engine) as session:
            session.execute(text('SET TRANSACTION READ ONLY'))
            yield session
    app.dependency_overrides[get_session]=read_only
    try:
        assert client.get('/bao-cao/ban-hang?nhom=8_30').status_code==200
        for table in ('ngay','nhan-vien','khach-hang','tuoi-no','don-no','bao-gia'):
            assert client.get(f'/bao-cao/ban-hang/{table}.csv?nhom=8_30').status_code==200
    finally:
        app.dependency_overrides.pop(get_session)


# T1: danh mục và bảng ghép tài chính dùng cùng CSRF/khuôn danh mục.
from test_finance_catalogs import finance_people


def t1_fund_form(csrf,**changes):
    return dict(csrf=csrf,code='qu y-tm',name='Tiền mặt',kind='TIEN_MAT',bank_name='',account_no='',
                note='Quỹ văn phòng',active='1',**changes)


def test_t1_web_catalog_create_live_search_duplicate_and_bank_error(client,db,finance_people):
    from app.models import Fund
    csrf=service_login(client,finance_people['finance'])
    r=client.post('/quy/moi',data=t1_fund_form(csrf))
    assert r.status_code==200 and 'QUY-TM · Tiền mặt' in r.text
    assert 'type="radio" name="kind"' in r.text and '<select' not in r.text
    assert db.get(Fund,'QUY-TM').name=='Tiền mặt'
    r=client.get('/quy?q=tien+mat&phan=1')
    assert 'QUY-TM' in r.text and '<html' not in r.text
    r=client.post('/quy/moi',data=t1_fund_form(csrf))
    assert r.status_code==409 and 'data-error-code="DUPLICATE_CODE"' in r.text
    assert 'value="qu y-tm"' in r.text and 'Quỹ văn phòng' in r.text
    data=t1_fund_form(csrf);data.update(code='TK-ACB',kind='NGAN_HANG',bank_name='Á Châu')
    r=client.post('/quy/moi',data=data)
    assert r.status_code==400 and 'Quỹ ngân hàng phải có số tài khoản' in r.text
    assert 'value="TK-ACB"' in r.text and 'value="Á Châu"' in r.text
    assert db.query(Fund).count()==1


def test_t1_web_category_update_in_use_and_version_preserve_form(client,db,finance_people):
    from app import finance_mappings
    from app.models import Fund,CashCategory
    f,d=finance_people['finance'],finance_people['director']
    csrf=service_login(client,f)
    data=dict(csrf=csrf,code='thu - bh',name='Thu bán hàng',direction='THU',note='',active='1')
    r=client.post('/danh-muc-thu-chi/moi',data=data)
    assert r.status_code==200 and 'THU-BH' in r.text and 'name="direction"' in r.text
    assert 'THU-BH' in client.get('/danh-muc-thu-chi?q=thu+ban&phan=1').text
    finance_mappings.set_mapping(db,d,'DANH_MUC_BAN_HANG',category_code='THU-BH',version=1)
    changed={**data,'version':'1','name':'Tên vẫn giữ'};changed.pop('active')
    r=client.post('/danh-muc-thu-chi/THU-BH',data=changed)
    assert r.status_code==409 and 'data-error-code="IN_USE"' in r.text and 'value="Tên vẫn giữ"' in r.text
    assert db.get(CashCategory,'THU-BH').active
    client.post('/quy/moi',data=t1_fund_form(csrf))
    update=t1_fund_form(csrf);update.update(version='1',name='Tên mới')
    assert client.post('/quy/QUY-TM',data=update).status_code==200
    update.update(name='Tên nhập chưa lưu',account_no='999')
    r=client.post('/quy/QUY-TM',data=update)
    assert r.status_code==409 and 'VERSION_CONFLICT' in r.text
    assert 'value="Tên nhập chưa lưu"' in r.text and 'value="999"' in r.text
    db.expire_all();assert db.get(Fund,'QUY-TM').name=='Tên mới'


def test_t1_web_mapping_blank_options_only_valid_and_director_save(client,db,finance_people):
    from app import finance_mappings
    from test_finance_catalogs import fund,category
    f,d=finance_people['finance'],finance_people['director']
    fund(db,f);fund(db,f,'TK-ACB','NGAN_HANG',account_no='123');fund(db,f,'TM-NGUNG',active=False)
    category(db,f);category(db,f,'CHI','CHI')
    csrf=service_login(client,d)
    r=client.get('/ghep-tai-chinh')
    assert r.status_code==200 and r.text.count('Chưa ghép')==5
    assert r.text.count('name="target_code" value=""')==5 and 'QUY-TM' not in r.text
    choices=client.get('/ghep-tai-chinh/tim/THU_TIEN_MAT?q=tien+mat').text
    assert 'QUY-TM' in choices and 'TK-ACB' not in choices and 'TM-NGUNG' not in choices
    choices=client.get('/ghep-tai-chinh/tim/THU_CHUYEN_KHOAN').text
    assert 'TK-ACB' in choices and 'QUY-TM' not in choices
    choices=client.get('/ghep-tai-chinh/tim/DANH_MUC_BAN_HANG').text
    assert 'THU-BH' in choices and 'data-finance-pick="CHI"' not in choices
    r=client.post('/ghep-tai-chinh/THU_TIEN_MAT',data=dict(csrf=csrf,version=1,target_code='TK-ACB'))
    assert r.status_code==400 and 'BAD_MAPPING' in r.text and 'value="TK-ACB"' in r.text
    r=client.post('/ghep-tai-chinh/THU_TIEN_MAT',data=dict(csrf=csrf,version=1,target_code='QUY-TM'))
    assert r.status_code==200 and r.text.count('Chưa ghép')==4
    assert finance_mappings.require(db,'THU_TIEN_MAT')=='QUY-TM'
    r=client.post('/ghep-tai-chinh/THU_TIEN_MAT',data=dict(csrf=csrf,version=1,target_code=''))
    assert r.status_code==409 and 'VERSION_CONFLICT' in r.text
    assert '<input name="target_code" value=""' in r.text
    assert finance_mappings.require(db,'THU_TIEN_MAT')=='QUY-TM'


def test_t1_web_permissions_and_csrf(client,db,finance_people):
    from app.models import Fund,AuditLog
    f,d,s=(finance_people[k] for k in ('finance','director','sales'))
    csrf=service_login(client,f)
    r=client.get('/ghep-tai-chinh')
    assert r.status_code==200 and '<button class="primary">Lưu</button>' not in r.text
    assert client.post('/ghep-tai-chinh/THU_TIEN_MAT',data=dict(csrf=csrf,version=1,target_code='')).status_code==403
    data=t1_fund_form(csrf);data.pop('csrf')
    assert client.post('/quy/moi',data=data).status_code==403
    assert client.post('/danh-muc-thu-chi/moi',data=dict(code='A',name='A',direction='THU')).status_code==403
    csrf=service_login(client,d)
    assert client.post('/ghep-tai-chinh/THU_TIEN_MAT',data=dict(version=1,target_code='')).status_code==403
    csrf=service_login(client,s)
    for path in ('/quy','/quy/moi','/danh-muc-thu-chi','/ghep-tai-chinh','/ghep-tai-chinh/tim/THU_TIEN_MAT'):
        assert client.get(path).status_code==403
    assert client.post('/quy/moi',data=t1_fund_form(csrf)).status_code==403
    assert client.post('/danh-muc-thu-chi/moi',data=dict(csrf=csrf,code='A',name='A',direction='THU')).status_code==403
    assert '>Tài chính</a>' not in client.get('/khach-hang').text
    assert db.query(Fund).count()==0 and db.query(AuditLog).count()==0


# T2: phiếu thu chi, ghi sổ, in và sổ quỹ.
from test_cash import cash_people


def t2_form(csrf,**changes):
    return {'csrf':csrf,'kind':'PT','doc_date':'2026-10-10','fund_code':'QUY-TM','category_code':'THU-BH',
            'amount':'1.000.000','partner_name':'Nguyễn Văn An','description':'Thu tiền thử nghiệm','action':'save',**changes}


def test_t2_web_opening_director_and_money_post_print_statement(client,db,cash_people):
    from app.models import CashDoc,FundLedger
    from app import cash_posting
    p=cash_people
    csrf=service_login(client,p['director'])
    r=client.post('/thu-chi/moi',data=t2_form(csrf,is_opening='1',category_code='',amount='5.000.000',doc_date='2026-10-01',action='post'))
    assert r.status_code==200 and 'PT-26-00001' in r.text
    csrf=service_login(client,p['finance'])
    r=client.post('/thu-chi/moi',data=t2_form(csrf,is_opening='1',category_code='',amount='5.000.000'))
    assert r.status_code==403
    r=client.post('/thu-chi/moi',data=t2_form(csrf))
    assert r.status_code==200 and 'type="number"' not in r.text
    doc=db.query(CashDoc).filter(CashDoc.is_opening.is_(False)).one()
    assert doc.amount==1000000 and doc.status=='NHAP'
    r=client.post(f'/thu-chi/{doc.id}/post',data=t2_form(csrf,version=doc.version,amount='1.200.000'))
    assert r.status_code==200 and 'Đã ghi' in r.text
    db.expire_all();assert doc.amount==1200000 and doc.status=='DA_GHI'
    r=client.get(f'/thu-chi/{doc.id}/in')
    assert r.status_code==200 and 'Một triệu hai trăm nghìn đồng' in r.text
    assert all(x in r.text for x in ('Người lập','Thủ quỹ','Người nộp',doc.number))
    r=client.post('/thu-chi/moi',data=t2_form(csrf,kind='PC',category_code='CHI-KHAC',amount='2.000.000',action='post'))
    assert r.status_code==200
    assert cash_posting.balance(db,p['finance'],'QUY-TM')==4200000
    book=client.get('/so-quy/QUY-TM?from=2026-10-10&to=2026-10-10')
    assert book.status_code==200
    assert 'data-cash-balance="opening">5.000.000 đồng' in book.text
    assert 'data-cash-balance="closing">4.200.000 đồng' in book.text
    assert doc.number in book.text
    funds=client.get('/quy')
    assert '4.200.000 đồng' in funds.text and 'href="/so-quy/QUY-TM"' in funds.text
    assert db.query(FundLedger).count()==3


def test_t2_web_negative_cash_preserves_form_and_cancel_reason(client,db,cash_people):
    from app.models import CashDoc,FundLedger
    from app import cash_posting
    from test_cash import opening,posted
    p=cash_people;opening(db,p)
    csrf=service_login(client,p['finance'])
    r=client.post('/thu-chi/moi',data=t2_form(csrf,kind='PC',category_code='CHI-KHAC',amount='10.000.000',action='post',description='Nội dung cần giữ'))
    assert r.status_code==409 and 'NEGATIVE_CASH' in r.text
    assert 'hiện có 5.000.000 đồng' in r.text and 'số cần 10.000.000 đồng' in r.text
    assert 'value="10.000.000"' in r.text and 'Nội dung cần giữ' in r.text
    assert db.query(FundLedger).count()==1 and cash_posting.balance(db,p['finance'],'QUY-TM')==5000000
    doc=db.query(CashDoc).filter(CashDoc.kind=='PC').one();assert doc.status=='NHAP'
    r=client.post(f'/thu-chi/{doc.id}/cancel',data=dict(csrf=csrf,version=doc.version,reason=''))
    assert r.status_code==400 and 'REASON_REQUIRED' in r.text
    assert client.post(f'/thu-chi/{doc.id}/cancel',data=dict(csrf=csrf,version=doc.version,reason='Hủy nháp')).status_code==200
    income=posted(db,p['finance'])
    r=client.post(f'/thu-chi/{income.id}/cancel',data=dict(csrf=csrf,version=income.version,reason=''))
    assert r.status_code==400 and 'REASON_REQUIRED' in r.text
    r=client.post(f'/thu-chi/{income.id}/cancel',data=dict(csrf=csrf,version=income.version,reason='Thu nhầm'))
    assert r.status_code==200 and 'Đã hủy' in r.text
    assert cash_posting.balance(db,p['finance'],'QUY-TM')==5000000
    r=client.get('/so-quy/QUY-TM?from=2026-10-18&to=2026-10-18')
    assert 'Đảo' in r.text and 'data-cash-balance="opening">6.000.000 đồng' in r.text
    assert 'data-cash-balance="closing">5.000.000 đồng' in r.text


def test_t2_web_transfer_filters_and_live_pickers(client,db,cash_people):
    from app.models import Customer,Supplier,CashDoc
    from test_cash import opening
    p=cash_people;opening(db,p)
    db.add(Customer(code='KTD-000001',name='Minh Phát',search_text='minh phat'))
    db.add(Supplier(code='NCC-0001',name='Mực in Việt',search_text='muc in viet'));db.commit()
    csrf=service_login(client,p['finance'])
    assert 'THU-BH' in client.get('/thu-chi/tim/danh-muc-PT?q=thu+ban').text
    assert 'CHI-KHAC' not in client.get('/thu-chi/tim/danh-muc-PT').text
    assert 'CHI-KHAC' in client.get('/thu-chi/tim/danh-muc-PC').text
    assert 'KTD-000001' in client.get('/thu-chi/tim/khach?q=minh+phat').text
    assert 'NCC-0001' in client.get('/thu-chi/tim/nha-cung-cap?q=muc+in').text
    page=client.get('/thu-chi/moi?loai=CQ')
    assert page.status_code==200 and 'name="to_fund_code"' in page.text
    assert 'name="category_code"' not in page.text and '<select' not in page.text
    r=client.post('/thu-chi/moi',data=t2_form(csrf,kind='CQ',category_code='',to_fund_code='TK-ACB',action='post'))
    assert r.status_code==200 and 'CQ-26-00001' in r.text
    doc=db.query(CashDoc).filter(CashDoc.kind=='CQ').one()
    page=client.get('/thu-chi?loai=CQ&quy=TK-ACB&trang_thai=DA_GHI&from=2026-10-01&to=2026-10-31')
    assert page.status_code==200 and doc.number in page.text and 'PT-26-00001' not in page.text
    assert client.get('/thu-chi?from=2026-11-01&to=2026-10-01').status_code==400


def test_t2_web_version_permissions_csrf_and_audit(client,db,cash_people):
    from app.models import AuditLog,CashDoc,FundLedger
    from test_cash import make_doc
    p=cash_people;doc=make_doc(db,p['finance']);csrf=service_login(client,p['finance'])
    r=client.post(f'/thu-chi/{doc.id}',data=t2_form(csrf,version=1,amount='1.200.000'))
    assert r.status_code==200
    r=client.post(f'/thu-chi/{doc.id}',data=t2_form(csrf,version=1,amount='999.999',description='Giữ xung đột'))
    assert r.status_code==409 and 'VERSION_CONFLICT' in r.text
    assert 'value="999.999"' in r.text and 'Giữ xung đột' in r.text
    assert client.post(f'/thu-chi/{doc.id}/post',data=dict(csrf=csrf,version=1)).status_code==409
    for path in ['/thu-chi/moi',f'/thu-chi/{doc.id}',f'/thu-chi/{doc.id}/post',f'/thu-chi/{doc.id}/cancel']:
        assert client.post(path,data=t2_form('',version=2)).status_code==403
    csrf=service_login(client,p['sales'])
    for path in ['/thu-chi','/thu-chi/moi',f'/thu-chi/{doc.id}',f'/thu-chi/{doc.id}/in','/so-quy/QUY-TM','/thu-chi/tim/danh-muc-PT']:
        assert client.get(path).status_code==403
    assert client.post('/thu-chi/moi',data=t2_form(csrf)).status_code==403
    assert client.post(f'/thu-chi/{doc.id}/post',data=dict(csrf=csrf,version=2)).status_code==403
    assert db.query(AuditLog).filter(AuditLog.entity=='cash_doc').count()==2
    assert db.query(FundLedger).count()==0


from test_receipt_cash import t3_people


def test_t3_web_service_collection_queue_source_pt_link_and_paid_disappears(client,db,t3_people,configured_finance):
    from app import sales_receipts
    from app.models import SalesReceipt,CashDoc
    from test_receipt_cash import ticket,order,report
    p=t3_people;t=ticket(db,p);o=order(db,p)
    order_receipt=report(db,p,o)
    csrf=service_login(client,p['dispatcher'])
    page=client.get(f'/dich-vu/{t.id}')
    assert page.status_code==200 and 'data-receipt-report' in page.text
    assert 'data-balance="remaining">500.000 đồng' in page.text
    r=client.post(f'/dich-vu/{t.id}/bao-thu',data=dict(csrf=csrf,version=t.version,method='TIEN_MAT',amount='500.000',received_at='2026-10-18',note='Thu công sửa'))
    assert r.status_code==200 and 'data-balance="pending">500.000 đồng' in r.text
    receipt=db.query(SalesReceipt).filter_by(ticket_id=t.id).one()
    csrf=service_login(client,p['finance'])
    queue=client.get('/bao-thu')
    assert queue.status_code==200 and o.number in queue.text and t.number in queue.text and 'Nguồn:' in queue.text
    assert f'href="/dich-vu/{t.id}#thu-tien"' in queue.text and f'href="/don-ban/{o.id}#thu-tien"' in queue.text
    assert client.post(f'/bao-thu/{receipt.id}/confirm',data=dict(csrf=csrf,version=1)).status_code==200
    db.expire_all();pt=db.get(CashDoc,receipt.cash_doc_id)
    assert client.get(f'/dich-vu/{t.id}').status_code==403  # Finance xem tiền qua Báo thu, không mở phiếu DV.
    service_login(client,p['director'])
    page=client.get(f'/dich-vu/{t.id}')
    assert pt.number in page.text and f'href="/thu-chi/{pt.id}"' in page.text
    assert 'data-balance="remaining">0 đồng' in page.text
    assert t.number not in client.get('/dich-vu/can-thu').text
    assert t.number not in client.get('/dich-vu/can-thu.csv').text
    assert sales_receipts.balance(o)['pending']==600000


def test_t3_web_mapping_error_and_source_locked(client,db,t3_people):
    from app.models import CashDoc
    from app import finance_catalogs,finance_mappings,sales_receipts
    from app.models import Fund,CashCategory
    from app.schemas import FundIn,CashCategoryIn
    from test_receipt_cash import order,report
    p=t3_people;o=order(db,p);r=report(db,p,o)
    csrf=service_login(client,p['finance'])
    response=client.post(f'/bao-thu/{r.id}/confirm',data=dict(csrf=csrf,version=1))
    assert response.status_code==409 and 'MAPPING_REQUIRED' in response.text
    assert 'Giám đốc cần ghép quỹ và danh mục ở trang Ghép tài chính' in response.text
    assert 'href="/ghep-tai-chinh"' in response.text and db.query(CashDoc).count()==0
    d=p['director']
    finance_catalogs.create(db,d,Fund,FundIn(code='TM',name='Tiền mặt',kind='TIEN_MAT'))
    finance_catalogs.create(db,d,CashCategory,CashCategoryIn(code='THU',name='Thu',direction='THU'))
    finance_mappings.set_mapping(db,d,'THU_TIEN_MAT',fund_code='TM',version=1)
    finance_mappings.set_mapping(db,d,'DANH_MUC_BAN_HANG',category_code='THU',version=1)
    assert client.post(f'/bao-thu/{r.id}/confirm',data=dict(csrf=csrf,version=1)).status_code==200
    db.expire_all();pt=db.get(CashDoc,r.cash_doc_id)
    assert 'cash-cancel' not in client.get(f'/thu-chi/{pt.id}').text
    response=client.post(f'/thu-chi/{pt.id}/cancel',data=dict(csrf=csrf,version=pt.version,reason='Hủy trực tiếp'))
    assert response.status_code==409 and 'SOURCE_LOCKED' in response.text
    db.expire_all();assert r.status=='DA_XAC_NHAN' and pt.status=='DA_GHI'


def test_t3_web_ticket_overpay_version_csrf_reopen_and_void_origin(client,db,t3_people,configured_finance):
    from app import sales_receipts,service_tickets
    from app.models import SalesReceipt
    from test_receipt_cash import ticket,report
    p=t3_people;t=ticket(db,p);csrf=service_login(client,p['dispatcher'])
    data=dict(csrf=csrf,version=t.version,method='TIEN_MAT',amount='500.001',received_at='2026-10-18',note='Giữ dữ liệu')
    response=client.post(f'/dich-vu/{t.id}/bao-thu',data=data)
    assert response.status_code==409 and 'OVER_PAYMENT' in response.text
    assert 'value="500.001"' in response.text and 'Giữ dữ liệu' in response.text
    assert client.post(f'/dich-vu/{t.id}/bao-thu',data={**data,'csrf':''}).status_code==403
    assert client.post(f'/dich-vu/{t.id}/bao-thu',data={**data,'amount':'500.000','version':1}).status_code==409
    csrf=service_login(client,p['technician'])
    assert client.post(f'/dich-vu/{t.id}/bao-thu',data={**data,'csrf':csrf}).status_code==409  # KT1: thợ được báo, nhưng vẫn vượt số phải thu.
    r=report(db,p,t,500000)
    sales_receipts.confirm(db,p['finance'],r.id,r.version)
    csrf=service_login(client,p['dispatcher'])
    response=client.post(f'/dich-vu/{t.id}/transition',data=dict(csrf=csrf,version=t.version,to_status='DANG_LAM',note='Khách cần sửa lại'))
    assert response.status_code==409 and 'HAS_RECEIPTS' in response.text
    csrf=service_login(client,p['director'])
    response=client.post(f'/bao-thu/{r.id}/void',data=dict(csrf=csrf,version=r.version,origin='ticket',reason='Hoàn lại cho khách'))
    assert response.status_code==200 and f'/dich-vu/{t.id}' in str(response.url)
    assert 'data-balance="remaining">500.000 đồng' in response.text
    assert t.number in client.get('/dich-vu/can-thu').text


from test_day_closings import cash_people as closing_people


def test_t4_web_close_unlock_badges_and_permissions(client,db,closing_people):
    from app import day_closings as days
    from test_cash import posted,DAY
    p=closing_people;doc=posted(db,p['finance'])
    csrf=service_login(client,p['finance'])
    r=client.get('/chot-so')
    assert r.status_code==200 and r.text.count('id="ngay-')==30
    r=client.post(f'/chot-so/{DAY}/close',data=dict(csrf=csrf,version=0))
    assert r.status_code==200 and 'Đã chốt sổ' in r.text
    page=client.get(f'/thu-chi/{doc.id}')
    assert 'data-day-closed' in page.text and 'cash-cancel' not in page.text and 'data-correction-form' in page.text
    assert '<button>Mở khóa</button>' not in client.get('/chot-so').text
    assert client.post(f'/chot-so/{DAY}/unlock',data=dict(csrf=csrf,version=1,reason='Mở')).status_code==403
    assert client.post(f'/chot-so/{DAY}/close',data=dict(version=1)).status_code==403
    csrf=service_login(client,p['director'])
    response=client.post(f'/chot-so/{DAY}/unlock',data=dict(csrf=csrf,version=1,reason=''))
    assert response.status_code==400 and 'REASON_REQUIRED' in response.text
    assert client.post(f'/chot-so/{DAY}/unlock',data=dict(csrf=csrf,version=1,reason='Mở kiểm tra')).status_code==200
    assert not days.is_closed(db,DAY)
    csrf=service_login(client,p['sales'])
    assert client.get('/chot-so').status_code==403 and client.get('/dieu-chinh').status_code==403


def test_t4_web_request_approve_reject_versions_and_csrf(client,db,closing_people):
    from app import day_closings as days,cash_posting
    from app.models import CashCorrection,CashDoc
    from test_cash import posted,DAY
    p=closing_people;doc=posted(db,p['finance']);days.close(db,p['finance'],DAY,0)
    csrf=service_login(client,p['finance'])
    data=dict(csrf=csrf,version=doc.version,reason='Sai tiền',new_amount='900.000',new_fund_code='QUY-TM',new_category_code='THU-BH',new_description='Thu đúng')
    assert client.post(f'/thu-chi/{doc.id}/dieu-chinh',data={**data,'csrf':''}).status_code==403
    response=client.post(f'/thu-chi/{doc.id}/dieu-chinh',data={**data,'version':1,'new_amount':'850.000'})
    assert response.status_code==409 and 'VERSION_CONFLICT' in response.text and 'value="850.000"' in response.text
    assert client.post(f'/thu-chi/{doc.id}/dieu-chinh',data=data).status_code==200
    req=db.query(CashCorrection).one()
    page=client.get('/dieu-chinh')
    assert '1.000.000 đồng' in page.text and '900.000 đồng' in page.text and '<button class="primary">Duyệt</button>' not in page.text
    assert client.post(f'/dieu-chinh/{req.id}/approve',data=dict(csrf=csrf,version=1)).status_code==403
    csrf=service_login(client,p['director'])
    assert client.post(f'/dieu-chinh/{req.id}/reject',data=dict(csrf=csrf,version=1,reason='')).status_code==400
    assert client.post(f'/dieu-chinh/{req.id}/approve',data=dict(version=1)).status_code==403
    assert client.post(f'/dieu-chinh/{req.id}/approve',data=dict(csrf=csrf,version=2)).status_code==409
    assert client.post(f'/dieu-chinh/{req.id}/approve',data=dict(csrf=csrf,version=1)).status_code==200
    db.expire_all()
    assert db.get(CashDoc,req.new_doc_id).amount==900000 and doc.status=='DA_HUY'
    assert cash_posting.balance(db,p['finance'],'QUY-TM')==900000


from test_finance_reports import t5_money, t5_debts


def test_t5_finance_page_totals_presets_and_csv(client, db, t5_money):
    import csv
    import io
    p, a, b = t5_money
    service_login(client, p['finance'])
    page = client.get('/bao-cao/tai-chinh')
    assert page.status_code == 200
    for expected in ('Số dư quỹ hiện tại','Thu chi theo danh mục','Dòng tiền theo ngày','Công nợ khách tổng hợp',
                     '1.500.000','300.000','6.200.000','Tháng trước','Hôm nay','Tháng này',
                     'name="from" value="2026-10-01"','name="to" value="2026-10-31"'):
        assert expected in page.text
    assert 'Báo cáo tài chính' in client.get('/ghep-tai-chinh').text
    today = client.get('/bao-cao/tai-chinh?period=today')
    assert 'name="from" value="2026-10-18"' in today.text
    previous = client.get('/bao-cao/tai-chinh?period=last_month')
    assert 'name="from" value="2026-09-01"' in previous.text and 'name="to" value="2026-09-30"' in previous.text
    headers = {
        'quy':['Mã quỹ','Quỹ','Số dư hiện tại'],
        'thu':['Mã danh mục','Danh mục','Số phiếu','Số tiền'],
        'chi':['Mã danh mục','Danh mục','Số phiếu','Số tiền'],
        'dong-tien':['Ngày','Thu','Chi','Chênh lệch','Số dư cuối ngày'],
        'cong-no':['Mã khách','Khách hàng','Phải thu','Đã thu','Chờ xác nhận','Còn nợ'],
        'chung-tu':['Chứng từ','Ngày chốt','Số ngày','Phải thu','Đã thu','Chờ xác nhận','Còn nợ']}
    for table, header in headers.items():
        r = client.get(f'/bao-cao/tai-chinh/{table}.csv')
        assert r.status_code == 200 and r.content.startswith(b'\xef\xbb\xbf')
        assert next(csv.reader(io.StringIO(r.content.decode('utf-8-sig')))) == header
        assert r.headers['cache-control']=='private, no-store'
    csv_income = client.get('/bao-cao/tai-chinh/thu.csv').text
    assert 'Cộng,,2,1500000' in csv_income and 'CQ' not in csv_income
    # Nội dung người dùng trong CSV được chống công thức Excel.
    from app.models import Fund
    db.get(Fund,'QUY-TM').name='=SUM(1)';db.commit()
    assert "'=SUM(1)" in client.get('/bao-cao/tai-chinh/quy.csv').text


def test_t5_combined_debt_customer_drilldown(client, t5_debts):
    p, o, t = t5_debts
    service_login(client, p['director'])
    page = client.get('/bao-cao/tai-chinh?from=2000-01-01&to=2000-01-01&khach=KTD-000001')
    assert page.status_code == 200
    for text in ('Minh Phát','1.000.000','400.000',o.number,t.number,'08/10/2026','09/10/2026'):
        assert text in page.text
    assert f'href="/don-ban/{o.id}#thu-tien"' in page.text
    assert f'href="/dich-vu/{t.id}#thu-tien"' in page.text
    csv = client.get('/bao-cao/tai-chinh/chung-tu.csv?khach=KTD-000001').text
    assert f'{o.number},2026-10-08,10' in csv and f'{t.number},2026-10-09,9' in csv
    csv = client.get('/bao-cao/tai-chinh/cong-no.csv').text
    assert '1500000,500000,400000,1000000' in csv


def test_t5_report_permissions_invalid_dates_and_no_writes(client, db, t5_money):
    from app.models import AuditLog
    p, a, b = t5_money
    service_login(client, p['finance'])
    before = db.query(AuditLog).count()
    assert client.get('/bao-cao/tai-chinh?from=sai').status_code == 400
    assert client.get('/bao-cao/tai-chinh?from=2026-10-31&to=2026-10-01').status_code == 400
    assert client.get('/bao-cao/tai-chinh/thu.csv?from=sai').status_code == 400
    assert client.get('/bao-cao/tai-chinh/sai.csv').status_code == 404
    assert client.get('/bao-cao/tai-chinh?khach=KHONG-CO').status_code == 200
    assert db.query(AuditLog).count()==before
    service_login(client, p['sales'])
    assert '>Báo cáo tài chính</a>' not in client.get('/khach-hang').text
    for path in ('/bao-cao/tai-chinh','/bao-cao/tai-chinh?khach=KTD-000001',
                 '/bao-cao/tai-chinh/quy.csv','/bao-cao/tai-chinh/thu.csv','/bao-cao/tai-chinh/chi.csv',
                 '/bao-cao/tai-chinh/dong-tien.csv','/bao-cao/tai-chinh/cong-no.csv','/bao-cao/tai-chinh/chung-tu.csv'):
        assert client.get(path).status_code==403


from test_purchase_orders import purchase_people


def purchase_form(csrf, **changes):
    data=dict(csrf=csrf,supplier_code='NCC-0001',order_date='2026-10-18',expected_date='2026-10-25',note='Mua hàng',
        product_code='TD-0001',qty='1,5',unit_cost='120.000',warehouse_0='K58')
    data.update(changes);return data


def test_m1_web_create_update_order_cancel_print_and_pickers(client,db,purchase_people):
    from app.models import PurchaseOrder,FundLedger,StockLedger
    from sqlalchemy import select,func
    p=purchase_people;service_login(client,p['warehouse'])
    form=client.get('/don-mua/moi');assert form.status_code==200
    csrf=csrf_of(form.text)
    assert re.search(r'value="8"[^>]*checked',form.text)
    assert 'type="number"' not in form.text
    assert 'NCC-0001' in client.get('/don-mua/tim-ncc?q=minh phat').text
    assert 'TD-0001' in client.get('/don-mua/tim-hang?q=may in').text
    r=client.post('/don-mua/moi',data=purchase_form(csrf));assert r.status_code==200,r.text
    o=db.scalar(select(PurchaseOrder));assert o.lines[0].unit_cost==120000 and o.lines[0].vat_rate==8 and o.total==194400
    assert 'DM-26-00001' in r.text
    r=client.post(f'/don-mua/{o.id}',data=purchase_form(csrf,version=o.version,qty='2',vat_0='0'))
    assert r.status_code==200
    db.expire_all();assert o.total==240000 and o.lines[0].vat_rate==0
    assert client.post(f'/don-mua/{o.id}/order',data={'csrf':csrf,'version':o.version}).status_code==200
    db.expire_all();assert o.status=='DA_DAT'
    pick=client.get('/don-mua/tim-hang?q=may in');assert 'data-cost="120000"' in pick.text
    page=client.get('/don-mua?status=DA_DAT&supplier_code=NCC-0001&q=DM-26')
    assert page.status_code==200 and o.number in page.text
    printed=client.get(f'/don-mua/{o.id}/in')
    for text in (o.number,'ĐƠN ĐẶT HÀNG','0313015734','Minh Phát','Bằng chữ','Hai trăm bốn mươi nghìn đồng','Ký, ghi rõ họ tên'):
        assert text in printed.text
    assert client.post(f'/don-mua/{o.id}/cancel',data={'csrf':csrf,'version':o.version}).status_code==400
    r=client.post(f'/don-mua/{o.id}/cancel',data={'csrf':csrf,'version':o.version,'reason':'Không mua'})
    assert r.status_code==200
    db.expire_all();assert o.status=='DA_HUY'
    for model in (FundLedger,StockLedger):assert db.scalar(select(func.count()).select_from(model))==0


def test_m1_form_errors_stale_preserve_inputs_csrf_permissions(client,db,purchase_people):
    from app import purchase_orders as svc
    from test_purchase_orders import body
    p=purchase_people;service_login(client,p['warehouse']);csrf=csrf_of(client.get('/don-mua/moi').text)
    assert client.post('/don-mua/moi',data=purchase_form('')).status_code==403
    row=svc.create(db,p['warehouse'],body());old=row.version
    from app.schemas import PurchaseOrderUpdate
    svc.update(db,p['director'],row.id,PurchaseOrderUpdate(version=old,**body().model_dump()))
    r=client.post(f'/don-mua/{row.id}',data=purchase_form(csrf,version=old,unit_cost='125.000',note='Giữ lời nhắn'))
    assert r.status_code==409 and 'VERSION_CONFLICT' in r.text and '125.000' in r.text and 'Giữ lời nhắn' in r.text
    r=client.post('/don-mua/moi',data=purchase_form(csrf,product_code='TD-SAI',unit_cost='123.456'))
    assert r.status_code==400 and 'BAD_PRODUCT' in r.text and 'TD-SAI' in r.text and '123.456' in r.text
    for role in ('finance','sales','technician'):
        service_login(client,p[role])
        page=client.get(f'/don-mua/{row.id}')
        if role=='finance':
            assert page.status_code==200 and 'Lưu đơn mua</button>' not in page.text and '>Đặt hàng</button>' not in page.text
        else:assert page.status_code==403
        csrf=csrf_of(client.get('/khach-hang').text)
        assert client.post('/don-mua/moi',data=purchase_form(csrf)).status_code==403
        assert client.post(f'/don-mua/{row.id}/order',data={'csrf':csrf,'version':row.version}).status_code==403


def test_m2_receive_form_partial_close_void_and_waiting_filter(client,db,purchase_people):
    from app import purchase_receipts as receipts,purchase_orders as orders
    from test_purchase_receipts import placed
    from app.models import PurchaseReceipt,StockDoc,FundLedger
    from sqlalchemy import select,func
    p=purchase_people;row=placed(db,p,lines=[dict(product_code='TD-0001',warehouse_code='K58',qty=2,unit_cost=1001),
        dict(product_code='TD-0002',warehouse_code='K145',qty=1,unit_cost=100000)])
    service_login(client,p['warehouse']);page=client.get(f'/don-mua/{row.id}/nhan')
    assert page.status_code==200 and 'Nhận và ghi sổ PN' in page.text and 'Đã nhận' in page.text and 'Còn lại' in page.text
    assert f'name="qty_{row.lines[0].id}" value="2"' in page.text
    csrf=csrf_of(page.text)
    data={'csrf':csrf,'version':row.version,'receipt_date':'2026-10-18',f'qty_{row.lines[0].id}':'0,5',f'qty_{row.lines[1].id}':'0'}
    missing=client.post(f'/don-mua/{row.id}/nhan',data={**data,'csrf':''});assert missing.status_code==403
    r=client.post(f'/don-mua/{row.id}/nhan',data=data);assert r.status_code==200,r.text
    db.expire_all();assert row.status=='NHAN_MOT_PHAN' and row.lines[0].received_qty==__import__('decimal').Decimal('0.5')
    receipt=db.scalar(select(PurchaseReceipt));doc=db.get(StockDoc,receipt.stock_doc_id)
    assert doc.status=='DA_GHI' and doc.number in r.text and 'Đóng đơn' in r.text
    assert f'href="/phieu-kho/{doc.id}"' in r.text
    waiting=client.get('/don-mua?status=CHO_NHAN');assert row.number in waiting.text and 'Chờ nhận' in waiting.text
    assert client.post(f'/don-mua/{row.id}/close',data={'csrf':csrf,'version':row.version,'reason':'NCC không giao tiếp'}).status_code==200
    db.expire_all();assert row.status=='DA_NHAN' and row.close_reason=='NCC không giao tiếp'
    assert row.number not in client.get('/don-mua?status=CHO_NHAN').text
    r=client.post(f'/don-mua/lan-nhan/{receipt.id}/huy',data={'csrf':csrf,'version':receipt.version,'reason':'Trả hàng'})
    assert r.status_code==200
    db.expire_all();assert row.status=='DA_DAT' and row.close_reason=='' and receipt.status=='DA_HUY'
    assert db.scalar(select(func.count()).select_from(FundLedger))==0


def test_m2_web_errors_preserve_quantities_version_and_permissions(client,db,purchase_people):
    from app import purchase_receipts as receipts
    from test_purchase_receipts import placed
    p=purchase_people;row=placed(db,p)
    service_login(client,p['warehouse']);csrf=csrf_of(client.get(f'/don-mua/{row.id}/nhan').text)
    data={'csrf':csrf,'version':row.version,'receipt_date':'2026-10-17',f'qty_{row.lines[0].id}':'2,5'}
    r=client.post(f'/don-mua/{row.id}/nhan',data=data)
    assert r.status_code==409 and 'OVER_RECEIVE' in r.text and '2,5' in r.text and '2026-10-17' in r.text
    r=client.post(f'/don-mua/{row.id}/nhan',data={**data,f'qty_{row.lines[0].id}':'0'})
    assert r.status_code==400 and 'EMPTY_RECEIPT' in r.text
    data['version']=row.version-1
    r=client.post(f'/don-mua/{row.id}/nhan',data={**data,f'qty_{row.lines[0].id}':'0,5'})
    assert r.status_code==409 and 'VERSION_CONFLICT' in r.text and '0,5' in r.text
    for role in ('sales','technician','finance'):
        service_login(client,p[role]);csrf=csrf_of(client.get('/khach-hang').text)
        assert client.get(f'/don-mua/{row.id}/nhan').status_code==403
        assert client.post(f'/don-mua/{row.id}/nhan',data={**data,'csrf':csrf}).status_code==403
        if role=='finance':
            page=client.get(f'/don-mua/{row.id}');assert page.status_code==200 and '>Nhận hàng</a>' not in page.text


from test_supplier_debts import m3_people


def test_m3_web_pay_from_order_void_and_negative_advance(client,db,m3_people):
    from app import purchase_receipts,supplier_debts
    from app.models import SupplierPayment,CashDoc
    from sqlalchemy import select
    from test_supplier_debts import received
    p=m3_people;order,receipt=received(db,p)
    service_login(client,p['finance'])
    page=client.get(f'/don-mua/{order.id}');assert page.status_code==200 and 'Công nợ đơn mua' in page.text
    assert f'/cong-no-ncc/NCC-0001?don={order.id}#tra-tien' in page.text
    page=client.get(f'/cong-no-ncc/NCC-0001?don={order.id}');csrf=csrf_of(page.text)
    assert 'name="amount" value="1.000.000"' in page.text and 'data-supplier-opening' not in page.text
    data=dict(csrf=csrf,fund_code='QUY-TM',amount='1.000.000',paid_at='2026-10-18',order_id=order.id,note='Trả đủ')
    assert client.post('/cong-no-ncc/NCC-0001/tra',data={**data,'csrf':''}).status_code==403
    r=client.post('/cong-no-ncc/NCC-0001/tra',data=data);assert r.status_code==200,r.text
    payment=db.scalar(select(SupplierPayment));doc=db.get(CashDoc,payment.cash_doc_id)
    assert payment.amount==1000000 and doc.status=='DA_GHI' and payment.number in r.text and doc.number in r.text
    assert 'class="card form cash-cancel"' not in client.get(f'/thu-chi/{doc.id}').text
    purchase_receipts.void(db,p['warehouse'],receipt.id,'Trả lại hàng',receipt.version)
    page=client.get('/cong-no-ncc/NCC-0001');assert 'Đã trả trước 1.000.000' in page.text
    assert 'Đã trả trước 1.000.000' in client.get('/cong-no-ncc').text
    r=client.post(f'/cong-no-ncc/tra/{payment.id}/huy',data={'csrf':csrf,'version':payment.version,'reason':'Hủy trả'})
    assert r.status_code==200
    db.expire_all();assert payment.status=='DA_HUY' and supplier_debts.balance(db,p['finance'],'NCC-0001')['remaining']==0


def test_m3_web_opening_versions_errors_preserved_and_permissions(client,db,m3_people):
    from app import supplier_debts
    from test_supplier_debts import DAY
    p=m3_people;service_login(client,p['director'])
    page=client.get('/cong-no-ncc/NCC-0001');csrf=csrf_of(page.text)
    assert page.status_code==200 and 'data-supplier-opening' in page.text
    data=dict(csrf=csrf,amount='900.000',as_of=str(DAY),note='Nợ cũ',version=0)
    assert client.post('/cong-no-ncc/NCC-0001/dau-ky',data=data).status_code==200
    r=client.post('/cong-no-ncc/NCC-0001/dau-ky',data={**data,'amount':'850.000','note':'Giữ số đã nhập'})
    assert r.status_code==409 and 'VERSION_CONFLICT' in r.text and '850.000' in r.text and 'Giữ số đã nhập' in r.text
    pay=dict(csrf=csrf,fund_code='QUY-TM',amount='950.000',paid_at=str(DAY),note='Giữ tiền trả')
    r=client.post('/cong-no-ncc/NCC-0001/tra',data=pay)
    assert r.status_code==409 and 'OVER_PAYMENT' in r.text and '950.000' in r.text and 'Giữ tiền trả' in r.text
    assert 'Minh Phát' in client.get('/cong-no-ncc/ket-qua?q=minh phat').text
    assert 'NCC-0001' in client.get('/cong-no-ncc/tim?q=minh phat').text
    service_login(client,p['finance']);csrf=csrf_of(client.get('/cong-no-ncc/NCC-0001').text)
    assert client.post('/cong-no-ncc/NCC-0001/dau-ky',data={**data,'csrf':csrf}).status_code==403
    for role in ('sales','warehouse','technician'):
        service_login(client,p[role]);csrf=csrf_of(client.get('/khach-hang').text)
        assert client.get('/cong-no-ncc').status_code==403
        assert client.get('/cong-no-ncc/NCC-0001').status_code==403
        assert client.post('/cong-no-ncc/NCC-0001/tra',data={**pay,'csrf':csrf}).status_code==403
        if role=='warehouse':assert 'Công nợ đơn mua' not in client.get('/don-mua/1').text


def test_m4_return_form_create_void_versions_csrf_and_progress(client,db,purchase_people):
    from app import purchase_returns
    from app.models import PurchaseReturn,StockDoc,FundLedger
    from sqlalchemy import select
    from test_purchase_receipts import placed,receive
    p=purchase_people;order=placed(db,p);receipt=receive(db,p,order)[0]
    service_login(client,p['warehouse'])
    detail=client.get(f'/don-mua/{order.id}')
    assert detail.status_code==200 and f'/don-mua/lan-nhan/{receipt.id}/tra' in detail.text and 'Đã trả hàng' in detail.text
    url=f'/don-mua/lan-nhan/{receipt.id}/tra';page=client.get(url)
    assert page.status_code==200 and 'Còn trả được' in page.text and f'name="qty_{receipt.lines[0].id}" inputmode="decimal" value="0"' in page.text
    csrf=csrf_of(page.text);data=dict(csrf=csrf,version=order.version,return_date='2026-10-18',reason='Hàng lỗi')
    data[f'qty_{receipt.lines[0].id}']='0,5'
    assert client.post(url,data={**data,'csrf':''}).status_code==403
    result=client.post(url,data=data);assert result.status_code==200,result.text
    db.expire_all();row=db.scalar(select(PurchaseReturn));doc=db.get(StockDoc,row.stock_doc_id)
    assert row.number in result.text and doc.number in result.text and f'/phieu-kho/{doc.id}' in result.text
    assert order.lines[0].received_qty==__import__('decimal').Decimal('1.5') and 'Hủy trả hàng' in result.text
    result=client.post(url,data={**data,'reason':'Giữ form','return_date':'2026-10-17'})
    assert result.status_code==409 and 'VERSION_CONFLICT' in result.text and '0,5' in result.text and 'Giữ form' in result.text and '2026-10-17' in result.text
    data['version']=order.version;data[f'qty_{receipt.lines[0].id}']='2,5'
    result=client.post(url,data=data)
    assert result.status_code==409 and 'OVER_RETURN' in result.text and '2,5' in result.text
    data[f'qty_{receipt.lines[0].id}']='0'
    assert 'EMPTY_RETURN' in client.post(url,data=data).text
    cancel=f'/don-mua/tra-hang/{row.id}/huy'
    assert client.post(cancel,data={'csrf':'','version':row.version,'reason':'Hủy trả'}).status_code==403
    result=client.post(cancel,data={'csrf':csrf,'version':row.version,'reason':''})
    assert result.status_code==400 and 'REASON_REQUIRED' in result.text
    result=client.post(cancel,data={'csrf':csrf,'version':row.version,'reason':'Nhận lại'})
    assert result.status_code==200
    db.expire_all();assert row.status=='DA_HUY' and doc.status=='DA_HUY' and db.query(FundLedger).count()==0


def test_m4_return_permissions_and_negative_stock_message_preserves_form(client,db,purchase_people):
    from app import stock_docs,stock_posting
    from app.schemas import StockDocIn
    from test_purchase_receipts import placed,receive
    p=purchase_people;order=placed(db,p);receipt=receive(db,p,order)[0]
    doc=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PX',warehouse_code='K58',doc_date='2026-10-18',lines=[dict(product_code='TD-0001',qty='1.5')]))
    stock_posting.post(db,p['warehouse'],doc.id,doc.version)
    url=f'/don-mua/lan-nhan/{receipt.id}/tra';service_login(client,p['warehouse']);csrf=csrf_of(client.get(url).text)
    data=dict(csrf=csrf,version=order.version,return_date='2026-10-18',reason='Máy lỗi',**{f'qty_{receipt.lines[0].id}':'1'})
    r=client.post(url,data=data)
    assert r.status_code==409 and 'NEGATIVE_STOCK' in r.text and 'TD-0001' in r.text and 'K58' in r.text and 'Máy lỗi' in r.text
    for role in ('sales','technician','finance'):
        service_login(client,p[role]);csrf=csrf_of(client.get('/khach-hang').text)
        assert client.get(url).status_code==403
        assert client.post(url,data={**data,'csrf':csrf}).status_code==403
        assert client.post('/don-mua/tra-hang/999/huy',data={'csrf':csrf,'version':1,'reason':'Hủy'}).status_code==403
        if role=='finance':
            r=client.get(f'/don-mua/{order.id}')
            assert r.status_code==200 and '>Trả hàng</a>' not in r.text


def test_m4_web_return_shows_advance_and_timeline_links(client,db,m3_people):
    from app import purchase_returns,supplier_debts
    from app.models import StockDoc,FundLedger
    from test_supplier_debts import received,pay
    p=m3_people;order,receipt=received(db,p);pay(db,p,1000000,order_id=order.id)
    n=db.query(FundLedger).count()
    row=purchase_returns.create(db,p['warehouse'],receipt.id,order.version,'2026-10-18','Hàng lỗi',
        [dict(receipt_line_id=receipt.lines[0].id,qty='0.4')])
    service_login(client,p['finance'])
    page=client.get('/cong-no-ncc');assert page.status_code==200 and 'Trả hàng' in page.text and 'Đã trả trước 400.000' in page.text
    page=client.get(f'/cong-no-ncc/NCC-0001?don={order.id}')
    assert page.status_code==200 and row.number in page.text and 'Trả hàng NCC' in page.text and 'Đã trả trước 400.000' in page.text
    assert db.get(StockDoc,row.stock_doc_id).number in page.text and f'/phieu-kho/{row.stock_doc_id}' in page.text
    order_page=client.get(f'/don-mua/{order.id}');assert order_page.status_code==200 and 'Trả hàng' in order_page.text and '400.000' in order_page.text
    assert db.query(FundLedger).count()==n
