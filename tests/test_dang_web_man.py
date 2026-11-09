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
        self.__dict__.setdefault("ten_anh", []).append((ten, alt))
        return {"id": 900 + self.media, "source_url": f"https://thanhduc.vn/wp-content/uploads/{ten}"}

    def sua_alt(self, media_id, alt):
        self.__dict__.setdefault("alt", []).append((media_id, alt))
        return {"id": media_id}

    def tao_sp(self, payload, cong_khai=False):
        self.tao.append(payload)
        self.__dict__.setdefault("cong_khai", []).append(cong_khai)
        return {"id": 101, "permalink": "https://thanhduc.vn/?p=101", "status": "publish" if cong_khai else "draft"}

    def sua_sp(self, sp_id, payload, cong_khai=False):
        self.sua.append((sp_id, payload))
        self.__dict__.setdefault("cong_khai", []).append(cong_khai)
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

    def lay_anh_theo_ids(self, ids):
        anh = getattr(self, "anh_media", {})
        return {i: anh[i] for i in ids if i in anh}

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
    assert "Duyệt và đăng lên thanhduc.vn" in page.text and "Chỉ lưu nháp" in page.text and 'name="ten_web"' in page.text
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
    assert wp.cong_khai == [False] and not v.get("cong_khai")  # "Chỉ lưu nháp": vẫn là Bản nháp như trước
    assert p["status"] == "draft" and p["name"] == "Hộp mực Canon 326 chính hãng" and p["regular_price"] == "360000"
    assert p["sku"] == "TD-0123" and p["categories"] == [{"id": 7}] and p["images"]
    assert p["description"] == "<p>Dài</p>"  # TUẤN 02/11: không chèn lại ảnh đại diện vào mô tả
    assert hd.doc_trang_thai(goc)["san_pham"]["TD-0123"]["id"] == 101

    may.lam_moi_web()
    assert client.get("/dang-web?tab=da-dang", follow_redirects=False).headers["location"].startswith("/dang-web?tab=tren-web")


def test_duyet_va_dang_len_web_cong_khai_ghi_nguoi_duyet(client, web_login, goc, san_pham):
    """TUẤN duyệt mẫu 1, 2, D1 ngày 08/11/2026: người bấm Duyệt và đăng thì sản phẩm lên web công khai luôn."""
    csrf = web_login("giam.doc", "director")
    viec_id = _soan_sp(client, csrf).headers["location"].rsplit("/", 1)[1]
    wp = _WP()
    may = _may(goc, wp)
    may.lam_mot_viec()
    # máy soạn xong chỉ dừng ở Xem trước, không tự đăng
    assert hd.doc(goc, viec_id)["trang_thai"] == hd.XEM_TRUOC and not may.lam_mot_viec() and wp.tao == []
    page = client.get(f"/dang-web/viec/{viec_id}")
    form = dict(csrf=csrf_of(page.text), form_key=str(uuid.uuid4()), hanh_dong="duyet_dang", ten_web="Hộp mực Canon 326",
                mo_ta_ngan_html="<p>Ngắn</p>", mo_ta_html="<p>Dài</p>", seo_title="SEO", seo_description="Mô tả", tu_khoa_chinh="canon 326",
                alt_anh="Hộp mực", gia="360.000", danh_muc="Hộp mực")
    assert client.post(f"/dang-web/viec/{viec_id}", data=form, follow_redirects=False).status_code == 303
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.CHO_DANG and v["cong_khai"] is True and v["duyet_boi"] == "Giam.Doc" and v["duyet_luc"]
    assert "Đang đăng lên thanhduc.vn" in client.get(f"/dang-web/viec/{viec_id}").text
    assert may.lam_mot_viec()
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.DA_DANG and v["trang_thai_web"] == "publish" and wp.cong_khai == [True]
    trang = client.get(f"/dang-web/viec/{viec_id}").text
    assert "Đã đăng công khai lên thanhduc.vn." in trang and "Duyệt: Giam.Doc" in trang and "Xem trang trên web" in trang
    assert "Đang hiện trên web" in client.get("/dang-web?tab=san-pham").text
    # bấm lại (gửi trùng) không đăng thêm lần nữa
    form["form_key"] = str(uuid.uuid4())
    assert client.post(f"/dang-web/viec/{viec_id}", data=form).status_code == 409


