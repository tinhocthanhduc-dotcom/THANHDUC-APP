"""Bài mạng xã hội trong màn Đăng web (TUẤN duyệt mẫu M1–M3, D1–D2 ngày 07/11/2026)."""
import io
import uuid

import pytest
from PIL import Image

from dang_web import anh_mxh
from dang_web import hang_doi as hd
from dang_web.viet_bai import ChoTraLoi, KetQuaAI, NguoiVietChatGPTWeb, don_mxh, yeu_cau_mxh
from test_dang_web_man import _WP, _jpg, _may, goc  # noqa: F401  (fixture)
from test_web import web_login  # noqa: F401  (fixture)

BAI = {"id": 45, "title": {"raw": "Canon 2900 kẹt giấy: cách xử lý"}, "status": "publish", "link": "https://thanhduc.vn/canon-2900-ket-giay",
       "excerpt": {"raw": "Tóm tắt bài"}, "content": {"raw": "<h2>Nguyên nhân</h2><p>Bánh xe kéo giấy mòn.</p>"}, "featured_media": 0, "meta": {}}


class _VietGia:
    model = "AI giả"

    def __init__(self):
        self.lan = 0
        self.nguon = None

    def viet_mxh(self, nguon, ghi_chu="", hotline=""):
        self.lan += 1
        self.nguon, self.ghi_chu = nguon, ghi_chu
        d = {"facebook": "Máy Canon 2900 kẹt giấy? <b>Xem</b> ngay", "zalo": "Canon 2900 kẹt giấy, gọi 0888.304.668",
             "google": "x" * 2000, "chu_anh": "Canon 2900 kẹt giấy liên tục", "can_kiem_tra": []}
        don_mxh(d)
        return KetQuaAI(du_lieu=d, model=self.model)


def _web(goc):
    hd.ghi_json(hd.file_bai_web(goc), {"luc": "2026-11-07T08:00", "ds": [
        {"id": 45, "ten": "Canon 2900 kẹt giấy: cách xử lý", "trang_thai": "publish", "xem": "https://thanhduc.vn/canon-2900-ket-giay",
         "ngay": "2026-11-05"}]})
    hd.ghi_json(hd.file_sp_web(goc), {"luc": "2026-11-07T08:00", "ds": [
        {"id": 7, "ten": "Hộp mực Canon 326", "sku": "TD-0123", "trang_thai": "publish", "xem": "https://thanhduc.vn/hop-muc-326"}]})


def _soan(client, csrf, files=None, **kw):
    data = dict(csrf=csrf, form_key=str(uuid.uuid4()), nguon="bai-45", mau="meo", co="vuong", chu_anh="", ghi_chu="", cach_viet="tu_dong")
    data.update(kw)
    return client.post("/dang-web/mxh/soan", data=data, files=files or [], follow_redirects=False)


def _kich_thuoc(p):
    return Image.open(p).size


# ---------- ghép ảnh ----------
@pytest.mark.parametrize("mau", list(anh_mxh.MAU))
@pytest.mark.parametrize("co", list(anh_mxh.CO))
def test_ghep_anh_moi_mau_ra_dung_co(mau, co):
    anh = [_jpg(), _jpg()] if mau == "truoc_sau" else [_jpg()]
    out = anh_mxh.ghep(mau, co, "Canon 2900 kẹt giấy liên tục ở văn phòng Quận 1", anh, "0888.304.668")
    im = Image.open(io.BytesIO(out))
    assert im.format == "JPEG" and im.size == anh_mxh.CO[co]


def test_truoc_sau_can_hai_anh_va_chu_dai_tu_thu_nho():
    with pytest.raises(ValueError, match="2 ảnh"):
        anh_mxh.ghep("truoc_sau", "vuong", "x", [_jpg()])
    f, dong, cao = anh_mxh.vua_khung("Máy in Canon LBP 2900 kẹt giấy liên tục " * 6, 900, 130, 76, 40, 2)
    assert len(dong) == 2 and dong[-1].endswith("…") and f.size == 40


def test_khung_canva_dat_len_tren(tmp_path):
    khung = Image.new("RGBA", (1080, 1080), (0, 0, 0, 0))
    khung.paste((255, 0, 0, 255), (0, 0, 1080, 40))  # dải đỏ trên cùng của khung anh làm
    khung.save(tmp_path / "meo-vuong.png")
    im = Image.open(io.BytesIO(anh_mxh.ghep("meo", "vuong", "Mẹo", [_jpg()], "", tmp_path))).convert("RGB")
    r, g, b = im.getpixel((540, 20))
    assert r > 200 and g < 60 and b < 60


