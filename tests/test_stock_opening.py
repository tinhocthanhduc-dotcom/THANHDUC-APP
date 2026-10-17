import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app import stock_opening, stock_posting
from app.errors import BusinessError
from app.models import Product, StockBalance, StockDoc, User, Warehouse
from app.security import hash_password

D = date(2026, 12, 31)


@pytest.fixture
def setup(db):
    db.add_all([
        Warehouse(code="K58", name="Kho 58"),
        Warehouse(code="K145", name="Kho 145"),
        Product(code="TD-0001", name="Mực HP"),
        Product(code="TD-0002", name="Drum"),
        User(username="thu.kho", full_name="Thủ kho", password_hash=hash_password("matkhau-test-123"), roles=["warehouse"]),
    ])
    db.commit()
    return db.scalars(select(User)).one()


ROWS = [
    {"MA_HANG": "td-0001", "SO_LUONG": "12", "DON_GIA": "350.000"},
    {"MA_HANG": "TD-0002", "SO_LUONG": "2,5", "DON_GIA": "1250000"},
    {"MA_HANG": "", "SO_LUONG": "", "DON_GIA": ""},
]


def test_tao_phieu_nhap_roi_ghi_so(db, setup):
    [doc] = stock_opening.create_opening(db, setup, "K58", D, ROWS)
    assert doc.kind == "KK" and doc.status == "NHAP" and doc.number == "KK-26-00001"
    assert [(x.product_code, x.qty, x.unit_cost) for x in doc.lines] == [
        ("TD-0001", Decimal("12.000"), 350_000), ("TD-0002", Decimal("2.500"), 1_250_000)]
    stock_posting.post(db, setup, doc.id, None)
    assert db.get(StockBalance, ("K58", "TD-0001")).value == 4_200_000
    assert db.get(StockBalance, ("K58", "TD-0002")).value == 3_125_000


def test_file_loi_bao_du_va_khong_tao_phieu(db, setup):
    rows = ROWS + [
        {"MA_HANG": "TD-9999", "SO_LUONG": "1", "DON_GIA": "1"},
        {"MA_HANG": "TD-8888", "SO_LUONG": "1", "DON_GIA": "1"},
        {"MA_HANG": "TD-0001", "SO_LUONG": "1", "DON_GIA": "1"},
        {"MA_HANG": "TD-0002", "SO_LUONG": "1.2345", "DON_GIA": "1"},
        {"MA_HANG": "", "SO_LUONG": "3", "DON_GIA": "1"},
    ]
    rows[1] = {"MA_HANG": "TD-0002", "SO_LUONG": "2", "DON_GIA": ""}
    with pytest.raises(BusinessError) as e:
        stock_opening.create_opening(db, setup, "K58", D, rows)
    errs = e.value.details["errors"]
    assert {(x["error"], x.get("code")) for x in errs} == {
        ("BAD_UNIT_COST", "TD-0002"), ("DUPLICATE_CODE", "TD-0001"), ("MISSING_CODE", None),
        ("UNKNOWN_PRODUCT", "TD-9999"), ("UNKNOWN_PRODUCT", "TD-8888"), ("BAD_QTY", "TD-0002"),
    }
    assert db.scalar(select(StockDoc.id)) is None


def test_ma_ngung_ban_o_phan_sau_khong_tao_phieu_nao(db, setup, monkeypatch):
    db.add(Product(code="TD-0003", name="Mực cũ", active=False))
    db.commit()
    monkeypatch.setattr(stock_opening, "MAX_LINES", 2)  # 3 dòng thành 2 phần, mã ngừng bán ở phần 2
    rows = ROWS[:2] + [{"MA_HANG": "TD-0003", "SO_LUONG": "1", "DON_GIA": "1"}]
    with pytest.raises(BusinessError) as e:
        stock_opening.create_opening(db, setup, "K58", D, rows)
    assert e.value.details["errors"] == [{"error": "INACTIVE_PRODUCT", "code": "TD-0003"}]
    assert db.scalar(select(StockDoc.id)) is None


def test_chay_lai_khong_nhan_doi(db, setup):
    [doc] = stock_opening.create_opening(db, setup, "K58", D, ROWS)
    with pytest.raises(BusinessError) as e:
        stock_opening.create_opening(db, setup, "K58", D, ROWS)
    assert e.value.code == "OPENING_EXISTS" and e.value.details["docs"] == [doc.number]
    stock_posting.post(db, setup, doc.id, None)
    with pytest.raises(BusinessError) as e:
        stock_opening.create_opening(db, setup, "K58", D, ROWS)
    assert e.value.code == "OPENING_EXISTS"
    # Kho khác vẫn nhập được.
    assert stock_opening.create_opening(db, setup, "K145", D, ROWS)


def test_kho_da_co_phat_sinh_thi_khong_nhap_dau_ky(db, setup):
    [doc] = stock_opening.create_opening(db, setup, "K58", D, ROWS)
    stock_posting.post(db, setup, doc.id, None)
    stock_posting.void(db, setup, doc.id, "Nhập sai file", None)
    with pytest.raises(BusinessError) as e:
        stock_opening.create_opening(db, setup, "K58", D, ROWS)
    assert e.value.code == "WAREHOUSE_NOT_EMPTY"


def test_file_lon_chia_nhieu_phieu(db, setup, monkeypatch):
    monkeypatch.setattr(stock_opening, "MAX_LINES", 1)
    docs = stock_opening.create_opening(db, setup, "K58", D, ROWS)
    assert [len(d.lines) for d in docs] == [1, 1] and "phần 2/2" in docs[1].note


def test_lenh_chay_tu_dong_lenh(engine, setup, tmp_path: Path):
    f = tmp_path / "ton.csv"
    f.write_text("MA_HANG,SO_LUONG,DON_GIA\nTD-0001,3,\"100.000\"\nTD-7777,1,1\n", encoding="utf-8-sig")
    cmd = [sys.executable, "-m", "scripts.import_opening", "--warehouse", "k58", "--date", "2026-12-31", "--user", "thu.kho", str(f)]
    cwd = Path(__file__).resolve().parent.parent
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 1 and "TD-7777" in r.stderr and "chưa có trong danh mục" in r.stderr
    f.write_text("MA_HANG,SO_LUONG,DON_GIA\nTD-0001,3,\"100.000\"\n", encoding="utf-8-sig")
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0 and "KK-26-00001" in r.stdout, r.stderr
