"""Xóa dữ liệu chạy thử nhưng giữ cấu hình (scripts/clear_pilot_data.py)."""
import sys
from pathlib import Path

from sqlalchemy import text
from test_attachments import _jpeg, ticket  # noqa: F401
from test_ns5_payroll import money, p, paid_advance  # noqa: F401

from app import attachments
from app.config import get_settings
from app.models import Customer, Product, Warehouse
from scripts import clear_pilot_data as clear


def run(monkeypatch, *flags):
    monkeypatch.setattr(sys, "argv", ["clear_pilot_data", *flags])
    clear.main()


def n(db, table):
    return db.execute(text(f"select count(*) from {table}")).scalar_one()


def test_moi_bang_deu_duoc_xep_giu_hoac_xoa(db):
    # Migration thêm bảng mới mà quên xếp nhóm thì script tự dừng; test này báo sớm.
    assert clear.all_tables(db) == clear.KEEP | clear.WIPE
    assert not clear.KEEP & clear.WIPE


def test_xoa_chung_tu_giu_cau_hinh(db, p, money, ticket, monkeypatch, capsys):  # noqa: F811
    paid_advance(db, p)
    t, tp = ticket
    a = attachments.add(db, tp["t1"], "service_ticket", str(t.id), "SAU", "a.jpg", _jpeg(200, 200))
    photo = Path(get_settings().files_dir) / a.path
    db.add(Customer(code="KTD-000099", name="Khách thử", is_test=True))
    db.add(Product(code="TD-9001", name="Hàng thử", unit="cái", is_test=True))
    db.add(Product(code="TD-0001", name="Mực thật", unit="hộp"))
    db.commit()
    db.close()
    assert photo.is_file()

    run(monkeypatch)
    assert "Chưa xóa gì" in capsys.readouterr().out
    assert n(db, "service_tickets") == 1 and n(db, "fund_ledger") > 0
    db.rollback()

    run(monkeypatch, "--yes")
    db.expire_all()
    for table in ("service_tickets", "service_steps", "attachments", "cash_docs", "fund_ledger", "fund_balances",
                  "salary_advances", "doc_counters"):
        assert n(db, table) == 0, table
    for table in ("users", "employees", "employee_salary_versions", "funds", "cash_categories", "audit_log"):
        assert n(db, table) > 0, table
    assert db.execute(text("select category_code from finance_mappings where key = 'DANH_MUC_LUONG'")).scalar_one() == "LUONG"
    assert set(db.execute(text("select code from customers")).scalars()) == {"KTD-000001"}
    assert set(db.execute(text("select code from products")).scalars()) == {"TD-0001"}
    assert not photo.exists()
    # Trigger chống sửa sổ phải bật lại sau khi xóa.
    assert db.execute(text("select count(*) from pg_trigger where not tgisinternal and tgenabled <> 'O'")).scalar_one() == 0


def test_xoa_ca_khach_va_hang(db, ticket, monkeypatch):  # noqa: F811
    db.add(Product(code="TD-0001", name="Mực thật", unit="hộp"))
    db.commit()
    db.close()
    run(monkeypatch, "--yes", "--xoa-khach", "--xoa-hang")
    assert n(db, "customers") == 0 and n(db, "products") == 0 and n(db, "users") > 0


def test_xoa_khi_co_ho_so_cho_xu_ly(db, ticket, monkeypatch):  # noqa: F811
    # Hồ sơ chờ xử lý có constraint trigger hoãn; trước đây xóa xong bật lại trigger bị lỗi "pending trigger events".
    db.add(Product(code="TD-0001", name="Mực thật", unit="hộp"))
    db.add(Warehouse(code="CXL", name="Chờ xử lý", kind="LOI"))
    db.flush()
    db.execute(text("insert into stock_balances(warehouse_code, product_code, qty, value, avg_cost) values ('CXL', 'TD-0001', 3, 300, 100)"))
    db.execute(text(
        "insert into pending_items(number, warehouse_code, product_code, qty_in, qty_left, unit_cost, group_code, source, source_ref, "
        "reason, entered_at, status, version) values ('CXL-26-00001', 'CXL', 'TD-0001', 3, 3, 100, 'HANG_LOI', 'TON_CU', '', 'thử', now(), 'DANG_CHO', 1)"))
    db.commit()
    db.close()
    run(monkeypatch, "--yes", "--xoa-khach", "--xoa-hang")
    assert n(db, "pending_items") == 0 and n(db, "stock_balances") == 0 and n(db, "warehouses") == 1
    assert db.execute(text("select count(*) from pg_trigger where not tgisinternal and tgenabled <> 'O'")).scalar_one() == 0
