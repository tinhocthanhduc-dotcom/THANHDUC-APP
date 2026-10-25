"""Nhập danh mục hàng từ CSV: tạo mới, cập nhật, ngừng bán, nhóm mới, mức tồn tối thiểu; chạy thử không ghi."""
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app import inventory_catalogs
from app.models import Product, StockMinLevel, Warehouse
from app.schemas import WarehouseIn
from scripts.import_products import import_rows, summary

ROWS = [
    {"MA_HANG": "TD-0001", "TEN": "Hộp mực in 79A - WB", "DVT": "Hộp", "NHOM": "Hộp mực", "BAO_HANH_THANG": "0", "TRANG_THAI": "DANG_BAN", "GIA_BAN": "", "MUC_TON_K58": "2", "MUC_TON_K145": "2"},
    {"MA_HANG": "TD-0230", "TEN": "Máy in Brother 2701DW", "DVT": "Cái", "NHOM": "Máy in", "BAO_HANH_THANG": "12", "TRANG_THAI": "DANG_BAN", "GIA_BAN": "6.900.000", "MUC_TON_K58": "2", "MUC_TON_K145": ""},
    {"MA_HANG": "TD-0118", "TEN": "Máy in Canon LBP 2900 đã sử dụng - CTY", "DVT": "Cái", "NHOM": "Máy in", "BAO_HANH_THANG": "3", "TRANG_THAI": "NGUNG_BAN", "GIA_BAN": "", "MUC_TON_K58": "", "MUC_TON_K145": ""},
    {"MA_HANG": "TD-0283", "TEN": "Drum máy in HP M15A", "DVT": "Cái", "NHOM": "Drum / Trống", "BAO_HANH_THANG": "", "TRANG_THAI": "", "GIA_BAN": "", "MUC_TON_K58": "2", "MUC_TON_K145": ""},
    {"MA_HANG": "ABC", "TEN": "Mã sai", "DVT": "Cái", "NHOM": "", "BAO_HANH_THANG": "0", "TRANG_THAI": "DANG_BAN"},
    {"MA_HANG": "TD-0001", "TEN": "Lặp", "DVT": "Hộp", "NHOM": "Hộp mực"},
    {"MA_HANG": "TD-0002", "TEN": "Kho lạ", "DVT": "Hộp", "NHOM": "Hộp mực", "MUC_TON_K99": "1"},
]


@pytest.fixture
def setup(db, make_user):
    boss = make_user("giamdoc", "director")
    inventory_catalogs.create(db, boss, Warehouse, WarehouseIn(code="K58", name="Kho 58"))
    inventory_catalogs.create(db, boss, Warehouse, WarehouseIn(code="K145", name="Kho 145"))
    return boss


def test_chay_thu_khong_ghi(db, setup):
    rep = import_rows(db, setup, ROWS, apply=False)
    assert [r["status"] for r in rep] == ["TAO_MOI", "TAO_MOI", "TAO_MOI", "TAO_MOI", "LOI", "LOI", "LOI"]
    assert rep[0]["new_group"] == "Hộp mực" and rep[0]["min_levels"] == {"K58": "2", "K145": "2"}
    assert "K99" in rep[6]["message"] and "lặp" in rep[5]["message"]
    assert db.execute(text("select count(*) from products")).scalar_one() == 0
    assert db.execute(text("select count(*) from product_groups")).scalar_one() == 0


def test_nhap_that_cap_nhat_va_chay_lai(db, setup):
    rep = import_rows(db, setup, ROWS, apply=True)
    assert summary(rep) == {"TAO_MOI": 4, "LOI": 3}
    p = db.get(Product, "TD-0230")
    assert p.warranty_months == 12 and p.sale_price == 6_900_000 and p.group_name == "Máy in" and p.active
    assert db.get(Product, "TD-0118").active is False and db.get(Product, "TD-0118").warranty_months == 3
    assert db.get(Product, "TD-0283").unit == "Cái" and db.get(Product, "TD-0283").warranty_months == 0
    assert sorted(db.execute(text("select name from product_groups")).scalars()) == ["Drum / Trống", "Hộp mực", "Máy in"]
    assert db.get(StockMinLevel, ("K145", "TD-0001")).min_qty == Decimal("2")
    assert db.get(StockMinLevel, ("K58", "TD-0230")).min_qty == Decimal("2") and db.get(StockMinLevel, ("K145", "TD-0230")) is None

    # Chạy lại cùng file: không đổi gì.
    rep2 = import_rows(db, setup, ROWS, apply=True)
    assert summary(rep2) == {"DA_CO": 4, "LOI": 3} and all("min_levels" not in r for r in rep2)

    # Đổi tên, nhóm, ngừng bán và mức tồn → CAP_NHAT đúng trường.
    rows = [dict(ROWS[1], TEN="Máy in Brother HL-L2701DW", NHOM="Máy in laser", TRANG_THAI="NGUNG_BAN", MUC_TON_K58="1")]
    rep3 = import_rows(db, setup, rows, apply=True)
    assert rep3[0]["status"] == "CAP_NHAT" and set(rep3[0]["changes"]) == {"name", "group_name", "active"} and rep3[0]["min_levels"] == {"K58": "1"}
    db.expire_all()
    p = db.get(Product, "TD-0230")
    assert p.name == "Máy in Brother HL-L2701DW" and p.group_name == "Máy in laser" and not p.active
    assert db.get(StockMinLevel, ("K58", "TD-0230")).min_qty == Decimal("1")
    assert db.execute(text("select count(*) from audit_log where action like 'product%'")).scalar_one() >= 6


def test_lenh_chay_tu_dong_lenh(engine, setup, tmp_path: Path):
    f = tmp_path / "dm.csv"
    f.write_text("MA_HANG,TEN,DVT,NHOM,BAO_HANH_THANG,TRANG_THAI,GIA_BAN,MUC_TON_K58\nTD-0001,Hộp mực in 79A - WB,Hộp,Hộp mực,0,DANG_BAN,,2\n", encoding="utf-8-sig")
    cwd = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-m", "scripts.import_products", "--csv", str(f)], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0 and '"TAO_MOI"' in r.stdout, r.stderr
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from products")).scalar_one() == 0
    r = subprocess.run([sys.executable, "-m", "scripts.import_products", "--csv", str(f), "--apply"], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    with engine.connect() as conn:
        assert conn.execute(text("select name, group_name from products")).one() == ("Hộp mực in 79A - WB", "Hộp mực")
