import threading
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import stock_posting
from app.models import AuditLog, Product, StockBalance, StockLedger, Supplier, User, Warehouse


@pytest.fixture
def catalog(db):
    db.add_all([
        Warehouse(code="K58", name="Kho 58"),
        Warehouse(code="K145", name="Kho 145"),
        Supplier(code="NCC-0001", name="Minh Phát"),
        Product(code="TD-0001", name="Mực HP"),
        Product(code="TD-0002", name="Drum"),
    ])
    db.commit()


@pytest.fixture
def kho(login, catalog):
    return login("kho", "warehouse")


def make(client, h, kind, lines, **kw):
    body = {"kind": kind, "warehouse_code": "K58", "doc_date": "2026-12-31", "lines": lines, **kw}
    if kind == "PN":
        body.setdefault("supplier_code", "NCC-0001")
    if kind == "CK":
        body.setdefault("to_warehouse_code", "K145")
    r = client.post("/stock-docs", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def post(client, h, doc):
    return client.post(f"/stock-docs/{doc['id']}/post", json={"version": doc["version"]}, headers=h)


def posted(client, h, kind, lines, **kw):
    r = post(client, h, make(client, h, kind, lines, **kw))
    assert r.status_code == 200, r.text
    return r.json()


def receive(client, h, qty, cost, product="TD-0001", warehouse="K58"):
    return posted(client, h, "PN", [{"product_code": product, "qty": str(qty), "unit_cost": cost}], warehouse_code=warehouse)


def issue(client, h, qty, product="TD-0001", warehouse="K58"):
    return post(client, h, make(client, h, "PX", [{"product_code": product, "qty": str(qty)}], warehouse_code=warehouse))


def balance(db, warehouse="K58", product="TD-0001"):
    db.expire_all()
    b = db.get(StockBalance, (warehouse, product))
    return (b.qty, b.value, b.avg_cost) if b else (Decimal(0), 0, 0)


def ledger(db):
    db.expire_all()
    return db.scalars(select(StockLedger).order_by(StockLedger.id)).all()


def test_binh_quan_gia_quyen(client, kho, db):
    receive(client, kho, 10, 100_000)
    receive(client, kho, 10, 120_000)
    assert balance(db) == (Decimal("20.000"), 2_200_000, 110_000)
    assert issue(client, kho, 5).status_code == 200
    assert balance(db) == (Decimal("15.000"), 1_650_000, 110_000)
    out = ledger(db)[-1]
    assert out.qty_out == 5 and out.amount == -550_000 and out.balance_qty == 15 and out.balance_value == 1_650_000


def test_xuat_het_lay_het_gia_tri_khong_lech_lam_tron(client, kho, db):
    receive(client, kho, 3, 100_000)
    receive(client, kho, 0.5, 33_333)
    for _ in range(3):
        assert issue(client, kho, 1).status_code == 200
    assert issue(client, kho, 0.5).status_code == 200
    assert balance(db)[:2] == (Decimal("0.000"), 0)


def test_xuat_qua_ton_bi_chan_so_khong_doi(client, kho, db):
    receive(client, kho, 2, 100_000)
    doc = make(client, kho, "PX", [{"product_code": "TD-0001", "qty": "1"}, {"product_code": "TD-0001", "qty": "1.5"}])
    r = post(client, kho, doc)
    assert r.status_code == 409 and r.json()["error"] == "NEGATIVE_STOCK"
    assert r.json()["details"]["available"] == "1.000"
    assert balance(db) == (Decimal("2.000"), 200_000, 100_000) and len(ledger(db)) == 1
    assert client.get(f"/stock-docs/{doc['id']}", headers=kho).json()["status"] == "NHAP"


def test_chuyen_kho_giu_tong_gia_tri(client, kho, db):
    receive(client, kho, 10, 100_000)
    receive(client, kho, 5, 130_000)
    posted(client, kho, "CK", [{"product_code": "TD-0001", "qty": "4"}])
    src, dst = balance(db), balance(db, "K145")
    assert src[0] == 11 and dst[0] == 4
    assert src[1] + dst[1] == 1_650_000 and dst[2] == 110_000


def test_huy_phieu_tra_ton_ve_nhu_truoc(client, kho, db):
    receive(client, kho, 10, 100_000)
    receive(client, kho, 5, 130_000)
    before = (balance(db), balance(db, "K145"))
    ck = posted(client, kho, "CK", [{"product_code": "TD-0001", "qty": "4"}])
    px = posted(client, kho, "PX", [{"product_code": "TD-0001", "qty": "3"}])
    for doc in (px, ck):
        r = client.post(f"/stock-docs/{doc['id']}/void", json={"reason": "Lập nhầm", "version": doc["version"]}, headers=kho)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "DA_HUY" and r.json()["void_reason"] == "Lập nhầm"
    assert (balance(db), balance(db, "K145"))[0][:2] == before[0][:2]
    assert balance(db, "K145")[:2] == (Decimal("0.000"), 0)
    assert sum(1 for x in ledger(db) if x.is_reversal) == 3


def test_huy_phieu_nhap_da_xuat_mot_phan_bi_chan(client, kho, db):
    pn = receive(client, kho, 10, 100_000)
    assert issue(client, kho, 5).status_code == 200
    r = client.post(f"/stock-docs/{pn['id']}/void", json={"reason": "Nhập nhầm"}, headers=kho)
    assert r.status_code == 409 and r.json()["error"] == "NEGATIVE_STOCK"
    assert balance(db)[:2] == (Decimal("5.000"), 500_000)


def test_huy_nhap_lam_lech_gia_tri_bi_chan(client, kho, db):
    receive(client, kho, 10, 100)
    pn = receive(client, kho, 10, 1000)
    assert issue(client, kho, 9).status_code == 200
    r = client.post(f"/stock-docs/{pn['id']}/void", json={"reason": "Nhập nhầm"}, headers=kho)
    assert r.status_code == 409 and r.json()["error"] == "VOID_COST_CONFLICT"
    assert balance(db)[:2] == (Decimal("11.000"), 6_050)


def test_khong_ghi_hai_lan_va_chi_ghi_phieu_nhap(client, kho):
    doc = make(client, kho, "PN", [{"product_code": "TD-0001", "qty": "1", "unit_cost": 5}])
    assert post(client, kho, doc).status_code == 200
    r = client.post(f"/stock-docs/{doc['id']}/post", json={}, headers=kho)
    assert r.status_code == 409 and r.json()["error"] == "DOC_NOT_DRAFT"
    draft = make(client, kho, "PN", [{"product_code": "TD-0001", "qty": "1", "unit_cost": 5}])
    client.post(f"/stock-docs/{draft['id']}/cancel", json={"reason": "Bỏ"}, headers=kho)
    assert client.post(f"/stock-docs/{draft['id']}/post", json={}, headers=kho).json()["error"] == "DOC_NOT_DRAFT"
    assert client.post(f"/stock-docs/{draft['id']}/void", json={"reason": "Bỏ đi"}, headers=kho).json()["error"] == "DOC_NOT_POSTED"


def test_ghi_so_sai_version(client, kho):
    doc = make(client, kho, "PN", [{"product_code": "TD-0001", "qty": "1", "unit_cost": 5}])
    r = client.post(f"/stock-docs/{doc['id']}/post", json={"version": doc["version"] + 1}, headers=kho)
    assert r.status_code == 409 and r.json()["error"] == "VERSION_CONFLICT"


def test_huy_phai_co_ly_do(client, kho):
    doc = receive(client, kho, 1, 5)
    r = client.post(f"/stock-docs/{doc['id']}/void", json={"reason": "x"}, headers=kho)
    assert r.json()["error"] == "REASON_REQUIRED"


def test_kiem_ke_dau_ky_can_don_gia(client, kho, db):
    doc = make(client, kho, "KK", [{"product_code": "TD-0001", "qty": "7"}])
    r = post(client, kho, doc)
    assert r.status_code == 400 and r.json()["error"] == "BAD_UNIT_COST"
    posted(client, kho, "KK", [{"product_code": "TD-0001", "qty": "7", "unit_cost": 90_000},
                               {"product_code": "TD-0002", "qty": "0"}])
    assert balance(db) == (Decimal("7.000"), 630_000, 90_000)
    assert db.get(StockBalance, ("K58", "TD-0002")).qty == 0


def test_kiem_ke_ghi_chenh_lech(client, kho, db):
    receive(client, kho, 10, 100_000)
    posted(client, kho, "KK", [{"product_code": "TD-0001", "qty": "8"}])
    assert balance(db) == (Decimal("8.000"), 800_000, 100_000)
    posted(client, kho, "KK", [{"product_code": "TD-0001", "qty": "9"}])
    assert balance(db) == (Decimal("9.000"), 900_000, 100_000)
    posted(client, kho, "KK", [{"product_code": "TD-0001", "qty": "9"}])
    assert len(ledger(db)) == 3


def test_kiem_ke_trung_ma_bi_chan(client, kho):
    doc = make(client, kho, "KK", [{"product_code": "TD-0001", "qty": "1", "unit_cost": 1},
                                   {"product_code": "TD-0001", "qty": "2", "unit_cost": 1}])
    assert post(client, kho, doc).json()["error"] == "DUPLICATE_LINE"


def test_chi_kho_duoc_ghi_so(client, kho, login):
    doc = make(client, kho, "PN", [{"product_code": "TD-0001", "qty": "1", "unit_cost": 5}])
    sales = login("ban.hang", "sales")
    assert client.post(f"/stock-docs/{doc['id']}/post", json={}, headers=sales).status_code == 403


def test_ghi_so_co_nhat_ky(client, kho, db):
    doc = receive(client, kho, 1, 5)
    client.post(f"/stock-docs/{doc['id']}/void", json={"reason": "Nhập nhầm"}, headers=kho)
    actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_id == str(doc["id"])).order_by(AuditLog.id)).all()
    assert actions == ["stock_doc.create", "stock_doc.post", "stock_doc.void"]


def test_so_kho_khong_sua_duoc(client, kho, engine):
    receive(client, kho, 1, 5)
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(text("update stock_ledger set amount = 0"))


def test_hai_nguoi_cung_xuat_mon_cuoi(client, kho, engine, db):
    receive(client, kho, 1, 100_000)
    docs = [make(client, kho, "PX", [{"product_code": "TD-0001", "qty": "1"}]) for _ in range(5)]
    user = db.scalars(select(User).where(User.username == "kho")).one()
    results, start = [], threading.Barrier(5)

    def go(doc_id):
        with Session(engine, expire_on_commit=False) as s:
            u = s.get(User, user.id)
            start.wait()
            try:
                stock_posting.post(s, u, doc_id, None)
                results.append("ok")
            except Exception as exc:  # noqa: BLE001
                results.append(getattr(exc, "code", repr(exc)))

    threads = [threading.Thread(target=go, args=(d["id"],)) for d in docs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["NEGATIVE_STOCK"] * 4 + ["ok"]
    assert balance(db)[:2] == (Decimal("0.000"), 0)
