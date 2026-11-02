"""Codex trên Z2 vẽ ảnh minh họa cho bài viết (TUẤN duyệt mẫu D4/4, D5/5 ngày 02/11/2026): chỉ bài viết,
ảnh Codex gửi được mở lại bằng Pillow, anh chọn ảnh rồi mới đăng; khóa điều phối không đụng được."""
import io
import uuid

from PIL import Image
from sqlalchemy import select

from app.models import AuditLog
from dang_web import hang_doi as hd
from dang_web import ve_anh
from test_codex_dang_web import _khoa
from test_dang_web_man import _ChatGPTGia, _may, _wp_co_hang, goc  # noqa: F401  (fixture)
from test_web import web_login  # noqa: F401  (fixture)


def _png(w=1672, h=941, mau=(90, 120, 160)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), mau).save(b, "PNG")
    return b.getvalue()


def test_ve_anh_lenh_khong_chu_khong_logo_va_chan_file_la(goc):
    lenh = ve_anh.lenh_ve("Máy in kẹt giấy", "Tóm tắt", "thợ mặc áo xanh")
    assert "image_gen" in lenh and "anh-1.png, anh-2.png" in lenh and "Không có chữ" in lenh and "áo xanh" in lenh
    yc = ve_anh.tao(goc, chu_de="Máy in kẹt giấy", wp_id=51)
    assert ve_anh.nhan_cho_codex(goc)[0]["id"] == yc and ve_anh.nhan_cho_codex(goc) == []  # đã nhận thì không giao lại
    try:
        ve_anh.nhan_anh(goc, yc, b"<script>")
        raise AssertionError("phải chặn")
    except ValueError:
        pass
    ten = ve_anh.nhan_anh(goc, yc, _png(3000, 1688))
    with Image.open(ve_anh.file_anh(goc, yc, ten)) as im:
        assert im.format == "JPEG" and max(im.size) == 1800
    assert ve_anh.file_anh(goc, yc, "../yeu-cau.json") is None
    assert ve_anh.ket_thuc(goc, yc)["trang_thai"] == ve_anh.XONG
    assert ve_anh.moi_nhat(goc, wp_id=51)["anh"] == ["anh-1.jpg"]


def test_sua_anh_bai_viet_ve_anh_bang_codex(client, db, web_login, make_user, goc):
    csrf = web_login("giam.doc", "director")
    hw, hdp = _khoa(db, make_user)
    wp = _wp_co_hang()
    _may(goc, wp).lam_moi_web()
    r = client.get("/dang-web/sua-anh?loai=bai&id=51")
    assert "Tạo ảnh minh họa bằng AI" in r.text and "Codex trên Z2 đang tắt" in r.text and "ve-anh.js" in r.text
    assert "Tạo ảnh minh họa" not in client.get("/dang-web/sua-anh?loai=sp&id=1").text  # sản phẩm dùng ảnh thật
    data = dict(csrf=csrf, form_key=str(uuid.uuid4()), id="51", loai="bai", dan="thợ mặc áo xanh")
    assert client.post("/dang-web/ve-anh", data=data, follow_redirects=False).status_code == 409  # Codex tắt
    assert ve_anh.ds(goc) == []

    assert client.get("/ai/dang-web/ve-anh", headers=hdp).status_code == 403  # khóa điều phối không vẽ được
    assert client.get("/ai/dang-web/ve-anh", headers=hw).json() == []  # cũng là nhịp Codex
    r = client.post("/dang-web/ve-anh", data=dict(data, form_key=str(uuid.uuid4())), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/dang-web/sua-anh?loai=bai&id=51")
    client.post("/dang-web/ve-anh", data=dict(data, form_key=str(uuid.uuid4())))
    assert len(ve_anh.ds(goc)) == 1  # đang vẽ thì không gửi thêm
    assert "Codex đang vẽ ảnh" in client.get("/dang-web/sua-anh?loai=bai&id=51").text

    ds = client.get("/ai/dang-web/ve-anh", headers=hw).json()
    assert len(ds) == 1 and "Giá nạp mực 2026" in ds[0]["lenh"] and "áo xanh" in ds[0]["lenh"]
    yc = ds[0]["id"]
    assert client.post(f"/ai/dang-web/ve-anh/{yc}/anh", headers={**hw, "Content-Type": "image/png"}, content=b"xx").status_code == 422
    for mau in ((10, 20, 30), (200, 100, 50)):
        r = client.post(f"/ai/dang-web/ve-anh/{yc}/anh", headers={**hw, "Content-Type": "image/png"}, content=_png(mau=mau))
        assert r.status_code == 200
    assert client.post(f"/ai/dang-web/ve-anh/{yc}/xong", headers=hw, json={"ly_do": ""}).json()["trang_thai"] == "xong"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "ai.dang_web_image", AuditLog.entity_id == yc))
    js = client.get(f"/dang-web/ve-anh/{yc}/trang-thai").json()
    assert js["trang_thai"] == "xong" and [a["ten"] for a in js["anh"]] == ["anh-1.jpg", "anh-2.jpg"]
    assert client.get(js["anh"][0]["url"]).headers["content-type"] == "image/jpeg"
    trang = client.get("/dang-web/sua-anh?loai=bai&id=51").text
    assert "Dùng ảnh này" in trang and f"/dang-web/ve-anh/{yc}/anh-2.jpg" in trang


