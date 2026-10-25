"""Xóa dữ liệu chạy thử (đơn, phiếu, tiền, kho, chấm công, lương...) nhưng GIỮ cấu hình.

Giữ: tài khoản + vai trò, hồ sơ nhân sự + lương + KPI + hoa hồng, ngày lễ, kho (cả kho Chờ Xử lý),
quỹ, danh mục thu chi, bảng ghép tài chính, nhà cung cấp + nợ đầu kỳ NCC, hàng hóa, mức tồn tối thiểu,
khách hàng + địa chỉ + máy của khách, đăng ký thông báo trên điện thoại.
Xóa: mọi chứng từ và sổ (tồn kho về 0, quỹ về 0, số chứng từ đếm lại từ 00001), ảnh đính kèm.
Nhật ký thao tác (audit_log) chỉ ghi thêm, không xóa được, nên vẫn giữ.
Khách và hàng đánh dấu thử nghiệm (is_test) cũng bị xóa.

Chạy trong container api, khi không ai đang dùng app. Sao lưu trước bằng scripts/backup.ps1.
    python -m scripts.clear_pilot_data                 # chỉ đếm, không xóa
    python -m scripts.clear_pilot_data --yes           # xóa thật
    thêm --xoa-khach để xóa luôn toàn bộ khách hàng, --xoa-hang để xóa luôn toàn bộ hàng hóa
"""
import argparse
from pathlib import Path

from sqlalchemy import text

from app.config import get_settings
from app.db import get_session

KEEP = {
    "alembic_version", "users", "employees", "employee_pay", "employee_salary_versions", "kpi_targets",
    "commission_rules", "attendance_holidays", "warehouses", "funds", "cash_categories", "finance_mappings",
    "suppliers", "supplier_opening_debts", "product_groups", "products", "stock_min_levels", "customers", "customer_phones",
    "customer_contacts", "customer_sites", "equipment", "push_keys", "push_subscriptions", "audit_log",
}
WIPE = {
    "notification_reads", "salary_advances", "payroll_lines", "payroll_months", "payroll_inputs",
    "commission_lines", "commission_months", "attendance_punches", "attendance_requests",
    "attendance_month_locks", "pending_item_moves", "pending_dispositions", "pending_items",
    "technician_return_lines", "technician_returns", "service_recovered_parts", "service_visits",
    "service_lines", "service_drafts", "sales_return_lines", "sales_returns", "sales_refunds",
    "sales_delivery_attempts", "sales_delivery_serials", "sales_delivery_lines", "sales_deliveries", "stock_reservations",
    "form_submissions", "equipment_custody", "part_requests", "daily_digests", "purchase_return_lines",
    "purchase_returns", "supplier_payments", "purchase_receipt_lines", "purchase_receipts",
    "purchase_order_lines", "purchase_orders", "cash_corrections", "day_closings", "fund_ledger",
    "fund_balances", "cash_docs", "sales_receipts", "sales_order_lines", "sales_orders", "quote_lines",
    # service_tickets includes MV1 repaired_by and MM1 split_from_id; wipe the entire dossier, keep users.
    "quotes", "attachments", "service_steps", "service_tickets", "stock_ledger", "stock_balances",
    "stock_doc_lines", "stock_docs", "doc_counters",
}
CUSTOMER_TABLES = ["equipment", "customer_sites", "customer_contacts", "customer_phones", "customers"]
PRODUCT_TABLES = ["equipment", "stock_min_levels", "products"]
TEST_CUSTOMERS = "(select code from customers where is_test)"
TEST_PRODUCTS = "(select code from products where is_test)"


def all_tables(session) -> set[str]:
    return set(session.execute(text("select tablename from pg_tables where schemaname = 'public'")).scalars())


def delete_order(session, tables: set[str]) -> list[str]:
    """Bảng con trước, bảng cha sau, theo khóa ngoại thật trong database."""
    rows = session.execute(text(
        "select conrelid::regclass::text, confrelid::regclass::text from pg_constraint "
        "where contype = 'f' and conrelid <> confrelid")).all()
    # equipment.custody_ticket_id được xóa trắng trước, nên không cần xóa máy trước phiếu.
    rows = [r for r in rows if tuple(r) != ("equipment", "service_tickets")]
    parents = {t: {p for c, p in rows if c == t and p in tables} for t in tables}
    order, left = [], set(tables)
    while left:
        # Bảng không còn bảng con nào chưa xóa thì xóa được.
        ready = sorted(t for t in left if not any(t in parents[c] for c in left if c != t))
        if not ready:
            raise SystemExit(f"Khóa ngoại vòng tròn giữa: {sorted(left)}")
        order += ready
        left -= set(ready)
    return order


