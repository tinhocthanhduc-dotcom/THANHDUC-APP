"""Đợt 2 Đăng web (TUẤN duyệt mẫu D6/6, D7/7 ngày 02/11/2026): ghép mã TD cho sản phẩm web, ghi ô SKU lên web,
và ô "Hàng mới nhập chưa có trên web". Chưa đồng bộ giá, còn hàng."""
import uuid

from dang_web import hang_doi as hd
from dang_web.wordpress import WordPress
from test_dang_web_man import _may, _wp_co_hang, goc, nhieu_hang  # noqa: F401  (fixture)
from test_web import web_login  # noqa: F401  (fixture)


def _wp_ghep():
    wp = _wp_co_hang()
    wp.tat_ca += [
        {"id": 3, "name": "Mực in Epson 003 đen 65ml", "sku": "", "status": "publish", "permalink": "https://thanhduc.vn/c",
         "categories": [{"id": 7}], "regular_price": "120000", "images": []},
        {"id": 4, "name": "Khay giấy máy in HP LaserJet M402 tháo máy", "sku": "CU-99", "status": "publish", "permalink": "https://thanhduc.vn/d",
         "categories": [{"id": 9}], "regular_price": "500000", "images": []},
        {"id": 5, "name": "Bao lụa sấy Samsung ML-2161", "sku": "", "status": "publish", "permalink": "https://thanhduc.vn/e",
         "categories": [{"id": 9}], "regular_price": "90000", "images": []},
    ]
    wp.ghi = []

    def ghi_sku(cap):
        wp.ghi.append(list(cap))
        return {i: ("Invalid or duplicated SKU." if ma == "TD-0341" and getattr(wp, "trung", False) else "") for i, ma in cap}

    wp.ghi_sku = ghi_sku
    return wp


def _post(client, csrf, **kw):
    return client.post("/dang-web/ghep-ma", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), **kw), follow_redirects=False)