# ---------- câu lệnh AI ----------
def test_cau_lenh_va_lam_sach():
    lenh = yeu_cau_mxh({"loai": "bai_viet", "ten": "Canon 2900", "link": "https://thanhduc.vn/a", "noi_dung": "Bánh xe mòn"},
                       "ghi chú thật", "0888.304.668")
    assert "KHÔNG viết HTML" in lenh and "https://thanhduc.vn/a" in lenh and "0888.304.668" in lenh and "ghi chú thật" in lenh
    assert "Không ghi giá tiền" in lenh
    d = {"facebook": "<p>Một</p><p>Hai</p>", "zalo": "z", "google": "g" * 3000, "chu_anh": "  a   b ", "can_kiem_tra": "xem lại"}
    don_mxh(d)
    assert d["facebook"] == "Một\nHai" and len(d["google"]) == 1500 and d["chu_anh"] == "a b" and d["can_kiem_tra"] == ["xem lại"]


def test_chatgpt_web_soan_cau_lenh_mxh(tmp_path):
    nv = NguoiVietChatGPTWeb(tmp_path)
    with pytest.raises(ChoTraLoi):
        nv.viet_mxh({"loai": "bai_viet", "ten": "A", "link": "", "noi_dung": ""})
    assert (tmp_path / "mxh.txt").exists()
    (tmp_path / "mxh-tra-loi.txt").write_text('```json\n{"facebook": "f", "zalo": "z", "google": "g", "chu_anh": "c", "can_kiem_tra": []}\n```')
    assert nv.viet_mxh({"loai": "bai_viet"}).du_lieu["facebook"] == "f"


# ---------- màn app + máy đăng web ----------
def test_soan_xem_truoc_chep_va_danh_dau_da_dang(client, web_login, goc):
    csrf = web_login("giam.doc", "director")
    _web(goc)
    assert "Soạn bài mạng xã hội" in client.get("/dang-web?tab=mang-xa-hoi").text
    # tìm không dấu ra cả bài viết và sản phẩm
    kq = client.get("/dang-web/tim-web", params={"q": "canon"}).text
    assert 'data-dw-pick="bai-45"' in kq and 'data-dw-pick="sp-7"' in kq
    assert 'data-dw-pick="bai-45"' in client.get("/dang-web/tim-web", params={"q": "ket giay"}).text
    assert "Canon 2900 kẹt giấy: cách xử lý" in client.get("/dang-web/mxh/soan", params={"nguon": "bai-45"}).text

    r = _soan(client, csrf, ghi_chu="Thợ tới trong ngày")
    assert r.status_code == 303
    vid = r.headers["location"].rsplit("/", 1)[1]
    v = hd.doc(goc, vid)
    assert v["loai"] == "mang_xa_hoi" and v["nguon_loai"] == "bai_viet" and v["wp_id"] == 45 and v["trang_thai"] == hd.CHO_SOAN
    # việc mạng xã hội không lẫn vào tab Bài viết / Sản phẩm
    assert "Canon 2900 kẹt giấy: cách xử lý" not in client.get("/dang-web?tab=bai-viet").text

    wp, nv = _WP(), _VietGia()
    wp.bai_web = [BAI]
    may = _may(goc, wp, nguoi_viet=nv)
    may.ch.hotline = "0888.304.668"
    may.lam_mot_viec()
    v = hd.doc(goc, vid)
    assert v["trang_thai"] == hd.XEM_TRUOC, v.get("loi")
    assert nv.nguon["link"] == "https://thanhduc.vn/canon-2900-ket-giay" and "Bánh xe kéo giấy mòn." in nv.nguon["noi_dung"]
    assert nv.ghi_chu == "Thợ tới trong ngày"
    assert v["noi_dung"]["facebook"] == "Máy Canon 2900 kẹt giấy? Xem ngay" and len(v["noi_dung"]["google"]) == 1500
    assert v["anh_mxh"] == ["mxh-vuong-1.jpg"]
    assert _kich_thuoc(hd.thu_muc_mot_viec(goc, vid) / "mxh-vuong-1.jpg") == (1080, 1080)

    trang = client.get(f"/dang-web/viec/{vid}").text
    for k in ("facebook", "zalo", "google"):
        assert f'id="dw-{k}"' in trang
    assert "Tải ảnh vuông" in trang and "✓ Đã đăng Facebook" in trang
    assert client.get(f"/dang-web/anh/{vid}/mxh-vuong-1.jpg").headers["content-type"] == "image/jpeg"

    def bam(hanh_dong, **kw):
        return client.post(f"/dang-web/viec/{vid}", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), hanh_dong=hanh_dong, **kw),
                           follow_redirects=False)

    assert bam("luu", facebook="Bài anh đã sửa").status_code == 303
    assert hd.doc(goc, vid)["noi_dung"]["facebook"] == "Bài anh đã sửa"
    bam("kenh:facebook", facebook="Bài anh đã sửa lần 2")
    v = hd.doc(goc, vid)
    assert set(v["kenh_da_dang"]) == {"facebook"} and v["trang_thai"] == hd.XEM_TRUOC and v["noi_dung"]["facebook"] == "Bài anh đã sửa lần 2"
    assert "Đã đăng 1/3" in client.get("/dang-web?tab=mang-xa-hoi").text
    bam("kenh:zalo")
    bam("kenh:google")
    v = hd.doc(goc, vid)
    assert v["trang_thai"] == hd.DA_DANG and v["dang_luc"]
    assert "Đã đăng đủ 3 kênh" in client.get(f"/dang-web/viec/{vid}").text
    bam("kenh:zalo")  # bỏ đánh dấu một kênh: quay lại chờ
    v = hd.doc(goc, vid)
    assert v["trang_thai"] == hd.XEM_TRUOC and set(v["kenh_da_dang"]) == {"facebook", "google"}
    assert "không hợp lệ" in bam("kenh:tiktok").text
    assert "không hợp lệ" in bam("dang").text  # máy không đăng bài mạng xã hội

    # đổi chữ trên ảnh và cỡ: chỉ ghép lại ảnh, không gọi AI
    bam("ghep_lai", chu_anh="Sửa Canon 2900 tại chỗ", mau="uu_dai", co="ca_hai")
    assert hd.doc(goc, vid)["trang_thai"] == hd.CHO_SOAN
    may.lam_mot_viec()
    v = hd.doc(goc, vid)
    assert v["trang_thai"] == hd.XEM_TRUOC and nv.lan == 1 and v["chu_anh"] == "Sửa Canon 2900 tại chỗ"
    assert v["anh_mxh"] == ["mxh-vuong-2.jpg", "mxh-doc-2.jpg"] and set(v["kenh_da_dang"]) == {"facebook", "google"}
    thu_muc = hd.thu_muc_mot_viec(goc, vid)
    assert not (thu_muc / "mxh-vuong-1.jpg").exists() and _kich_thuoc(thu_muc / "mxh-doc-2.jpg") == (1080, 1350)
    assert v["noi_dung"]["facebook"] == "Bài anh đã sửa lần 2"

    # viết lại cả 3 bản: gọi AI lại, bỏ đánh dấu kênh
    bam("viet_lai")
    may.lam_mot_viec()
    v = hd.doc(goc, vid)
    assert nv.lan == 2 and v["kenh_da_dang"] == {} and v["noi_dung"]["facebook"].startswith("Máy Canon 2900")


