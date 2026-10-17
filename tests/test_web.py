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


def test_opening_web_sales_read_only_and_csrf_enforced(client, web_login, stock_catalog, db):
    from app.models import StockDoc
    csrf = web_login('ban.hang', 'sales')
    r = client.get('/ton-dau-ky')
    assert r.status_code == 200 and '>Tạo phiếu KK nháp<' not in r.text and '<fieldset disabled>' in r.text
    assert opening_upload(client, csrf).status_code == 403
    csrf = web_login('thu.kho', 'warehouse')
    assert client.post('/ton-dau-ky', data={'warehouse_code': 'K58', 'doc_date': '2026-12-31'},
                       files={'file': ('opening.csv', OPENING_CSV, 'text/csv')}).status_code == 403
    assert db.query(StockDoc).count() == 0


def test_opening_web_sample_csv(client, web_login):
    import csv
    from io import StringIO
    web_login('ban.hang', 'sales')
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
    for path in ['/ton-kho?canh-bao=1', '/the-kho/K58/TD-0001']:
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
    csrf = web_login('ky.thuat', 'technician')
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
