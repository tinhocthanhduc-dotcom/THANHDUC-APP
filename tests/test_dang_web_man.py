"""Màn Đăng web trong app + máy đăng web chạy nền (TUẤN duyệt mẫu 01/11/2026)."""
import io
import json
import uuid
from decimal import Decimal

import pytest
from PIL import Image, ImageDraw

import app.web.dang_web as man
from app.models import Product
from dang_web import hang_doi as hd
from dang_web.cau_hinh import CauHinh
from dang_web.du_lieu import SanPham
from dang_web.may_chay import MayDangWeb
from dang_web.viet_bai import NguoiVietMau
from test_web import csrf_of, web_login  # noqa: F401  (fixture)


def _jpg():
    im = Image.new("RGB", (500, 400), (238, 236, 230))
    ImageDraw.Draw(im).rectangle((150, 100, 350, 300), fill=(20, 20, 20))
    b = io.BytesIO()
    im.save(b, "JPEG")
    return b.getvalue()


class _WP:
    """Web giả: ghi lại mọi lần tạo/sửa; tìm trùng trả về theo self.giong."""

    def __init__(self):
        self.tao, self.sua, self.giong, self.sku = [], [], [], None
        self.media = 0

    def tim_sp_theo_sku(self, sku):
        return self.sku

    def _json(self, method, duong, **kw):
        return list(self.giong)

    def danh_muc_sp(self):
        return [{"id": 7, "name": "Hộp mực", "slug": "hop-muc"}]

    def chuyen_muc_bai(self):
        return list(getattr(self, "cm_bai", []))

    def tai_anh(self, data, ten, mime, alt=""):
        self.media += 1
        return {"id": 900 + self.media, "source_url": f"https://thanhduc.vn/wp-content/uploads/{ten}"}

    def tao_sp(self, payload):
        self.tao.append(payload)
        return {"id": 101, "permalink": "https://thanhduc.vn/?p=101", "status": "draft"}

    def sua_sp(self, sp_id, payload):
        self.sua.append((sp_id, payload))
        return {"id": sp_id, "permalink": "https://thanhduc.vn/hop-muc", "status": "publish"}

    def tao_bai(self, payload):
        self.tao.append(payload)
        return {"id": 202, "link": "https://thanhduc.vn/?p=202", "status": "draft"}

    def sua_bai(self, bai_id, payload):
        self.sua.append((bai_id, payload))
        return {"id": bai_id, "link": "https://thanhduc.vn/bai", "status": "publish"}

    def lay_sp_theo_ids(self, ids):
        return [{"id": i, "name": "Hộp mực Canon 326 (web)", "status": "publish", "permalink": "https://thanhduc.vn/x"} for i in ids]

    def lay_bai_theo_ids(self, ids):
        return []

    def tat_ca_sp(self):
        return list(getattr(self, "tat_ca", []))

    def tat_ca_bai(self):
        return list(getattr(self, "bai_web", []))

    def lay_sp(self, sp_id):
        return next(x for x in self.tat_ca if x["id"] == sp_id)

    def lay_bai(self, bai_id):
        return next(x for x in self.bai_web if x["id"] == bai_id)

    def lay_anh(self, media_id):
        return {"id": media_id, "source_url": "https://thanhduc.vn/wp-content/uploads/cu.jpg"}

    def tai_ve(self, url):
        self.da_tai = getattr(self, "da_tai", []) + [url]
        return _jpg()

    def doi_danh_muc_sp(self, cap):
        self.doi_dm = getattr(self, "doi_dm", []) + list(cap)
        return len(cap)

    def xoa_anh(self, media_id):
        self.xoa = getattr(self, "xoa", []) + [media_id]

    def anh_con_dung(self, ten_file, bo_sp_id):
        return ten_file in getattr(self, "con_dung", set())

    def doi_chuyen_muc_bai(self, bai_id, dm):
        self.doi_dm = getattr(self, "doi_dm", []) + [(bai_id, dm)]


class _ChatGPTGia:
    """Người viết giả trả JSON cố định cho bài viết."""

    model = "AI giả"

    def viet_bai(self, chu_de, ghi_chu="", hotline=""):
        from dang_web.viet_bai import KetQuaAI

        return KetQuaAI(du_lieu={"tieu_de": chu_de, "slug": "bai-test", "tom_tat": "Tóm tắt", "noi_dung_html": "<p>Nội dung</p>",
                                 "seo_title": "SEO", "seo_description": "Mô tả", "tu_khoa_chinh": "kẹt giấy", "alt_anh": "ảnh",
                                 "can_kiem_tra": []}, model=self.model)


@pytest.fixture
def goc(tmp_path, monkeypatch):
    monkeypatch.setattr(man, "goc", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def san_pham(db):
    db.add(Product(code="TD-0123", name="Hộp mực Canon 326", group_name="Hộp mực", sale_price=355_000))
    db.commit()


def _may(goc, wp, nguoi_viet=None):
    ch = CauHinh(thu_muc=goc, tach_nen=False, anh_kich_thuoc=400, wp_user="u", wp_app_password="p")
    sp = SanPham(code="TD-0123", name="Hộp mực Canon 326", group_name="Hộp mực", unit="cái", sale_price=355_000,
                 warranty_months=0, ton=Decimal("3"))
    return MayDangWeb(ch, wp=wp, nguoi_viet=nguoi_viet, ket_noi_db=lambda code: sp if code == "TD-0123" else None)


def _soan_sp(client, csrf, **kw):
    data = dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="san_pham", ma="TD-0123", cach_viet="mau", ghi_chu="", wp_id="")
    data.update(kw)
    return client.post("/dang-web/soan", data=data, files=[("anh", ("a.jpg", _jpg(), "image/jpeg"))], follow_redirects=False)


def test_kinh_doanh_vao_duoc_tu_soan_chi_giam_doc(client, web_login, goc):
    web_login("kho", "warehouse")
    assert client.get("/dang-web", follow_redirects=False).status_code == 403
    csrf = web_login("ban.hang", "sales")
    r = client.get("/dang-web")
    assert r.status_code == 200 and "Máy đăng web đang tắt" in r.text and 'action="/dang-web/lich"' not in r.text
    r = client.post("/dang-web/lich", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), bat="1", gio="07:00"), follow_redirects=False)
    assert r.status_code == 403 and hd.doc_lich(goc).get("bat") is not True
    web_login("giam.doc", "director")
    r = client.get("/dang-web")
    assert r.status_code == 200 and "Đăng web" in r.text and 'action="/dang-web/lich"' in r.text


