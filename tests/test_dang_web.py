"""Công cụ đăng web (dang_web): ảnh, ghép danh mục, gọi WordPress, đọc kết quả AI. Không gọi mạng thật."""
import io
import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image, ImageDraw

from dang_web import anh, dang
from dang_web.__main__ import chon_tu_dong, main as dang_main
from dang_web.cau_hinh import CauHinh
from dang_web.du_lieu import READ_ONLY_OPTIONS, SanPham, url_psycopg
from dang_web.viet_bai import KHUON_SAN_PHAM, NguoiViet, lam_sach_html
from dang_web.wordpress import LoiWeb, WordPress, slug, tim_danh_muc


def _sp(**kw):
    base = dict(code="TD-0001", name="Hop muc Canon 326", group_name="Hộp mực", unit="cái",
                sale_price=355_000, warranty_months=0, ton=Decimal("5"))
    base.update(kw)
    return SanPham(**base)


def _anh_mau(path, nen=(238, 236, 230)):
    im = Image.new("RGB", (900, 600), nen)
    ImageDraw.Draw(im).rectangle((300, 150, 600, 450), fill=(20, 20, 20))
    im.save(path)
    return path


# ---------- ảnh ----------
def test_xu_ly_anh_vuong_nen_trang_co_logo(tmp_path):
    data = anh.xu_ly(_anh_mau(tmp_path / "TD-0001.jpg", nen=(255, 255, 255)), tach_nen=False, kich_thuoc=800)
    im = Image.open(io.BytesIO(data))
    assert im.format == "WEBP" and im.size == (800, 800)
    im = im.convert("RGB")
    assert min(im.getpixel((5, 5))) > 245  # góc trên trái trắng
    assert im.getpixel((400, 400))[0] < 60  # sản phẩm nằm giữa
    vung_logo = im.crop((800 - 150, 800 - 150, 800 - 25, 800 - 25))
    assert min(min(p) for p in vung_logo.get_flattened_data()) < 200  # có logo góc dưới phải


def test_xu_ly_anh_jpg_va_cat_vien(tmp_path):
    data = anh.xu_ly(_anh_mau(tmp_path / "a.png", nen=(255, 255, 255)), tach_nen=False, kich_thuoc=600, dinh_dang="jpg", logo=None)
    im = Image.open(io.BytesIO(data)).convert("RGB")
    assert im.size == (600, 600)
    # Viền trắng thừa đã cắt: sản phẩm chiếm gần hết vùng trong lề 10%.
    assert im.getpixel((75, 300))[0] < 60


def test_tim_anh_theo_ma(tmp_path):
    for ten in ("TD-0001-2.jpg", "TD-0001.jpg", "td-0001_3.png", "TD-00011.jpg", "TD-0002.jpg", "TD-0001.txt"):
        (tmp_path / ten).write_bytes(b"x")
    (tmp_path / "TD-0001").mkdir()
    (tmp_path / "TD-0001" / "z.webp").write_bytes(b"x")
    assert [p.name for p in anh.tim_anh(tmp_path, "td-0001")] == ["TD-0001.jpg", "TD-0001-2.jpg", "td-0001_3.png", "z.webp"]


# ---------- danh mục, slug, giá ----------
def test_slug_va_ghep_danh_muc():
    assert slug("Hộp mực Canon 326 – Đen") == "hop-muc-canon-326-den"
    dm = [{"id": 5, "name": "Hộp Mực &amp; Linh kiện", "slug": "hop-muc"}, {"id": 7, "name": "Mực in laser", "slug": "muc-laser"}]
    assert tim_danh_muc("hop muc & linh kien", dm)["id"] == 5
    assert tim_danh_muc("Hộp mực", dm, {"Hộp mực": "Mực in laser"})["id"] == 7
    assert tim_danh_muc("Máy in", dm) is None


