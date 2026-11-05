"""Lệnh thêm bộ danh mục thu chi: chạy thử không ghi, chỉ thêm cái thiếu, ghép ô trống, giữ ô đã ghép."""
from sqlalchemy import func, select

from app import finance_catalogs, finance_mappings
from app.models import CashCategory, FinanceMapping
from app.schemas import CashCategoryIn
from scripts.them_danh_muc_thu_chi import BO_DANH_MUC, run


def test_chay_thu_khong_ghi(db, make_user):
    boss = make_user("giamdoc", "director")
    rep = run(db, boss)
    assert rep["tong_ket"] == {"THEM_MOI": len(BO_DANH_MUC)}
    assert db.scalar(select(func.count()).select_from(CashCategory)) == 0


def test_them_that_giu_cai_da_co_va_chay_lai(db, make_user):
    boss = make_user("giamdoc", "director")
    finance_catalogs.create(db, boss, CashCategory, CashCategoryIn(code="CP-DIEN", name="Điện nước", direction="CHI"))
    finance_catalogs.create(db, boss, CashCategory, CashCategoryIn(code="LUONG", name="Lương nhân viên", direction="CHI"))
    row = db.get(FinanceMapping, "DANH_MUC_LUONG")
    if row is None:
        db.add(FinanceMapping(key="DANH_MUC_LUONG", version=1)); db.commit()
        row = db.get(FinanceMapping, "DANH_MUC_LUONG")
    finance_mappings.set_mapping(db, boss, "DANH_MUC_LUONG", category_code="LUONG", version=row.version)

    rep = run(db, boss, apply=True)
    assert rep["tong_ket"] == {"THEM_MOI": len(BO_DANH_MUC) - 1, "DA_CO": 1}
    assert db.get(CashCategory, "CHI-DIEN-NUOC") is None  # trùng tên → không thêm
    assert db.get(CashCategory, "CHI-XANG-XE").direction == "CHI"
    assert db.scalar(select(FinanceMapping.category_code).where(FinanceMapping.key == "DANH_MUC_LUONG")) == "LUONG"
    assert db.scalar(select(FinanceMapping.category_code).where(FinanceMapping.key == "DANH_MUC_BAN_HANG")) == "THU-BAN-HANG"

    lai = run(db, boss, apply=True)
    assert lai["tong_ket"] == {"DA_CO": len(BO_DANH_MUC)}
    assert all(g["trang_thai"] == "GIU_NGUYEN" for g in lai["bang_ghep"])
