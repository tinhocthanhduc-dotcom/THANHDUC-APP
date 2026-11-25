"""Sản phẩm từ ảnh tự đăng (TUẤN chọn phương án A 25/11/2026, Đợt 2): app Dịch vụ SEO gửi ảnh + mã TD sang Đăng web,
máy soạn không giá, không số tháng bảo hành; app kia nhắn Zalo rồi báo "đã báo" thì 4 giờ sau máy tự đăng công khai.
Có Hoãn, trần 3 sản phẩm/ngày, lỗi 2 lần liên tiếp tự dừng, Gỡ về nháp trong 7 ngày."""
import uuid
from datetime import timedelta

import pytest

import app.marketing_publish as mp
from app import ai_agent
from app.models import AuditLog, Product
from dang_web import hang_doi as hd
from dang_web.may_chay import ly_do_khong_tu_dang
from dang_web.viet_bai import KetQuaAI
from test_dang_web_man import _jpg, _may, _WP, goc, san_pham  # noqa: F401  (fixture)
from test_web import csrf_of, web_login  # noqa: F401  (fixture)


class _AIChac:
    """Người viết giả: bài sạch, không giá, không điểm chưa chắc."""

    model = "AI giả"

    def __init__(self, ngan="<p>Hộp mực Canon 326 in sắc nét.</p>", can=None):
        self.ngan, self.can, self.ghi_chu = ngan, can or [], ""

    def viet_san_pham(self, sp, ghi_chu="", so_anh=0, hotline=""):
        self.ghi_chu, self.sp = ghi_chu, sp
        return KetQuaAI(du_lieu={"ten_web": "Hộp mực Canon 326", "mo_ta_ngan_html": self.ngan, "mo_ta_html": "<p>Dài</p>",
                                 "seo_title": "SEO", "seo_description": "Mô tả", "tu_khoa_chinh": "canon 326",
                                 "alt_anh": "Hộp mực", "can_kiem_tra": self.can}, model=self.model)


@pytest.fixture
def api(goc, monkeypatch, db, make_user, san_pham):  # noqa: F811
    monkeypatch.setattr(mp, "goc", lambda: goc)
    monkeypatch.setattr(hd, "codex_song", lambda g: True)
    raw = ai_agent.create_key(db, make_user("gd", "director"), "growth-os-dang")
    return {"Authorization": "Bearer " + raw}


def _gui(client, h, ref="41", ma="TD-0123", n=2):
    return client.post("/marketing/v1/dang-web/san-pham", headers=h, data={"ref": ref, "ma": ma},
                       files=[("anh", (f"a{i}.jpg", _jpg(), "image/jpeg")) for i in range(n)])


def _viec_tu_dang(goc, **kw):  # noqa: F811
    return hd.tao_viec(goc, "san_pham", dict(ma="TD-0123", cach_viet="mau", nguon="dvseo", ref="1", tu_dang=True, **kw),
                       [("a.jpg", _jpg())])


def test_gui_anh_tao_viec_tu_dang_va_khong_tao_trung(client, api, goc, db):  # noqa: F811
    r = _gui(client, api)
    assert r.status_code == 200, r.text
    j = r.json()
    v = hd.doc(goc, j["viec_id"])
    assert v["tu_dang"] and v["nguon"] == "dvseo" and v["ref"] == "41" and v["cach_viet"] == "codex" and len(v["anh_goc"]) == 2
    assert j["trang_thai"] == hd.CHO_SOAN and j["tu_dang"] and not j["tu_dang_luc"]
    # gửi lại cùng phiếu (mạng chập chờn): trả việc cũ
    assert _gui(client, api).json()["viec_id"] == j["viec_id"] and len(hd.ds(goc)) == 1
    assert db.query(AuditLog).filter(AuditLog.action == "ai.marketing_publish").count() == 1
    assert client.get(f"/marketing/v1/dang-web/viec/{j['viec_id']}", headers=api).json()["ma"] == "TD-0123"


