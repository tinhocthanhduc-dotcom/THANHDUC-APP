"""Đợt 1 nâng cấp Đăng web (TUẤN 02/11/2026 "làm đợt 1"): máy đăng web chạy chung lệnh cập nhật, Codex hỏi việc dày hơn,
bảng sức khỏe Codex, và mở màn Sửa ảnh / Sửa bằng AI thì lấy lại đúng mục đó từ web."""
import pathlib
from datetime import timedelta

from dang_web import hang_doi as hd
from test_codex_dang_web import _khoa
from test_dang_web_man import _may, _wp_co_hang, goc  # noqa: F401  (fixture)
from test_web import web_login  # noqa: F401  (fixture)

GOC_REPO = pathlib.Path(__file__).parents[1]


def test_may_dang_web_nam_trong_lenh_cap_nhat_thuong_le():
    chinh = (GOC_REPO / "deploy/docker-compose.yml").read_text()
    may = chinh[chinh.index("\n  dang-web-may:"):chinh.index("\n  caddy:")]
    assert "profiles" not in may  # up -d --build thường lệ dựng lại và bật máy này
    assert 'command: ["cho-viec"]' in may and "restart: unless-stopped" in may and ":/data" in may
    assert "dockerfile: dang_web/Dockerfile" in may
    web = (GOC_REPO / "deploy/docker-compose.web.yml").read_text()
    assert "\n  dang-web:\n" in web and 'profiles: ["web"]' in web and "service: dang-web-may" in web
    assert "\n  dang-web-may:" not in web


def _song(goc, co_wp=True):
    hd.ghi_nhip(goc, {"ai": None, "co_wp": co_wp, "dang_lam": None, "web": "https://thanhduc.vn"})


def test_mo_sua_anh_lay_lai_dung_san_pham_do_tu_web(client, web_login, goc):
    web_login("ban.hang", "sales")
    wp = _wp_co_hang()
    may = _may(goc, wp)
    may.lam_moi_web()
    # Máy tắt: không xin lấy lại, màn dùng bản đã có.
    r = client.get("/dang-web/sua-anh?id=1")
    assert r.status_code == 200 and "Đang lấy bản mới nhất" not in r.text
    assert not hd.dang_cho_lam_moi_mot(goc, "san_pham", 1)

    _song(goc)
    r = client.get("/dang-web/sua-anh?id=1")
    assert "Đang lấy bản mới nhất" in r.text and "/dang-web/lay-lai?loai=sp&amp;id=1" in r.text
    assert hd.dang_cho_lam_moi_mot(goc, "san_pham", 1)
    assert client.get("/dang-web/lay-lai?loai=sp&id=1").json() == {"cho": True}

    # Trên web anh vừa đổi ảnh: máy lấy lại đúng sản phẩm đó, danh sách cả loạt không đổi gì khác.
    moi = dict(wp.tat_ca[0], name="Hộp mực Canon 326 (mới)", images=[{"id": 9, "src": "https://thanhduc.vn/wp-content/uploads/moi.jpg"}])
    wp.lay_sp_theo_ids = lambda ids: [moi] if 1 in ids else []
    wp.tat_ca = []  # nếu lỡ lấy cả loạt thì sản phẩm 2 sẽ mất: test bắt được
    for loai, web_id in hd.lay_yeu_cau_lam_moi_mot(goc):
        may.lam_moi_mot(loai, web_id)
        hd.xong_lam_moi_mot(goc, loai, web_id)
    assert client.get("/dang-web/lay-lai?loai=sp&id=1").json() == {"cho": False}
    ds = hd.doc_json(hd.file_sp_web(goc), {})["ds"]
    assert [x["id"] for x in ds] == [1, 2]
    assert ds[0]["ten"] == "Hộp mực Canon 326 (mới)" and ds[0]["anhs"] == [{"id": 9, "src": "https://thanhduc.vn/wp-content/uploads/moi.jpg"}]
    assert hd.moi_lay(ds[0]) and not hd.moi_lay(ds[1])

    # Vừa lấy xong thì mở lại màn không xin lấy lại nữa (không lặp vô tận).
    r = client.get("/dang-web/sua-anh?id=1")
    assert "Đang lấy bản mới nhất" not in r.text and "uploads/moi.jpg" in r.text
    assert not hd.dang_cho_lam_moi_mot(goc, "san_pham", 1)