def count(session, table: str, where: str = "") -> int:
    return session.execute(text(f"select count(*) from {table} {where}")).scalar_one()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="xóa thật")
    ap.add_argument("--xoa-khach", action="store_true", help="xóa luôn toàn bộ khách hàng và máy của khách")
    ap.add_argument("--xoa-hang", action="store_true", help="xóa luôn toàn bộ hàng hóa")
    args = ap.parse_args()
    session = next(get_session())
    try:
        _run(session, args)
    finally:
        session.close()


def _run(session, args) -> None:

    unknown = all_tables(session) - KEEP - WIPE
    if unknown:
        raise SystemExit(f"Có bảng mới chưa xếp vào nhóm giữ/xóa: {sorted(unknown)}. Dừng, chưa xóa gì.")
    wipe = set(WIPE)
    if args.xoa_khach:
        wipe |= set(CUSTOMER_TABLES)
    if args.xoa_hang:
        wipe |= set(PRODUCT_TABLES)
    order = delete_order(session, wipe)

    print("SẼ XÓA:")
    for t in sorted(wipe):
        n = count(session, t)
        if n:
            print(f"  {t}: {n}")
    if not args.xoa_khach:
        print(f"  khách thử nghiệm (is_test): {count(session, 'customers', 'where is_test')}")
    if not args.xoa_hang:
        print(f"  hàng thử nghiệm (is_test): {count(session, 'products', 'where is_test')}")
    print("GIỮ:")
    for t in sorted(KEEP - wipe - {"alembic_version"}):
        print(f"  {t}: {count(session, t)}")
    if not args.yes:
        print("Chưa xóa gì. Thêm --yes để xóa thật.")
        return

    # Chờ tối đa 10 giây; ai đang lưu phiếu dở thì dừng, không treo app.
    session.execute(text("set local lock_timeout = '10s'"))
    session.execute(text("lock table " + ", ".join(sorted(wipe)) + " in access exclusive mode"))
    sale_equipment = list(session.scalars(text('select distinct serial from sales_delivery_serials where created_equipment')))
    files = list(session.execute(text("select path from attachments")).scalars())
    # Máy khách đang được ghi là thợ/kho giữ theo phiếu cũ: đưa về "tại khách".
    session.execute(text(
        "update equipment set custody_location = 'TAI_KHACH', custody_holder_id = null, custody_ticket_id = null "
        "where custody_ticket_id is not null or custody_location <> 'TAI_KHACH'"))
    # Sổ kho, sổ quỹ, chấm công, lương... có trigger cấm xóa. Tắt tạm trong giao dịch này, xóa xong bật lại.
    guards = session.execute(text(
        "select tgrelid::regclass::text, tgname from pg_trigger where not tgisinternal and tgconstraint = 0 "
        "and tgrelid::regclass::text = any(:t)"), {"t": sorted(wipe)}).all()
    for t, g in guards:
        session.execute(text(f'alter table {t} disable trigger "{g}"'))
    for t in order:
        session.execute(text(f"delete from {t}"))
    # Hồ sơ chờ xử lý có constraint trigger hoãn đến cuối giao dịch; còn sự kiện chờ thì Postgres không cho
    # ALTER TABLE bật lại trigger. Cho chạy ngay (bảng đã trống nên luôn đạt) rồi mới bật.
    session.execute(text("set constraints all immediate"))
    for t, g in guards:
        session.execute(text(f'alter table {t} enable trigger "{g}"'))
    if 'equipment' not in wipe and sale_equipment:
        session.execute(text('delete from equipment where serial = any(:serials)'), {'serials': sale_equipment})
    if not args.xoa_khach:
        session.execute(text(f"update customers set merged_into = null where merged_into in {TEST_CUSTOMERS}"))
        session.execute(text(f"delete from equipment where customer_code in {TEST_CUSTOMERS}"))
        for t in ("customer_sites", "customer_contacts", "customer_phones"):
            session.execute(text(f"delete from {t} where customer_code in {TEST_CUSTOMERS}"))
        session.execute(text("delete from customers where is_test"))
    if not args.xoa_hang:
        session.execute(text(f"delete from equipment where product_code in {TEST_PRODUCTS}"))
        session.execute(text(f"delete from stock_min_levels where product_code in {TEST_PRODUCTS}"))
        session.execute(text("delete from products where is_test"))
    session.commit()

    root = Path(get_settings().files_dir)
    removed = 0
    for rel in files:
        p = root / rel
        if p.is_file():
            p.unlink()
            removed += 1
    print(f"Đã xóa xong. Đã xóa {removed} tệp ảnh/PDF đính kèm.")


if __name__ == "__main__":
    main()
