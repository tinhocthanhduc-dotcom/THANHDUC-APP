from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app import attachments, service_charges, service_sla
from app import service_tickets as svc
from app.errors import BusinessError
from app.models import (
    AuditLog,
    Customer,
    CustomerSite,
    Equipment,
    Product,
    ServiceStep,
    ServiceTicket,
)
from app.schemas import ServiceCharges, ServiceTicketIn


@pytest.fixture
def people(db, make_user):
    db.add_all([
        Customer(code="KTD-000001", name="Công ty Minh Phát", search_text="cong ty minh phat"),
        Customer(code="KTD-000002", name="Khách đã gộp", status="NGUNG"),
        Product(code="TD-0001", name="Máy in HP"),
    ])
    db.commit()
    dp = make_user("dieu.phoi", "dispatcher")
    db.add_all([
        CustomerSite(customer_code="KTD-000001", name="Văn phòng Q3", created_by=dp.id),
        CustomerSite(customer_code="KTD-000001", name="Kho cũ", active=False, created_by=dp.id),
        Equipment(serial="AB123", product_code="TD-0001", customer_code="KTD-000001", created_by=dp.id),
        Equipment(serial="XY999", product_code="TD-0001", customer_code="KTD-000002", created_by=dp.id),
    ])
    db.commit()
    return {
        "dp": dp,
        "kd": make_user("kinh.doanh", "sales"),
        "t1": make_user("tho.an", "technician"),
        "t2": make_user("tho.binh", "technician"),
        "kho": make_user("thu.kho", "warehouse"),
    }


def _new(db, p, **kw):
    body = {"customer_code": "KTD-000001", "kind": "SUA_TAI_CHO", "symptom": "Máy in kẹt giấy", **kw}
    return svc.create(db, p["kd"], ServiceTicketIn(**body))


def _code(fn, *args, **kw):
    with pytest.raises(BusinessError) as e:
        fn(*args, **kw)
    return e.value.code


def test_nhan_don_cap_so_va_han_giao(db, people):
    before = datetime.now(UTC)
    t = _new(db, people, serial="ab 123", priority="KHAN")
    assert t.number.startswith("DV-") and t.number.endswith("-00001")
    assert t.status == "MOI" and t.serial == "AB123"
    assert before + timedelta(minutes=15) <= t.due_at <= datetime.now(UTC) + timedelta(minutes=15)
    assert _new(db, people).number.endswith("-00002")
    assert [s.action for s in svc.load(db, people["dp"], t.id).steps] == ["create"]
    assert db.scalar(select(AuditLog.action).where(AuditLog.entity == "service_ticket")) == "service_ticket.create"


def test_dia_diem_va_may_phai_dung_khach(db, people):
    site = db.scalar(select(CustomerSite.id).where(CustomerSite.name == "Văn phòng Q3"))
    old = db.scalar(select(CustomerSite.id).where(CustomerSite.name == "Kho cũ"))
    assert _new(db, people, site_id=site, serial="ab123").site_id == site
    assert _code(_new, db, people, site_id=old) == "BAD_SITE"
    assert _code(_new, db, people, site_id=999999) == "BAD_SITE"
    assert _code(_new, db, people, serial="XY999") == "BAD_SERIAL"
    assert _code(_new, db, people, serial="CHUA-CO") == "BAD_SERIAL"


def test_nhan_don_kiem_quyen_va_khach(db, people):
    body = ServiceTicketIn(customer_code="KTD-000001", kind="BAO_HANH", symptom="Lỗi mực")
    assert _code(svc.create, db, people["t1"], body) == "FORBIDDEN"
    assert _code(svc.create, db, people["kho"], body) == "FORBIDDEN"
    assert _code(svc.create, db, people["kd"], body.model_copy(update={"customer_code": "KTD-000002"})) == "BAD_CUSTOMER"
    assert _code(svc.create, db, people["kd"], body.model_copy(update={"customer_code": "KTD-999999"})) == "BAD_CUSTOMER"
    assert db.scalar(select(ServiceTicket.id)) is None