def test_gui_sai_bi_tu_choi(client, api, goc, db, make_user):  # noqa: F811
    assert _gui(client, api, ma="TD-9999").status_code == 422
    assert _gui(client, api, ref="../x").status_code == 422
    assert _gui(client, api, n=0).status_code == 422
    db.add(Product(code="TD-0999", name="Hàng chạy thử", is_test=True))
    db.commit()
    assert _gui(client, api, ma="TD-0999").status_code == 422
    assert not hd.ds(goc)
    # khóa Growth OS chỉ đọc không gửi được; đi qua Cloudflare bị chặn
    doc = "Bearer " + ai_agent.create_key(db, make_user("gd2", "director"), "growth-os")
    assert _gui(client, {"Authorization": doc}).status_code == 403
    assert _gui(client, dict(api, **{"cf-ray": "x"})).status_code == 403
    # việc do màn Đăng web tạo thì khóa này không đọc được
    vid = hd.tao_viec(goc, "san_pham", {"ma": "TD-0123"})
    assert client.get(f"/marketing/v1/dang-web/viec/{vid}", headers=api).status_code == 404


def test_soan_tu_dang_khong_gia_khong_bao_hanh(goc):  # noqa: F811
    vid = _viec_tu_dang(goc)
    ai = _AIChac()
    may = _may(goc, _WP(), ai)
    assert may.lam_mot_viec()
    v = hd.doc(goc, vid)
    assert v["trang_thai"] == hd.XEM_TRUOC, v.get("loi")
    assert v["gia"] == "" and v["ly_do_khong_tu_dang"] == ""
    assert ai.sp.sale_price == 0 and ai.sp.warranty_months == 0 and "CHẾ ĐỘ TỰ ĐĂNG" in ai.ghi_chu


def test_ly_do_khong_tu_dang():
    d = {"mo_ta_ngan_html": "<p>Hộp mực</p>", "can_kiem_tra": []}
    assert ly_do_khong_tu_dang(d, []) == "Chưa có ảnh sản phẩm."
    assert ly_do_khong_tu_dang(d, ["a"]) == ""
    assert "chưa chắc" in ly_do_khong_tu_dang(dict(d, can_kiem_tra=["Số trang in"]), ["a"])
    assert "giá" in ly_do_khong_tu_dang(dict(d, mo_ta_html="Giá chỉ 355.000đ"), ["a"])
    assert "bảo hành" in ly_do_khong_tu_dang(dict(d, mo_ta_html="Bảo hành 12 tháng"), ["a"])


def test_da_bao_hen_4_gio_roi_tu_dang_cong_khai(client, api, goc):  # noqa: F811
    vid = _viec_tu_dang(goc)
    wp = _WP()
    may = _may(goc, wp, _AIChac())
    may.lam_mot_viec()
    now = hd.bay_gio()
    # chưa báo Zalo thì không bao giờ tự đăng
    assert hd.den_han_tu_dang(goc, now + timedelta(days=3)) == []
    r = client.post(f"/marketing/v1/dang-web/viec/{vid}/da-bao", headers=api)
    assert r.status_code == 200, r.text
    luc = hd.doc(goc, vid)["tu_dang_luc"]
    assert r.json()["tu_dang_luc"] == luc
    client.post(f"/marketing/v1/dang-web/viec/{vid}/da-bao", headers=api)
    assert hd.doc(goc, vid)["tu_dang_luc"] == luc  # báo lại không dời giờ
    assert hd.den_han_tu_dang(goc, now + timedelta(hours=3)) == []
    assert hd.den_han_tu_dang(goc, now + timedelta(hours=4, minutes=2)) == [vid]
    v = hd.doc(goc, vid)
    assert v["trang_thai"] == hd.CHO_DANG and v["cong_khai"] and v["duyet_boi"] == hd.MAY_TU_DANG
    assert may.lam_mot_viec()
    v = hd.doc(goc, vid)
    assert v["trang_thai"] == hd.DA_DANG and wp.cong_khai == [True] and "regular_price" not in wp.tao[0]
    assert client.get(f"/marketing/v1/dang-web/viec/{vid}", headers=api).json()["trang_thai_web"] == "publish"


def test_co_ly_do_thi_khong_hen_duoc(client, api, goc):  # noqa: F811
    vid = _viec_tu_dang(goc)
    _may(goc, _WP()).lam_mot_viec()  # mẫu có sẵn luôn có điểm cần kiểm tra
    v = hd.doc(goc, vid)
    assert v["trang_thai"] == hd.XEM_TRUOC and v["ly_do_khong_tu_dang"]
    assert client.post(f"/marketing/v1/dang-web/viec/{vid}/da-bao", headers=api).status_code == 409


