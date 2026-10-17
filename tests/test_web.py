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