def test_soan_san_pham_khong_anh_van_duoc_ma_phai_dang_dung(client, web_login, goc, san_pham):
    csrf = web_login("ban.hang", "sales")
    r = _soan_sp(client, csrf, ma="TD-9999")
    assert "Chọn mã hàng đang dùng" in r.text
    assert hd.ds(goc) == []
    r = client.post("/dang-web/soan", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="san_pham", ma="TD-0123", cach_viet="mau"),
                    follow_redirects=False)
    assert r.status_code == 303
    v = hd.ds(goc)[0]
    assert v["ma"] == "TD-0123" and not v.get("anh_goc")

    wp = _WP()
    may = _may(goc, wp)
    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.XEM_TRUOC and v["anh_web"] == []
    assert "Chưa có ảnh" in client.get(f"/dang-web/viec/{v['id']}").text
    client.post(f"/dang-web/viec/{v['id']}", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), hanh_dong="dang", ten_web="Hộp mực Canon 326",
                                                      mo_ta_ngan_html="<p>Ngắn</p>", mo_ta_html="<p>Dài</p>", gia="360.000"))
    assert may.lam_mot_viec()
    assert hd.doc(goc, v["id"])["trang_thai"] == hd.DA_DANG
    p = wp.tao[0]
    assert p["status"] == "draft" and "images" not in p and wp.media == 0


def test_tron_vong_soan_xem_truoc_sua_roi_dang_nhap(client, web_login, goc, san_pham):
    csrf = web_login("giam.doc", "director")
    r = _soan_sp(client, csrf)
    assert r.status_code == 303
    viec_id = r.headers["location"].rsplit("/", 1)[1]
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.CHO_SOAN and v["anh_goc"] == ["anh-1.jpg"] and v["ma"] == "TD-0123"
    assert "Đang chờ máy đăng web" in client.get(r.headers["location"]).text

    wp = _WP()
    may = _may(goc, wp)
    assert may.lam_mot_viec()
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.XEM_TRUOC, v.get("loi")
    assert v["gia"] == "355000" and v["danh_muc"] == "Hộp mực" and v["anh_web"]
    page = client.get(f"/dang-web/viec/{viec_id}")
    assert "Đăng nháp lên thanhduc.vn" in page.text and 'name="ten_web"' in page.text
    assert client.get(f"/dang-web/anh-web/{v['anh_web'][0]}").status_code == 200

    form = dict(csrf=csrf_of(page.text), form_key=str(uuid.uuid4()), hanh_dong="dang", ten_web="Hộp mực Canon 326 chính hãng",
                mo_ta_ngan_html="<p>Ngắn</p>", mo_ta_html="<p>Dài</p>", seo_title="SEO", seo_description="Mô tả", tu_khoa_chinh="canon 326",
                alt_anh="Hộp mực", gia="360.000", danh_muc="Hộp mực")
    r = client.post(f"/dang-web/viec/{viec_id}", data=form, follow_redirects=False)
    assert r.status_code == 303
    assert hd.doc(goc, viec_id)["trang_thai"] == hd.CHO_DANG

    assert may.lam_mot_viec()
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.DA_DANG, v.get("loi")
    p = wp.tao[0]
    assert p["status"] == "draft" and p["name"] == "Hộp mực Canon 326 chính hãng" and p["regular_price"] == "360000"
    assert p["sku"] == "TD-0123" and p["categories"] == [{"id": 7}] and p["images"]
    assert p["description"].startswith("<p>Dài</p><figure class=\"td-anh\"><img src=\"https://thanhduc.vn/wp-content/uploads/")
    assert hd.doc_trang_thai(goc)["san_pham"]["TD-0123"]["id"] == 101

    may.lam_moi_web()
    assert client.get("/dang-web?tab=da-dang", follow_redirects=False).headers["location"].startswith("/dang-web?tab=tren-web")


def test_co_the_trung_bo_qua_thi_ghi_nghi_trung(client, web_login, goc, san_pham):
    csrf = web_login("giam.doc", "director")
    viec_id = _soan_sp(client, csrf).headers["location"].rsplit("/", 1)[1]
    wp = _WP()
    wp.giong = [{"id": 55, "name": "Hộp mực Canon 326 cũ", "status": "publish", "permalink": "https://thanhduc.vn/cu"}]
    _may(goc, wp).lam_mot_viec()
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.CO_THE_TRUNG and v["trung"][0]["id"] == 55 and not wp.tao
    page = client.get(f"/dang-web/viec/{viec_id}")
    assert "gần giống" in page.text and "Không trùng, vẫn soạn" in page.text
    r = client.post(f"/dang-web/viec/{viec_id}", data=dict(csrf=csrf_of(page.text), form_key=str(uuid.uuid4()), hanh_dong="bo_qua_trung"),
                    follow_redirects=False)
    assert r.status_code == 303
    assert hd.doc(goc, viec_id)["trang_thai"] == hd.BO
    assert hd.doc_trang_thai(goc)["nghi_trung"]["TD-0123"] == ["https://thanhduc.vn/cu"]


def test_trung_sku_viet_de_len_san_pham_cu(client, web_login, goc, san_pham):
    csrf = web_login("giam.doc", "director")
    viec_id = _soan_sp(client, csrf).headers["location"].rsplit("/", 1)[1]
    wp = _WP()
    wp.sku = {"id": 77, "name": "Canon 326", "status": "publish", "permalink": "https://thanhduc.vn/c"}
    may = _may(goc, wp)
    may.lam_mot_viec()
    assert hd.doc(goc, viec_id)["trung_sku"] is True
    page = client.get(f"/dang-web/viec/{viec_id}")
    assert "Không trùng, vẫn soạn" not in page.text
    client.post(f"/dang-web/viec/{viec_id}", data=dict(csrf=csrf_of(page.text), form_key=str(uuid.uuid4()), hanh_dong="viet_de", wp_id="77"))
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.CHO_SOAN and v["wp_id"] == 77 and v["viet_lai"]
    may.lam_mot_viec()
    hd.sua(goc, viec_id, lambda x: x.update(trang_thai=hd.CHO_DANG))
    may.lam_mot_viec()
    assert hd.doc(goc, viec_id)["trang_thai"] == hd.DA_DANG
    sp_id, payload = wp.sua[0]
    assert sp_id == 77 and not wp.tao