def test_ghep_ma_goi_y_dung_khong_co_roi_ghi_sku_len_web(client, web_login, goc, nhieu_hang):
    csrf = web_login("ban.hang", "sales")
    wp = _wp_ghep()
    may = _may(goc, wp)
    may.lam_moi_web()
    assert "/dang-web/ghep-ma" in client.get("/dang-web?tab=tren-web&loai=sp").text

    trang = client.get("/dang-web/ghep-ma?nhom=chac").text
    # TD-0123 đã có trong ô SKU trên web nên không hiện; Epson 003 và khay M402 có gợi ý chắc.
    assert "Hộp mực Canon 326" not in trang
    assert 'value="3:TD-0410"' in trang and 'value="4:TD-0341"' in trang and "SKU cũ CU-99" in trang
    assert "Bao lụa sấy Samsung" in client.get("/dang-web/ghep-ma?nhom=khong").text

    # Tick nhiều rồi Đúng hết; một mã không thể ghép vào 2 sản phẩm web.
    r = _post(client, csrf, nhom="chac", hanh_dong="dung_nhieu", chon=["3:TD-0410", "4:TD-0341", "5:TD-0410"])
    assert r.status_code == 303 and "da_luu=2" in r.headers["location"] and "TD-0410" in r.headers["location"]
    assert hd.doc_xac_nhan(goc)["TD-0410"]["dung"] == 3 and hd.doc_xac_nhan(goc)["TD-0341"]["dung"] == 4

    # Không có trong app: bỏ khỏi danh sách.
    assert _post(client, csrf, nhom="khong", hanh_dong="khong_co", web_id="5").status_code == 303
    assert 5 in hd.doc_bo_ghep(goc) and "Bao lụa sấy Samsung" not in client.get("/dang-web/ghep-ma?nhom=khong").text

    # Chọn mã khác cho sản phẩm 2 (Brother) bằng ô tìm; mã không có trong app thì báo.
    r = _post(client, csrf, nhom="khong", hanh_dong="dung", web_id="2", ma="TD-9999")
    assert "loi=" in r.headers["location"]
    assert _post(client, csrf, nhom="khong", hanh_dong="dung", web_id="2", ma="td-0215").status_code == 303
    cho = client.get("/dang-web/ghep-ma?nhom=cho-ghi").text
    assert "Ghi 3 mã lên web" in cho and "Bỏ ghép" in cho
    # Danh sách Trên web hiện mã đã ghép ngay (trước khi ghi lên web).
    assert "TD-0215" in client.get("/dang-web?tab=tren-web&loai=sp&loc=khop").text

    # Bỏ ghép rồi ghép lại.
    _post(client, csrf, nhom="cho-ghi", hanh_dong="bo", web_id="2")
    assert "TD-0215" not in hd.doc_xac_nhan(goc)
    _post(client, csrf, nhom="khong", hanh_dong="dung", web_id="2", ma="TD-0215")

    # Ghi lên web: một việc, chỉ ô SKU; mã trùng trên web báo lỗi từng dòng.
    wp.trung = True
    r = client.post("/dang-web/ghi-ma", data=dict(csrf=csrf, form_key=str(uuid.uuid4())), follow_redirects=False)
    viec_id = r.headers["location"].rsplit("/", 1)[1]
    v = hd.doc(goc, viec_id)
    assert v["kieu"] == "ghi_sku" and v["trang_thai"] == hd.CHO_DANG and len(v["ghi"]) == 3
    # Bấm lần nữa khi đang ghi thì mở lại việc đó, không tạo việc mới.
    r2 = client.post("/dang-web/ghi-ma", data=dict(csrf=csrf, form_key=str(uuid.uuid4())), follow_redirects=False)
    assert r2.headers["location"].endswith(viec_id)
    assert may.lam_mot_viec()
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.DA_DANG and v["so_da_ghi"] == 2
    assert sorted(wp.ghi[0]) == [(2, "TD-0215"), (3, "TD-0410"), (4, "TD-0341")]
    assert v["loi_ghi"][0]["ma"] == "TD-0341" and "đã gắn cho sản phẩm khác" in v["loi_ghi"][0]["loi"]
    trang = client.get(f"/dang-web/viec/{viec_id}").text
    assert "Không ghi được" in trang and "Ghép lại" in trang and "Đã ghi" in trang
    ds = {x["id"]: x for x in hd.doc_json(hd.file_sp_web(goc), {})["ds"]}
    assert ds[3]["sku"] == "TD-0410" and ds[2]["sku"] == "TD-0215" and ds[4]["sku"] == "CU-99"
    # Sau khi ghi: chỉ còn sản phẩm lỗi chờ ghi lại.
    cho = client.get("/dang-web/ghep-ma?nhom=cho-ghi").text
    assert "Ghi 1 mã lên web" in cho and "Khay giấy" in cho
    # Việc ghi mã không cho soạn lại.
    r = client.post(f"/dang-web/viec/{viec_id}", data=dict(csrf=csrf, form_key=str(uuid.uuid4()), hanh_dong="viet_lai"))
    assert r.status_code == 409

    # Ô thứ 5 "Đã có mã TD": Bỏ ghép xóa liên kết trong app và xóa SKU trên web để ghép lại.
    da_co = client.get("/dang-web/ghep-ma?nhom=da-co").text
    assert "Mực in Epson 003" in da_co and "Hộp mực Canon 326" in da_co and "Bỏ ghép" in da_co
    r = _post(client, csrf, nhom="da-co", hanh_dong="bo_web", web_id="3")
    assert r.status_code == 303
    v2 = hd.doc(goc, r.headers["location"].rsplit("/", 1)[1])
    assert v2["kieu"] == "ghi_sku" and v2["ghi"][0]["ma"] == "" and v2["ghi"][0]["ma_cu"] == "TD-0410"
    assert "TD-0410" not in hd.doc_xac_nhan(goc)
    assert may.lam_mot_viec()
    assert wp.ghi[-1] == [(3, "")]
    ds = {x["id"]: x for x in hd.doc_json(hd.file_sp_web(goc), {})["ds"]}
    assert ds[3]["sku"] == "" and "Mực in Epson 003" not in client.get("/dang-web/ghep-ma?nhom=da-co").text