def test_may_khong_dang_cong_khai_neu_chua_co_nguoi_duyet(goc):
    """Việc có cong_khai mà thiếu người duyệt (sửa tay file) vẫn chỉ lên Bản nháp."""
    vid = hd.tao_viec(goc, "san_pham", {"ma": "TD-0123", "cach_viet": "mau"})
    wp = _WP()
    may = _may(goc, wp)
    may.lam_mot_viec()
    hd.sua(goc, vid, lambda x: x.update(trang_thai=hd.CHO_DANG, cong_khai=True))
    may.lam_mot_viec()
    assert wp.cong_khai == [False] and hd.doc(goc, vid)["trang_thai_web"] == "draft"


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


def test_bai_viet_da_chuyen_sang_dich_vu_seo(client, web_login, goc):
    """TUẤN 08/11 (Đợt 4): tắt viết bài trong app; việc bài viết còn sót vẫn hiện ở tab Sản phẩm và đăng nháp được."""
    csrf = web_login("giam.doc", "director")
    r = client.post("/dang-web/chu-de", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), chu_de="Canon 2900 kẹt giấy: cách xử lý"))
    assert r.status_code == 410 and hd.doc_chu_de(goc) == []
    r = client.post("/dang-web/soan", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="bai_viet", chu_de="Canon 2900 kẹt giấy: cách xử lý",
                                                cach_viet="tu_dong"), follow_redirects=False)
    assert r.status_code == 410 and hd.ds(goc) == []
    assert client.get("/dang-web?tab=bai-viet", follow_redirects=False).headers["location"] == "/dang-web?tab=san-pham"
    assert client.get("/dang-web/soan?loai=bai-viet", follow_redirects=False).headers["location"] == "/dang-web?tab=san-pham"
    trang = client.get("/dang-web").text
    assert "Chủ đề chờ viết" not in trang.split("đã tắt")[1] and "Số bài viết" not in trang and "app <b>Dịch vụ SEO</b>" in trang
    viec_id = hd.tao_viec(goc, "bai_viet", {"chu_de": "Canon 2900 kẹt giấy: cách xử lý", "ten": "Canon 2900 kẹt giấy: cách xử lý",
                                            "cach_viet": "tu_dong"})
    assert "Canon 2900 kẹt giấy: cách xử lý" in client.get("/dang-web?tab=san-pham").text
    wp = _WP()
    may = _may(goc, wp, nguoi_viet=_ChatGPTGia())
    may.lam_mot_viec()
    assert hd.doc(goc, viec_id)["trang_thai"] == hd.XEM_TRUOC
    page = client.get(f"/dang-web/viec/{viec_id}")
    assert "Duyệt và đăng" not in page.text and "Đăng nháp lên thanhduc.vn" in page.text  # bài viết: chỉ nháp
    r = client.post(f"/dang-web/viec/{viec_id}", data=dict(csrf=csrf_of(page.text), form_key=str(uuid.uuid4()), hanh_dong="duyet_dang",
                                                          tieu_de="x", tom_tat="", noi_dung_html="<p>x</p>"))
    assert r.status_code == 409 and hd.doc(goc, viec_id)["trang_thai"] == hd.XEM_TRUOC
    client.post(f"/dang-web/viec/{viec_id}", data=dict(csrf=csrf_of(page.text), form_key=str(uuid.uuid4()), hanh_dong="dang",
                                                      tieu_de="Canon 2900 kẹt giấy", tom_tat="", noi_dung_html="<p>x</p>"))
    may.lam_mot_viec()
    assert hd.doc(goc, viec_id)["trang_thai"] == hd.DA_DANG
    assert wp.tao[0]["title"] == "Canon 2900 kẹt giấy" and wp.tao[0]["content"] == "<p>x</p>"


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
    hd.them_chu_de(goc, "Nạp mực tận nơi có hại máy không")  # chủ đề cũ còn trong file: lịch không viết bài nữa
    hd.ghi_lich(goc, {"bat": True, "gio": "07:00", "so_sp": 2, "so_bai": 1})
    monkeypatch.setattr(hd, "bay_gio", lambda: datetime(2026, 11, 2, 7, 30, tzinfo=hd.GIO_VN))  # thứ 2
    may = _may(goc, _WP())
    assert may.kiem_lich() == 0 and hd.ds(goc) == []  # chưa có Codex hay khóa AI: không soạn bằng mẫu nữa
    hd.ghi_lich(goc, {"lan_cuoi": ""})
    hd.ghi_nhip_codex(goc)
    assert may.kiem_lich() == 1  # Codex đang chạy: 1 sản phẩm, không còn bài viết (TUẤN 08/11)
    assert may.kiem_lich() == 0  # mỗi ngày một lần
    assert [x["loai"] for x in hd.ds(goc)] == ["san_pham"]
    v = next(x for x in hd.ds(goc) if x["loai"] == "san_pham")
    assert v["ma"] == "TD-0123" and v["cach_viet"] == "codex" and v["nguon"] == "lich"


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
    # TUẤN 08/11: bài viết không soạn mới trong app; việc còn sót (tạo trước đây) vẫn đăng đúng 4 ảnh.
    viec_id = hd.tao_viec(goc, "bai_viet", {"chu_de": "Máy in kẹt giấy", "ten": "Máy in kẹt giấy", "cach_viet": "tu_dong",
                                            "da_co_logo": True}, [(f"a{i}.jpg", _jpg()) for i in range(4)])
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


