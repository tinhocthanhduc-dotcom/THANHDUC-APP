import io
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from app import attachments, service_tickets
from app.config import get_settings
from app.errors import BusinessError
from app.models import AuditLog, Customer
from app.schemas import ServiceTicketIn


def _jpeg(w=4000, h=3000, orientation=None) -> bytes:
    im = Image.new("RGB", (w, h), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = orientation or 1  # Orientation
    exif[0x8825] = {2: (10.0, 45.0, 0.0)}  # GPS
    out = io.BytesIO()
    im.save(out, "JPEG", exif=exif)
    return out.getvalue()


@pytest.fixture
def ticket(db, make_user):
    db.add(Customer(code="KTD-000001", name="Minh Phát"))
    db.commit()
    p = {"dp": make_user("dp", "dispatcher"), "t1": make_user("t1", "technician"), "t2": make_user("t2", "technician"),
         "kho": make_user("kho", "warehouse")}
    t = service_tickets.create(db, p["dp"], ServiceTicketIn(customer_code="KTD-000001", kind="SUA_TAI_CHO", symptom="Kẹt giấy"))
    service_tickets.assign(db, p["dp"], t.id, p["t1"].id, None)
    return t, p


def _code(fn, *args):
    with pytest.raises(BusinessError) as e:
        fn(*args)
    return e.value.code


def test_anh_thu_nho_xoay_dung_va_bo_exif(db, ticket):
    t, p = ticket
    a = attachments.add(db, p["t1"], "service_ticket", str(t.id), "TRUOC", "IMG_1.HEIC.jpg", _jpeg(4000, 3000, orientation=6))
    # Orientation 6 là xoay 90 độ: ảnh ngang 4000x3000 thành dọc 1200x1600.
    assert (a.width, a.height, a.content_type) == (1200, 1600, "image/jpeg")
    path, _ = attachments.open_file(db, p["t1"], a.id)
    assert path.is_relative_to(Path(get_settings().files_dir)) and path.stat().st_size == a.size
    with Image.open(path) as im:
        assert im.size == (1200, 1600) and not im.getexif()
    assert db.scalar(select(AuditLog.action).where(AuditLog.entity == "attachment")) == "attachment.add"


def test_chi_nhan_anh_va_pdf(db, ticket):
    t, p = ticket
    add = attachments.add
    assert _code(add, db, p["t1"], "service_ticket", str(t.id), "SAU", "a.exe", b"MZ\x90\x00") == "BAD_FILE_TYPE"
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, "GIF")
    assert _code(add, db, p["t1"], "service_ticket", str(t.id), "SAU", "a.gif", gif.getvalue()) == "BAD_FILE_TYPE"
    assert _code(add, db, p["t1"], "service_ticket", str(t.id), "SAU", "a.jpg", b"") == "EMPTY_FILE"
    assert _code(add, db, p["t1"], "service_ticket", str(t.id), "XYZ", "a.pdf", b"%PDF-1") == "BAD_TAG"
    assert _code(add, db, p["t1"], "khach_hang", "1", "SAU", "a.pdf", b"%PDF-1") == "BAD_ENTITY"
    pdf = add(db, p["t1"], "service_ticket", str(t.id), "BIEN_BAN", "bien-ban.pdf", b"%PDF-1.4 noi dung")
    assert pdf.content_type == "application/pdf" and pdf.width is None


def test_quyen_theo_phieu(db, ticket):
    t, p = ticket
    pdf = b"%PDF-1.4"
    assert _code(attachments.add, db, p["t2"], "service_ticket", str(t.id), "SAU", "a.pdf", pdf) == "NOT_FOUND"
    a = attachments.add(db, p["dp"], "service_ticket", str(t.id), "KHAC", "a.pdf", pdf)
    assert _code(attachments.open_file, db, p["t2"], a.id) == "NOT_FOUND"
    assert _code(attachments.list_for, db, p["t2"], "service_ticket", str(t.id)) == "NOT_FOUND"
    assert [x.id for x in attachments.list_for(db, p["t1"], "service_ticket", str(t.id))] == [a.id]
    # Phiếu kho: chỉ Kho thêm được.
    assert _code(attachments.add, db, p["kho"], "stock_doc", "999", "KHAC", "a.pdf", pdf) == "NOT_FOUND"


def test_an_tep_co_ly_do_khong_xoa(db, ticket):
    t, p = ticket
    a = attachments.add(db, p["t1"], "service_ticket", str(t.id), "SAU", "a.pdf", b"%PDF-1.4")
    b = attachments.add(db, p["dp"], "service_ticket", str(t.id), "SAU", "b.pdf", b"%PDF-1.4")
    assert _code(attachments.hide, db, p["t1"], a.id, "", None) == "REASON_REQUIRED"
    assert _code(attachments.hide, db, p["t1"], b.id, "Ảnh nhầm", None) == "FORBIDDEN"
    attachments.hide(db, p["t1"], a.id, "Ảnh mờ", a.version)
    assert _code(attachments.hide, db, p["dp"], b.id, "Ảnh nhầm", 99) == "VERSION_CONFLICT"
    attachments.hide(db, p["dp"], b.id, "Ảnh nhầm", None)
    assert attachments.list_for(db, p["t1"], "service_ticket", str(t.id)) == []
    assert _code(attachments.open_file, db, p["t1"], a.id) == "NOT_FOUND"
    assert not attachments.has_visible(db, "service_ticket", t.id, "SAU")
    # File vẫn còn trên đĩa để đối chiếu.
    assert (Path(get_settings().files_dir) / a.path).is_file()


def test_api_tai_len_va_tai_ve(client, login, db):
    db.add(Customer(code="KTD-000001", name="Minh Phát"))
    db.commit()
    dp = login("dp", "dispatcher")
    r = client.post("/service-tickets", json={"customer_code": "KTD-000001", "kind": "MANG_VE", "symptom": "Kẹt giấy"}, headers=dp)
    tid = r.json()["id"]
    r = client.post("/attachments", data={"entity": "service_ticket", "entity_id": str(tid), "tag": "TRUOC"},
                    files={"file": ("anh.jpg", _jpeg(800, 600), "image/jpeg")}, headers=dp)
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    r = client.get(f"/attachments/{aid}/file", headers=dp)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    r = client.get("/attachments", params={"entity": "service_ticket", "entity_id": tid}, headers=dp)
    assert [x["id"] for x in r.json()] == [aid]
    assert client.post(f"/attachments/{aid}/hide", json={"reason": "Ảnh nhầm"}, headers=dp).json()["hidden"] is True
    assert client.get(f"/attachments/{aid}/file", headers=dp).status_code == 404
