"""KH1: báo trùng khách khi đang gõ và quay về đơn đang làm bằng "Dùng khách này"."""
from app.models import Customer

from test_sales_orders import people, stocked  # noqa: F401  (fixtures)
from test_web import customer_form, service_login, service_people, web_login  # noqa: F401  (fixtures)


def _two(client, web_login):
    csrf = web_login("ban.hang", "sales")
    client.post("/khach-hang/moi", data=customer_form(csrf))  # KTD-000001 cá nhân, 0901234567
    client.post("/khach-hang/moi", data=customer_form(csrf, kind="CONG_TY", name="Công ty A", phones="02838123456", tax_code="0301234567"))
    return csrf


def test_kiem_trung_theo_so_va_mst(client, web_login, db):
    _two(client, web_login)
    html = client.get("/khach-hang/kiem-trung", params={"phone": "090 123 4567", "kind": "CA_NHAN"}).text
    assert "KTD-000001 · Nguyễn Văn An" in html and "trùng SĐT" in html
    assert "Sẽ không lưu được" in html and "Còn nợ" not in html and "Hạn mức" not in html
    assert 'href="/khach-hang/KTD-000001"' in html and "<html" not in html
    html = client.get("/khach-hang/kiem-trung", params={"phone": "0901234567", "kind": "CONG_TY"}).text
    assert "Có thể cùng người liên hệ" in html
    html = client.get("/khach-hang/kiem-trung", params={"tax": "030 123 4567"}).text
    assert "KTD-000002 · Công ty A" in html and "trùng MST" in html and "Sẽ không lưu được" in html
    assert "KTD-" not in client.get("/khach-hang/kiem-trung", params={"phone": "0901234567", "except": "KTD-000001"}).text
    # Đang gõ dở: không lỗi, không kết quả.
    r = client.get("/khach-hang/kiem-trung", params={"phone": "0901", "tax": "12"})
    assert r.status_code == 200 and "KTD-" not in r.text


def test_kiem_trung_bo_khach_da_gop_va_can_quyen(client, web_login, db, make_user):
    _two(client, web_login)
    c = db.get(Customer, "KTD-000001")
    c.status, c.merged_into = "DA_GOP", "KTD-000002"
    db.commit()
    assert "KTD-000001" not in client.get("/khach-hang/kiem-trung", params={"phone": "0901234567"}).text
    web_login("tho.a", "technician")
    assert client.get("/khach-hang/kiem-trung", params={"phone": "0901234567"}, follow_redirects=False).status_code in (403, 303)


def test_dung_khach_nay_quay_ve_dung_don(client, web_login):
    _two(client, web_login)
    html = client.get("/khach-hang/kiem-trung", params={"phone": "0901234567", "quay_ve": "/don-ban/moi"}).text
    assert 'href="/don-ban/moi?customer_code=KTD-000001"' in html
    html = client.get("/khach-hang/kiem-trung", params={"phone": "0901234567", "quay_ve": "https://evil.com"}).text
    assert "evil" not in html and 'data-use-customer href="/khach-hang/KTD-000001"' in html


def test_form_tao_khach_dien_san_va_giu_quay_ve(client, web_login):
    web_login("ban.hang", "sales")
    html = client.get("/khach-hang/moi", params={"quay_ve": "/don-ban/moi", "q": "0909123456"}).text
    assert 'name="phones" value="0909123456"' in html and 'name="quay_ve" value="/don-ban/moi"' in html
    assert 'data-dup-check="phone"' in html and 'data-dup-check="tax"' in html and 'data-dup-result aria-live="polite"' in html
    assert 'name="tax_code" value="0312345678"' not in html
    assert 'name="tax_code" value="0123456789012"' in client.get("/khach-hang/moi", params={"q": "0123456789012"}).text
    assert 'name="name" value="Minh Phát"' in client.get("/khach-hang/moi", params={"q": "Minh Phát"}).text
    html = client.get("/khach-hang/moi", params={"quay_ve": "https://evil.com"}).text
    assert 'name="quay_ve"' not in html


def test_tao_khach_xong_quay_ve(client, web_login):
    csrf = web_login("ban.hang", "sales")
    r = client.post("/khach-hang/moi", data=customer_form(csrf, quay_ve="/bao-gia/moi"), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/bao-gia/moi?customer_code=KTD-000001"
    # Không có quay_ve: như cũ.
    r = client.post("/khach-hang/moi", data=customer_form(csrf, name="Khách 2", phones="0912345678"), follow_redirects=False)
    assert r.headers["location"].startswith("/khach-hang/KTD-000002?da_luu=1")


def test_luu_trung_van_chan_va_giu_quay_ve(client, web_login, db):
    csrf = _two(client, web_login)
    data = customer_form(csrf, kind="CONG_TY", name="Công ty B", phones="0987654321", tax_code="0301234567", quay_ve="/dich-vu/moi")
    r = client.post("/khach-hang/moi", data=data)
    assert r.status_code == 409 and "MST đã thuộc khách khác" in r.text
    assert 'name="quay_ve" value="/dich-vu/moi"' in r.text
    assert 'href="/dich-vu/moi?customer_code=KTD-000002"' in r.text
    assert db.query(Customer).count() == 2


def test_don_ban_va_bao_gia_moi_chon_san_khach(client, db, stocked):
    service_login(client, stocked["sales"])
    html = client.get("/don-ban/moi?customer_code=KTD-000001").text
    assert 'name="customer_code" value="KTD-000001"' in html and "KTD-000001 · Minh Phát" in html
    assert 'href="/khach-hang/moi?quay_ve=/don-ban/moi"' in html
    r = client.get("/don-ban/moi?customer_code=KTD-999999")
    assert r.status_code == 200 and 'name="customer_code" value=""' in r.text
    html = client.get("/bao-gia/moi?customer_code=KTD-000001").text
    assert 'name="customer_code" value="KTD-000001"' in html and 'href="/khach-hang/moi?quay_ve=/bao-gia/moi"' in html
    db.get(Customer, "KTD-000001").status = "NGUNG"
    db.commit()
    assert 'name="customer_code" value=""' in client.get("/bao-gia/moi?customer_code=KTD-000001").text


def test_nhan_don_dich_vu_chon_san_khach_va_may(client, db, service_people):
    service_login(client, service_people["dispatcher"])
    html = client.get("/dich-vu/moi?customer_code=KTD-000001").text
    assert 'name="customer_code" value="KTD-000001"' in html and "KTD-000001 · Minh Phát" in html
    assert 'name="serial" value="AB123" checked' in html
    assert 'href="/khach-hang/moi?quay_ve=/dich-vu/moi"' in html