def test_giao_tho_chi_dieu_phoi_va_phai_la_tho(db, people):
    t = _new(db, people)
    assert _code(svc.assign, db, people["kd"], t.id, people["t1"].id, None) == "FORBIDDEN"
    assert _code(svc.assign, db, people["dp"], t.id, people["kho"].id, None) == "BAD_ASSIGNEE"
    t = svc.assign(db, people["dp"], t.id, people["t1"].id, t.version)
    assert t.status == "DA_GIAO" and t.assignee_id == people["t1"].id
    assert t.due_at <= datetime.now(UTC) + timedelta(minutes=15)
    assert _code(svc.assign, db, people["dp"], t.id, people["t1"].id, None) == "SAME_ASSIGNEE"
    assert svc.open_counts(db) == {people["t1"].id: 1}


def test_luong_chinh_cua_tho(db, people):
    t1 = people["t1"]
    t = svc.assign(db, people["dp"], _new(db, people).id, t1.id, None)
    t = svc.transition(db, t1, t.id, "DA_NHAN", t.version)
    assert _code(svc.transition, db, t1, t.id, "DA_HEN", None) == "APPOINTMENT_REQUIRED"
    hen = datetime.now(UTC) + timedelta(hours=3)
    t = svc.transition(db, t1, t.id, "DA_HEN", None, "Khách hẹn chiều", hen)
    assert t.due_at == hen  # đến đúng giờ hẹn
    t = svc.transition(db, t1, t.id, "DA_HEN", None, "Khách dời", hen + timedelta(days=1))
    t = svc.transition(db, t1, t.id, "DANG_LAM", None)
    assert t.due_at == service_sla.end_of_day(datetime.now(UTC))
    t = svc.transition(db, t1, t.id, "CHO_LINH_KIEN", None, "Chờ drum")
    assert t.due_at is None
    t = svc.transition(db, t1, t.id, "DANG_LAM", None)
    assert _code(svc.transition, db, t1, t.id, "HOAN_THANH", None) == "PHOTO_REQUIRED"
    attachments.add(db, t1, "service_ticket", str(t.id), "SAU", "sau.pdf", b"%PDF-1.4 test")
    assert _code(svc.transition, db, t1, t.id, "HOAN_THANH", None) == "BILLING_REQUIRED"
    service_charges.set_charges(db, t1, t.id, ServiceCharges(billing="MIEN_PHI", free_reason="Bảo hành"))
    t = svc.transition(db, t1, t.id, "HOAN_THANH", None)
    assert t.status == 'DA_DONG' and t.due_at is None
    steps = svc.load(db, people["dp"], t.id).steps
    assert [(s.action, s.from_status, s.to_status) for s in steps] == [
        ("create", None, "MOI"), ("assign", "MOI", "DA_GIAO"), ("transition", "DA_GIAO", "DA_NHAN"),
        ("transition", "DA_NHAN", "DA_HEN"), ("reschedule", "DA_HEN", "DA_HEN"), ("transition", "DA_HEN", "DANG_LAM"),
        ("transition", "DANG_LAM", "CHO_LINH_KIEN"), ("transition", "CHO_LINH_KIEN", "DANG_LAM"),
        ("transition", "DANG_LAM", "HOAN_THANH"), ("close", "HOAN_THANH", "DA_DONG"),
    ]
    assert all(s.actor_id == t1.id for s in steps[2:])
    assert _code(svc.transition, db, t1, t.id, "DA_DONG", None) == "FORBIDDEN"
    t = svc.transition(db, people["dp"], t.id, "DA_HUY", None, "Khách hủy")
    assert t.status == 'DA_HUY'
    assert _code(svc.transition, db, people["dp"], t.id, "DANG_LAM", None, "Mở lại") == "BAD_TRANSITION"


def test_tho_chi_thay_va_sua_phieu_cua_minh(db, people):
    t1, t2 = people["t1"], people["t2"]
    mine = svc.assign(db, people["dp"], _new(db, people).id, t1.id, None)
    _new(db, people)  # phiếu chưa giao
    assert [x.id for x in svc.search(db, t1)] == [mine.id]
    assert svc.search(db, t2) == []
    assert len(svc.search(db, people["kd"])) == 2
    assert _code(svc.load, db, t2, mine.id) == "NOT_FOUND"
    assert _code(svc.transition, db, t2, mine.id, "DA_NHAN", None) == "NOT_FOUND"
    # Điều phối cập nhật thay thợ được.
    assert svc.transition(db, people["dp"], mine.id, "DA_NHAN", None).status == "DA_NHAN"