def test_sua_anh_rieng_dat_khung_tung_anh_roi_xoa_anh_cu(client, web_login, goc, nhieu_hang):
    """TUẤN duyệt mẫu D2/2 ngày 02/11/2026: chỉ đặt ảnh vào khung web (căn giữa, xoay, zoom, logo), tick nhiều ảnh.
    Mẫu D8/8 cùng ngày: chia 3 nhóm ảnh đại diện / album / trong mô tả; ảnh mô tả trùng ảnh đại diện thì đánh dấu bỏ."""
    csrf = web_login("ban.hang", "sales")
    wp = _wp_co_hang()
    sp = wp.tat_ca[0]
    sp["images"] = [{"id": 5, "src": "https://thanhduc.vn/wp-content/uploads/a.jpg"}, {"id": 6, "src": "https://thanhduc.vn/wp-content/uploads/b.jpg"}]
    sp["description"] = ('<p>Mở đầu</p><figure class="td-anh"><img src="https://thanhduc.vn/wp-content/uploads/a-300x300.jpg"></figure>'
                         '<h2>A</h2><p>x</p><h2>B</h2><p>y</p><h2>C</h2>')
    may = _may(goc, wp)
    may.lam_moi_web()
    ds = client.get("/dang-web?tab=tren-web&loai=sp").text
    assert "/dang-web/sua-anh?loai=sp&amp;id=1" in ds and "Sửa AI" in ds and "dw-xbtn" in ds
    r = client.get("/dang-web/sua-anh?id=1")
    assert r.status_code == 200 and 'name="chon_web-5"' in r.text and 'value="web-6"' in r.text and "uploads/b.jpg" in r.text
    assert "che_do" not in r.text and "Tách nền" not in r.text and "sua-anh.js" in r.text
    assert "① Ảnh đại diện" in r.text and "② Ảnh album" in r.text and "③ Ảnh trong mô tả" in r.text
    assert 'name="vai_web-5" value="dai_dien"' in r.text and 'name="vai_web-6" value="album"' in r.text
    assert 'name="vai_mota-0" value="mo_ta"' in r.text and "Trùng ảnh đại diện" in r.text and 'name="xoa_mota-0" value="1" checked' in r.text
    assert "AI vẽ 1 ảnh cho mô tả" in r.text and "&#34;A&#34;" in r.text  # danh sách mục H2 cho ô chọn vị trí
    base = dict(csrf=csrf, id="1", logo="1", logo_goc="trai")
    r = client.post("/dang-web/sua-anh", data=dict(base, form_key=str(uuid.uuid4()), thu_tu=["web-5", "web-6", "mota-0"],
                                                   **{"vai_web-5": "dai_dien", "vai_web-6": "album", "vai_mota-0": "mo_ta"}))
    assert r.status_code == 400 and "Chưa có gì thay đổi" in r.text
    r = client.post("/dang-web/sua-anh", data=dict(base, form_key=str(uuid.uuid4()), thu_tu=["web-5", "web-6"],
                                                   **{"xoa_web-5": "1", "xoa_web-6": "1"}))
    assert r.status_code == 400 and "ít nhất 1 ảnh" in r.text and hd.ds(goc) == []

    # Ảnh 6 lên đại diện, ảnh 5 xuống album (có chỉnh), bỏ ảnh trùng trong mô tả, thêm 1 ảnh mới dưới mục B.
    r = client.post("/dang-web/sua-anh", data=dict(base, form_key=str(uuid.uuid4()), thu_tu=["web-6", "web-5", "mota-0", "moi-1"],
                                                   alt_tat_ca="1",
                                                   **{"vai_web-6": "dai_dien", "vai_web-5": "album", "vai_mota-0": "dai_dien",
                                                      "xoa_mota-0": "1", "vai_moi-1": "mo_ta", "vitri_moi-1": "1",
                                                      "chon_web-5": "1", "bien_web-5": '{"z": 1.2, "r": 5, "x": 0.1, "q": 1, "f": true}',
                                                      "bien_web-6": "{}", "bien_moi-1": '{"z": 99, "tu": 1}'}),
                    files=[("anh", ("moi.jpg", _jpg(), "image/jpeg"))], follow_redirects=False)
    assert r.status_code == 303
    v = hd.ds(goc)[0]
    assert v["kieu"] == "sua_anh" and v["logo_goc"] == "trai" and v["ba_nhom"] and v["xoa_cu"] and not v["chen_mo_ta"]
    assert [(a["nguon"], a.get("media_id"), a["vai"], a["sua"]) for a in v["anh"]] == [
        ("web", 6, "dai_dien", False), ("web", 5, "album", True), ("moi", None, "mo_ta", True)]
    assert v["anh"][2]["vi_tri"] == "1" and v["bo_trong_bai"] == ["https://thanhduc.vn/wp-content/uploads/a-300x300.jpg"]
    assert v["anh"][1]["bien"]["z"] == 1.2 and v["anh"][1]["bien"]["q"] == 1 and v["anh"][1]["bien"]["f"] is True
    assert v["anh"][2]["bien"]["z"] == 5 and v["anh"][2]["bien"]["tu"] is True  # thông số lạ bị chặn trong giới hạn

    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.XEM_TRUOC, v.get("loi")
    assert wp.da_tai == ["https://thanhduc.vn/wp-content/uploads/a.jpg"]  # ảnh không tick Sửa thì không tải về
    k = v["ket_anh"]
    assert k[0]["media_id"] == 6 and not k[0]["sau"] and k[1]["sau"] and k[2]["sau"] and k[2]["goc"]
    with Image.open(may.ch.anh_da_xu_ly / k[1]["sau"]) as im:
        assert im.size == (400, 400)  # ảnh album: khung vuông đúng cỡ ảnh web
    with Image.open(may.ch.anh_da_xu_ly / k[2]["sau"]) as im:
        assert im.size == (1200, 675)  # ảnh trong mô tả: khung ngang
    trang = client.get(f"/dang-web/viec/{v['id']}").text
    assert "Ghi ảnh lên web" in trang and 'name="dung_1"' in trang and "Xóa ảnh cũ khỏi web" in trang and "Đã chỉnh" in trang
    assert "① Ảnh đại diện" in trang and "② Ảnh album" in trang and "③ Trong mô tả · dưới mục “B”" in trang
    assert "Bỏ 1 ảnh khỏi mô tả" in trang and "Chèn ảnh vào mô tả" not in trang

    client.post(f"/dang-web/viec/{v['id']}", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), hanh_dong="dang", dung_1="sau", xoa_cu="1"))
    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.DA_DANG, v.get("loi")
    sp_id, payload = wp.sua[0]
    assert sp_id == 1 and set(payload) == {"images", "description"}
    assert payload["images"] == [{"id": 6}, {"id": 901}] and wp.media == 2  # ảnh mô tả không vào album
    mo_ta = payload["description"]
    assert "a-300x300.jpg" not in mo_ta and mo_ta.count('class="td-anh"') == 1 and "wp-image-902" in mo_ta
    assert mo_ta.index("<h2>B</h2>") < mo_ta.index("wp-image-902") < mo_ta.index("<p>y</p>")  # đặt ngay dưới mục B
    assert wp.xoa == [5] and v["so_anh_xoa"] == 1 and v["so_anh_giu"] == 0  # ảnh 6 vẫn dùng nên không xóa
    luu = next(x for x in hd.doc_json(hd.file_sp_web(goc), {})["ds"] if x["id"] == 1)
    assert [m["id"] for m in luu["anhs"]] == [6, 901] and luu["so_anh"] == 2 and len(luu["anhs_mo_ta"]) == 1
    # TUẤN 02/11: tên file gọn theo tên sản phẩm; alt theo nhóm; ảnh giữ nguyên chỉ đổi chữ alt.
    assert wp.ten_anh == [("hop-muc-canon-326-2.webp", "Hộp mực Canon 326 góc 2"), ("hop-muc-canon-326-3.webp", "Hộp mực Canon 326 – B")]
    assert wp.alt == [(6, "Hộp mực Canon 326")] and v["so_alt"] == 1
    assert 'alt="Hộp mực Canon 326 – B"' in mo_ta
    assert "Đã thay 2 ảnh" in client.get(f"/dang-web/viec/{v['id']}").text


