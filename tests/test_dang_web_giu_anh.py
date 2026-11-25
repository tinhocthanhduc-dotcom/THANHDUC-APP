"""Sửa bằng AI giữ nguyên ảnh trong mô tả và ảnh bộ sưu tập (TUẤN 25/11/2026)."""
import uuid

from dang_web import giu_anh
from dang_web import hang_doi as hd
from tests.test_dang_web_man import _AISua, _may, _wp_co_hang, goc, nhieu_hang, web_login  # noqa: F401 (fixture)

MO_TA_CU = ('<p>Mở đầu cũ</p>\n<p><img class="wp-image-5" src="https://thanhduc.vn/wp-content/uploads/mo-ta-1.jpg"></p>\n'
            '<h2>Máy tương thích</h2><p>HP 1020</p>\n'
            '<!-- wp:image --><figure class="wp-block-image"><img src="https://thanhduc.vn/wp-content/uploads/mo-ta-2.jpg"></figure>'
            '<!-- /wp:image -->\n<h2>Thông số</h2><p>Dung lượng <a href="https://thanhduc.vn/x.jpg"><img src="https://thanhduc.vn/x.jpg"></a> 2000 trang</p>')


def test_tach_va_dat_lai_dung_cho():
    con, ds = giu_anh.tach_anh(MO_TA_CU)
    assert "<img" not in con and "Dung lượng" in con and "2000 trang" in con and len(ds) == 3
    assert [(g["h2"], g["truoc_h2"]) for g in ds] == [(0, True), (1, True), (2, False)]
    moi = "<p>Mới 1</p><p>Mới 2</p><h2>Máy in dùng được</h2><p>A</p><h2>Thông số</h2><p>B</p><p>C</p>"
    ra = giu_anh.dat_lai_anh(moi, ds)
    assert giu_anh.anh_bi_mat(giu_anh.nguon_anh(MO_TA_CU), ra) == []
    assert ra.index("mo-ta-1.jpg") < ra.index("Máy in dùng được") < ra.index("mo-ta-2.jpg") < ra.index("<h2>Thông số")
    assert ra.index("<p>B</p>") < ra.index("x.jpg") < ra.index("<p>C</p>")  # ảnh trong đoạn: đặt sau đoạn đầu của mục đó
    assert "<!-- wp:image -->" in ra  # khối Gutenberg giữ nguyên vẹn
    # Bài mới ít mục hơn: ảnh của mục không còn đặt cuối, không mất
    ra = giu_anh.dat_lai_anh("<p>Chỉ một đoạn</p>", ds)
    assert giu_anh.anh_bi_mat(giu_anh.nguon_anh(MO_TA_CU), ra) == [] and ra.startswith("<p>Chỉ một đoạn</p>")
    assert giu_anh.tach_anh("<p>Không ảnh</p><p></p>") == ("<p>Không ảnh</p><p></p>", [])


class _AIThemAnh(_AISua):
    def sua_san_pham(self, hien_tai, sp=None, ghi_chu="", hotline=""):
        kq = super().sua_san_pham(hien_tai, sp, ghi_chu, hotline)
        kq.du_lieu["mo_ta_html"] = '<p>Dài mới</p><img src="https://bia.com/ai.jpg"><h2>Máy in dùng được</h2><p>A</p><h2>Thông số</h2><p>B</p>'
        return kq


def test_sua_bang_ai_giu_anh_mo_ta_va_bo_suu_tap(client, web_login, goc, nhieu_hang):
    csrf = web_login("giam.doc", "director")
    wp = _wp_co_hang()
    wp.tat_ca[0]["description"] = MO_TA_CU
    ai = _AIThemAnh()
    may = _may(goc, wp, nguoi_viet=ai)
    may.lam_moi_web()
    r = client.post("/dang-web/sua-ai", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="san_pham", id="1", cach_viet="chatgpt_web",
                                                  noi_dung="1", seo="1"), follow_redirects=False)
    assert r.status_code == 303
    v = hd.ds(goc)[0]
    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.XEM_TRUOC, v.get("loi")
    assert "<img" not in ai.hien_tai["mo_ta_html"] and "Máy tương thích" in ai.hien_tai["mo_ta_html"]  # AI không thấy ảnh
    mo_ta = v["noi_dung"]["mo_ta_html"]
    assert "bia.com" not in mo_ta and all(s in mo_ta for s in v["anh_giu"]) and len(v["anh_giu"]) == 3
    assert v["so_anh_bst"] == 1
    trang = client.get(f"/dang-web/viec/{v['id']}").text
    assert "Giữ nguyên ảnh." in trang and "1 ảnh bộ sưu tập không đổi" in trang and "3 ảnh trong mô tả" in trang

    # Sửa tay xóa mất 1 ảnh: app chặn, không ghi
    thieu = mo_ta.replace("mo-ta-2.jpg", "khac.jpg")
    r = client.post(f"/dang-web/viec/{v['id']}", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), hanh_dong="dang", ten_web="Hộp mực",
                                                          mo_ta_html=thieu, gia=""))
    assert r.status_code == 400 and "thiếu 1 ảnh" in r.text and hd.doc(goc, v["id"])["trang_thai"] == hd.XEM_TRUOC

    client.post(f"/dang-web/viec/{v['id']}", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), hanh_dong="dang", ten_web="Hộp mực",
                                                      mo_ta_html=mo_ta, gia=""))
    assert may.lam_mot_viec()
    v = hd.doc(goc, v["id"])
    assert v["trang_thai"] == hd.DA_DANG, v.get("loi")
    _, payload = wp.sua[0]
    assert "images" not in payload  # không tick Ảnh: bộ sưu tập không đụng tới
    assert giu_anh.anh_bi_mat(giu_anh.nguon_anh(MO_TA_CU), payload["description"]) == []