def test_chuyen_trang_thai_sai_va_ly_do(db, people):
    t1 = people["t1"]
    t = _new(db, people)
    assert _code(svc.transition, db, people["dp"], t.id, "DA_NHAN", None) == "BAD_TRANSITION"
    t = svc.assign(db, people["dp"], t.id, t1.id, None)
    assert _code(svc.transition, db, t1, t.id, "HOAN_THANH", None) == "BAD_TRANSITION"
    t = svc.transition(db, t1, t.id, "DA_NHAN", None)
    assert _code(svc.transition, db, t1, t.id, "KHONG_LAM_DUOC", None, "  ") == "REASON_REQUIRED"
    assert _code(svc.transition, db, t1, t.id, "DA_HUY", None, "Không muốn làm") == "FORBIDDEN"
    assert _code(svc.transition, db, people["dp"], t.id, "DA_HUY", None) == "REASON_REQUIRED"
    assert _code(svc.transition, db, t1, t.id, "DA_NHAN", None, "", datetime.now(UTC)) == "BAD_TRANSITION"
    t = svc.transition(db, t1, t.id, "KHONG_LAM_DUOC", None, "Máy hỏng bo mạch")
    assert t.status_reason == "Máy hỏng bo mạch"
    assert _code(svc.assign, db, people["dp"], t.id, people["t2"].id, None) == "BAD_STATUS"
    # Hủy phiếu chưa giao.
    t = svc.transition(db, people["dp"], _new(db, people).id, "DA_HUY", None, "Khách gọi hủy")
    assert t.status == "DA_HUY" and t.assignee_id is None


def test_xin_chuyen_tho_dieu_phoi_duyet_hoac_tu_choi(db, people):
    t1, t2, dp = people["t1"], people["t2"], people["dp"]
    t = svc.assign(db, dp, _new(db, people).id, t1.id, None)
    t = svc.transition(db, t1, t.id, "DA_NHAN", None)
    assert _code(svc.request_transfer, db, t1, t.id, "", None) == "REASON_REQUIRED"
    assert _code(svc.request_transfer, db, dp, t.id, "Thử", None) == "FORBIDDEN"
    t = svc.request_transfer(db, t1, t.id, "Đang ở quận khác", None)
    assert _code(svc.request_transfer, db, t1, t.id, "Lần hai", None) == "TRANSFER_PENDING"
    assert _code(svc.reject_transfer, db, t1, t.id, "", None) == "FORBIDDEN"
    t = svc.reject_transfer(db, dp, t.id, "Không có thợ khác", None)
    assert t.transfer_request == "" and t.assignee_id == t1.id and t.status == "DA_NHAN"
    t = svc.request_transfer(db, t1, t.id, "Vẫn kẹt", None)
    t = svc.assign(db, dp, t.id, t2.id, None, "Duyệt chuyển")
    assert (t.assignee_id, t.status, t.transfer_request) == (t2.id, "DA_GIAO", "")
    assert _code(svc.load, db, t1, t.id) == "NOT_FOUND"
    actions = [s.action for s in svc.load(db, dp, t.id).steps]
    assert actions[-4:] == ["transfer_request", "transfer_reject", "transfer_request", "reassign"]


def test_xung_dot_phien_ban(db, people):
    t = _new(db, people)
    old = t.version
    svc.assign(db, people["dp"], t.id, people["t1"].id, old)
    assert _code(svc.assign, db, people["dp"], t.id, people["t2"].id, old) == "VERSION_CONFLICT"


def test_lich_su_khong_sua_duoc(db, people):
    _new(db, people)
    with pytest.raises(DBAPIError):
        db.execute(text("update service_steps set note = 'sửa'"))
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("delete from service_steps"))
    db.rollback()