def test_chen_sau_h2_va_xoa_anh_trong_bai():
    from dang_web.wordpress import chen_sau_h2, cung_anh, tieu_de_h2, xoa_anh_trong_bai

    html = '<p>a</p><h2>Một <b>hai</b></h2><p>x</p><!-- wp:image --><figure class="wp-block-image"><img src="https://w/u/c-300x200.jpg"></figure><!-- /wp:image --><h2>Ba</h2>'
    assert tieu_de_h2(html) == ["Một hai", "Ba"]
    assert chen_sau_h2(html, "<i>", 1).endswith("<h2>Ba</h2><i>") and chen_sau_h2(html, "<i>", "").endswith("<i>")
    assert chen_sau_h2(html, "<i>", 0).startswith("<p>a</p><h2>Một <b>hai</b></h2><i>")
    sach = xoa_anh_trong_bai(html, "https://w/u/c-300x200.jpg")
    assert "<figure" not in sach and "wp:image" not in sach and "<h2>Ba</h2>" in sach
    assert cung_anh("https://w/u/c-300x200.jpg", "https://w/u/c.jpg") and not cung_anh("https://w/u/c.jpg", "https://w/u/d.jpg")


def test_sua_anh_bai_viet_thay_anh_trong_bai_tai_cho(client, web_login, goc):
    csrf = web_login("ban.hang", "sales")
    wp = _wp_co_hang()
    wp.anh_media = {77: {"src": "https://thanhduc.vn/wp-content/uploads/dd.jpg", "nho": "https://thanhduc.vn/wp-content/uploads/dd-300.jpg"}}
    bai = wp.bai_web[1]
    bai["content"] = {"raw": '<p>Giá</p><figure class="wp-block-image"><img class="wp-image-60" src="https://thanhduc.vn/wp-content/uploads/trong.jpg" '
                             'srcset="https://thanhduc.vn/wp-content/uploads/trong-300.jpg 300w" alt="x"></figure><p>Hết</p>'}
    may = _may(goc, wp)
    may.lam_moi_web()
    ds = client.get("/dang-web?tab=tren-web&loai=bai").text
    assert "/dang-web/sua-anh?loai=bai&amp;id=51" in ds and "dd-300.jpg" in ds and "1 ảnh trong bài" in ds
    assert "Chưa có ảnh đại diện" in ds and "Máy in kẹt giấy" in client.get("/dang-web?tab=tren-web&loai=bai&loc=chua-anh").text
    assert "Giá nạp mực" not in client.get("/dang-web?tab=tren-web&loai=bai&loc=chua-anh").text
    r = client.get("/dang-web/sua-anh?loai=bai&id=51")
    assert r.status_code == 200 and 'value="web-77"' in r.text and 'value="bai-0"' in r.text and "sa-wide" in r.text
    assert 'name="dai_dien"' not in r.text and "uploads/trong.jpg" in r.text
    r = client.get("/dang-web/anh-tren-web?loai=bai&id=51&k=bai-9")
    assert r.status_code == 404  # chỉ chuyển ảnh có trong danh sách của đúng bài đó

    r = client.post("/dang-web/sua-anh", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="bai", id="51", logo="1",
                                                   thu_tu=["web-77", "bai-0"], **{"chon_bai-0": "1", "bien_bai-0": '{"tu": 1}'}),
                    follow_redirects=False)
    assert r.status_code == 303
    v = hd.ds(goc)[0]
    assert v["loai"] == "bai_viet" and not v["chen_mo_ta"] and not v["xoa_cu"]
    assert [(a["key"], a["vai"], a["sua"]) for a in v["anh"]] == [("web-77", "dai_dien", False), ("bai-0", "bai", True)]
    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.XEM_TRUOC, v.get("loi")
    assert wp.da_tai == ["https://thanhduc.vn/wp-content/uploads/trong.jpg"]
    with Image.open(may.ch.anh_da_xu_ly / v["ket_anh"][1]["sau"]) as im:
        assert im.size == (1200, 675)  # bài viết dùng khung ngang 16:9
    trang = client.get(f"/dang-web/viec/{v['id']}").text
    assert "Ảnh trong bài" in trang and "Chèn ảnh vào mô tả" not in trang and "loai=bai" in trang

    client.post(f"/dang-web/viec/{v['id']}", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), hanh_dong="dang", dung_1="sau"))
    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.DA_DANG, v.get("loi")
    bai_id, payload = wp.sua[0]
    assert bai_id == 51 and payload["featured_media"] == 77 and wp.media == 1
    assert "uploads/trong.jpg" not in payload["content"] and "wp-image-901" in payload["content"] and "wp-image-60" not in payload["content"]
    assert "srcset" not in payload["content"] and "<p>Giá</p>" in payload["content"] and "<p>Hết</p>" in payload["content"]
    assert not getattr(wp, "xoa", [])  # ảnh bài viết không xóa