def test_bai_viet_cu_chua_co_anh_thi_lay_lai_thay_vi_khoa_nut(client, web_login, goc):
    web_login("ban.hang", "sales")
    wp = _wp_co_hang()
    wp.anh_media = {77: {"src": "https://thanhduc.vn/wp-content/uploads/dd.jpg", "nho": "https://thanhduc.vn/wp-content/uploads/dd-300.jpg"}}
    may = _may(goc, wp)
    may.lam_moi_web()
    # Giả lập danh sách lấy từ bản app cũ (chưa có anhs_bai) như lỗi anh gặp hôm nay.
    d = hd.doc_json(hd.file_bai_web(goc), {})
    for it in d["ds"]:
        it.pop("anhs_bai", None)
    hd.ghi_json(hd.file_bai_web(goc), d)
    _song(goc)
    r = client.get("/dang-web/sua-anh?loai=bai&id=51")
    assert "Đang lấy bản mới nhất" in r.text and "Danh sách web cũ chưa có ảnh" not in r.text
    wp.lay_bai_theo_ids = lambda ids: [b for b in wp.bai_web if b["id"] in ids]
    for loai, web_id in hd.lay_yeu_cau_lam_moi_mot(goc):
        may.lam_moi_mot(loai, web_id)
        hd.xong_lam_moi_mot(goc, loai, web_id)
    it = next(x for x in hd.doc_json(hd.file_bai_web(goc), {})["ds"] if x["id"] == 51)
    assert it["anhs_bai"] == [] and it["anh_goc"].endswith("dd.jpg")
    r = client.get("/dang-web/sua-anh?loai=bai&id=51")
    assert "Danh sách web cũ chưa có ảnh" not in r.text and "data-sa-submit" in r.text


def test_sua_bang_ai_cung_lay_lai_va_may_chua_noi_web_thi_thoi(client, web_login, goc):
    web_login("ban.hang", "sales")
    may = _may(goc, _wp_co_hang())
    may.lam_moi_web()
    _song(goc, co_wp=False)
    assert "Đang lấy bản mới nhất" not in client.get("/dang-web/sua-ai?loai=sp&id=1").text
    _song(goc)
    assert "Đang lấy bản mới nhất" in client.get("/dang-web/sua-ai?loai=sp&id=1").text
    assert hd.lay_yeu_cau_lam_moi_mot(goc) == [("san_pham", 1)]


def test_bang_suc_khoe_codex(client, db, web_login, make_user, goc, monkeypatch):
    web_login("giam.doc", "director")
    hw, _ = _khoa(db, make_user)
    assert "Sức khỏe Codex" not in client.get("/dang-web").text  # chưa từng chạy Codex thì không hiện
    r = client.post("/ai/dang-web/tinh-trang", headers=hw, json={"dang_nhap": True, "ban": "codex-cli 0.80.0"})
    assert r.status_code == 200
    trang = client.get("/dang-web").text
    assert "Sức khỏe Codex" in trang and "còn đăng nhập" in trang and "Đang chạy" in trang and "dw-codex-dot ok" in trang

    client.post("/ai/dang-web/tinh-trang", headers=hw, json={"dang_nhap": False})
    trang = client.get("/dang-web").text
    assert "Codex hết đăng nhập ChatGPT" in trang and "codex login --device-auth" in trang and "dw-codex-dot bad" in trang
    sk = hd.suc_khoe_codex(goc)
    assert sk["ban"] == "codex-cli 0.80.0" and sk["dang_nhap"] is False  # báo sau không xóa phiên bản đã biết

    hd.ghi_nhip_codex(goc, loi="codex thoát mã 1: usage limit")
    assert "usage limit" in client.get("/dang-web").text

    # Quá 30 phút không hỏi việc: báo đỏ "không chạy".
    tre = hd.bay_gio() + timedelta(minutes=31)
    monkeypatch.setattr(hd, "bay_gio", lambda: tre)
    trang = client.get("/dang-web").text
    assert "Codex trên máy chủ không chạy" in trang and "codex-dang-web.log" in trang


def test_codex_hoi_viec_moi_30_giay_trong_mot_lan_cron(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("codex_dang_web_dot1", GOC_REPO / "scripts" / "codex_dang_web.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.HOI_GIAY == 30 and m.CHAY_GIAY == 270
    goi, ngu = [], []
    dong_ho = iter(range(0, 10_000, 30))
    monkeypatch.setattr(m.time, "monotonic", lambda: next(dong_ho))
    monkeypatch.setattr(m.time, "sleep", lambda s: ngu.append(s))
    monkeypatch.setattr(m, "call", lambda method, path, body=None: goi.append((method, path, body)) or [])
    monkeypatch.setattr(m, "tinh_trang", lambda: {"dang_nhap": True})
    monkeypatch.setattr(m, "HOME", pathlib.Path(__import__("tempfile").mkdtemp()))
    m.main()
    assert goi[0] == ("POST", "/ai/dang-web/tinh-trang", {"dang_nhap": True})
    so_lan = sum(1 for g in goi if g[1] == "/ai/dang-web/viec")
    assert 5 <= so_lan <= 9 and set(ngu) == {30}  # ~4,5 phút, mỗi 30 giây một lần, xong trước lần cron sau