def test_loc_qua_han_va_tim(db, people):
    a, b, c = _new(db, people), _new(db, people), _new(db, people)
    now = datetime.now(UTC)
    db.execute(text("update service_tickets set due_at = :d where id = :i"), [
        {"d": now - timedelta(hours=1), "i": a.id}, {"d": now - timedelta(hours=5), "i": b.id}, {"d": now + timedelta(hours=1), "i": c.id}])
    db.commit()
    svc.transition(db, people["dp"], a.id, "DA_HUY", None, "Khách hủy")
    db.execute(text("update service_tickets set due_at = :d where id = :i"), {"d": now - timedelta(hours=1), "i": a.id})
    db.commit()
    assert [x.id for x in svc.search(db, people["dp"], overdue=True)] == [b.id]
    assert len(svc.search(db, people["dp"], q="minh phat")) == 3
    assert [x.id for x in svc.search(db, people["dp"], q=b.number.lower())] == [b.id]
    assert svc.search(db, people["dp"], q="khong co") == []


def test_cuoi_gio_lam():
    tz = service_sla.TZ
    assert service_sla.end_of_day(datetime(2026, 10, 17, 9, 0, tzinfo=tz)) == datetime(2026, 10, 17, 18, 0, tzinfo=tz)
    assert service_sla.end_of_day(datetime(2026, 10, 17, 19, 0, tzinfo=tz)) == datetime(2026, 10, 18, 18, 0, tzinfo=tz)
    # 23:30 UTC là 6:30 sáng hôm sau giờ Việt Nam.
    assert service_sla.end_of_day(datetime(2026, 10, 17, 23, 30, tzinfo=UTC)) == datetime(2026, 10, 18, 18, 0, tzinfo=tz)


def test_api_phieu_dich_vu(client, login, db):
    db.add(Customer(code="KTD-000001", name="Minh Phát"))
    db.commit()
    kd, dp, tho = login("kd", "sales"), login("dp", "dispatcher"), login("tho", "technician")
    from app.models import User
    db.add(Product(code='TD-0001', name='Máy in'))
    db.flush()
    db.add(Equipment(serial='API-MAY', product_code='TD-0001', customer_code='KTD-000001',
                     created_by=db.scalar(select(User.id).where(User.username == 'dp'))))
    db.commit()
    r = client.post("/service-tickets", json={"customer_code": "KTD-000001", "kind": "MANG_VE", "serial": "API-MAY", "symptom": "Kẹt giấy"}, headers=kd)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    tho_id = client.get("/auth/me", headers=tho).json()["id"]
    assert client.post(f"/service-tickets/{tid}/assign", json={"assignee_id": tho_id}, headers=kd).status_code == 403
    r = client.post(f"/service-tickets/{tid}/assign", json={"assignee_id": tho_id, "version": 1}, headers=dp)
    assert r.status_code == 200 and r.json()["status"] == "DA_GIAO"
    r = client.post(f"/service-tickets/{tid}/transition", json={"to_status": "DA_NHAN"}, headers=tho)
    assert r.status_code == 200, r.text
    r = client.post(f"/service-tickets/{tid}/transition",
                    json={"to_status": "DA_HEN", "appointment_at": "2026-12-01T09:00:00+07:00"}, headers=tho)
    assert r.status_code == 200
    assert datetime.fromisoformat(r.json()["due_at"]) == datetime(2026, 12, 1, 2, tzinfo=UTC)
    r = client.get(f"/service-tickets/{tid}", headers=tho)
    assert [s["to_status"] for s in r.json()["steps"]] == ["MOI", "DA_GIAO", "DA_NHAN", "DA_HEN"]
    assert [x["id"] for x in client.get("/service-tickets", headers=tho).json()] == [tid]
    # Giờ hẹn không có múi giờ bị từ chối.
    r = client.post(f"/service-tickets/{tid}/transition", json={"to_status": "DA_HEN", "appointment_at": "2026-12-01T09:00:00"}, headers=tho)
    assert r.status_code == 422
    assert db.scalar(select(ServiceStep.id).order_by(ServiceStep.id.desc()).limit(1)) == 4