def test_wordpress_sua_sp_giu_trang_thai_va_sku():
    import httpx

    from dang_web.wordpress import WordPress

    gui = []

    def xu_ly(req):
        gui.append((req.method, req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"id": 77})

    wp = WordPress("https://web.test", "u", "p", client=httpx.Client(transport=httpx.MockTransport(xu_ly)))
    wp.sua_sp(77, {"name": "A", "status": "draft", "sku": "TD-1", "type": "simple"})
    wp.sua_bai(5, {"title": "B", "status": "draft"})
    assert gui[0][:2] == ("PUT", "/wp-json/wc/v3/products/77") and gui[0][2] == {"name": "A"}
    assert gui[1][:2] == ("POST", "/wp-json/wp/v2/posts/5") and gui[1][2] == {"title": "B"}


def test_chatgpt_web_dan_tra_loi(client, web_login, goc, san_pham):
    csrf = web_login("giam.doc", "director")
    viec_id = _soan_sp(client, csrf, cach_viet="chatgpt_web").headers["location"].rsplit("/", 1)[1]
    ch = CauHinh(thu_muc=goc, tach_nen=False, anh_kich_thuoc=400)  # không có web: bỏ qua kiểm trùng
    sp = SanPham(code="TD-0123", name="Hộp mực Canon 326", group_name="Hộp mực", unit="cái", sale_price=355_000,
                 warranty_months=0, ton=Decimal("3"))
    may = MayDangWeb(ch, ket_noi_db=lambda code: sp)
    may.lam_mot_viec()
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.CHO_DAN and "TD-0123" in v["lenh_chatgpt"]
    page = client.get(f"/dang-web/viec/{viec_id}")
    assert "Chép câu lệnh" in page.text
    tra_loi = "Đây là kết quả:\n```json\n" + json.dumps({
        "ten_web": "Hộp mực Canon 326", "slug": "hop-muc-canon-326", "mo_ta_ngan_html": "<p>a</p>", "mo_ta_html": "<p>b</p>",
        "seo_title": "t", "seo_description": "d", "tu_khoa_chinh": "k", "alt_anh": "a", "can_kiem_tra": []}, ensure_ascii=False) + "\n```"
    client.post(f"/dang-web/viec/{viec_id}", data=dict(csrf=csrf_of(page.text), form_key=str(uuid.uuid4()), hanh_dong="dan", tra_loi=tra_loi))
    assert hd.doc(goc, viec_id)["trang_thai"] == hd.CHO_SOAN
    may.lam_mot_viec()
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.XEM_TRUOC and v["noi_dung"]["ten_web"] == "Hộp mực Canon 326"


def test_bai_viet_va_chu_de(client, web_login, goc):
    csrf = web_login("giam.doc", "director")
    client.post("/dang-web/chu-de", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), chu_de="Canon 2900 kẹt giấy: cách xử lý"))
    assert hd.doc_chu_de(goc) == ["Canon 2900 kẹt giấy: cách xử lý"]
    r = client.post("/dang-web/soan", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="bai_viet", chu_de="Canon 2900 kẹt giấy: cách xử lý",
                                                cach_viet="tu_dong"), follow_redirects=False)
    viec_id = r.headers["location"].rsplit("/", 1)[1]
    assert "Đang làm" in client.get("/dang-web?tab=bai-viet").text
    wp = _WP()
    may = _may(goc, wp, nguoi_viet=_ChatGPTGia())
    may.lam_mot_viec()
    assert hd.doc(goc, viec_id)["trang_thai"] == hd.XEM_TRUOC
    page = client.get(f"/dang-web/viec/{viec_id}")
    client.post(f"/dang-web/viec/{viec_id}", data=dict(csrf=csrf_of(page.text), form_key=str(uuid.uuid4()), hanh_dong="dang",
                                                      tieu_de="Canon 2900 kẹt giấy", tom_tat="", noi_dung_html="<p>x</p>"))
    may.lam_mot_viec()
    assert hd.doc(goc, viec_id)["trang_thai"] == hd.DA_DANG
    assert wp.tao[0]["title"] == "Canon 2900 kẹt giấy" and wp.tao[0]["content"] == "<p>x</p>"
    assert "Đã đăng" in client.get("/dang-web?tab=bai-viet").text


def test_loi_thi_thu_lai_va_phuc_hoi_khi_dung_giua_chung(goc):
    vid = hd.tao_viec(goc, "san_pham", {"ma": "TD-0001", "cach_viet": "mau"})
    may = _may(goc, _WP())
    may.lam_mot_viec()
    v = hd.doc(goc, vid)
    assert v["trang_thai"] == hd.LOI and "TD-0001" in v["loi"] and v["buoc_loi"] == "soan"
    hd.sua(goc, vid, lambda x: x.update(trang_thai=hd.DANG_DANG))
    assert hd.phuc_hoi(goc) == 1
    assert hd.doc(goc, vid)["trang_thai"] == hd.LOI and hd.doc(goc, vid)["buoc_loi"] == "dang"


def test_anh_khong_cho_di_ra_ngoai_thu_muc(client, web_login, goc):
    web_login("giam.doc", "director")
    (goc / "bi-mat.txt").write_text("x")
    assert client.get("/dang-web/anh-web/..%2Fbi-mat.txt").status_code == 404
    assert client.get("/dang-web/anh/khong-hop-le/anh-1.jpg").status_code == 404


