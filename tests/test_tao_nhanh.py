"""Tạo mới nhanh trong cửa sổ (TUẤN duyệt 06/11/2026): khách, mã hàng, NCC tạo ngay trên màn đang soạn."""
import re

from app.models import Customer, Product, Supplier

from test_web import customer_form, web_login  # noqa: F401  (fixtures)


def _key(html: str) -> str:
    return re.search(r'name="form_key" value="([^"]+)"', html).group(1)


def test_cua_so_khach_dien_san_va_luu_tra_ma(client, web_login, db):
    csrf = web_login("ban.hang", "sales")
    html = client.get("/tao-nhanh/khach", params={"q": "0909 123 456"}).text
    assert "<html" not in html and 'data-qn-form' in html and 'name="phones" value="0909 123 456"' in html
    assert 'Lưu và chọn' in html and 'Hạn mức' not in html.split("Mở form đầy đủ")[0].replace("hạn mức nợ", "")
    assert 'name="name" value="Minh Phát"' in client.get("/tao-nhanh/khach", params={"q": "Minh Phát"}).text
    mixed = client.get("/tao-nhanh/khach", params={"q": "Anh Hùng 0909123456"}).text
    assert 'name="name" value="Anh Hùng"' in mixed and 'name="phones" value="0909123456"' in mixed
    r = client.post("/tao-nhanh/khach", data={"csrf": csrf, "form_key": _key(html), "kind": "CA_NHAN", "name": "Hùng", "phones": "0909 123 456", "address": "Q.7"})
    assert r.status_code == 200 and r.json()["code"].startswith("KTD-") and r.json()["name"] == "Hùng"
    c = db.get(Customer, r.json()["code"])
    assert c is not None and c.address == "Q.7" and not c.allow_debt


def test_khach_trung_so_bao_trong_cua_so_co_nut_dung_khach_nay(client, web_login):
    csrf = web_login("ban.hang", "sales")
    client.post("/khach-hang/moi", data=customer_form(csrf))  # KTD-000001, 0901234567
    r = client.post("/tao-nhanh/khach", data={"csrf": csrf, "kind": "CA_NHAN", "name": "An khác", "phones": "0901234567"})
    assert r.status_code >= 400 and "<html" not in r.text and "Chưa lưu được" in r.text
    assert 'data-qn-use="KTD-000001"' in r.text
    # Báo trùng khi rời ô SĐT: nút Dùng khách này mang mã để cửa sổ chọn luôn.
    assert 'data-code="KTD-000001"' in client.get("/khach-hang/kiem-trung", params={"phone": "0901234567"}).text


def test_gui_lai_cung_khoa_khong_tao_trung(client, web_login, db):
    csrf = web_login("ban.hang", "sales")
    key = _key(client.get("/tao-nhanh/khach").text)
    data = {"csrf": csrf, "form_key": key, "kind": "CA_NHAN", "name": "Lan", "phones": "0912000111"}
    first = client.post("/tao-nhanh/khach", data=data).json()
    again = client.post("/tao-nhanh/khach", data=data)
    assert again.status_code == 200 and again.json()["code"] == first["code"]
    assert db.query(Customer).filter(Customer.name == "Lan").count() == 1


def test_quyen_theo_form_day_du(client, web_login):
    web_login("ban.hang", "sales")
    page = client.get("/don-ban/moi").text
    assert re.search(r'data-qn="khach"', page) and "tao-nhanh.js" in page
    assert client.get("/tao-nhanh/hang").status_code == 403 and client.get("/tao-nhanh/ncc").status_code == 403
    web_login("tho.a", "technician")
    assert client.get("/tao-nhanh/khach", follow_redirects=False).status_code in (403, 303)
    assert client.get("/tao-nhanh/khac").status_code in (403, 404)


def test_kho_tao_ma_hang_va_ncc(client, web_login, db):
    csrf = web_login("thu.kho", "warehouse")
    client.post("/nhom-hang/moi", data={"csrf": csrf, "name": "Hộp mực Renew"})
    html = client.get("/tao-nhanh/hang", params={"q": "Brother TN-2385"}).text
    assert 'name="code" value="TD-0001"' in html and 'name="name" value="Brother TN-2385"' in html
    assert 'data-money' in html and 'name="warranty_months"' not in html  # bảo hành chỉ giám đốc
    r = client.post("/tao-nhanh/hang", data={"csrf": csrf, "code": "td-0001", "name": "Brother TN-2385 Renew", "group_name": "Hộp mực Renew", "unit": "hộp", "sale_price": "350.000"})
    assert r.json()["code"] == "TD-0001" and r.json()["sale_price"] == 350_000
    assert db.get(Product, "TD-0001").group_name == "Hộp mực Renew"
    bad = client.post("/tao-nhanh/hang", data={"csrf": csrf, "code": "TD-0001", "name": "Trùng", "unit": "cái"})
    assert bad.status_code >= 400 and "Mã hàng đã có" in bad.text and "<html" not in bad.text
    r = client.post("/tao-nhanh/ncc", data={"csrf": csrf, "name": "Phúc Anh", "tax_code": "0301234567", "phones": "0283 123 456", "address": "Q.1"})
    code = r.json()["code"]
    assert code.startswith("NCC-") and db.get(Supplier, code).name == "Phúc Anh"
    assert "MST đã thuộc nhà cung cấp khác" in client.post("/tao-nhanh/ncc", data={"csrf": csrf, "name": "Khác", "tax_code": "0301234567"}).text


def test_giam_doc_dat_bao_hanh_va_serial(client, web_login, db):
    csrf = web_login("giam.doc", "director")
    html = client.get("/tao-nhanh/hang").text
    assert 'name="warranty_months"' in html and 'name="track_serial"' in html
    r = client.post("/tao-nhanh/hang", data={"csrf": csrf, "code": "TD-0002", "name": "Máy in HP M211dw", "unit": "cái", "warranty_months": "12", "track_serial": "1"})
    assert r.json()["warranty_months"] == 12 and r.json()["track_serial"] is True
    p = db.get(Product, "TD-0002")
    assert p.warranty_months == 12 and p.track_serial


def test_nut_tao_ma_canh_dong_hang_chi_ai_tao_duoc(client, web_login):
    web_login("giam.doc", "director")
    for url in ("/don-ban/moi", "/bao-gia/moi", "/don-mua/moi", "/phieu-kho/moi?loai=PN"):
        assert "data-qn-new-line" in client.get(url).text, url
    web_login("ban.hang", "sales")
    assert "data-qn-new-line" not in client.get("/don-ban/moi").text