def test_payload_san_pham_luon_la_nhap_va_gia_vat():
    ch = CauHinh(vat_phan_tram=8)
    d = {"ten_web": "Hộp mực Canon 326", "slug": "Hộp mực Canon 326", "mo_ta_html": "<p>a</p>", "mo_ta_ngan_html": "<ul><li>b</li></ul>",
         "seo_title": "t", "seo_description": "d", "tu_khoa_chinh": "k"}
    p = dang.payload_san_pham(_sp(sale_price=355_000), d, ch, [11, 12], {"id": 5})
    assert p["status"] == "draft" and p["sku"] == "TD-0001"
    assert p["regular_price"] == "383000"  # 355.000 + 8% = 383.400, làm tròn nghìn
    assert p["slug"] == "hop-muc-canon-326"
    assert p["images"] == [{"id": 11}, {"id": 12}] and p["categories"] == [{"id": 5}]
    assert {m["key"] for m in p["meta_data"]} == {"_yoast_wpseo_title", "_yoast_wpseo_metadesc", "_yoast_wpseo_focuskw"}
    assert "regular_price" not in dang.payload_san_pham(_sp(sale_price=0), d, CauHinh(), [], None)
    assert dang.payload_san_pham(_sp(), d, CauHinh(), [], None)["regular_price"] == "355000"


def test_cau_hinh_doc_env():
    ch = CauHinh.tu_moi_truong({"WP_APP_PASSWORD": "abcd efgh ijkl", "WEB_TACH_NEN": "0", "WEB_VAT_PHAN_TRAM": "10",
                                "WEB_AI_EFFORT": "bừa", "WP_URL": "https://thanhduc.vn/"})
    assert ch.wp_app_password == "abcdefghijkl" and not ch.tach_nen and ch.vat_phan_tram == 10
    assert ch.ai_effort == "medium" and ch.wp_url == "https://thanhduc.vn" and ch.ai_model == "claude-opus-5-5"


# ---------- database chỉ đọc ----------
def test_url_va_ket_noi_chi_doc(monkeypatch):
    assert url_psycopg("postgresql+psycopg://u:p@db:5432/x") == "postgresql://u:p@db:5432/x"
    assert "default_transaction_read_only=on" in READ_ONLY_OPTIONS
    import psycopg
    from dang_web import du_lieu

    goi = {}
    monkeypatch.setattr(psycopg, "connect", lambda url, **kw: goi.update(url=url, **kw) or "conn")
    assert du_lieu.ket_noi("postgresql+psycopg://u:p@db/x") == "conn"
    assert goi["options"] == READ_ONLY_OPTIONS and goi["url"] == "postgresql://u:p@db/x"


def test_doc_san_pham_tu_database_that(engine):
    from sqlalchemy import text

    from dang_web.du_lieu import dem_san_pham, ket_noi, lay_san_pham

    with engine.begin() as c:
        c.execute(text("insert into products(code,name,group_name,unit,sale_price,warranty_months,active,is_test,search_text,version) values"
                       "('TD-0901','Muc A','Hộp mực','cái',100000,0,true,false,'',1),('TD-0902','Muc B','Hộp mực','cái',0,0,false,false,'',1)"))
        c.execute(text("insert into warehouses(code,name,address,kind,active,version) values ('K1','Kho 1','','KHO',true,1),('K2','Kho cũ','','KHO',false,1)"))
        c.execute(text("insert into stock_balances(warehouse_code,product_code,qty,value,avg_cost) values ('K1','TD-0901',3,300000,100000),('K2','TD-0901',2,200000,100000)"))
    url = str(engine.url.render_as_string(hide_password=False))
    with ket_noi(url) as conn:
        hang = lay_san_pham(conn, ["td-0901", "TD-0902"])
        assert list(hang) == ["TD-0901"]  # mã ngừng dùng bị bỏ
        assert hang["TD-0901"].ton == 3 and hang["TD-0901"].con_hang  # kho ngừng dùng không tính
        assert dem_san_pham(conn) == 1
        with pytest.raises(Exception):
            conn.execute("insert into product_groups(name) values ('x')")


