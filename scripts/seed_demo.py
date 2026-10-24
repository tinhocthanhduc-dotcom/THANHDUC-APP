"""Nạp dữ liệu mẫu để bấm thử trên máy thử (KHÔNG chạy trên máy chủ thật).

    python -m scripts.seed_demo --user tuan
Chạy bằng tài khoản Giám đốc đã tạo bằng create_admin. Mọi dữ liệu đi qua lõi như khi bấm trên web
(kiểm quyền, ghi sổ kho/quỹ, audit), nên số liệu khớp nhau. Kho K58 đã có thì dừng, không nạp lần hai.
Muốn làm lại từ đầu: tắt app với `down -v` (xóa sạch database), bật lại, tạo admin, chạy lại lệnh này.
"""
import argparse
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app import (
    audit,
    cash_docs,
    cash_posting,
    customers,
    finance_catalogs,
    finance_mappings,
    inventory_catalogs,
    products,
    purchase_orders,
    purchase_receipts,
    quotes,
    sales_orders,
    sales_receipts,
    supplier_debts,
)
from app.db import get_session
from app.models import CashCategory, FinanceMapping, Fund, Supplier, User, Warehouse
from app.schemas import (
    CashCategoryIn,
    CashDocIn,
    CustomerCreate,
    CustomerIn,
    FundIn,
    ProductIn,
    PurchaseOrderIn,
    PurchaseOrderLineIn,
    QuoteIn,
    QuoteLineIn,
    SalesOrderIn,
    SalesOrderLineIn,
    SalesReceiptReport,
    SupplierIn,
    WarehouseIn,
)
from app.security import hash_password

DEMO_PASSWORD = "thanhduc2026"
STAFF = [
    ("banhang", "Nhân viên bán hàng", ["sales"]),
    ("thukho", "Thủ kho", ["warehouse"]),
    ("ketoan", "Kế toán", ["finance"]),
    ("kythuat", "Kỹ thuật viên", ["technician"]),
    ("dieuphoi", "Điều phối", ["dispatcher", "reception"]),
]
PRODUCTS = [
    ("TD-0001", "Máy in HP LaserJet Pro M404dn", "Máy in", 6_900_000),
    ("TD-0002", "Máy in Canon LBP 2900", "Máy in", 3_450_000),
    ("TD-0003", "Hộp mực HP 76A (CF276A)", "Mực in", 1_650_000),
    ("TD-0004", "Hộp mực Canon 303", "Mực in", 450_000),
    ("TD-0005", "Mực nạp HP 1kg", "Mực in", 380_000),
    ("TD-0006", "Trống (drum) Brother DR-2385", "Linh kiện", 520_000),
    ("TD-0007", "Bao lụa (fuser film) HP M402", "Linh kiện", 280_000),
    ("TD-0008", "Giấy A4 Double A 70gsm (ram)", "Giấy", 78_000),
]


