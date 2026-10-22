from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app import attachments, service_charges, stock_docs, stock_posting
from app import service_tickets as svc
from app.errors import BusinessError
from app.models import (
    Customer,
    Product,
    StockBalance,
    StockDoc,
    StockLedger,
    Supplier,
    Warehouse,
)
from app.schemas import ServiceCharges, ServiceTicketIn, StockDocIn

PART = {"kind": "VAT_TU", "product_code": "TD-0001", "warehouse_code": "K58", "qty": "2", "unit_price": 350_000, "vat_rate": 10}
FEE = {"kind": "PHI", "description": "Công sửa", "qty": "1", "unit_price": 200_000, "vat_rate": 8}


@pytest.fixture
def ctx(db, make_user):
    db.add_all([
        Customer(code="KTD-000001", name="Minh Phát"),
        Warehouse(code="K58", name="Kho 58"),
        Warehouse(code="K145", name="Kho 145"),
        Supplier(code="NCC-0001", name="NCC"),
        Product(code="TD-0001", name="Mực HP"),
        Product(code="TD-0002", name="Drum"),
        Product(code="TD-0009", name="Ngừng bán", active=False),
    ])
    db.commit()
    p = {"dp": make_user("dp", "dispatcher"), "t1": make_user("t1", "technician"), "t2": make_user("t2", "technician"),
         "kd": make_user("kd", "sales"), "kt": make_user("kt", "finance"), "kho": make_user("kho", "warehouse")}
    # Tồn: K58 có 5 TD-0001 giá 100.000, K145 có 3 TD-0002 giá 400.000.
    for wh, code, cost, qty in (("K58", "TD-0001", 100_000, 5), ("K145", "TD-0002", 400_000, 3)):
        doc = stock_docs.create(db, p["kho"], StockDocIn(kind="PN", warehouse_code=wh, doc_date=date(2026, 10, 1),
                                supplier_code="NCC-0001", lines=[{"product_code": code, "qty": qty, "unit_cost": cost}]))
        stock_posting.post(db, p["kho"], doc.id, None)
    return p


def _working(db, p):
    t = svc.create(db, p["kd"], ServiceTicketIn(customer_code="KTD-000001", kind="SUA_TAI_CHO", symptom="Kẹt giấy"))
    t = svc.assign(db, p["dp"], t.id, p["t1"].id, None)
    t = svc.transition(db, p["t1"], t.id, "DA_NHAN", None)
    t = svc.transition(db, p["t1"], t.id, "DA_HEN", None, "", svc._now()+timedelta(days=1))
    t = svc.transition(db, p["t1"], t.id, "DANG_LAM", None)
    attachments.add(db, p["t1"], "service_ticket", str(t.id), "SAU", "sau.pdf", b"%PDF-1.4")
    return t


def _charges(db, user, t, lines, billing="THU_TIEN", **kw):
    return service_charges.set_charges(db, user, t.id, ServiceCharges(billing=billing, lines=lines, **kw))


def _code(fn, *args, **kw):
    with pytest.raises(BusinessError) as e:
        fn(*args, **kw)
    return e.value.code


def _bal(db, wh, code):
    db.expire_all()
    b = db.get(StockBalance, (wh, code))
    return b.qty, b.value


def test_tinh_tien_lam_tron_va_vat_tung_dong(db, ctx):
    t = _working(db, ctx)
    odd = {**FEE, "qty": "1.5", "unit_price": 333, "vat_rate": 10}
    t = _charges(db, ctx["t1"], t, [PART, odd])
    assert [(x.line_no, x.amount, x.vat_amount) for x in t.lines] == [(1, 700_000, 70_000), (2, 500, 50)]
    assert t.total is None and t.priced_at is None  # chưa khóa giá


def test_sua_tien_kiem_quyen_va_du_lieu(db, ctx):
    t = _working(db, ctx)
    assert _code(_charges, db, ctx["t2"], t, [FEE]) == "NOT_FOUND"
    assert _code(_charges, db, ctx["kd"], t, [FEE]) == "FORBIDDEN"
    assert _code(_charges, db, ctx["t1"], t, [{**FEE, "description": ""}]) == "BAD_LINE"
    assert _code(_charges, db, ctx["t1"], t, [{**FEE, "product_code": "TD-0001"}]) == "BAD_LINE"
    assert _code(_charges, db, ctx["t1"], t, [{**PART, "warehouse_code": None}]) == "BAD_LINE"
    assert _code(_charges, db, ctx["t1"], t, [{**PART, "product_code": "TD-0009"}]) == "BAD_PRODUCT"
    assert _code(_charges, db, ctx["t1"], t, [{**PART, "warehouse_code": "K99"}]) == "BAD_WAREHOUSE"
    assert _code(_charges, db, ctx["t1"], t, [], billing="MIEN_PHI") == "REASON_REQUIRED"
    assert _charges(db, ctx["dp"], t, [], billing="MIEN_PHI", free_reason="Bảo hành").billing == "MIEN_PHI"


