"""Nút quay lại về đúng trang vừa đến (back-nav.js), không về link cố định."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app" / "web"


def test_base_loads_back_nav_on_every_page():
    base = (ROOT / "templates" / "base.jinja").read_text()
    assert "asset('back-nav.js')" in base


def test_back_nav_covers_all_back_link_styles():
    js = (ROOT / "static" / "back-nav.js").read_text()
    for sel in ("a.ph-back", "a.tho-back", ".in-tools a.back", 'aria-label^="Quay lại"', 'aria-label^="Về "', "^←"):
        assert sel in js
    # Nhớ trang trong tab, bỏ qua form tạo mới/sửa vừa gửi, không có trang trước thì đi theo href cũ.
    assert "sessionStorage" in js and "/(moi|sua)$/" in js
    assert "if (!to || to === a.pathname + a.search) return;" in js


def test_pages_include_back_nav(client, make_user):
    from conftest import PASSWORD
    make_user("gd_back", "director")
    client.post("/dang-nhap", data={"username": "gd_back", "password": PASSWORD})
    html = client.get("/don-ban").text
    assert "/static/back-nav.js?v=" in html