def test_lich_tu_soan_tao_viec_mot_lan_moi_ngay(goc, monkeypatch):
    from datetime import datetime

    (goc / "anh-goc").mkdir()
    (goc / "anh-goc" / "TD-0123.jpg").write_bytes(_jpg())
    hd.them_chu_de(goc, "Nạp mực tận nơi có hại máy không")
    hd.ghi_lich(goc, {"bat": True, "gio": "07:00", "so_sp": 2, "so_bai": 1})
    monkeypatch.setattr(hd, "bay_gio", lambda: datetime(2026, 11, 2, 7, 30, tzinfo=hd.GIO_VN))  # thứ 2
    may = _may(goc, _WP())
    assert may.kiem_lich() == 1  # chưa có khóa AI: chỉ soạn sản phẩm bằng mẫu, không tự viết bài
    assert may.kiem_lich() == 0
    v = hd.ds(goc)[0]
    assert v["ma"] == "TD-0123" and v["cach_viet"] == "mau" and v["nguon"] == "lich"


# ---------- tab Hàng hóa: so Hàng hóa trong app với sản phẩm trên web (TUẤN duyệt màn 8 / D5) ----------
@pytest.fixture
def nhieu_hang(db):
    db.add_all([
        Product(code="TD-0123", name="Hộp mực Canon 326", group_name="Hộp mực", sale_price=355_000),
        Product(code="TD-0215", name="Drum Brother DR-2385", group_name="Linh kiện", sale_price=280_000),
        Product(code="TD-0341", name="Khay giấy HP M402", group_name="Linh kiện", sale_price=500_000),
        Product(code="TD-0410", name="Mực Epson 003 đen", group_name="Mực in", sale_price=120_000),
    ])
    db.commit()


def test_so_khop_hang_sku_giong_va_khac_mon():
    from dang_web.so_ten import so_khop_hang

    web = [{"id": 1, "ten": "Hộp mực Canon 326", "sku": "TD-0123"},
           {"id": 2, "ten": "Cụm trống Brother DR2385", "sku": ""},
           {"id": 3, "ten": "Bao lụa sấy HP M402", "sku": ""},
           {"id": 4, "ten": "Hộp mực Canon 326 bản cũ", "sku": "TD-0999"}]
    kq = so_khop_hang([("TD-0123", "Hộp mực Canon 326"), ("TD-0215", "Drum Brother DR-2385"),
                       ("TD-0341", "Khay giấy HP M402"), ("TD-0410", "Mực Epson 003 đen")], web)
    assert kq["TD-0123"]["kieu"] == "sku" and kq["TD-0123"]["web"][0]["id"] == 1
    assert kq["TD-0215"]["kieu"] == "giong" and kq["TD-0215"]["web"][0]["id"] == 2
    assert "TD-0341" not in kq  # cùng mã máy M402 nhưng khay giấy khác bao lụa
    assert "TD-0410" not in kq


DM_SP = [{"id": 1, "name": "Uncategorized", "parent": 0, "slug": "uncategorized"},
         {"id": 7, "name": "Hộp mực", "parent": 0, "slug": "hop-muc"},
         {"id": 8, "name": "Hộp mực Canon", "parent": 7, "slug": "hop-muc-canon"},
         {"id": 9, "name": "Linh kiện &amp; phụ tùng", "parent": 0, "slug": "linh-kien"}]


def _wp_co_hang():
    wp = _WP()
    wp.danh_muc_sp = lambda: list(DM_SP)
    wp.tat_ca = [{"id": 1, "name": "Hộp mực Canon 326", "sku": "TD-0123", "status": "publish", "permalink": "https://thanhduc.vn/a",
                  "categories": [{"id": 8}], "regular_price": "360000", "images": [{"id": 5, "src": "https://thanhduc.vn/wp-content/uploads/a.jpg"}]},
                 {"id": 2, "name": "Cụm trống Brother DR2385", "sku": "", "status": "draft", "permalink": "https://thanhduc.vn/b",
                  "categories": [{"id": 1}], "regular_price": "", "images": []}]
    wp.cm_bai = [{"id": 3, "name": "Tin tức", "parent": 0, "slug": "tin-tuc"}, {"id": 4, "name": "Chưa phân loại", "parent": 0, "slug": "chua-phan-loai"}]
    wp.bai_web = [{"id": 50, "title": {"raw": "Máy in kẹt giấy làm sao"}, "status": "publish", "link": "https://thanhduc.vn/ket-giay",
                   "categories": [4], "date": "2026-10-20T08:00:00", "featured_media": 0, "content": {"raw": "<p>Cũ</p>"},
                   "excerpt": {"raw": "Tóm"}, "slug": "ket-giay"},
                  {"id": 51, "title": {"raw": "Giá nạp mực 2026"}, "status": "draft", "link": "https://thanhduc.vn/?p=51",
                   "categories": [3], "date": "2026-10-21T08:00:00", "featured_media": 77, "content": {"raw": "<p>Giá</p>"},
                   "excerpt": {"raw": ""}, "slug": "gia-nap-muc"}]
    return wp


def test_cay_danh_muc_dem_ca_con_va_chua_phan_loai():
    from dang_web.tren_web import CayDanhMuc, gon_danh_muc

    cay = CayDanhMuc(gon_danh_muc(DM_SP))
    assert cay.duong(8) == "Hộp mực › Hộp mực Canon" and cay.theo_id[9]["ten"] == "Linh kiện & phụ tùng"
    assert cay.lua_chon() == [(7, "Hộp mực"), (8, "Hộp mực › Hộp mực Canon"), (9, "Linh kiện & phụ tùng")]
    dem, chua = cay.dem([{"id": 1, "dm": [8]}, {"id": 2, "dm": [1]}, {"id": 3, "dm": []}, {"id": 4, "dm": [7, 8]}])
    assert dem == {8: 2, 7: 2} and chua == 2
    assert cay.thuoc({"dm": [8]}, 7) and not cay.thuoc({"dm": [9]}, 7)
    vong = CayDanhMuc([{"id": 1, "ten": "A", "cha": 2}, {"id": 2, "ten": "B", "cha": 1}])
    assert vong.to_tien(1) == [1, 2]  # dữ liệu web lỗi vòng lặp thì không treo