def test_hoan_thanh_khoa_gia_va_xuat_kho_hai_kho(db, ctx):
    t = _working(db, ctx)
    part2 = {**PART, "product_code": "TD-0002", "warehouse_code": "K145", "qty": "1", "unit_price": 900_000}
    _charges(db, ctx["t1"], t, [PART, FEE, part2])
    t = svc.transition(db, ctx["t1"], t.id, "HOAN_THANH", None)
    assert (t.subtotal, t.vat_total, t.total, t.receivable) == (1_800_000, 70_000 + 16_000 + 90_000, 1_976_000, 1_976_000)
    assert t.priced_at is not None
    docs = db.scalars(select(StockDoc).where(StockDoc.kind == "PX").order_by(StockDoc.warehouse_code)).all()
    assert [(d.warehouse_code, d.status, d.customer_code) for d in docs] == [("K145", "DA_GHI", "KTD-000001"), ("K58", "DA_GHI", "KTD-000001")]
    assert t.number in docs[0].reason
    assert _bal(db, "K58", "TD-0001") == (Decimal("3.000"), 300_000)
    assert _bal(db, "K145", "TD-0002") == (Decimal("2.000"), 800_000)
    lines = svc.load(db, ctx["dp"], t.id).lines
    assert {x.kind: x.stock_doc_id is not None for x in lines} == {"VAT_TU": True, "PHI": False}
    assert _code(_charges, db, ctx["dp"], t, [FEE]) == "PRICE_LOCKED"


def test_thieu_ton_thi_khong_hoan_thanh_va_khong_ghi_so(db, ctx):
    t = _working(db, ctx)
    _charges(db, ctx["t1"], t, [{**PART, "qty": "6"}])
    ledger = db.scalar(select(func.count()).select_from(StockLedger))
    assert _code(svc.transition, db, ctx["t1"], t.id, "HOAN_THANH", None) == "NEGATIVE_STOCK"
    db.expire_all()
    assert db.get(type(t), t.id).status == "DANG_LAM"
    assert db.scalar(select(func.count()).select_from(StockLedger)) == ledger
    assert db.scalar(select(StockDoc.id).where(StockDoc.kind == "PX")) is None


def test_thu_tien_phai_co_so_tien(db, ctx):
    t = _working(db, ctx)
    _charges(db, ctx["t1"], t, [])
    assert _code(svc.transition, db, ctx["t1"], t.id, "HOAN_THANH", None) == "EMPTY_CHARGES"
    _charges(db, ctx["t1"], t, [], billing="MIEN_PHI", free_reason="Khách quen")
    t = svc.transition(db, ctx["t1"], t.id, "HOAN_THANH", None)
    assert (t.total, t.receivable) == (0, 0)


def test_mo_lai_nghiem_thu_tra_vat_tu_ve_kho(db, ctx):
    t = _working(db, ctx)
    _charges(db, ctx["t1"], t, [PART, FEE])
    t = svc.transition(db, ctx["t1"], t.id, "HOAN_THANH", None)
    assert _code(svc.transition, db, ctx["t1"], t.id, "DANG_LAM", None, "Sửa lại") == "FORBIDDEN"
    assert _code(svc.transition, db, ctx["dp"], t.id, "DANG_LAM", None) == "REASON_REQUIRED"
    t = svc.transition(db, ctx["dp"], t.id, "DANG_LAM", None, "Khách báo còn kẹt")
    assert t.status == "DANG_LAM" and t.total is None and t.priced_at is None
    assert _bal(db, "K58", "TD-0001") == (Decimal("5.000"), 500_000)
    assert db.scalar(select(StockDoc.status).where(StockDoc.kind == "PX")) == "DA_HUY"
    # Sửa lại tiền rồi hoàn thành lần hai: phiếu PX mới.
    _charges(db, ctx["t1"], t, [{**PART, "qty": "1"}, FEE])
    t = svc.transition(db, ctx["t1"], t.id, "HOAN_THANH", None)
    assert t.total == 385_000 + 216_000
    assert _bal(db, "K58", "TD-0001") == (Decimal("4.000"), 400_000)
    assert [s.action for s in svc.load(db, ctx["dp"], t.id).steps][-3:] == ["reopen", "transition", "close"]


def test_huy_phieu_da_hoan_thanh_tra_vat_tu(db, ctx):
    t = _working(db, ctx)
    _charges(db, ctx["t1"], t, [PART])
    t = svc.transition(db, ctx["t1"], t.id, "HOAN_THANH", None)
    t = svc.transition(db, ctx["dp"], t.id, "DA_HUY", None, "Khách trả hàng")
    assert t.status == "DA_HUY" and t.receivable is None
    assert _bal(db, "K58", "TD-0001") == (Decimal("5.000"), 500_000)


def test_danh_sach_can_thu(db, ctx):
    a, b, c = _working(db, ctx), _working(db, ctx), _working(db, ctx)
    _charges(db, ctx["t1"], a, [FEE])
    _charges(db, ctx["t1"], b, [], billing="MIEN_PHI", free_reason="Bảo hành")
    _charges(db, ctx["t1"], c, [FEE])
    for t in (a, b):
        svc.transition(db, ctx["t1"], t.id, "HOAN_THANH", None)
    assert [x.id for x in service_charges.receivables(db, ctx["kt"])] == [a.id]
    assert service_charges.receivables(db, ctx["dp"], date(2000, 1, 1), date(2000, 1, 2)) == []
    assert _code(service_charges.receivables, db, ctx["t1"]) == "FORBIDDEN"


def test_api_tien_dich_vu(client, login, db, ctx):
    t = _working(db, ctx)
    h = login("tho.api", "technician")
    dp = login("dp.api", "dispatcher")
    r = client.post(f"/service-tickets/{t.id}/charges", json={"billing": "THU_TIEN", "lines": [FEE]}, headers=dp)
    assert r.status_code == 200, r.text
    assert r.json()["lines"][0]["amount"] == 200_000
    assert client.post(f"/service-tickets/{t.id}/charges", json={"billing": "THU_TIEN", "lines": [FEE]}, headers=h).status_code == 404
    r = client.post(f"/service-tickets/{t.id}/charges", json={"billing": "THU_TIEN", "lines": [{**FEE, "unit_price": 1.5}]}, headers=dp)
    assert r.status_code == 422
    assert client.get("/service-tickets/receivables", headers=dp).json() == []
