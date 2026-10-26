"""Safari iOS 15: http:// phải chuyển sang https, có thay thế form.requestSubmit, ô tên đăng nhập không tự sửa chữ."""
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture
def secure_cookie(monkeypatch):
    monkeypatch.setattr(get_settings(), "cookie_secure", True)


def test_http_qua_cloudflare_chuyen_sang_https(secure_cookie):
    c = TestClient(app, base_url="http://quanlythanhduc.com")
    for headers in ({"x-forwarded-proto": "http"}, {"cf-visitor": '{"scheme":"http"}'}):
        r = c.get("/dang-nhap?next=/tho", headers=headers, follow_redirects=False)
        assert r.status_code == 308
        assert r.headers["location"] == "https://quanlythanhduc.com/dang-nhap?next=/tho"
    r = c.post("/dang-nhap", headers={"x-forwarded-proto": "http"}, data={"username": "x"}, follow_redirects=False)
    assert r.status_code == 308


def test_https_va_may_chay_thu_khong_chuyen(secure_cookie):
    c = TestClient(app, base_url="http://quanlythanhduc.com")
    assert c.get("/dang-nhap", headers={"x-forwarded-proto": "https", "cf-visitor": '{"scheme":"https"}'}).status_code == 200
    assert c.get("/dang-nhap").status_code == 200


def test_khong_bat_cookie_secure_thi_khong_chuyen():
    assert get_settings().cookie_secure is False
    c = TestClient(app)
    assert c.get("/dang-nhap", headers={"x-forwarded-proto": "http"}).status_code == 200


def test_trang_dang_nhap_cho_safari_cu():
    html = TestClient(app).get("/dang-nhap").text
    assert "HTMLFormElement.prototype.requestSubmit" in html
    assert 'autocorrect="off"' in html
