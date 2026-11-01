"""Codex trên máy chủ làm việc Đăng web qua cửa /ai/dang-web (TUẤN 01/11/2026, đợt 2): khóa riêng phạm vi dang_web,
chỉ lấy câu lệnh và trả câu trả lời nháp; Codex lỗi hoặc tắt thì việc chuyển sang API hoặc dán tay, không mất việc."""
import json
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

import app.web.dang_web as man
from app import ai_agent as svc
from app.models import AuditLog
from dang_web import hang_doi as hd
from dang_web.cau_hinh import CauHinh
from dang_web.du_lieu import SanPham
from dang_web.may_chay import MayDangWeb
from test_dang_web_man import _soan_sp, goc, san_pham  # noqa: F401  (fixture)
from test_web import web_login  # noqa: F401  (fixture)

TRA_LOI = "Kết quả:\n```json\n" + json.dumps({
    "ten_web": "Hộp mực Canon 326", "slug": "hop-muc-canon-326", "mo_ta_ngan_html": "<p>a</p>", "mo_ta_html": "<p>b</p>",
    "seo_title": "t", "seo_description": "d", "tu_khoa_chinh": "k", "alt_anh": "a", "can_kiem_tra": []}, ensure_ascii=False) + "\n```"


def _may(goc):
    sp = SanPham(code="TD-0123", name="Hộp mực Canon 326", group_name="Hộp mực", unit="cái", sale_price=355_000,
                 warranty_months=0, ton=Decimal("3"))
    return MayDangWeb(CauHinh(thu_muc=goc, tach_nen=False, anh_kich_thuoc=400), ket_noi_db=lambda code: sp)


def _khoa(db, make_user):
    director = db.scalar(select(svc.User).where(svc.User.username == "giam.doc")) or make_user("giam.doc", "director")
    web = svc.create_key(db, director, "dang-web")
    dp = svc.create_key(db, director, "dieu-phoi")
    return {"Authorization": "Bearer " + web}, {"Authorization": "Bearer " + dp}


def test_codex_lay_cau_lenh_tra_loi_thanh_ban_xem_truoc(client, db, web_login, make_user, goc, san_pham):
    csrf = web_login("giam.doc", "director")
    hw, hdp = _khoa(db, make_user)
    # Mỗi khóa chỉ dùng đúng phạm vi của nó.
    assert client.get("/ai/dang-web/viec", headers=hdp).status_code == 403
    assert client.get("/ai/tong-quan", headers=hw).status_code == 403
    assert client.get("/ai/dang-web/viec", headers={**hw, "cf-ray": "x"}).status_code == 403

    assert not hd.codex_song(goc)
    assert client.get("/ai/dang-web/viec", headers=hw).json() == []
    assert hd.codex_song(goc)  # lần hỏi việc cũng là nhịp: app biết Codex đang chạy
    trang = client.get("/dang-web/soan?ma=TD-0123").text
    assert 'value="codex" checked' in trang

    viec_id = _soan_sp(client, csrf, cach_viet="codex").headers["location"].rsplit("/", 1)[1]
    may = _may(goc)
    may.lam_mot_viec()
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.CHO_DAN and v["cach_viet"] == "codex"
    assert "Chờ Codex" in client.get("/dang-web?tab=san-pham").text
    assert "Codex trên máy chủ đang viết" in client.get(f"/dang-web/viec/{viec_id}").text

    ds = client.get("/ai/dang-web/viec", headers=hw).json()
    assert [x["id"] for x in ds] == [viec_id] and "TD-0123" in ds[0]["lenh"]
    assert "sale_price" not in json.dumps(ds) and "355" not in json.dumps(ds).replace(ds[0]["lenh"], "")

    r = client.post(f"/ai/dang-web/viec/{viec_id}/tra-loi", headers=hw, json={"tra_loi": "không có JSON"})
    assert r.status_code == 422 and hd.doc(goc, viec_id)["trang_thai"] == hd.CHO_DAN
    r = client.post(f"/ai/dang-web/viec/{viec_id}/tra-loi", headers=hw, json={"tra_loi": TRA_LOI})
    assert r.status_code == 200 and hd.doc(goc, viec_id)["trang_thai"] == hd.CHO_SOAN
    assert client.post(f"/ai/dang-web/viec/{viec_id}/tra-loi", headers=hw, json={"tra_loi": TRA_LOI}).status_code == 409
    assert db.scalar(select(AuditLog).where(AuditLog.action == "ai.dang_web_answer", AuditLog.entity_id == viec_id))

    may.lam_mot_viec()
    v = hd.doc(goc, viec_id)
    assert v["trang_thai"] == hd.XEM_TRUOC and v["noi_dung"]["ten_web"] == "Hộp mực Canon 326"  # vẫn chờ anh xem và bấm Đăng