def test_tren_web_chi_cap_nhat_khi_bam_nut_loc_danh_muc_va_ma_app(client, web_login, goc, nhieu_hang):
    csrf = web_login("ban.hang", "sales")
    assert client.get("/dang-web?tab=hang-hoa&loc=co-the", follow_redirects=False).headers["location"] == "/dang-web?tab=tren-web&loai=sp&loc=co-the"
    r = client.get("/dang-web?tab=tren-web")
    assert r.status_code == 200 and "Chưa có danh sách trên web" in r.text
    r = client.get("/dang-web?tab=tren-web&loai=sp&loc=chua-len")
    assert "Khay giấy HP M402" in r.text  # trước khi lấy, mọi mã đều chưa lên web
    assert not hd.dang_cho_lam_moi(goc)  # mở tab không tự gọi web

    r = client.post("/dang-web/lam-moi", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="bai"), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/dang-web?tab=tren-web&loai=bai")
    assert hd.dang_cho_lam_moi(goc)

    may = _may(goc, _wp_co_hang())
    assert hd.lay_yeu_cau_lam_moi(goc)
    may.lam_moi_web()
    luu = hd.doc_json(hd.file_sp_web(goc), {})
    assert [x["id"] for x in luu["ds"]] == [1, 2] and luu["ds"][0]["sua"].endswith("post=1&action=edit")
    assert luu["ds"][0]["dm"] == [8] and luu["ds"][0]["so_anh"] == 1 and luu["ds"][0]["gia"] == "360000"
    bai = hd.doc_json(hd.file_bai_web(goc), {})
    assert [x["ten"] for x in bai["ds"]] == ["Máy in kẹt giấy làm sao", "Giá nạp mực 2026"] and bai["ds"][1]["co_anh"]

    r = client.get("/dang-web?tab=tren-web&loai=sp")
    assert "Hộp mực Canon 326" in r.text and "Cụm trống Brother DR2385" in r.text and "Sửa bằng AI" in r.text
    assert "Hộp mực › Hộp mực Canon" in r.text and "Chưa phân loại" in r.text
    r = client.get("/dang-web?tab=tren-web&loai=sp&dm=7")  # danh mục cha gồm cả danh mục con
    assert "Hộp mực Canon 326" in r.text and "Cụm trống" not in r.text
    r = client.get("/dang-web?tab=tren-web&loai=sp&dm=chua")
    assert "Cụm trống Brother DR2385" in r.text and "Hộp mực Canon 326" not in r.text
    r = client.get("/dang-web?tab=tren-web&loai=sp&loc=chua-anh")
    assert "Cụm trống Brother DR2385" in r.text and "Hộp mực Canon 326" not in r.text
    r = client.get("/dang-web?tab=tren-web&loai=sp&loc=khop")
    assert "Hộp mực Canon 326" in r.text and "TD-0123" in r.text and "Cụm trống" not in r.text
    r = client.get("/dang-web?tab=tren-web&loai=bai&dm=chua")
    assert "Máy in kẹt giấy làm sao" in r.text and "Giá nạp mực" not in r.text

    # Mã app chưa lên web / có thể trùng (phần cũ của tab Hàng hóa)
    r = client.get("/dang-web?tab=tren-web&loai=sp&loc=co-the")
    assert "Drum Brother DR-2385" in r.text and "Cụm trống Brother DR2385" in r.text and "Đúng là nó" in r.text
    r = client.get("/dang-web?tab=tren-web&loai=sp&loc=chua-len")
    assert "Khay giấy HP M402" in r.text and "Mực Epson 003 đen" in r.text and "Drum Brother" not in r.text and "Hộp mực Canon 326" not in r.text

    r = client.post("/dang-web/hang-hoa/xac-nhan", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), ma="TD-0215", web_id="2",
                                                            hanh_dong="khong", tab="tren-web", loai="sp", loc="co-the"), follow_redirects=False)
    assert r.status_code == 303 and "loc=co-the" in r.headers["location"] and "tab=tren-web" in r.headers["location"]
    assert "Drum Brother" in client.get("/dang-web?tab=tren-web&loai=sp&loc=chua-len").text
    client.post("/dang-web/hang-hoa/xac-nhan", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), ma="TD-0215", web_id="2", hanh_dong="dung"))
    r = client.get("/dang-web?tab=tren-web&loai=sp&loc=khop")
    assert "Cụm trống Brother DR2385" in r.text and "TD-0215" in r.text
    client.post("/dang-web/hang-hoa/xac-nhan", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), ma="TD-0215", hanh_dong="bo"))
    assert "Drum Brother" in client.get("/dang-web?tab=tren-web&loai=sp&loc=co-the").text

    # Lọc nhóm, còn hàng, tìm không dấu
    r = client.get("/dang-web?tab=tren-web&loai=sp&loc=chua-len&nhom=Linh+ki%E1%BB%87n")
    assert "Khay giấy" in r.text and "Epson" not in r.text
    assert "Khay giấy" in client.get("/dang-web?tab=tren-web&loai=sp&loc=chua-len&q=khay+giay").text
    assert "Khay giấy" not in client.get("/dang-web?tab=tren-web&loai=sp&loc=chua-len&ton=1").text  # chưa có tồn


def test_soan_nhieu_ma_khong_can_anh(client, web_login, goc, nhieu_hang):
    csrf = web_login("ban.hang", "sales")
    (goc / "anh-goc").mkdir()
    (goc / "anh-goc" / "TD-0410.jpg").write_bytes(_jpg())
    r = client.get("/dang-web?tab=tren-web&loai=sp&loc=chua-len")
    assert 'value="TD-0410" form="dw-soan-nhieu"' in r.text and 'value="TD-0341" form="dw-soan-nhieu"' in r.text
    r = client.post("/dang-web/soan-nhieu", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), ma=["TD-0410", "TD-0341"]),
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("da_luu=tu-chon-2")
    viec = hd.ds(goc)
    assert sorted(v["ma"] for v in viec) == ["TD-0341", "TD-0410"]
    client.post("/dang-web/soan-nhieu", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), ma=["TD-0410"]))
    assert len(hd.ds(goc)) == 2  # mã đang có việc thì không tạo thêm
    r = client.post("/dang-web/soan-nhieu", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), ma=[f"TD-{i:04d}" for i in range(11)]))
    assert r.status_code == 400 and "tối đa 10" in r.text


