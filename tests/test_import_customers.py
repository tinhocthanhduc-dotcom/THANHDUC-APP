"""Nhập danh bạ thật từ file gộp: tạo mới, gộp theo mã cũ/MST/SĐT, GOP:<STT>, BO, không ghi khi chạy thử."""
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from app.models import Customer, Supplier
from scripts.import_customers import import_customers, import_suppliers, summary

ROWS = [
    {"MA_CU_SALE": "KH0001", "LOAI": "CONG_TY", "TEN": "CÔNG TY TNHH HẢI ĐĂNG", "MST": "392755310", "DIEN_THOAI": "0392755310", "DIA_CHI": "Lâm Đồng", "HAN_MUC_NO": "5.000.000", "SO_NGAY_NO": "30"},
    {"MA_CU_SALE": "KH0004", "LOAI": "CA_NHAN", "TEN": "Chị Huệ", "DIEN_THOAI": "905588121", "DIA_CHI": "Quảng Nam"},
    {"MA_CU_CARE": "KH-260724-001", "LOAI": "CA_NHAN", "TEN": "Anh Hoàng", "DIEN_THOAI": "0938363580", "CONG_TY": "Kỹ thuật Hoàng"},
    {"MA_CU_SALE": "KH0100", "LOAI": "CA_NHAN", "TEN": "ANH SINH", "DIEN_THOAI": "0969221365"},
    {"MA_CU_SALE": "KH0101", "LOAI": "CA_NHAN", "TEN": "Anh Sinh", "DIEN_THOAI": "0969221365"},  # trùng số trong file
    {"MA_CU_SALE": "KH0102", "LOAI": "CA_NHAN", "TEN": "Không số", "DIEN_THOAI": ""},
    {"MA_CU_SALE": "KH0103", "LOAI": "CA_NHAN", "TEN": "Người của Hải Đăng", "DIEN_THOAI": "0392755310"},  # trùng số công ty → người liên hệ
    {"MA_CU_SALE": "KH0104", "LOAI": "CA_NHAN", "TEN": "Bỏ", "DIEN_THOAI": "0911111111", "XU_LY": "BO"},
    {"MA_CU_SALE": "KH0105", "LOAI": "CA_NHAN", "TEN": "Số phụ của chị Huệ", "DIEN_THOAI": "0922222222", "XU_LY": "GOP:2"},
]


@pytest.fixture
def director(make_user):
    return make_user("giamdoc", "director")


def test_chay_thu_khong_ghi(db, director):
    rep = import_customers(db, director, ROWS, apply=False)
    assert [r["status"] for r in rep] == [
        "TAO_MOI", "TAO_MOI", "TAO_MOI", "TAO_MOI", "GOP_SDT", "LOI", "GOP_SDT", "BO_QUA", "LOI",
    ]
    assert rep[5]["code"] == "MISSING_PHONE" and rep[4]["other_row"] == 4 and rep[6]["other_row"] == 1
    assert "chưa có trong app" in rep[8]["message"]  # GOP:2 chỉ gộp được khi dòng 2 đã tạo (chạy thật)
    assert db.execute(text("select count(*) from customers")).scalar_one() == 0


def test_nhap_that_roi_chay_lai_khong_trung(db, director):
    rep = import_customers(db, director, ROWS, apply=True)
    assert summary(rep) == {"TAO_MOI": 4, "GOP_SDT": 2, "LOI": 1, "BO_QUA": 1, "GOP_THEO_FILE": 1}
    hai_dang = db.get(Customer, rep[0]["customer_code"])
    assert hai_dang.kind == "CONG_TY" and hai_dang.tax_code == "0392755310" and hai_dang.old_codes == ["KH0001", "KH0103"]
    assert hai_dang.allow_debt and hai_dang.credit_limit == 5_000_000 and "30" in hai_dang.note
    hue = db.get(Customer, rep[1]["customer_code"])
    assert sorted(p.phone for p in hue.phones) == ["0905588121", "0922222222"] and "KH0105" in hue.old_codes
    hoang = db.get(Customer, rep[2]["customer_code"])
    assert hoang.old_codes == ["KH-260724-001"] and "Công ty: Kỹ thuật Hoàng" in hoang.note
    # Dòng 5: cùng người (ANH SINH / Anh Sinh) → một khách, giữ cả hai mã cũ.
    sinh = db.get(Customer, rep[3]["customer_code"])
    assert rep[4]["customer_code"] == sinh.code and sorted(sinh.old_codes) == ["KH0100", "KH0101"]
    # Dòng 7: cá nhân cùng số với công ty → người liên hệ của công ty, không tạo khách riêng.
    db.expire_all()
    hai_dang = db.get(Customer, rep[0]["customer_code"])
    assert rep[6]["customer_code"] == hai_dang.code and [x.name for x in hai_dang.contacts] == ["Người của Hải Đăng"]
    assert db.execute(text("select count(*) from customers")).scalar_one() == 4
    assert db.execute(text("select count(*) from audit_log where action in ('customer.create','customer.import')")).scalar_one() >= 5

    # Chạy lại cùng file: mọi dòng đã có đều khớp theo mã cũ, không tạo thêm.
    rep2 = import_customers(db, director, ROWS, apply=True)
    assert summary(rep2)["DA_CO"] >= 4 and "TAO_MOI" not in summary(rep2)
    assert db.execute(text("select count(*) from customers")).scalar_one() == 4