def test_wordpress_ghi_sku_chi_gui_sku_va_doc_loi_theo_thu_tu():
    wp = WordPress.__new__(WordPress)
    goi = []

    def _json(method, duong, **kw):
        goi.append((method, duong, kw["json"]))
        return {"update": [{"id": 3, "sku": "TD-0410"}, {"id": 0, "error": {"code": "product_invalid_sku", "message": "Invalid or duplicated SKU."}}]}

    wp._json = _json
    assert wp.ghi_sku([(3, "TD-0410"), (4, "TD-0341")]) == {3: "", 4: "Invalid or duplicated SKU."}
    assert goi == [("POST", "wc/v3/products/batch", {"update": [{"id": 3, "sku": "TD-0410"}, {"id": 4, "sku": "TD-0341"}]})]


def test_hang_moi_nhap_chua_len_web(client, db, web_login, goc, make_user):
    from app.web.lists import _today
    from test_purchase_orders import purchase_people  # noqa: F401
    from test_purchase_reports import received

    from app.models import Product, Supplier, Warehouse
    db.add_all([Supplier(code="NCC-0001", name="Minh Phát", search_text="minh phat"), Warehouse(code="K58", name="Kho 58"),
                Product(code="TD-0001", name="Máy in Brother HL-L2366DW", search_text="may in brother", sale_price=200000),
                Product(code="TD-0002", name="Mực in Epson 001", search_text="muc in epson", sale_price=90000)])
    db.commit()
    p = {r: make_user("m2." + r, r) for r in ("warehouse", "director")}
    received(db, p, qty=2, day=_today(), product="TD-0001")
    web_login("ban.hang", "sales")
    assert "Hàng mới nhập chưa có trên web" not in client.get("/dang-web?tab=san-pham").text  # chưa lấy danh sách web
    may = _may(goc, _wp_co_hang())
    may.lam_moi_web()
    trang = client.get("/dang-web?tab=san-pham").text
    assert "Hàng mới nhập chưa có trên web" in trang and "TD-0002" not in trang
    o = trang[trang.index('class="dw-moi"'):]
    o = o[:o.index("</section>")]
    # TUẤN 05/11: 5 dòng gọn, có tồn + giá bán + nút Soạn từng dòng, không còn ô tick
    assert "/dang-web/soan?loai=san-pham&amp;ma=TD-0001" in o and "200.000" in o and "Tồn <b>2</b>" in o and 'type="checkbox"' not in o
    # Ghép mã với sản phẩm web rồi thì không còn gợi ý.
    hd.ghep_ma(goc, "TD-0001", 2)
    assert "Hàng mới nhập chưa có trên web" not in client.get("/dang-web?tab=san-pham").text
    hd.bo_ghep_web(goc, 2)
    assert 'value="TD-0001"' in client.get("/dang-web?tab=tren-web&loai=sp&loc=chua-len&moi=1").text


def test_app_khong_can_httpx_khi_mo_man_sua_anh():
    """Máy app (api) không cài httpx — chỉ máy đăng web có. Lỗi 500 Sửa ảnh ngày 02/11/2026."""
    import subprocess
    import sys

    ma = ("import sys; sys.modules['httpx'] = None\n"
          "from app.web.dang_web import _anh_cua_muc\n"
          "it = {'anhs': [{'id': 5, 'src': 'https://w/a.jpg'}], 'anhs_mo_ta': [{'id': 0, 'src': 'https://w/a-300x300.jpg'}]}\n"
          "assert _anh_cua_muc('san_pham', it)[1]['trung']\n"
          "from dang_web.so_ten import slug\n")
    kq = subprocess.run([sys.executable, "-c", ma], capture_output=True, text=True)
    assert kq.returncode == 0, kq.stderr[-800:]