class _AISua:
    """AI giả cho Sửa bằng AI / Xếp danh mục."""

    model = "AI giả"

    def sua_san_pham(self, hien_tai, sp=None, ghi_chu="", hotline=""):
        from dang_web.viet_bai import KetQuaAI

        self.hien_tai, self.sp = hien_tai, sp
        return KetQuaAI(du_lieu={"ten_web": "Cụm trống Brother DR-2385 chính hãng", "slug": "x", "mo_ta_ngan_html": "<p>Ngắn mới</p>",
                                 "mo_ta_html": "<p>Dài mới</p>", "seo_title": "SEO mới", "seo_description": "Mô tả mới",
                                 "tu_khoa_chinh": "drum dr2385", "alt_anh": "drum", "can_kiem_tra": []}, model=self.model)

    def sua_bai(self, hien_tai, ghi_chu="", hotline=""):
        raise AssertionError("không gọi")

    def xep_danh_muc(self, loai, muc, danh_muc):
        from dang_web.viet_bai import KetQuaAI

        self.xep = (loai, muc, danh_muc)
        return KetQuaAI(du_lieu={"ket_qua": [{"id": 2, "danh_muc": "Linh kiện & phụ tùng", "chac": True},
                                             {"id": 99, "danh_muc": "Không có", "chac": True}]}, model=self.model)


def test_sua_bang_ai_san_pham_khong_ma_chi_ghi_phan_da_tick(client, web_login, goc, nhieu_hang):
    csrf = web_login("ban.hang", "sales")
    wp = _wp_co_hang()
    ai = _AISua()
    may = _may(goc, wp, nguoi_viet=ai)
    may.lam_moi_web()
    r = client.get("/dang-web/sua-ai?loai=sp&id=2")
    assert r.status_code == 200 and "Cụm trống Brother DR2385" in r.text and "không có mã TD" in r.text
    assert "Giữ như cũ (chưa phân loại)" in r.text and '<option value="9"' in r.text
    assert client.get("/dang-web/sua-ai?loai=sp&id=404").status_code == 404
    base = dict(csrf=csrf, loai="san_pham", id="2", cach_viet="chatgpt_web", ghi_chu="")
    r = client.post("/dang-web/sua-ai", data=dict(base, form_key=str(uuid.uuid4())))
    assert r.status_code == 400 and "Chọn ít nhất một phần" in r.text and hd.ds(goc) == []
    r = client.post("/dang-web/sua-ai", data=dict(base, form_key=str(uuid.uuid4()), seo="1", danh_muc_id="9"), follow_redirects=False)
    assert r.status_code == 303
    v = hd.ds(goc)[0]
    assert v["kieu"] == "sua_web" and v["wp_id"] == 2 and v["ma"] == "" and v["sua"] == {"noi_dung": False, "seo": True, "anh": False}
    assert v["danh_muc_id"] == 9
    r = client.post("/dang-web/sua-ai", data=dict(base, form_key=str(uuid.uuid4()), noi_dung="1"), follow_redirects=False)
    assert r.headers["location"] == f"/dang-web/viec/{v['id']}" and len(hd.ds(goc)) == 1  # đang có việc thì mở việc đó

    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.XEM_TRUOC, v.get("loi")
    assert v["noi_dung"]["ten_web"] == "Cụm trống Brother DR2385"  # không tick Nội dung thì giữ tên cũ
    assert v["noi_dung"]["seo_title"] == "SEO mới" and ai.sp is None and v["danh_muc"] == "Linh kiện & phụ tùng"
    trang = client.get(f"/dang-web/viec/{v['id']}").text
    assert "SEO mới" in trang
    client.post(f"/dang-web/viec/{v['id']}", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), hanh_dong="dang", ten_web="Cụm trống Brother DR2385",
                                                      seo_title="SEO mới", seo_description="Mô tả mới", tu_khoa_chinh="drum dr2385",
                                                      danh_muc_id="9", gia=""))
    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.DA_DANG, v.get("loi")
    sp_id, payload = wp.sua[0]
    assert sp_id == 2 and set(payload) == {"meta_data", "categories"} and payload["categories"] == [{"id": 9}]
    assert "status" not in payload and "slug" not in payload and "sku" not in payload
    luu = hd.doc_json(hd.file_sp_web(goc), {})
    assert next(x for x in luu["ds"] if x["id"] == 2)["dm"] == [9]  # tab Trên web thấy ngay
    assert "Cụm trống Brother DR2385" in client.get("/dang-web?tab=tren-web&loai=sp&dm=9").text


def test_sua_bang_ai_anh_lay_anh_tren_web_khi_khong_chon_anh_moi(client, web_login, goc, nhieu_hang):
    csrf = web_login("giam.doc", "director")
    wp = _wp_co_hang()
    may = _may(goc, wp, nguoi_viet=_AISua())
    may.lam_moi_web()
    r = client.post("/dang-web/sua-ai", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="san_pham", id="1", cach_viet="chatgpt_web",
                                                  anh_lai="1", nen_trang="1"), follow_redirects=False)
    assert r.status_code == 303
    v = hd.ds(goc)[0]
    assert v["ma"] == "TD-0123" and v["sua"]["anh"] and not v["tach_nen"]
    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.XEM_TRUOC, v.get("loi")
    assert wp.da_tai == ["https://thanhduc.vn/wp-content/uploads/a.jpg"] and len(v["anh_web"]) == 1
    assert v["gia"] == "355000"  # có mã app thì lấy giá app
    assert v["anh_cu"] == [{"id": 5, "src": "https://thanhduc.vn/wp-content/uploads/a.jpg"}]
    client.post(f"/dang-web/viec/{v['id']}", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), hanh_dong="dang", gia=""))
    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.DA_DANG, v.get("loi")
    assert wp.sua[0][1]["images"] == [{"id": 901}] and wp.xoa == [5] and v["so_anh_xoa"] == 1  # ghi ảnh mới xong mới xóa ảnh cũ


