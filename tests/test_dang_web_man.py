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
        return []

    def tai_anh(self, data, ten, mime, alt=""):
        self.media += 1
        return {"id": 900 + self.media}

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


def test_chi_giam_doc_vao_duoc(client, web_login, goc):
    web_login("ban.hang", "sales")
    assert client.get("/dang-web", follow_redirects=False).status_code == 403
    web_login("giam.doc", "director")
    r = client.get("/dang-web")
    assert r.status_code == 200 and "Máy đăng web đang tắt" in r.text and "Đăng web" in r.text


def test_soan_san_pham_can_anh_va_ma_dang_dung(client, web_login, goc, san_pham):
    csrf = web_login("giam.doc", "director")
    r = client.post("/dang-web/soan", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="san_pham", ma="TD-0123", cach_viet="mau"))
    assert r.status_code == 400 and "ít nhất 1 ảnh" in r.text
    r = _soan_sp(client, csrf, ma="TD-9999")
    assert "Chọn mã hàng đang dùng" in r.text
    assert hd.ds(goc) == []


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
    assert hd.doc_trang_thai(goc)["san_pham"]["TD-0123"]["id"] == 101

    may.lam_moi_web()
    ds = client.get("/dang-web?tab=da-dang")
    assert "Sửa trên web" in ds.text and "Hộp mực Canon 326 (web)" in ds.text and "Đang bán" in ds.text


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
