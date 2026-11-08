"""Đợt 4 rút gọn đăng web (TUẤN 08/11/2026): cổng n8n chỉ đọc, lịch tự soạn lấy hàng mới nhập, Duyệt và đăng."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app import ai_agent
from app import n8n_api
from app.config import get_settings
from dang_web import hang_doi as hd
from dang_web.cau_hinh import CauHinh
from dang_web.du_lieu import SanPham
from dang_web.may_chay import MayDangWeb


@pytest.fixture
def goc(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "web_dir", str(tmp_path))
    return tmp_path


# ---------- cổng n8n ----------
def test_cong_n8n_can_khoa_rieng_chi_doc(client, db, make_user, goc):
    director = make_user("giam.doc", "director")
    assert client.get("/n8n/viec-cho").status_code == 401
    zalo = ai_agent.create_key(db, director, "tro-ly-zalo")
    r = client.get("/n8n/viec-cho", headers={"Authorization": "Bearer " + zalo})
    assert r.status_code == 403 and r.json()["error"] == "AI_SCOPE"  # khóa trợ lý khác không dùng được
    raw = ai_agent.create_key(db, director, "n8n")
    h = {"Authorization": "Bearer " + raw}
    # đi qua Cloudflare (Internet) thì khóa đúng cũng bị chặn
    assert client.get("/n8n/viec-cho", headers={**h, "cf-ray": "x"}).status_code == 403
    r = client.get("/n8n/viec-cho", headers=h)
    assert r.status_code == 200 and r.json()["so_viec"] == 0 and r.json()["co_tin"] is False
    # chỉ có GET: n8n không duyệt, không đăng được
    assert client.post("/n8n/viec-cho", headers=h).status_code == 405
    ai_agent.set_paused(db, director, True, "n8n")
    assert client.get("/n8n/viec-cho", headers=h).status_code == 423


def test_tom_tat_viec_cho_kem_link(goc, monkeypatch):
    a = hd.tao_viec(goc, "san_pham", {"ma": "TD-0412", "ten": "Hộp mực Canon 337"})
    b = hd.tao_viec(goc, "san_pham", {"ma": "TD-0415", "ten": "TD-0415"})
    c = hd.tao_viec(goc, "san_pham", {"ma": "TD-0390", "ten": "Mực HP 107A"})
    d = hd.tao_viec(goc, "san_pham", {"ma": "TD-0500", "ten": "Chờ Codex", "cach_viet": "codex"})
    m = hd.tao_viec(goc, "mang_xa_hoi", {"ten": "Bài Facebook"})
    hd.sua(goc, a, lambda x: x.update(trang_thai=hd.XEM_TRUOC))
    hd.sua(goc, b, lambda x: x.update(trang_thai=hd.XEM_TRUOC))
    hd.sua(goc, c, lambda x: x.update(trang_thai=hd.LOI))
    hd.sua(goc, d, lambda x: x.update(trang_thai=hd.CHO_DAN, lenh_chatgpt="x"))
    hd.sua(goc, m, lambda x: x.update(trang_thai=hd.XEM_TRUOC))
    sau = hd.bay_gio() + timedelta(days=2)
    kq = n8n_api.summary(goc, "https://quanlythanhduc.com", now=sau)
    assert kq["so_viec"] == 3 and kq["cho_xem_truoc"] == 2 and kq["loi"] == 1 and kq["cho_dan"] == 0
    assert kq["co_tin"] and kq["tin"].startswith("Đăng web: 3 việc đang chờ anh")
    assert f"TD-0412 · Hộp mực Canon 337 (chờ hơn 1 ngày): https://quanlythanhduc.com/dang-web/viec/{a}" in kq["tin"]
    assert f"- TD-0415 (chờ hơn 1 ngày): https://quanlythanhduc.com/dang-web/viec/{b}" in kq["tin"]
    assert "Máy đăng web bị lỗi (1)" in kq["tin"] and "Bài Facebook" not in kq["tin"] and "Chờ Codex" not in kq["tin"]
    assert len(kq["tin"]) <= 1900
    # việc chờ máy mà máy đăng web tắt: nhắc luôn
    hd.tao_viec(goc, "san_pham", {"ma": "TD-0600", "ten": "x"})
    assert n8n_api.summary(goc, "https://q")["may_tat"] is True
    hd.ghi_nhip(goc, {})
    assert n8n_api.summary(goc, "https://q")["may_tat"] is False


def test_tom_tat_gioi_han_5_dong(goc):
    for i in range(7):
        v = hd.tao_viec(goc, "san_pham", {"ma": f"TD-07{i:02d}", "ten": f"Hàng {i}"})
        hd.sua(goc, v, lambda x: x.update(trang_thai=hd.XEM_TRUOC))
    kq = n8n_api.summary(goc, "https://q")
    assert kq["cho_xem_truoc"] == 7 and "- và 2 việc khác" in kq["tin"] and kq["tin"].count("/dang-web/viec/") == 5


# ---------- lịch tự soạn lấy hàng mới nhập ----------
def _sp(code, name):
    return SanPham(code=code, name=name, group_name="Hộp mực", unit="cái", sale_price=100_000, warranty_months=0, ton=Decimal("2"))


def _may(goc, moi):
    ch = CauHinh(thu_muc=goc, tach_nen=False, anh_kich_thuoc=400)
    hang = {"TD-0100": _sp("TD-0100", "Hộp mực Canon 326"), "TD-0200": _sp("TD-0200", "Drum Brother DR-2385"),
            "TD-0300": _sp("TD-0300", "Mực HP 107A"), "TD-0400": _sp("TD-0400", "Hộp mực Canon 337"),
            "TD-0500": _sp("TD-0500", "Máy in Brother HL-L2321D")}
    return MayDangWeb(ch, ket_noi_db=lambda c: hang.get(c), hang_moi=lambda: moi)


def test_lich_uu_tien_hang_moi_nhap_chua_co_tren_web(goc, monkeypatch):
    (goc / "anh-goc").mkdir()
    for c in ("TD-0100", "TD-0200", "TD-0300", "TD-0400", "TD-0500", "TD-0999"):
        (goc / "anh-goc" / f"{c}.jpg").write_bytes(b"x")
    # trên web: TD-0300 trùng SKU, "Hộp mực Canon 337" gần giống TD-0400; TD-0100 đã ghép
    hd.ghi_json(hd.file_sp_web(goc), {"luc": "x", "ds": [
        {"id": 1, "ten": "Mực in HP 107A", "sku": "TD-0300"},
        {"id": 2, "ten": "Hộp mực Canon 337 chính hãng", "sku": ""},
        {"id": 3, "ten": "Hộp mực Canon 326", "sku": ""}]})
    hd.ghep_ma(goc, "TD-0100", 3)
    # hàng mới nhập (mới nhất trước): TD-0500 chưa có ảnh? có ảnh; TD-0400 gần giống web; TD-0300 trùng SKU
    may = _may(goc, [("TD-0500", "Máy in Brother HL-L2321D"), ("TD-0400", "Hộp mực Canon 337"), ("TD-0300", "Mực HP 107A")])
    assert may.chon_ma_lich(5, set()) == [("TD-0500", True), ("TD-0200", False)]  # TD-0999 ngừng dùng: bỏ
    # anh bấm "Không phải" cho TD-0400 → thành chưa có trên web, đứng ngay sau TD-0500 vì là hàng mới nhập
    hd.xac_nhan(goc, "TD-0400", 2, dung=False)
    assert may.chon_ma_lich(2, set()) == [("TD-0500", True), ("TD-0400", True)]
    assert may.chon_ma_lich(2, {"TD-0500"}) == [("TD-0400", True), ("TD-0200", False)]
    # lịch tạo việc sản phẩm, không còn bài viết
    hd.them_chu_de(goc, "Nạp mực tận nơi có hại máy không")
    hd.ghi_lich(goc, {"bat": True, "gio": "07:00", "so_sp": 2, "so_bai": 1})
    monkeypatch.setattr(hd, "bay_gio", lambda: datetime(2026, 11, 9, 7, 30, tzinfo=hd.GIO_VN))  # thứ 2
    hd.ghi_nhip_codex(goc)
    assert may.kiem_lich() == 2
    ds = hd.ds(goc)
    assert {v["loai"] for v in ds} == {"san_pham"} and {v["ma"]: v["hang_moi"] for v in ds} == {"TD-0500": True, "TD-0400": True}
    assert all(v["nguon"] == "lich" and v["trang_thai"] == hd.CHO_SOAN for v in ds)


def test_lich_chua_lay_danh_sach_web_van_chon_ma_co_anh(goc):
    (goc / "anh-goc").mkdir()
    for c in ("TD-0100", "TD-0200"):
        (goc / "anh-goc" / f"{c}.jpg").write_bytes(b"x")
    hd.sua_trang_thai(goc, lambda tt: tt["san_pham"].__setitem__("TD-0100", {"id": 5}))
    may = _may(goc, [("TD-0200", "Drum Brother DR-2385")])
    assert may.chon_ma_lich(3, set()) == [("TD-0200", True)]


def test_lich_doc_hang_moi_loi_thi_van_soan_ma_co_anh(goc):
    (goc / "anh-goc").mkdir()
    (goc / "anh-goc" / "TD-0200.jpg").write_bytes(b"x")

    def hong():
        raise RuntimeError("db down")

    may = MayDangWeb(CauHinh(thu_muc=goc), ket_noi_db=lambda c: _sp(c, "Drum") if c == "TD-0200" else None, hang_moi=hong)
    assert may.chon_ma_lich(2, set()) == [("TD-0200", False)]


def test_cau_hang_moi_nhap_chay_duoc_tren_database(db):
    """Câu SQL của máy đăng web (psycopg, chỉ đọc) chạy đúng trên lược đồ thật."""
    from conftest import TEST_URL
    from dang_web.du_lieu import hang_moi_nhap, ket_noi

    with ket_noi(TEST_URL) as conn:
        assert hang_moi_nhap(conn, 30) == []