def test_xep_danh_muc_bang_ai_chi_ghi_dong_da_tick(client, web_login, goc, nhieu_hang):
    csrf = web_login("ban.hang", "sales")
    wp = _wp_co_hang()
    ai = _AISua()
    may = _may(goc, wp, nguoi_viet=ai)
    r = client.post("/dang-web/xep-danh-muc", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="sp"))
    assert r.status_code == 409  # chưa lấy danh sách web
    may.lam_moi_web()
    r = client.post("/dang-web/xep-danh-muc", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="sp"), follow_redirects=False)
    assert r.status_code == 303
    v = hd.ds(goc)[0]
    assert v["kieu"] == "xep_danh_muc" and v["muc"] == [{"id": 2, "ten": "Cụm trống Brother DR2385"}]
    r = client.post("/dang-web/xep-danh-muc", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="sp"))
    assert r.status_code == 409  # đang xếp thì không tạo việc trùng

    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.XEM_TRUOC, v.get("loi")
    assert ai.xep[2] == ["Hộp mực", "Hộp mực › Hộp mực Canon", "Linh kiện & phụ tùng"]
    assert v["de_xuat"] == [{"id": 2, "ten": "Cụm trống Brother DR2385", "dm_id": 9, "chac": True}]
    trang = client.get(f"/dang-web/viec/{v['id']}").text
    assert 'name="dm_2"' in trang and "Cụm trống Brother DR2385" in trang
    r = client.post(f"/dang-web/viec/{v['id']}", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), hanh_dong="dang", dm_2="9"))
    assert r.status_code == 400 and "Tick ít nhất một dòng" in r.text
    client.post(f"/dang-web/viec/{v['id']}", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), hanh_dong="dang", chon="2", dm_2="8"))
    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.DA_DANG and v["so_da_ghi"] == 1
    assert wp.doi_dm == [(2, [8])]  # anh đổi sang Hộp mực Canon thì ghi đúng lựa chọn của anh
    assert "Cụm trống" not in client.get("/dang-web?tab=tren-web&loai=sp&dm=chua").text


def test_bai_viet_4_anh_anh_dau_dai_dien_con_lai_chen_than_bai(client, web_login, goc, monkeypatch):
    csrf = web_login("giam.doc", "director")
    nhieu = [("anh", (f"a{i}.jpg", _jpg(), "image/jpeg")) for i in range(5)]
    base = dict(csrf=csrf, loai="bai_viet", chu_de="Máy in kẹt giấy", cach_viet="tu_dong")
    r = client.post("/dang-web/soan", data=dict(base, form_key=str(uuid.uuid4())), files=nhieu, follow_redirects=False)
    assert r.status_code != 303 and "Bài viết tối đa 4 ảnh." in r.text
    r = client.post("/dang-web/soan", data=dict(base, form_key=str(uuid.uuid4()), da_co_logo="1"), files=nhieu[:4],
                    follow_redirects=False)
    assert r.status_code == 303
    viec_id = r.headers["location"].rsplit("/", 1)[1]
    assert hd.doc(goc, viec_id)["da_co_logo"] is True

    from dang_web import anh as anh_mod
    goi_logo = []
    that = anh_mod.xu_ly

    def xu_ly(*a, **kw):
        goi_logo.append(kw.get("logo"))
        return that(*a, **kw)

    monkeypatch.setattr(anh_mod, "xu_ly", xu_ly)
    wp = _WP()
    may = _may(goc, wp, nguoi_viet=_ChatGPTGia())
    may.lam_mot_viec()
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.XEM_TRUOC and len(v["anh_web"]) == 4
    assert goi_logo == [None] * 4  # ảnh đã có logo thì không đóng thêm
    page = client.get(f"/dang-web/viec/{viec_id}")
    noi_dung = "<p>Mở đầu</p><h2>A</h2><p>a</p><h2>B</h2><p>b</p><h2>C</h2><p>c</p>"
    client.post(f"/dang-web/viec/{viec_id}", data=dict(csrf=csrf_of(page.text), form_key=str(uuid.uuid4()), hanh_dong="dang",
                                                      tieu_de="Máy in kẹt giấy", tom_tat="", noi_dung_html=noi_dung))
    may.lam_mot_viec()
    assert hd.doc(goc, viec_id)["trang_thai"] == hd.DA_DANG
    p = wp.tao[0]
    assert p["featured_media"] == 901
    assert p["content"].count("<img") == 3 and "wp-image-902" in p["content"] and "wp-image-904" in p["content"]
    assert p["content"].index("wp-image-902") < p["content"].index("<h2>A")
    assert p["content"].index("<h2>A") < p["content"].index("wp-image-903") < p["content"].index("<h2>B")


def test_sua_bai_viet_chi_doi_chuyen_muc_khong_goi_ai(client, web_login, goc):
    csrf = web_login("ban.hang", "sales")
    wp = _wp_co_hang()
    may = _may(goc, wp, nguoi_viet=_AISua())  # sua_bai giả sẽ báo lỗi nếu bị gọi
    may.lam_moi_web()
    r = client.post("/dang-web/sua-ai", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="bai_viet", id="50", cach_viet="chatgpt_web",
                                                  danh_muc_id="3"), follow_redirects=False)
    assert r.status_code == 303
    v = hd.ds(goc)[0]
    assert v["loai"] == "bai_viet" and v["danh_muc_id"] == 3 and not any(v["sua"].values())
    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.XEM_TRUOC, v.get("loi")
    assert v["noi_dung"]["tieu_de"] == "Máy in kẹt giấy làm sao" and v["danh_muc"] == "Tin tức"
    client.post(f"/dang-web/viec/{v['id']}", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), hanh_dong="dang", danh_muc_id="3"))
    assert may.lam_mot_viec()
    assert hd.doc(goc, v["id"])["trang_thai"] == hd.DA_DANG
    assert wp.sua == [(50, {"categories": [3]})]
    assert "Máy in kẹt giấy" not in client.get("/dang-web?tab=tren-web&loai=bai&dm=chua").text