def test_truoc_sau_can_dung_hai_anh_va_nguon_phai_co_tren_web(client, web_login, goc):
    csrf = web_login("giam.doc", "director")
    _web(goc)
    r = _soan(client, csrf, mau="truoc_sau", files=[("anh", ("a.jpg", _jpg(), "image/jpeg"))])
    assert r.status_code == 400 and "cần đúng 2 ảnh" in r.text
    assert "Không thấy mục này" in _soan(client, csrf, nguon="bai-99").text
    assert "Chọn một bài" in _soan(client, csrf, nguon="abc").text
    r = _soan(client, csrf, mau="truoc_sau", nguon="sp-7", co="doc",
              files=[("anh", ("truoc.jpg", _jpg(), "image/jpeg")), ("anh", ("sau.jpg", _jpg(), "image/jpeg"))])
    vid = r.headers["location"].rsplit("/", 1)[1]
    v = hd.doc(goc, vid)
    assert v["nguon_loai"] == "san_pham" and len(v["anh_goc"]) == 2
    wp = _WP()
    wp.tat_ca = [{"id": 7, "name": "Hộp mực Canon 326", "status": "publish", "permalink": "https://thanhduc.vn/hop-muc-326",
                  "short_description": "<ul><li>Chính hãng</li></ul>", "description": "<p>In 2.100 trang</p>", "images": [], "meta_data": []}]
    may = _may(goc, wp, nguoi_viet=_VietGia())
    may.lam_mot_viec()
    v = hd.doc(goc, vid)
    assert v["trang_thai"] == hd.XEM_TRUOC, v.get("loi")
    assert v["anh_mxh"] == ["mxh-doc-1.jpg"] and not getattr(wp, "da_tai", [])  # dùng ảnh thợ chụp, không tải ảnh web


def test_mau_san_pham_lay_anh_tren_web(client, web_login, goc):
    csrf = web_login("giam.doc", "director")
    _web(goc)
    vid = _soan(client, csrf, nguon="sp-7", mau="san_pham").headers["location"].rsplit("/", 1)[1]
    wp = _WP()
    wp.url = "https://thanhduc.vn"
    wp.tat_ca = [{"id": 7, "name": "Hộp mực Canon 326", "status": "publish", "permalink": "https://thanhduc.vn/hop-muc-326",
                  "short_description": "", "description": "<p>x</p>", "meta_data": [],
                  "images": [{"id": 3, "src": "https://thanhduc.vn/wp-content/uploads/326.jpg"}]}]
    may = _may(goc, wp, nguoi_viet=_VietGia())
    may.lam_mot_viec()
    v = hd.doc(goc, vid)
    assert v["trang_thai"] == hd.XEM_TRUOC, v.get("loi")
    assert wp.da_tai == ["https://thanhduc.vn/wp-content/uploads/326.jpg"] and v["anh_tu_web"]