def test_dat_khung_can_giua_xoay_va_logo(tmp_path):
    from dang_web import anh as anh_mod

    im = Image.new("RGB", (800, 400), (255, 255, 255))
    ImageDraw.Draw(im).rectangle((40, 40, 200, 200), fill=(200, 0, 0))  # sản phẩm lệch góc trái trên
    tu = Image.open(io.BytesIO(anh_mod.dat_khung(im, 600, 600, {"tu": True}, dinh_dang="jpg")))
    assert tu.size == (600, 600)
    hop = anh_mod.vung_san_pham(tu.convert("RGB"), (255, 255, 255))
    cx, cy = (hop[0] + hop[2]) / 2, (hop[1] + hop[3]) / 2
    assert abs(cx - 300) < 12 and abs(cy - 300) < 12 and hop[2] - hop[0] > 480  # nằm giữa, chiếm gần hết khung
    ngang = Image.open(io.BytesIO(anh_mod.dat_khung(im, *anh_mod.KHUNG_BAI, {"q": 1, "r": 10}, dinh_dang="jpg")))
    assert ngang.size == (1200, 675)
    assert anh_mod.chuan_bien({"z": "abc", "r": 400, "x": -9, "q": 7}) == {"z": 1.0, "r": 45.0, "x": -1.5, "y": 0.0, "q": 3, "f": False, "tu": False}
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (100, 40), (0, 0, 255, 255)).save(logo)
    co_logo = Image.open(io.BytesIO(anh_mod.dat_khung(im, 600, 600, {}, logo=logo, logo_goc="trai", dinh_dang="jpg"))).convert("RGB")
    assert co_logo.getpixel((40, 568))[0] < 80 and co_logo.getpixel((560, 568))[0] > 200  # logo góc trái dưới