def test_sua_anh_rieng_xu_ly_tung_anh_roi_xoa_anh_cu(client, web_login, goc, nhieu_hang):
    csrf = web_login("ban.hang", "sales")
    wp = _wp_co_hang()
    sp = wp.tat_ca[0]
    sp["images"] = [{"id": 5, "src": "https://thanhduc.vn/wp-content/uploads/a.jpg"}, {"id": 6, "src": "https://thanhduc.vn/wp-content/uploads/b.jpg"}]
    sp["description"] = '<p>Mở đầu</p><figure class="td-anh"><img src="https://thanhduc.vn/wp-content/uploads/a.jpg"></figure><h2>A</h2><p>x</p><h2>B</h2><p>y</p><h2>C</h2>'
    may = _may(goc, wp)
    may.lam_moi_web()
    assert "/dang-web/sua-anh?id=1" in client.get("/dang-web?tab=tren-web&loai=sp").text
    r = client.get("/dang-web/sua-anh?id=1")
    assert r.status_code == 200 and 'name="che_do_5"' in r.text and 'value="web-6"' in r.text and "uploads/b.jpg" in r.text
    base = dict(csrf=csrf, id="1", che_do_moi="giu", logo="1")
    r = client.post("/dang-web/sua-anh", data=dict(base, form_key=str(uuid.uuid4()), thu_tu=["web-5", "web-6"], che_do_5="giu",
                                                   che_do_6="giu", dai_dien="web-5"))
    assert r.status_code == 400 and "Chưa có gì thay đổi" in r.text
    r = client.post("/dang-web/sua-anh", data=dict(base, form_key=str(uuid.uuid4()), thu_tu=["web-5", "web-6"], xoa_5="1", xoa_6="1"))
    assert r.status_code == 400 and "ít nhất 1 ảnh" in r.text and hd.ds(goc) == []

    r = client.post("/dang-web/sua-anh", data=dict(base, form_key=str(uuid.uuid4()), thu_tu=["web-6", "web-5"], che_do_5="nen_trang",
                                                   che_do_6="giu", dai_dien="web-6"),
                    files=[("anh", ("moi.jpg", _jpg(), "image/jpeg"))], follow_redirects=False)
    assert r.status_code == 303
    v = hd.ds(goc)[0]
    assert v["kieu"] == "sua_anh" and [(a["nguon"], a.get("media_id"), a["che_do"]) for a in v["anh"]] == [
        ("web", 6, "giu"), ("web", 5, "nen_trang"), ("moi", None, "giu")]

    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.XEM_TRUOC, v.get("loi")
    assert wp.da_tai == ["https://thanhduc.vn/wp-content/uploads/a.jpg"]  # ảnh Giữ nguyên trên web không tải về
    k = v["ket_anh"]
    assert k[0]["media_id"] == 6 and not k[0]["sau"] and k[1]["sau"] and not k[2]["sau"] and k[2]["goc"]
    trang = client.get(f"/dang-web/viec/{v['id']}").text
    assert "Ghi ảnh lên web" in trang and 'name="dung_1"' in trang and "Xóa ảnh cũ khỏi web" in trang

    client.post(f"/dang-web/viec/{v['id']}", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), hanh_dong="dang", dung_1="sau",
                                                      chen_mo_ta="1", xoa_cu="1"))
    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.DA_DANG, v.get("loi")
    sp_id, payload = wp.sua[0]
    assert sp_id == 1 and set(payload) == {"images", "description"}
    assert payload["images"] == [{"id": 6}, {"id": 901}, {"id": 902}] and wp.media == 2
    assert "uploads/a.jpg" not in payload["description"] and payload["description"].count('class="td-anh"') == 3
    assert wp.xoa == [5] and v["so_anh_xoa"] == 1 and v["so_anh_giu"] == 0  # ảnh 6 vẫn dùng nên không xóa
    luu = next(x for x in hd.doc_json(hd.file_sp_web(goc), {})["ds"] if x["id"] == 1)
    assert [m["id"] for m in luu["anhs"]] == [6, 901, 902] and luu["so_anh"] == 3
    assert "Đã thay 3 ảnh" in client.get(f"/dang-web/viec/{v['id']}").text


def test_xoa_anh_cu_giu_anh_con_dung_o_cho_khac(goc):
    wp = _wp_co_hang()
    wp.tat_ca[1]["images"] = [{"id": 7, "src": "https://thanhduc.vn/wp-content/uploads/chung.jpg"}]
    wp.con_dung = {"trong-bai.jpg"}
    may = _may(goc, wp)
    may.lam_moi_web()
    cu = [{"id": 7, "src": "https://thanhduc.vn/wp-content/uploads/chung.jpg"},
          {"id": 8, "src": "https://thanhduc.vn/wp-content/uploads/trong-bai.jpg"},
          {"id": 9, "src": "https://thanhduc.vn/wp-content/uploads/con-trong-mo-ta.jpg"},
          {"id": 10, "src": "https://thanhduc.vn/wp-content/uploads/bo-di.jpg"},
          {"id": 11, "src": "https://thanhduc.vn/wp-content/uploads/van-giu.jpg"}]
    kq = may._xoa_anh_cu(1, cu, {11}, '<img src="https://thanhduc.vn/wp-content/uploads/con-trong-mo-ta.jpg">')
    assert wp.xoa == [10] and kq == {"so_anh_xoa": 1, "so_anh_giu": 3}


def test_sua_anh_chon_lai_thay_ban_dang_xem_truoc(client, web_login, goc, nhieu_hang):
    csrf = web_login("ban.hang", "sales")
    wp = _wp_co_hang()
    may = _may(goc, wp)
    may.lam_moi_web()
    data = dict(csrf=csrf, id="1", che_do_moi="giu", logo="1", thu_tu=["web-5"], che_do_5="nen_trang", dai_dien="web-5")
    client.post("/dang-web/sua-anh", data=dict(data, form_key=str(uuid.uuid4())))
    cu = hd.ds(goc)[0]["id"]
    r = client.post("/dang-web/sua-anh", data=dict(data, form_key=str(uuid.uuid4())), follow_redirects=False)
    assert r.headers["location"] == f"/dang-web/viec/{cu}" and len(hd.ds(goc)) == 1  # đang chờ máy thì mở việc đó
    assert may.lam_mot_viec() and hd.doc(goc, cu)["trang_thai"] == hd.XEM_TRUOC
    r = client.post("/dang-web/sua-anh", data=dict(data, form_key=str(uuid.uuid4()), che_do_5="tach_nen"), follow_redirects=False)
    moi = r.headers["location"].rsplit("/", 1)[-1]
    assert moi != cu and hd.doc(goc, cu)["trang_thai"] == hd.BO and hd.doc(goc, moi)["anh"][0]["che_do"] == "tach_nen"