def step(title):
    print(f"- {title}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="tài khoản Giám đốc, ví dụ tuan")
    args = ap.parse_args()
    s = next(get_session())
    boss = s.scalars(select(User).where(User.username == args.user.lower())).first()
    if boss is None or "director" not in boss.roles:
        raise SystemExit("Không thấy tài khoản Giám đốc này. Tạo trước bằng scripts.create_admin.")
    if s.get(Warehouse, "K58") is not None:
        raise SystemExit("Đã có kho K58, có vẻ đã nạp dữ liệu mẫu rồi. Muốn làm lại: down -v rồi chạy lại.")
    today = cash_docs.today()

    step("Tài khoản nhân viên")
    staff = {}
    for username, name, roles in STAFF:
        user = s.scalars(select(User).where(User.username == username)).first()
        if user is None:
            user = User(username=username, full_name=name, password_hash=hash_password(DEMO_PASSWORD), roles=roles)
            s.add(user)
            s.flush()
            audit.record(s, boss, "user.create", "user", str(user.id), None, {"username": username, "roles": roles, "seed": True})
        staff[username] = user
    s.commit()

    step("Kho, nhà cung cấp, hàng hóa")
    inventory_catalogs.create(s, boss, Warehouse, WarehouseIn(code="K58", name="Kho 58", address="58 (cửa hàng chính)"))
    inventory_catalogs.create(s, boss, Warehouse, WarehouseIn(code="K145", name="Kho 145", address="145 (kho phụ)"))
    suppliers = [
        inventory_catalogs.create(s, boss, Supplier, SupplierIn(name="Công ty Mực in Sài Gòn (mẫu)", tax_code="0312345678", phones=["0281234567"])),
        inventory_catalogs.create(s, boss, Supplier, SupplierIn(name="Nhà phân phối HP Phương Nam (mẫu)", tax_code="0309876543", phones=["0287654321"])),
        inventory_catalogs.create(s, boss, Supplier, SupplierIn(name="Linh kiện máy in Chợ Lớn (mẫu)", phones=["0909111222"])),
    ]
    # Từ PR #133 mã hàng phải thuộc nhóm có trong danh mục Nhóm hàng.
    for group in dict.fromkeys(group for _, _, group, _ in PRODUCTS):
        products.create_group(s, boss, group)
    for code, name, group, price in PRODUCTS:
        products.create(s, boss, ProductIn(code=code, name=name, group_name=group, sale_price=price, is_test=True))
    s.commit()

    step("Khách hàng")
    new_customers = []
    for body in [
        CustomerIn(kind="CONG_TY", name="Công ty TNHH ABC (mẫu)", tax_code="0311112222", phones=["0283333444"],
                   address="12 Lê Lợi, Q.1", allow_debt=True, credit_limit=20_000_000, is_test=True),
        CustomerIn(kind="CONG_TY", name="Trường THCS Minh Khai (mẫu)", tax_code="0315556666", phones=["0285556666"],
                   address="Q.3", allow_debt=True, credit_limit=10_000_000, is_test=True),
        CustomerIn(kind="CA_NHAN", name="Anh Nam (mẫu)", phones=["0901234567"], address="Q. Bình Thạnh", is_test=True),
        CustomerIn(kind="CA_NHAN", name="Chị Lan (mẫu)", phones=["0912345678"], is_test=True),
    ]:
        customer, _ = customers.create(s, boss, CustomerCreate(customer=body))
        new_customers.append(customer)
    s.commit()

    step("Quỹ, danh mục thu chi, ghép tài chính")
    finance_catalogs.create(s, boss, Fund, FundIn(code="QUY-TM", name="Quỹ tiền mặt cửa hàng", kind="TIEN_MAT"))
    finance_catalogs.create(s, boss, Fund, FundIn(code="TK-ACB", name="Tài khoản ACB (mẫu)", kind="NGAN_HANG",
                                                  bank_name="ACB", account_no="000000000"))
    for code, name, direction in [
        ("THU-BAN-HANG", "Thu tiền bán hàng", "THU"), ("THU-DICH-VU", "Thu tiền dịch vụ", "THU"),
        ("THU-KHAC", "Thu khác", "THU"), ("CHI-MUA-HANG", "Chi trả nhà cung cấp", "CHI"),
        ("CHI-VAN-HANH", "Chi phí vận hành", "CHI"), ("CHI-LUONG", "Chi lương", "CHI"),
    ]:
        finance_catalogs.create(s, boss, CashCategory, CashCategoryIn(code=code, name=name, direction=direction))
    s.commit()
    targets = {
        "THU_TIEN_MAT": {"fund_code": "QUY-TM"}, "THU_CHUYEN_KHOAN": {"fund_code": "TK-ACB"},
        "DANH_MUC_BAN_HANG": {"category_code": "THU-BAN-HANG"}, "DANH_MUC_DICH_VU": {"category_code": "THU-DICH-VU"},
        "DANH_MUC_MUA_HANG": {"category_code": "CHI-MUA-HANG"},
    }
    for key, target in targets.items():
        row = s.get(FinanceMapping, key)
        finance_mappings.set_mapping(s, boss, key, version=row.version if row else None, **target)

    step("Tồn quỹ đầu kỳ")
    for fund, amount in [("QUY-TM", 20_000_000), ("TK-ACB", 150_000_000)]:
        doc = cash_docs.create(s, boss, CashDocIn(kind="PT", doc_date=today - timedelta(days=7), fund_code=fund,
                                                  amount=amount, is_opening=True, description="Tồn quỹ đầu kỳ (mẫu)"))
        cash_posting.post(s, boss, doc.id, doc.version)

    step("Đơn mua, nhận hàng, trả tiền nhà cung cấp")
    ncc_muc, ncc_hp = suppliers[0].code, suppliers[1].code
    po1 = purchase_orders.create(s, boss, PurchaseOrderIn(
        supplier_code=ncc_hp, order_date=today - timedelta(days=6), note="Nhập máy in và mực HP (mẫu)",
        lines=[
            PurchaseOrderLineIn(product_code="TD-0001", warehouse_code="K58", qty=Decimal(5), unit_cost=5_600_000),
            PurchaseOrderLineIn(product_code="TD-0002", warehouse_code="K58", qty=Decimal(4), unit_cost=2_750_000),
            PurchaseOrderLineIn(product_code="TD-0003", warehouse_code="K58", qty=Decimal(20), unit_cost=1_250_000),
            PurchaseOrderLineIn(product_code="TD-0008", warehouse_code="K145", qty=Decimal(100), unit_cost=62_000),
        ]))
    po1 = purchase_orders.order(s, boss, po1.id, po1.version)
    purchase_receipts.receive(s, boss, po1.id, po1.version, today - timedelta(days=5),
                              [{"order_line_id": line.id, "qty": line.qty} for line in po1.lines])
    po2 = purchase_orders.create(s, boss, PurchaseOrderIn(
        supplier_code=ncc_muc, order_date=today - timedelta(days=4), note="Nhập mực và linh kiện (mẫu)",
        lines=[
            PurchaseOrderLineIn(product_code="TD-0004", warehouse_code="K58", qty=Decimal(30), unit_cost=300_000),
            PurchaseOrderLineIn(product_code="TD-0005", warehouse_code="K145", qty=Decimal(10), unit_cost=250_000),
            PurchaseOrderLineIn(product_code="TD-0006", warehouse_code="K145", qty=Decimal(15), unit_cost=360_000),
            PurchaseOrderLineIn(product_code="TD-0007", warehouse_code="K58", qty=Decimal(20), unit_cost=170_000),
        ]))
    po2 = purchase_orders.order(s, boss, po2.id, po2.version)
    # Nhận một phần: NCC giao thiếu, còn chờ giao nốt.
    purchase_receipts.receive(s, boss, po2.id, po2.version, today - timedelta(days=3),
                              [{"order_line_id": line.id, "qty": line.qty / 2} for line in po2.lines])
    supplier_debts.pay(s, boss, ncc_hp, "TK-ACB", 40_000_000, today - timedelta(days=2), po1.id, "Trả đợt 1 (mẫu)")
    po3 = purchase_orders.create(s, boss, PurchaseOrderIn(
        supplier_code=suppliers[2].code, order_date=today, expected_date=today + timedelta(days=3), note="Đơn chờ giao (mẫu)",
        lines=[PurchaseOrderLineIn(product_code="TD-0007", warehouse_code="K145", qty=Decimal(10), unit_cost=165_000)]))
    purchase_orders.order(s, boss, po3.id, po3.version)

    step("Báo giá, đơn bán, giao hàng, báo thu và kế toán xác nhận")
    seller, accountant = staff["banhang"], staff["ketoan"]
    abc, truong, nam, lan = (c.code for c in new_customers)
    q = quotes.create(s, seller, QuoteIn(customer_code=abc, contact_name="Chị Hoa", valid_until=today + timedelta(days=7),
                                         note="Báo giá máy in văn phòng (mẫu)", lines=[
        QuoteLineIn(product_code="TD-0001", qty=Decimal(2), unit_price=6_900_000),
        QuoteLineIn(product_code="TD-0003", qty=Decimal(4), unit_price=1_650_000),
    ]))
    quotes.send(s, seller, q.id, q.version)
    quotes.create(s, seller, QuoteIn(customer_code=truong, note="Báo giá mực nạp cả năm (mẫu, chưa gửi)", lines=[
        QuoteLineIn(product_code="TD-0005", qty=Decimal(12), unit_price=380_000),
        QuoteLineIn(description="Công nạp mực tại chỗ", qty=Decimal(12), unit="lần", unit_price=50_000),
    ]))

    def sale(customer_code, lines, note, receipt=None):
        order = sales_orders.create(s, seller, SalesOrderIn(customer_code=customer_code, note=note, lines=lines))
        order = sales_orders.confirm(s, seller, order.id, order.version)
        order = sales_orders.deliver(s, seller, order.id, order.version)
        if receipt:
            method, amount, confirm = receipt
            r = sales_receipts.report(s, seller, order.id, SalesReceiptReport(
                version=order.version, method=method, amount=amount or order.receivable, received_at=today))
            if confirm:
                sales_receipts.confirm(s, accountant, r.id, r.version)
        return order

    sale(abc, [SalesOrderLineIn(kind="HANG", warehouse_code="K58", product_code="TD-0001", qty=Decimal(2), unit_price=6_900_000),
               SalesOrderLineIn(kind="HANG", warehouse_code="K58", product_code="TD-0003", qty=Decimal(4), unit_price=1_650_000)],
         "Bán cho công ty ABC, cho nợ (mẫu)")
    sale(nam, [SalesOrderLineIn(kind="HANG", warehouse_code="K58", product_code="TD-0002", qty=Decimal(1), unit_price=3_450_000),
               SalesOrderLineIn(kind="KHAC", description="Công cài đặt tại nhà", qty=Decimal(1), unit="lần", unit_price=100_000)],
         "Khách lẻ trả tiền mặt (mẫu)", ("TIEN_MAT", None, True))
    sale(lan, [SalesOrderLineIn(kind="HANG", warehouse_code="K58", product_code="TD-0004", qty=Decimal(2), unit_price=450_000)],
         "Khách chuyển khoản, chờ kế toán xác nhận (mẫu)", ("CHUYEN_KHOAN", None, False))
    sale(truong, [SalesOrderLineIn(kind="HANG", warehouse_code="K145", product_code="TD-0008", qty=Decimal(20), unit_price=78_000)],
         "Trường trả trước một phần (mẫu)", ("CHUYEN_KHOAN", 800_000, True))

    print("\nĐã nạp xong dữ liệu mẫu.")
    print(f"Tài khoản nhân viên (mật khẩu chung {DEMO_PASSWORD}): " + ", ".join(f"{u} ({'/'.join(r)})" for u, _, r in STAFF))
    print("Gợi ý: xem Tồn kho, Sổ quỹ, Công nợ NCC, Đơn mua (một đơn nhận một phần, một đơn chờ giao),")
    print("Đơn bán, Báo thu chờ xác nhận (đăng nhập ketoan), các trang báo cáo.")


if __name__ == "__main__":
    main()