def test_may_khong_ghi_de_khi_mo_ta_thieu_anh(goc, nhieu_hang):
    wp = _wp_co_hang()
    may = _may(goc, wp)
    v = {"id": "x", "loai": "san_pham", "wp_id": 1, "sua": {"noi_dung": True}, "anh_giu": ["https://thanhduc.vn/a.jpg"],
         "noi_dung": {"ten_web": "A", "mo_ta_html": "<p>Không ảnh</p>", "mo_ta_ngan_html": ""}}
    try:
        may._dang_sua_web(v)
        raise AssertionError("phải dừng")
    except RuntimeError as e:
        assert "thiếu 1 ảnh" in str(e)
    assert wp.sua == []


def _viec_moi(goc):
    vid = hd.tao_viec(goc, "san_pham", dict(ma="TD-0123", ten="Hộp mực Canon 326", cach_viet="chatgpt_web", ghi_chu="Ghi chú cũ"))
    hd.sua(goc, vid, lambda x: x.update(trang_thai=hd.XEM_TRUOC, noi_dung={"ten_web": "Hộp mực Canon 326", "mo_ta_html": "<p>x</p>",
                                                                           "mo_ta_ngan_html": ""}))
    return vid


def test_soan_moi_chuyen_sang_san_pham_co_san(client, web_login, goc, nhieu_hang):
    from app.web.dang_web import _tim_sp_web

    csrf = web_login("giam.doc", "director")
    wp = _wp_co_hang()
    wp.tat_ca[0]["sku"] = ""  # web chưa ghi mã TD nên app chưa tự nhận ra
    vid = _viec_moi(goc)
    trang = client.get(f"/dang-web/viec/{vid}").text
    assert "Sản phẩm này đã có trên web?" in trang and "Cập nhật từ web" in trang and "Đúng, viết lại cái này" not in trang
    _may(goc, wp).lam_moi_web()
    trang = client.get(f"/dang-web/viec/{vid}").text
    assert "Đúng, viết lại cái này" in trang and "Hộp mực Canon 326" in trang and "Cụm trống" not in trang

    ds = [{"id": 1, "xem": "https://thanhduc.vn/san-pham/hop-muc-canon-326/"}, {"id": 2, "xem": "https://thanhduc.vn/b"}]
    assert _tim_sp_web(ds, "http://www.thanhduc.vn/san-pham/hop-muc-canon-326?x=1")["id"] == 1
    assert _tim_sp_web(ds, "https://thanhduc.vn/wp-admin/post.php?post=2&action=edit")["id"] == 2
    assert _tim_sp_web(ds, "hop-muc-canon-326")["id"] == 1 and _tim_sp_web(ds, "https://thanhduc.vn/khac") is None

    r = client.post(f"/dang-web/viec/{vid}/co-san", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), link_web="https://thanhduc.vn/khong-co"))
    assert r.status_code == 400 and "Không thấy sản phẩm này" in r.text and hd.doc(goc, vid)["trang_thai"] == hd.XEM_TRUOC
    r = client.post(f"/dang-web/viec/{vid}/co-san", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), link_web="https://thanhduc.vn/a",
                                                             ghi_ma="1"), follow_redirects=False)
    assert r.status_code == 303
    cu = hd.doc(goc, vid)
    assert cu["trang_thai"] == hd.BO
    moi = hd.doc(goc, cu["chuyen_sang"])
    assert moi["kieu"] == "sua_web" and moi["wp_id"] == 1 and moi["ma"] == "TD-0123" and moi["ghi_chu"] == "Ghi chú cũ"
    assert moi["sua"] == {"noi_dung": True, "seo": True, "anh": False} and moi["cach_viet"] == "chatgpt_web"
    assert r.headers["location"] == f"/dang-web/viec/{moi['id']}"
    assert hd.doc_xac_nhan(goc)["TD-0123"]["dung"] == 1
    ghi = next(x for x in hd.ds(goc) if x.get("kieu") == "ghi_sku")
    assert ghi["ghi"] == [{"id": 1, "ma": "TD-0123", "ten": "Hộp mực Canon 326"}] and ghi["trang_thai"] == hd.CHO_DANG
    # Bấm lại (bản cũ đã bỏ): không tạo thêm
    r = client.post(f"/dang-web/viec/{vid}/co-san", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), web_id="1"))
    assert r.status_code == 409 and len([x for x in hd.ds(goc) if x.get("kieu") == "sua_web"]) == 1


def test_chuyen_co_san_khong_ghi_ma_khi_web_da_co_ma_khac(client, web_login, goc, nhieu_hang):
    csrf = web_login("giam.doc", "director")
    wp = _wp_co_hang()
    wp.tat_ca[0]["sku"] = "TD-0999"
    _may(goc, wp).lam_moi_web()
    vid = _viec_moi(goc)
    r = client.post(f"/dang-web/viec/{vid}/co-san", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), web_id="1", ghi_ma="1"),
                    follow_redirects=False)
    assert r.status_code == 303
    assert not any(x.get("kieu") == "ghi_sku" for x in hd.ds(goc))  # ô Mã trên web đang có mã khác thì không đè