def test_gop_theo_mst_va_sdt_vao_khach_da_co_tren_app(db, director, client, login):
    h = login("banhang", "sales")
    r = client.post("/customers", json={"customer": {"kind": "CONG_TY", "name": "Cty Phát Đạt", "tax_code": "0301234567", "phones": ["0903123456"]}}, headers=h)
    code = r.json()["customer"]["code"]
    rows = [
        {"MA_CU_SALE": "KH0200", "LOAI": "CONG_TY", "TEN": "CÔNG TY PHÁT ĐẠT", "MST": "301234567", "DIEN_THOAI": "0909000001", "DIA_CHI": "Q1"},
        {"MA_CU_CARE": "KH-1", "LOAI": "CA_NHAN", "TEN": "Anh Đạt", "DIEN_THOAI": "0903123456"},
    ]
    rep = import_customers(db, director, rows, apply=True)
    assert [r["status"] for r in rep] == ["GOP_MST", "GOP_SDT"] and {r["customer_code"] for r in rep} == {code}
    db.expire_all()
    c = db.get(Customer, code)
    assert sorted(c.old_codes) == ["KH-1", "KH0200"] and c.address == "Q1" and c.name == "Cty Phát Đạt"
    assert sorted(p.phone for p in c.phones) == ["0903123456", "0909000001"]
    assert "Anh Đạt" in [x.name for x in c.contacts]


def test_nha_cung_cap(db, director):
    rows = [
        {"MA_CU_FINANCE": "NCC000007", "TEN": "CÔNG TY TNHH MTV ĐỨC LAN", "MST": "302884193", "DIEN_THOAI": "0902871817", "DIA_CHI": "149 Võ Văn Tần", "SO_TAI_KHOAN": "123"},
        {"MA_CU_FINANCE": "NCC000008", "TEN": "NHẤT PHONG", "DIEN_THOAI": "0914720107"},
        {"TEN": "", "DIEN_THOAI": "0900000000"},
    ]
    rep = import_suppliers(db, director, rows, apply=False)
    assert [r["status"] for r in rep] == ["TAO_MOI", "TAO_MOI", "LOI"]
    assert db.execute(text("select count(*) from suppliers")).scalar_one() == 0
    rep = import_suppliers(db, director, rows, apply=True)
    assert [r["status"] for r in rep] == ["TAO_MOI", "TAO_MOI", "LOI"]
    s = db.get(Supplier, rep[0]["supplier_code"])
    assert s.code == "NCC-0001" and s.tax_code == "0302884193" and "NCC000007" in s.note and "123" in s.note
    rep = import_suppliers(db, director, rows, apply=True)
    assert [r["status"] for r in rep] == ["DA_CO", "DA_CO", "LOI"]
    assert db.execute(text("select count(*) from suppliers")).scalar_one() == 2


def test_lenh_chay_tu_dong_lenh(engine, director, tmp_path: Path):
    f = tmp_path / "khach.csv"
    f.write_text("MA_CU_SALE,LOAI,TEN,DIEN_THOAI\nKH0001,CA_NHAN,Anh A,0903000001\n", encoding="utf-8-sig")
    cwd = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-m", "scripts.import_customers", "--csv", str(f)], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0 and '"TAO_MOI"' in r.stdout and '"apply": false' in r.stdout, r.stderr
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from customers")).scalar_one() == 0
    r = subprocess.run([sys.executable, "-m", "scripts.import_customers", "--csv", str(f), "--apply", "--user", "giamdoc"], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    with engine.connect() as conn:
        assert conn.execute(text("select old_codes from customers")).scalar_one() == ["KH0001"]