def test_dat_khung_logo_keo_tha_doi_co(tmp_path):
    """TUẤN 09/11/2026: logo đặt đúng tâm (lx, ly) và cỡ ls anh kéo trên màn; không ra khỏi khung."""
    from dang_web import anh as anh_mod

    im = Image.new("RGB", (600, 600), (255, 255, 255))
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (100, 100), (0, 0, 255, 255)).save(logo)
    b = anh_mod.chuan_bien({"lx": 0.5, "ly": 0.25, "ls": 0.3})
    assert (b["lx"], b["ly"], b["ls"]) == (0.5, 0.25, 0.3)
    assert anh_mod.chuan_bien({"lx": 2, "ly": "x", "ls": 9})["ls"] == anh_mod.LOGO_MAX
    assert "lx" not in anh_mod.chuan_bien({"lx": 0.5})  # thiếu số đo thì logo về góc như cũ
    ra = Image.open(io.BytesIO(anh_mod.dat_khung(im, 600, 600, b, logo=logo, dinh_dang="jpg"))).convert("RGB")
    hop = anh_mod.vung_san_pham(ra, (255, 255, 255))  # logo 180×180, tâm (300, 150)
    assert abs(hop[0] - 210) <= 3 and abs(hop[2] - 390) <= 3 and abs(hop[1] - 60) <= 3 and abs(hop[3] - 240) <= 3
    ngoai = Image.open(io.BytesIO(anh_mod.dat_khung(im, 1200, 675, {"lx": 1, "ly": 1, "ls": 0.1}, logo=logo, dinh_dang="jpg"))).convert("RGB")
    assert ngoai.getpixel((1195, 670))[2] > 200 and ngoai.getpixel((1195, 670))[0] < 80  # ép vào trong khung, sát góc


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
    data = {"csrf": csrf, "id": "1", "logo": "1", "thu_tu": ["web-5"], "dai_dien": "web-5", "chon_web-5": "1", "bien_web-5": '{"z": 1.1}'}
    client.post("/dang-web/sua-anh", data=dict(data, form_key=str(uuid.uuid4())))
    cu = hd.ds(goc)[0]["id"]
    r = client.post("/dang-web/sua-anh", data=dict(data, form_key=str(uuid.uuid4())), follow_redirects=False)
    assert r.headers["location"] == f"/dang-web/viec/{cu}" and len(hd.ds(goc)) == 1  # đang chờ máy thì mở việc đó
    assert may.lam_mot_viec() and hd.doc(goc, cu)["trang_thai"] == hd.XEM_TRUOC
    r = client.post("/dang-web/sua-anh", data=dict(data, form_key=str(uuid.uuid4()), **{"bien_web-5": '{"z": 1.5}'}), follow_redirects=False)
    moi = r.headers["location"].rsplit("/", 1)[-1]
    assert moi != cu and hd.doc(goc, cu)["trang_thai"] == hd.BO and hd.doc(goc, moi)["anh"][0]["bien"]["z"] == 1.5