# ---------- WordPress ----------
def _wp(handler):
    return WordPress("https://web.test", "admin", "abcd", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_wordpress_tao_sp_luon_nhap_va_xac_thuc():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(201, json={"id": 99, "permalink": "https://web.test/?p=99"})

    wp = _wp(handler)
    moi = wp.tao_sp({"name": "x", "status": "publish"})
    assert moi["id"] == 99
    assert json.loads(seen[0].content)["status"] == "draft"
    assert seen[0].url.path == "/wp-json/wc/v3/products"
    assert seen[0].headers["Authorization"] == "Basic YWRtaW46YWJjZA=="


def test_wordpress_loi_tra_ve_thong_bao():
    wp = _wp(lambda req: httpx.Response(401, json={"code": "rest_not_logged_in", "message": "Bạn chưa đăng nhập"}))
    with pytest.raises(LoiWeb) as e:
        wp.toi()
    assert e.value.status == 401 and "rest_not_logged_in" in str(e.value)


def test_tai_anh_webp_bi_chan_thi_doi_jpg(tmp_path):
    goi = []

    def handler(req):
        if req.url.path.endswith("/wp/v2/media"):
            goi.append(req.headers["Content-Type"])
            if req.headers["Content-Type"] == "image/webp":
                return httpx.Response(500, json={"code": "rest_upload_sniff_error", "message": "không hỗ trợ"})
            return httpx.Response(201, json={"id": 7})
        return httpx.Response(200, json={"id": 7})

    src = _anh_mau(tmp_path / "TD-0001.jpg")
    ch = CauHinh(thu_muc=tmp_path, anh_kich_thuoc=600)
    ds = dang.xu_ly_anh(ch, "hop-muc", [src], False)
    assert ds[0][0] == "hop-muc.webp" and (tmp_path / "anh-da-xu-ly" / "hop-muc.webp").exists()
    assert dang.tai_anh_len(_wp(handler), ch, ds, "Hộp mực", [src], False) == [7]
    assert goi == ["image/webp", "image/jpeg"]


def test_tim_sp_theo_sku_va_phan_trang():
    def handler(req):
        if req.url.path.endswith("/products/categories"):
            page = int(req.url.params["page"])
            return httpx.Response(200, json=[{"id": page}], headers={"X-WP-TotalPages": "2"})
        assert req.url.params["sku"] == "TD-0001"
        return httpx.Response(200, json=[{"id": 3, "status": "publish"}])

    wp = _wp(handler)
    assert wp.tim_sp_theo_sku("TD-0001")["id"] == 3
    assert [d["id"] for d in wp.danh_muc_sp()] == [1, 2]


# ---------- AI ----------
class _FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _resp(data, stop="end_turn"):
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="text", text="đang tìm"), SimpleNamespace(type="text", text=json.dumps(data, ensure_ascii=False))],
        usage=SimpleNamespace(input_tokens=2000, output_tokens=1500, cache_read_input_tokens=0, server_tool_use=SimpleNamespace(web_search_requests=1)),
    )


def _du_lieu_sp():
    return {k: "x" for k in KHUON_SAN_PHAM["required"]} | {
        "can_kiem_tra": ["máy tương thích"], "mo_ta_html": '<h2 style="color:red">A</h2><script>alert(1)</script><p onclick="x()">B</p>'}


def test_ai_viet_san_pham_doc_json_va_lam_sach():
    beta = _FakeMessages([_resp(_du_lieu_sp())])
    client = SimpleNamespace(beta=SimpleNamespace(messages=beta), messages=_FakeMessages([]))
    kq = NguoiViet("", client=client).viet_san_pham(_sp(warranty_months=12), "Dùng cho LBP 6200d", 2, "0909")
    assert kq.du_lieu["mo_ta_html"] == "<h2>A</h2><p>B</p>"
    call = beta.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["output_config"]["format"]["schema"] is KHUON_SAN_PHAM
    noi_dung = call["messages"][0]["content"]
    assert "TD-0001" in noi_dung and "LBP 6200d" in noi_dung and "12 tháng" in noi_dung and "0909" in noi_dung
    assert "sale_price" not in noi_dung and "355000" not in noi_dung  # không đưa giá cho AI
    assert kq.chi_phi_vnd > 0


def test_ai_loi_400_thi_thu_lai_khong_tra_web():
    loi = Exception("bad")
    loi.status_code = 400
    plain = _FakeMessages([_resp(_du_lieu_sp())])
    client = SimpleNamespace(beta=SimpleNamespace(messages=_FakeMessages([loi])), messages=plain)
    kq = NguoiViet("", client=client).viet_san_pham(_sp())
    assert "tools" not in plain.calls[0] and kq.ghi_chu


def test_ai_bi_cat_hoac_tu_choi_bao_loi():
    for stop in ("max_tokens", "refusal"):
        client = SimpleNamespace(beta=SimpleNamespace(messages=_FakeMessages([_resp({}, stop)])), messages=None)
        with pytest.raises(RuntimeError):
            NguoiViet("", client=client).viet_san_pham(_sp())


def test_lam_sach_html_bo_h1():
    assert lam_sach_html("<h1>T</h1><p>a</p>") == "<p>a</p>"


