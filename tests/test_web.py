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
    assert r.headers["location"] == "/hang-hoa"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie


def test_khong_chuyen_huong_ra_trang_ngoai(client, make_user):
    make_user("le.tan", "reception")
    r = client.post("/dang-nhap", data={"username": "le.tan", "password": PASSWORD, "next": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/khach-hang"


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


def test_ky_thuat_chi_xem(client, web_login):
    csrf = web_login("ban.hang", "sales")
    client.post("/khach-hang/moi", data=customer_form(csrf))
    csrf = web_login("ky.thuat", "technician")
    page = client.get("/khach-hang/KTD-000001").text
    assert "<fieldset disabled>" in page and ">Lưu<" not in page
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
    assert '<fieldset disabled>' in client.get(path + '/' + code).text
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
    for path in ['/phieu-kho', '/phieu-kho/moi', f'/phieu-kho/{doc_id}', '/ton-kho', '/the-kho/K58/TD-0001']:
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