def test_sua_anh_bai_viet_bo_anh_trong_bai_va_dat_anh_moi_duoi_h2(client, web_login, goc):
    csrf = web_login("ban.hang", "sales")
    wp = _wp_co_hang()
    wp.anh_media = {77: {"src": "https://thanhduc.vn/wp-content/uploads/dd.jpg", "nho": "https://thanhduc.vn/wp-content/uploads/dd-300.jpg"}}
    wp.bai_web[1]["featured_media"] = 77
    wp.bai_web[1]["content"] = {"raw": '<p>Mở</p><figure><img src="https://thanhduc.vn/wp-content/uploads/dd-1024x576.jpg"></figure>'
                                       '<h2>Bảng giá</h2><p>g</p><h2>Liên hệ</h2><p>h</p>'}
    may = _may(goc, wp)
    may.lam_moi_web()
    r = client.get("/dang-web/sua-anh?loai=bai&id=51")
    assert "③ Ảnh trong bài" in r.text and "② Ảnh album" not in r.text and "Trùng ảnh đại diện" in r.text
    r = client.post("/dang-web/sua-anh", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="bai", id="51", logo="1",
                                                   thu_tu=["web-77", "bai-0", "moi-1"], alt_tat_ca="1",
                                                   **{"xoa_bai-0": "1", "vai_moi-1": "bai", "vitri_moi-1": "1", "bien_moi-1": "{}"}),
                    files=[("anh", ("moi.jpg", _jpg(), "image/jpeg"))], follow_redirects=False)
    assert r.status_code == 303
    v = hd.ds(goc)[0]
    assert [(a["key"], a["vai"]) for a in v["anh"]] == [("web-77", "dai_dien"), ("moi-1", "them")] and v["anh"][1]["vi_tri"] == "1"
    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert "dưới mục “Liên hệ”" in client.get(f"/dang-web/viec/{v['id']}").text
    client.post(f"/dang-web/viec/{v['id']}", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), hanh_dong="dang"))
    assert may.lam_mot_viec()
    assert hd.doc(goc, v["id"])["trang_thai"] == hd.DA_DANG
    _, payload = wp.sua[0]
    html = payload["content"]
    assert payload["featured_media"] == 77 and "dd-1024x576" not in html
    assert html.index("<h2>Liên hệ</h2>") < html.index("wp-image-901") < html.index("<p>h</p>")
    assert wp.ten_anh[0][1] == "Giá nạp mực 2026 – Liên hệ" and wp.alt == [(77, "Giá nạp mực 2026")]