def test_bai_moi_de_ai_ve_anh_dai_dien_chon_o_xem_truoc(client, db, web_login, make_user, goc):
    csrf = web_login("giam.doc", "director")
    hw, _ = _khoa(db, make_user)
    assert "Codex trên Z2 vẽ 2 ảnh" not in client.get("/dang-web/soan?loai=bai-viet").text
    client.get("/ai/dang-web/viec", headers=hw)
    assert "để AI vẽ ảnh đại diện" in client.get("/dang-web/soan?loai=bai-viet").text
    r = client.post("/dang-web/soan", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), loai="bai_viet", chu_de="Máy in kẹt giấy làm sao",
                                                cach_viet="tu_dong", ve_anh="1"), follow_redirects=False)
    viec_id = r.headers["location"].rsplit("/", 1)[1]
    assert hd.doc(goc, viec_id)["ve_anh"] is True
    assert ve_anh.moi_nhat(goc, viec_id=viec_id) is None  # TUẤN 02/11: viết bài xong mới vẽ, theo dàn ý bài

    wp = _wp_co_hang()
    may = _may(goc, wp, nguoi_viet=_ChatGPTGia())
    may.lam_mot_viec()
    assert hd.doc(goc, viec_id)["trang_thai"] == hd.XEM_TRUOC
    yc = ve_anh.moi_nhat(goc, viec_id=viec_id)
    assert yc and yc["trang_thai"] == ve_anh.CHO and yc["so_anh"] == 3 and "kẹt giấy" in yc["lenh"]
    assert "Mở bài: Nội dung" in yc["lenh"]
    assert "Codex đang vẽ ảnh" in client.get(f"/dang-web/viec/{viec_id}").text
    client.get("/ai/dang-web/ve-anh", headers=hw)
    client.post(f"/ai/dang-web/ve-anh/{yc['id']}/anh", headers={**hw, "Content-Type": "image/png"}, content=_png())
    client.post(f"/ai/dang-web/ve-anh/{yc['id']}/anh", headers={**hw, "Content-Type": "image/png"}, content=_png(mau=(1, 2, 3)))
    client.post(f"/ai/dang-web/ve-anh/{yc['id']}/xong", headers=hw, json={"ly_do": ""})
    trang = client.get(f"/dang-web/viec/{viec_id}").text
    assert 'name="anh_ai"' in trang and f'value="{yc["id"]}/anh-1.jpg" checked' in trang and "Không dùng ảnh AI" in trang
    assert "Chưa có ảnh." not in trang

    base = dict(csrf=csrf, hanh_dong="dang", tieu_de="Máy in kẹt giấy làm sao", tom_tat="", noi_dung_html="<p>Nội dung</p>")
    r = client.post(f"/dang-web/viec/{viec_id}", data=dict(base, form_key=str(uuid.uuid4()), anh_ai=f"{yc['id']}/anh-9.jpg"))
    assert r.status_code == 400 and hd.doc(goc, viec_id)["trang_thai"] == hd.XEM_TRUOC
    client.post(f"/dang-web/viec/{viec_id}", data=dict(base, form_key=str(uuid.uuid4()), anh_ai=f"{yc['id']}/anh-2.jpg"))
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.CHO_DANG and v["anh_web"][0].startswith("ai-")
    with Image.open(goc / "anh-da-xu-ly" / v["anh_web"][0]) as im:
        assert im.size == (1200, 675)
    may.lam_mot_viec()
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.DA_DANG, v.get("loi")
    assert wp.tao[0]["featured_media"] == 901  # ảnh AI anh chọn làm ảnh đại diện