def test_tran_3_moi_ngay_va_dung(goc):  # noqa: F811
    now = hd.bay_gio()
    ids = []
    for _ in range(4):
        vid = _viec_tu_dang(goc)
        hd.sua(goc, vid, lambda x: x.update(trang_thai=hd.XEM_TRUOC, noi_dung={}, ly_do_khong_tu_dang=""))
        hd.da_bao(goc, vid, now)
        ids.append(vid)
    sau = now + timedelta(hours=5)
    hd.dung_tu_dang(goc, "Anh dừng.")
    assert hd.den_han_tu_dang(goc, sau) == []
    hd.bat_lai_tu_dang(goc)
    assert len(hd.den_han_tu_dang(goc, sau)) == 3 and hd.den_han_tu_dang(goc, sau) == []
    assert sum(hd.doc(goc, i)["trang_thai"] == hd.XEM_TRUOC for i in ids) == 1


def test_loi_2_lan_lien_tiep_tu_dung(goc):  # noqa: F811
    assert not hd.ghi_loi_tu_dang(goc, "web lỗi")
    hd.ghi_ok_tu_dang(goc)
    assert not hd.ghi_loi_tu_dang(goc, "web lỗi")
    assert hd.ghi_loi_tu_dang(goc, "web lỗi 500")
    assert "500" in hd.doc_cong_tac(goc)["ly_do"]


def test_hoan_go_va_bat_lai_tren_man(client, web_login, goc, api):  # noqa: F811
    csrf = web_login("giam.doc", "director")
    vid = _viec_tu_dang(goc)
    wp = _WP()
    may = _may(goc, wp, _AIChac())
    may.lam_mot_viec()
    hd.da_bao(goc, vid)
    page = client.get(f"/dang-web/viec/{vid}").text
    assert "máy tự đăng, không giá" in page and "Hoãn tự đăng" in page
    r = client.post(f"/dang-web/viec/{vid}", data=dict(csrf=csrf_of(page), form_key=str(uuid.uuid4()), hanh_dong="hoan_tu_dang"),
                    follow_redirects=False)
    assert r.status_code == 303
    v = hd.doc(goc, vid)
    assert not v.get("tu_dang") and not v.get("tu_dang_luc") and v["hoan_boi"]
    assert hd.den_han_tu_dang(goc, hd.bay_gio() + timedelta(days=1)) == []

    # việc máy đã tự đăng: gỡ về nháp được trong 7 ngày
    vid2 = _viec_tu_dang(goc)
    may.lam_mot_viec()
    now = hd.bay_gio()
    hd.da_bao(goc, vid2, now - timedelta(hours=5))
    assert hd.den_han_tu_dang(goc) == [vid2]
    may.lam_mot_viec()
    v = hd.doc(goc, vid2)
    assert hd.go_duoc(v) and not hd.go_duoc(v, now + timedelta(days=8))
    wp.an_sp = lambda sp_id: wp.__dict__.setdefault("an", []).append(sp_id) or {"id": sp_id, "status": "draft"}
    page = client.get(f"/dang-web/viec/{vid2}").text
    assert "Gỡ về nháp" in page
    r = client.post(f"/dang-web/viec/{vid2}", data=dict(csrf=csrf_of(page), form_key=str(uuid.uuid4()), hanh_dong="go_web"),
                    follow_redirects=False)
    assert r.status_code == 303 and hd.doc(goc, vid2)["go"]
    may.lam_mot_viec()
    v = hd.doc(goc, vid2)
    assert wp.an == [101] and v["da_go"] and v["trang_thai_web"] == "draft" and not hd.go_duoc(v)
    assert "Đã gỡ về Nháp" in client.get(f"/dang-web/viec/{vid2}").text

    # dừng / bật lại ở danh sách
    assert "Dừng tự đăng" in client.get("/dang-web?tab=san-pham").text
    client.post("/dang-web/tu-dang/bat-lai", data={"csrf": csrf, "dung": "1"})
    assert hd.doc_cong_tac(goc)["dung"] and "Bật lại tự đăng" in client.get("/dang-web?tab=san-pham").text
    client.post("/dang-web/tu-dang/bat-lai", data={"csrf": csrf})
    assert not hd.doc_cong_tac(goc)["dung"]


def test_kinh_doanh_khong_bat_lai_duoc(client, web_login, goc):  # noqa: F811
    csrf = web_login("ban.hang", "sales")
    hd.dung_tu_dang(goc, "x")
    assert client.post("/dang-web/tu-dang/bat-lai", data={"csrf": csrf}, follow_redirects=False).status_code == 403
    assert hd.doc_cong_tac(goc)["dung"]