# ---------- chọn mã tự động, trạng thái, chạy thử ----------
def test_chon_tu_dong_bo_ma_da_dang(tmp_path):
    ch = CauHinh(thu_muc=tmp_path)
    ch.tao_thu_muc()
    for ten in ("TD-0003.jpg", "TD-0001-2.jpg", "TD-0002.png", "ghi-chu.jpg"):
        (ch.anh_goc / ten).write_bytes(b"x")
    (ch.anh_goc / "TD-0004").mkdir()
    tt = {"san_pham": {"TD-0002": {}}, "bai_viet": {}}
    assert chon_tu_dong(ch, tt, 5) == ["TD-0001", "TD-0003", "TD-0004"]


def test_trang_thai_va_khoa(tmp_path):
    p = tmp_path / "da-dang.json"
    tt = dang.doc_trang_thai(p)
    tt["san_pham"]["TD-0001"] = {"id": 1}
    dang.ghi_trang_thai(p, tt)
    assert dang.doc_trang_thai(p)["san_pham"]["TD-0001"]["id"] == 1
    with dang.khoa(tmp_path):
        with pytest.raises(RuntimeError):
            with dang.khoa(tmp_path):
                pass


def test_chay_thu_san_pham_khong_dang(tmp_path, monkeypatch):
    import dang_web.__main__ as m

    monkeypatch.setenv("WEB_THU_MUC_TRONG", str(tmp_path))
    monkeypatch.setenv("WEB_TACH_NEN", "false")
    monkeypatch.setenv("WEB_ANH_KICH_THUOC", "600")

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(m, "ket_noi", lambda url: _Conn())
    monkeypatch.setattr(m, "lay_san_pham", lambda conn, codes: {"TD-0001": _sp()})
    beta = _FakeMessages([_resp(_du_lieu_sp() | {"ten_web": "Hộp mực Canon 326", "slug": "hop-muc-canon-326"})])
    monkeypatch.setattr(m, "_nguoi_viet", lambda ch: NguoiViet("", client=SimpleNamespace(beta=SimpleNamespace(messages=beta))))
    monkeypatch.setattr(m, "_wp", lambda ch: pytest.fail("chạy thử không được gọi web"))
    (tmp_path / "anh-goc").mkdir()
    _anh_mau(tmp_path / "anh-goc" / "TD-0001.jpg")

    assert dang_main(["san-pham", "--ma", "TD-0001", "--thu"]) == 0
    assert (tmp_path / "xem-truoc" / "TD-0001.html").exists()
    assert (tmp_path / "anh-da-xu-ly" / "hop-muc-canon-326.webp").exists()
    assert dang.doc_trang_thai(tmp_path / "da-dang.json")["san_pham"] == {}  # chạy thử không đánh dấu đã đăng


# ---------- ChatGPT (OpenAI) và chế độ không AI ----------
class _FakeResponses:
    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.items.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _oa_resp(data, status="completed", refusal=False):
    content = [SimpleNamespace(type="refusal", refusal="no")] if refusal else [SimpleNamespace(type="output_text", text="x")]
    return SimpleNamespace(
        status=status,
        output=[SimpleNamespace(type="web_search_call"), SimpleNamespace(type="message", content=content)],
        output_text=json.dumps(data, ensure_ascii=False),
        usage=SimpleNamespace(input_tokens=1200, output_tokens=900),
    )


def test_openai_viet_san_pham():
    from dang_web.viet_bai import NguoiVietOpenAI

    fake = _FakeResponses([_oa_resp(_du_lieu_sp())])
    kq = NguoiVietOpenAI("", client=SimpleNamespace(responses=fake)).viet_san_pham(_sp(), "LBP 6200d")
    call = fake.calls[0]
    assert call["model"] == "gpt-5.5" and call["tools"] == [{"type": "web_search"}]
    assert call["text"]["format"]["strict"] is True and call["text"]["format"]["schema"] is KHUON_SAN_PHAM
    assert "LBP 6200d" in call["input"] and kq.du_lieu["mo_ta_html"] == "<h2>A</h2><p>B</p>"
    assert kq.so_lan_tra_web == 1 and kq.chi_phi_vnd == 0 and "token" in kq.mo_ta_chi_phi


def test_openai_loi_400_thu_lai_va_bao_loi():
    from dang_web.viet_bai import NguoiVietOpenAI

    loi = Exception("bad")
    loi.status_code = 400
    fake = _FakeResponses([loi, _oa_resp(_du_lieu_sp())])
    kq = NguoiVietOpenAI("", client=SimpleNamespace(responses=fake)).viet_san_pham(_sp())
    assert "tools" not in fake.calls[1] and "reasoning" not in fake.calls[1] and kq.ghi_chu
    for r in (_oa_resp({}, status="incomplete"), _oa_resp({}, refusal=True)):
        with pytest.raises(RuntimeError):
            NguoiVietOpenAI("", client=SimpleNamespace(responses=_FakeResponses([r]))).viet_san_pham(_sp())