def test_bai_trung_dong_nghia_va_so_voi_bai_da_lay_ve_va_hang_cho(goc):
    """TUẤN 05/11: bài sắp đăng trùng bài đã có trên web (đặt tên khác) không bị báo."""
    from dang_web.so_ten import cac_cau_tim_bai, do_giong_bai

    assert do_giong_bai("Hướng dẫn xử lý lỗi kẹt giấy máy in HP", "Cách khắc phục máy in HP bị kẹt giấy") >= 0.7
    assert do_giong_bai("Hướng dẫn xử lý lỗi kẹt giấy máy in HP", "Máy in HP không nhận hộp mực") < 0.7
    assert cac_cau_tim_bai("Hướng dẫn xử lý lỗi kẹt giấy máy in HP")[-1] == "kẹt giấy"
    # Web tìm không ra, nhưng danh sách đã lấy về khi bấm Cập nhật từ web có bài gần giống
    hd.ghi_json(hd.file_bai_web(goc), {"luc": "x", "ds": [{"id": 77, "ten": "Cách khắc phục máy in HP bị kẹt giấy", "trang_thai": "publish", "xem": "https://web.test/kg"}]})
    vid = hd.tao_viec(goc, "bai_viet", {"chu_de": "Hướng dẫn xử lý lỗi kẹt giấy máy in HP", "ten": "Hướng dẫn xử lý lỗi kẹt giấy máy in HP"})
    _may(goc, _WP(), nguoi_viet=_ChatGPTGia()).lam_mot_viec()
    v = hd.doc(goc, vid)
    assert v["trang_thai"] == hd.CO_THE_TRUNG and v["trung"][0]["id"] == 77 and "post=77" in v["trung"][0]["link_sua"]
    # Bài tạo trước còn nằm trong hàng cũng được so (bài tạo sau thì không, để hai bài không chặn nhau)
    hd.ghi_json(hd.file_bai_web(goc), {})
    a = hd.tao_viec(goc, "bai_viet", {"chu_de": "Canon 2900 nạp mực tại nhà", "ten": "Canon 2900 nạp mực tại nhà"})
    hd.sua(goc, a, lambda x: x.update(trang_thai=hd.XEM_TRUOC))
    b = hd.tao_viec(goc, "bai_viet", {"chu_de": "Cách nạp mực Canon LBP 2900", "ten": "Cách nạp mực Canon LBP 2900"})
    _may(goc, _WP(), nguoi_viet=_ChatGPTGia()).lam_mot_viec()
    vb = hd.doc(goc, b)
    assert vb["trang_thai"] == hd.CO_THE_TRUNG and vb["trung"][0]["trang_thai"] == "hang_cho" and vb["trung"][0]["ten"] == "Canon 2900 nạp mực tại nhà"


def test_tieu_de_ai_dat_trung_thi_canh_bao_o_xem_truoc(client, web_login, goc):
    class _AIDoiTen(_ChatGPTGia):
        def viet_bai(self, chu_de, ghi_chu="", hotline=""):
            kq = super().viet_bai(chu_de, ghi_chu, hotline)
            kq.du_lieu["tieu_de"] = "Cách khắc phục máy in Brother bị kẹt giấy"
            return kq

    csrf = web_login("giam.doc", "director")
    hd.ghi_json(hd.file_bai_web(goc), {"luc": "x", "ds": [{"id": 88, "ten": "Xử lý kẹt giấy máy in Brother tại nhà", "trang_thai": "draft", "xem": ""}]})
    vid = hd.tao_viec(goc, "bai_viet", {"chu_de": "Máy in Brother kéo nhiều tờ một lúc", "ten": "Máy in Brother kéo nhiều tờ một lúc"})
    _may(goc, _WP(), nguoi_viet=_AIDoiTen()).lam_mot_viec()
    v = hd.doc(goc, vid)
    assert v["trang_thai"] == hd.XEM_TRUOC and v["trung_sau"][0]["id"] == 88
    page = client.get(f"/dang-web/viec/{vid}").text
    assert "Tiêu đề AI đặt gần giống bài đã có" in page and "Xử lý kẹt giấy máy in Brother tại nhà" in page and csrf