def test_codex_khong_lam_duoc_chuyen_api_hoac_dan_tay(client, db, web_login, make_user, goc, san_pham):
    csrf = web_login("giam.doc", "director")
    hw, _ = _khoa(db, make_user)
    client.get("/ai/dang-web/viec", headers=hw)
    may = _may(goc)
    a = _soan_sp(client, csrf, cach_viet="codex").headers["location"].rsplit("/", 1)[1]
    may.lam_mot_viec()
    # Việc không giao cho Codex thì Codex không trả lời được.
    hd.sua(goc, a, lambda x: x.update(cach_viet="chatgpt_web"))
    assert client.post(f"/ai/dang-web/viec/{a}/tra-loi", headers=hw, json={"tra_loi": TRA_LOI}).status_code == 403
    hd.sua(goc, a, lambda x: x.update(cach_viet="codex"))

    hd.ghi_nhip(goc, {"ai": None})  # máy đăng web không có khóa API → về dán tay
    r = client.post(f"/ai/dang-web/viec/{a}/khong-lam-duoc", headers=hw, json={"ly_do": "hết lượt"})
    v = hd.doc(goc, a)
    assert r.status_code == 200 and v["cach_viet"] == "chatgpt_web" and v["trang_thai"] == hd.CHO_DAN and "hết lượt" in v["loi"]

    hd.sua(goc, a, lambda x: x.update(cach_viet="codex"))
    hd.ghi_nhip(goc, {"ai": "OpenAI gpt"})  # có khóa API → AI tự động soạn lại
    client.post(f"/ai/dang-web/viec/{a}/khong-lam-duoc", headers=hw, json={})
    v = hd.doc(goc, a)
    assert v["cach_viet"] == "tu_dong" and v["trang_thai"] == hd.CHO_SOAN and not v["lenh_chatgpt"]


def test_codex_tat_lau_thi_viec_tu_chuyen(goc, monkeypatch):
    v_id = hd.tao_viec(goc, "san_pham", {"ma": "TD-0123", "ten": "x", "cach_viet": "codex"})
    hd.sua(goc, v_id, lambda x: x.update(trang_thai=hd.CHO_DAN, lenh_chatgpt="lệnh", file_tra_loi="TD-0123-tra-loi.txt"))
    assert hd.chuyen_codex_qua_han(goc, co_api=False) == 0  # mới chờ, chưa quá hạn
    sau = hd.bay_gio() + timedelta(minutes=hd.CODEX_CHO_PHUT + 1)
    monkeypatch.setattr(hd, "bay_gio", lambda: sau)
    assert hd.chuyen_codex_qua_han(goc, co_api=False) == 1
    v = hd.doc(goc, v_id)
    assert v["cach_viet"] == "chatgpt_web" and v["trang_thai"] == hd.CHO_DAN and v["lenh_chatgpt"] == "lệnh"
    # Codex còn chạy (vừa hỏi việc) thì không chuyển.
    hd.sua(goc, v_id, lambda x: x.update(cach_viet="codex"))
    hd.ghi_nhip_codex(goc)
    assert hd.chuyen_codex_qua_han(goc, co_api=True) == 0