def test_chon_nguoi_viet_theo_khoa(monkeypatch):
    import sys

    from dang_web.viet_bai import NguoiViet, NguoiVietMau, NguoiVietOpenAI, chon_nguoi_viet

    # CI không cài thư viện AI (chỉ có trong image dang-web): thay bằng module giả.
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda **kw: SimpleNamespace(kw=kw)))
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=lambda **kw: SimpleNamespace(kw=kw)))

    assert isinstance(chon_nguoi_viet(CauHinh.tu_moi_truong({"OPENAI_API_KEY": "sk-x"})), NguoiVietOpenAI)
    ch = CauHinh.tu_moi_truong({"OPENAI_API_KEY": "sk-x", "ANTHROPIC_API_KEY": "sk-ant", "WEB_AI_NHA_CUNG_CAP": "OpenAI"})
    assert isinstance(chon_nguoi_viet(ch), NguoiVietOpenAI)
    assert type(chon_nguoi_viet(CauHinh.tu_moi_truong({"ANTHROPIC_API_KEY": "sk-ant"}))) is NguoiViet
    assert isinstance(chon_nguoi_viet(CauHinh.tu_moi_truong({"WEB_AI_NHA_CUNG_CAP": "mau"})), NguoiVietMau)
    with pytest.raises(RuntimeError):
        chon_nguoi_viet(CauHinh.tu_moi_truong({}))


def test_khong_ai_dien_mau():
    from dang_web.viet_bai import KHUON_SAN_PHAM, NguoiVietMau

    d = NguoiVietMau().viet_san_pham(_sp(warranty_months=12, name="Hộp mực <Canon> 326"), "Dùng cho LBP 6200d", hotline="0909").du_lieu
    assert set(KHUON_SAN_PHAM["required"]) <= set(d)
    assert "&lt;Canon&gt;" in d["mo_ta_html"] and "LBP 6200d" in d["mo_ta_html"] and "0909" in d["mo_ta_html"]
    assert "12 tháng" in d["mo_ta_ngan_html"] and len(d["seo_title"]) <= 60
    with pytest.raises(RuntimeError):
        NguoiVietMau().viet_bai("x")


def test_chatgpt_web_soan_lenh_roi_doc_tra_loi(tmp_path):
    from dang_web.viet_bai import ChoTraLoi, NguoiVietChatGPTWeb, doc_json_tu_van_ban

    nv = NguoiVietChatGPTWeb(tmp_path)
    with pytest.raises(ChoTraLoi) as e:
        nv.viet_san_pham(_sp(), "LBP 6200d")
    lenh = e.value.file_lenh.read_text(encoding="utf-8")
    assert "TD-0001" in lenh and "LBP 6200d" in lenh and '"mo_ta_html"' in lenh and "JSON" in lenh
    tra_loi = "Đây là kết quả:\n```json\n" + json.dumps(_du_lieu_sp() | {"can_kiem_tra": "số trang"}, ensure_ascii=False) + "\n```"
    e.value.file_tra_loi.write_text(tra_loi, encoding="utf-8")
    kq = nv.viet_san_pham(_sp())
    assert kq.du_lieu["mo_ta_html"] == "<h2>A</h2><p>B</p>" and kq.du_lieu["can_kiem_tra"] == ["số trang"]
    with pytest.raises(RuntimeError):
        doc_json_tu_van_ban("không có json")


def test_chay_chatgpt_web_bao_cho(tmp_path, monkeypatch, capsys):
    import dang_web.__main__ as m

    monkeypatch.setenv("WEB_THU_MUC_TRONG", str(tmp_path))

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(m, "ket_noi", lambda url: _Conn())
    monkeypatch.setattr(m, "lay_san_pham", lambda conn, codes: {"TD-0001": _sp()})
    (tmp_path / "anh-goc").mkdir()
    _anh_mau(tmp_path / "anh-goc" / "TD-0001.jpg")
    assert m.main(["san-pham", "--ma", "TD-0001", "--thu", "--chatgpt-web"]) == 0
    out = capsys.readouterr().out
    assert "CHỜ ANH" in out and "chờ dán ChatGPT 1" in out
    assert (tmp_path / "chatgpt" / "TD-0001.txt").exists()