def test_dan_y_bai_lay_tieu_de_h1_h2_vao_lenh_ve():
    html = ("<h1>Máy in Canon 2900 bị kẹt giấy</h1><p>Kẹt giấy là lỗi hay gặp &amp; dễ sửa.</p>"
            "<h2>Nguyên nhân do <b>quả đào</b> mòn</h2><p>x</p><h3>Cách kiểm tra</h3><h2>Thay quả đào tại chỗ</h2>")
    dy = ve_anh.dan_y_bai(html)
    assert dy.splitlines() == ["H1: Máy in Canon 2900 bị kẹt giấy", "H2: Nguyên nhân do quả đào mòn", "  H3: Cách kiểm tra",
                               "H2: Thay quả đào tại chỗ", "Mở bài: Kẹt giấy là lỗi hay gặp & dễ sửa."]
    lenh = ve_anh.lenh_ve("Canon 2900 kẹt giấy", dan_y=dy)
    assert "vẽ 3 ảnh" in lenh and "H2: Thay quả đào tại chỗ" in lenh and "mỗi ảnh minh họa một mục H2" in lenh
    assert ve_anh.dan_y_bai("") == ""


def test_codex_ve_khong_duoc_thi_bao_loi_de_ve_lai(client, db, web_login, make_user, goc):
    web_login("giam.doc", "director")
    hw, _ = _khoa(db, make_user)
    yc = ve_anh.tao(goc, chu_de="Máy in kẹt giấy", wp_id=51)
    client.get("/ai/dang-web/ve-anh", headers=hw)
    r = client.post(f"/ai/dang-web/ve-anh/{yc}/xong", headers=hw, json={"ly_do": "hết lượt"})
    assert r.json()["trang_thai"] == "loi" and "hết lượt" in ve_anh.doc(goc, yc)["loi"]
    assert client.post(f"/ai/dang-web/ve-anh/{yc}/anh", headers={**hw, "Content-Type": "image/png"}, content=_png()).status_code == 409


def _script(monkeypatch, tmp_path):
    import importlib.util
    import pathlib

    monkeypatch.setenv("HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("codex_dang_web_tam", pathlib.Path(__file__).parents[1] / "scripts" / "codex_dang_web.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.HOME = tmp_path
    return m


def test_script_ve_codex_lay_anh_trong_thu_muc_hoac_generated_images(monkeypatch, tmp_path):
    import os
    import stat
    import sys

    m = _script(monkeypatch, tmp_path)
    fake = tmp_path / "codex"
    fake.write_text(f"#!{sys.executable}\nimport os, sys\nfrom PIL import Image\nsys.stdin.read()\n"
                    "d = os.environ.get('NOI_LUU') or os.getcwd()\nos.makedirs(d, exist_ok=True)\n"
                    "for i in (1, 2): Image.frombytes('RGB', (160, 90), os.urandom(160 * 90 * 3)).save(os.path.join(d, f'anh-{i}.png'))\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("TD_CODEX_BIN", str(fake))
    lam = tmp_path / "ve-1"
    lam.mkdir()
    assert [p.name for p in m.ve_codex("vẽ", lam, 2)] == ["anh-1.png", "anh-2.png"]
    # Codex quên chép ảnh ra thư mục: lấy ảnh vừa tạo trong ~/.codex/generated_images
    monkeypatch.setenv("NOI_LUU", str(tmp_path / ".codex" / "generated_images" / "abc"))
    lam2 = tmp_path / "ve-2"
    lam2.mkdir()
    assert len(m.ve_codex("vẽ", lam2, 2)) == 2 and not os.listdir(lam2)


def test_san_pham_ve_anh_cho_mo_ta_khong_lan_voi_bai_viet(client, db, web_login, make_user, goc):
    """TUẤN 02/11: sản phẩm cũng có ô AI vẽ 1 ảnh cho phần mô tả (chọn 1 trong 3); không dùng làm ảnh đại diện."""
    csrf = web_login("giam.doc", "director")
    hw, _ = _khoa(db, make_user)
    wp = _wp_co_hang()
    wp.tat_ca[0]["description"] = "<p>Mở</p><h2>Thông số kỹ thuật</h2><p>x</p>"
    _may(goc, wp).lam_moi_web()
    client.get("/ai/dang-web/ve-anh", headers=hw)  # nhịp Codex
    trang = client.get("/dang-web/sua-anh?loai=sp&id=1").text
    assert "AI vẽ 1 ảnh cho mô tả" in trang and "ve-anh.js" in trang and "Vẽ 3 ảnh mới" in trang
    r = client.post("/dang-web/ve-anh", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), id="1", loai="sp", dan=""), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/dang-web/sua-anh?loai=sp&id=1")
    yc = ve_anh.moi_nhat(goc, wp_id=1, loai="san_pham")
    assert yc and yc["so_anh"] == 3 and "PHẦN MÔ TẢ" in yc["lenh"] and "H2: Thông số kỹ thuật" in yc["lenh"]
    assert ve_anh.moi_nhat(goc, wp_id=1) is None  # bài viết id 1 không thấy yêu cầu của sản phẩm id 1
