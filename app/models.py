"""Lược đồ dữ liệu chung. Mọi thay đổi cấu trúc phải đi kèm một file trong migrations/versions."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

ROLES = ("sales", "reception", "dispatcher", "warehouse", "technician", "finance", "director", "admin")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(200))
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Customer(Base):
    """Khách hàng dùng chung cho Sales, CARE, Finance, Điều phối. Mã KTD-000001."""

    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint("kind in ('CA_NHAN','CONG_TY')", name="ck_customers_kind"),
        CheckConstraint("status in ('DANG_DUNG','NGUNG','DA_GOP')", name="ck_customers_status"),
        CheckConstraint("credit_limit >= 0", name="ck_customers_credit_limit"),
        CheckConstraint("(status = 'DA_GOP') = (merged_into is not null)", name="ck_customers_merge"),
        # MST là duy nhất trong số khách đang dùng.
        Index("uq_customers_tax", "tax_code", unique=True, postgresql_where="tax_code <> '' and status <> 'DA_GOP'"),
        Index("ix_customers_search", "search_text", postgresql_using="gin", postgresql_ops={"search_text": "gin_trgm_ops"}),
    )

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="CA_NHAN")
    name: Mapped[str] = mapped_column(String(300))
    tax_code: Mapped[str] = mapped_column(String(13), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    allow_debt: Mapped[bool] = mapped_column(Boolean, default=False)
    credit_limit: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16), default="DANG_DUNG")
    merged_into: Mapped[str | None] = mapped_column(ForeignKey("customers.code"), nullable=True)
    old_codes: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    search_text: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    phones: Mapped[list["CustomerPhone"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan", order_by="CustomerPhone.phone"
    )
    contacts: Mapped[list["CustomerContact"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan", order_by="CustomerContact.id"
    )

    __mapper_args__ = {"version_id_col": version}


class CustomerPhone(Base):
    __tablename__ = "customer_phones"

    customer_code: Mapped[str] = mapped_column(ForeignKey("customers.code", ondelete="CASCADE"), primary_key=True)
    phone: Mapped[str] = mapped_column(String(11), primary_key=True, index=True)
    # True khi số này chỉ là của người liên hệ, không phải số chính của khách.
    is_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    customer: Mapped[Customer] = relationship(back_populates="phones")


class CustomerContact(Base):
    __tablename__ = "customer_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_code: Mapped[str] = mapped_column(ForeignKey("customers.code", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    phones: Mapped[list[str]] = mapped_column(ARRAY(String(11)), default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    customer: Mapped[Customer] = relationship(back_populates="contacts")


class Product(Base):
    """Danh mục hàng theo mã TD (TD-0001), dùng chung cho Kho, Sales, CARE."""

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("code ~ '^TD-[0-9]{4,}$'", name="ck_products_code"),
        CheckConstraint("sale_price >= 0", name="ck_products_price"),
        Index("ix_products_search", "search_text", postgresql_using="gin", postgresql_ops={"search_text": "gin_trgm_ops"}),
    )

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    group_name: Mapped[str] = mapped_column(String(100), default="")
    unit: Mapped[str] = mapped_column(String(32), default="cái")
    sale_price: Mapped[int] = mapped_column(BigInteger, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    search_text: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __mapper_args__ = {"version_id_col": version}


class AuditLog(Base):
    """Nhật ký: ai sửa gì, lúc nào, trước và sau. Không sửa, không xóa."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text, default="")


class DocCounter(Base):
    """Bộ đếm số chứng từ theo loại và năm: PN-26-00001. Chỉ app/doc_numbers.py ghi vào bảng này."""

    __tablename__ = "doc_counters"
    __table_args__ = (CheckConstraint("last >= 0", name="ck_doc_counters_last"),)

    prefix: Mapped[str] = mapped_column(String(4), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last: Mapped[int] = mapped_column(Integer, default=0)


class Warehouse(Base):
    __tablename__ = "warehouses"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    address: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        Index("uq_suppliers_tax", "tax_code", unique=True, postgresql_where="tax_code <> ''"),
        Index("ix_suppliers_search", "search_text", postgresql_using="gin", postgresql_ops={"search_text": "gin_trgm_ops"}),
    )

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    tax_code: Mapped[str] = mapped_column(String(13), default="")
    phones: Mapped[list[str]] = mapped_column(ARRAY(String(11)), default=list)
    address: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    search_text: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}


class StockDoc(Base):
    """Phiếu kho; K3 chỉ lập, sửa và hủy nháp, chưa ghi sổ."""

    __tablename__ = "stock_docs"
    __table_args__ = (
        CheckConstraint("kind in ('PN','PX','CK','KK')", name="ck_stock_docs_kind"),
        CheckConstraint("status in ('NHAP','DA_GHI','DA_HUY')", name="ck_stock_docs_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    kind: Mapped[str] = mapped_column(String(2))
    warehouse_code: Mapped[str] = mapped_column(ForeignKey("warehouses.code"))
    to_warehouse_code: Mapped[str | None] = mapped_column(ForeignKey("warehouses.code"))
    doc_date: Mapped[date] = mapped_column(Date)
    supplier_code: Mapped[str | None] = mapped_column(ForeignKey("suppliers.code"))
    customer_code: Mapped[str | None] = mapped_column(ForeignKey("customers.code"))
    reason: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(8), default="NHAP")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    posted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)

    lines: Mapped[list["StockDocLine"]] = relationship(
        back_populates="doc", cascade="all, delete-orphan", order_by="StockDocLine.line_no"
    )

    __mapper_args__ = {"version_id_col": version}


class StockDocLine(Base):
    __tablename__ = "stock_doc_lines"
    __table_args__ = (
        UniqueConstraint("doc_id", "line_no", name="uq_stock_doc_lines_no"),
        CheckConstraint("qty >= 0", name="ck_stock_doc_lines_qty"),
        CheckConstraint("unit_cost >= 0", name="ck_stock_doc_lines_cost"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("stock_docs.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    product_code: Mapped[str] = mapped_column(ForeignKey("products.code"))
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit_cost: Mapped[int | None] = mapped_column(BigInteger)
    serial: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str] = mapped_column(Text, default="")
    doc: Mapped[StockDoc] = relationship(back_populates="lines")


class StockBalance(Base):
    """Tồn hiện tại theo kho và mã hàng. Chỉ app/stock_posting.py ghi vào bảng này."""

    __tablename__ = "stock_balances"
    __table_args__ = (
        CheckConstraint("qty >= 0", name="ck_stock_balances_qty"),
        CheckConstraint("value >= 0", name="ck_stock_balances_value"),
        CheckConstraint("qty > 0 or value = 0", name="ck_stock_balances_empty"),
    )

    warehouse_code: Mapped[str] = mapped_column(ForeignKey("warehouses.code"), primary_key=True)
    product_code: Mapped[str] = mapped_column(ForeignKey("products.code"), primary_key=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal(0))
    value: Mapped[int] = mapped_column(BigInteger, default=0)
    # Giá vốn bình quân; khi tồn về 0 vẫn giữ giá lần cuối để kiểm kê tăng tồn có giá tham chiếu.
    avg_cost: Mapped[int] = mapped_column(BigInteger, default=0)


class StockLedger(Base):
    """Sổ kho: mỗi dòng là một lần tồn thay đổi do phiếu. Chỉ thêm, database chặn sửa và xóa."""

    __tablename__ = "stock_ledger"
    __table_args__ = (
        CheckConstraint("qty_in >= 0 and qty_out >= 0 and (qty_in > 0) <> (qty_out > 0)", name="ck_stock_ledger_qty"),
        Index("ix_stock_ledger_card", "warehouse_code", "product_code", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    warehouse_code: Mapped[str] = mapped_column(ForeignKey("warehouses.code"))
    product_code: Mapped[str] = mapped_column(ForeignKey("products.code"))
    doc_id: Mapped[int] = mapped_column(ForeignKey("stock_docs.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    # True với các dòng đảo ngược khi hủy phiếu đã ghi sổ.
    is_reversal: Mapped[bool] = mapped_column(Boolean, default=False)
    qty_in: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal(0))
    qty_out: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal(0))
    unit_cost: Mapped[int] = mapped_column(BigInteger)
    # Giá trị nhập (dương) hoặc xuất (âm), đơn vị đồng.
    amount: Mapped[int] = mapped_column(BigInteger)
    balance_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    balance_value: Mapped[int] = mapped_column(BigInteger)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StockMinLevel(Base):
    """Mức cảnh báo theo kho/mã hàng; min_qty=0 tắt cảnh báo, không xóa."""

    __tablename__ = "stock_min_levels"
    __table_args__ = (CheckConstraint("min_qty >= 0", name="ck_stock_min_levels_qty"),)

    warehouse_code: Mapped[str] = mapped_column(ForeignKey("warehouses.code"), primary_key=True)
    product_code: Mapped[str] = mapped_column(ForeignKey("products.code"), primary_key=True)
    min_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal(0))
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))

    __mapper_args__ = {"version_id_col": version}


SERVICE_KINDS = ("SUA_TAI_CHO", "MANG_VE", "BAO_HANH", "MUC_DINH_KY")
SERVICE_PRIORITIES = ("KHAN", "GAP", "THUONG")
SERVICE_STATUSES = ("MOI", "DA_GIAO", "DA_NHAN", "DA_HEN", "DANG_LAM", "CHO_LINH_KIEN", "HOAN_THANH", "DA_DONG", "KHONG_LAM_DUOC", "DA_HUY")
VAT_RATES = (0, 5, 8, 10)


def _in(column: str, values) -> str:
    return f"{column} in (" + ",".join(f"'{v}'" for v in values) + ")"


class ServiceTicket(Base):
    """Phiếu dịch vụ (D2): một việc sửa chữa, bảo hành hoặc giao mực, số DV-26-00001."""

    __tablename__ = "service_tickets"
    __table_args__ = (
        CheckConstraint(_in("kind", SERVICE_KINDS), name="ck_service_tickets_kind"),
        CheckConstraint(_in("priority", SERVICE_PRIORITIES), name="ck_service_tickets_priority"),
        CheckConstraint(_in("status", SERVICE_STATUSES), name="ck_service_tickets_status"),
        CheckConstraint("status = 'MOI' or assignee_id is not null or status = 'DA_HUY'", name="ck_service_tickets_assignee"),
        Index("ix_service_tickets_open", "status", "due_at"),
        CheckConstraint("billing in ('THU_TIEN','MIEN_PHI')", name="ck_service_tickets_billing"),
        CheckConstraint("billing <> 'MIEN_PHI' or free_reason <> ''", name="ck_service_tickets_free_reason"),
        CheckConstraint("(priced_at is null) = (total is null)", name="ck_service_tickets_priced"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    customer_code: Mapped[str] = mapped_column(ForeignKey("customers.code"), index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("customer_sites.id"))
    serial: Mapped[str | None] = mapped_column(ForeignKey("equipment.serial"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    priority: Mapped[str] = mapped_column(String(8), default="THUONG")
    symptom: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="MOI")
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    appointment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_reason: Mapped[str] = mapped_column(Text, default="")
    # Thợ xin chuyển việc: lý do chờ điều phối duyệt (giao thợ khác) hoặc từ chối.
    transfer_request: Mapped[str] = mapped_column(Text, default="")
    # Tiền (D6): THU_TIEN hoặc MIEN_PHI kèm lý do. Tổng chỉ ghi khi hoàn thành (khóa giá), null là chưa khóa.
    billing: Mapped[str | None] = mapped_column(String(8))
    free_reason: Mapped[str] = mapped_column(Text, default="")
    subtotal: Mapped[int | None] = mapped_column(BigInteger)
    vat_total: Mapped[int | None] = mapped_column(BigInteger)
    total: Mapped[int | None] = mapped_column(BigInteger)
    receivable: Mapped[int | None] = mapped_column(BigInteger)
    priced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, default=1)

    steps: Mapped[list["ServiceStep"]] = relationship(order_by="ServiceStep.id", viewonly=True)
    lines: Mapped[list["ServiceLine"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="ServiceLine.line_no"
    )

    __mapper_args__ = {"version_id_col": version}


class ServiceStep(Base):
    """Lịch sử phiếu dịch vụ: mỗi lần tạo, giao, chuyển trạng thái một dòng. Chỉ thêm."""

    __tablename__ = "service_steps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("service_tickets.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Attachment(Base):
    """Tệp đính kèm (D4) gắn vào một chứng từ bất kỳ. File nằm trên máy chủ, bảng chỉ giữ đường dẫn. Không xóa, chỉ ẩn."""

    __tablename__ = "attachments"
    __table_args__ = (
        CheckConstraint("size > 0", name="ck_attachments_size"),
        CheckConstraint("hidden = (hidden_reason <> '')", name="ck_attachments_hidden"),
        Index("ix_attachments_entity", "entity", "entity_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    tag: Mapped[str] = mapped_column(String(16), default="KHAC")
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(255))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden_reason: Mapped[str] = mapped_column(Text, default="")
    hidden_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}


class CustomerSite(Base):
    __tablename__ = "customer_sites"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_code: Mapped[str] = mapped_column(ForeignKey("customers.code"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(Text, default="")
    contact_name: Mapped[str] = mapped_column(String(200), default="")
    contact_phone: Mapped[str] = mapped_column(String(32), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __mapper_args__ = {"version_id_col": version}


class Equipment(Base):
    __tablename__ = "equipment"
    __table_args__ = (
        CheckConstraint("condition in ('MOI','CU')", name="ck_equipment_condition"),
        Index("ix_equipment_search", "search_text", postgresql_using="gin", postgresql_ops={"search_text": "gin_trgm_ops"}),
    )

    serial: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_code: Mapped[str] = mapped_column(ForeignKey("products.code"))
    customer_code: Mapped[str | None] = mapped_column(ForeignKey("customers.code"), index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("customer_sites.id"))
    warranty_until: Mapped[date | None] = mapped_column(Date)
    condition: Mapped[str] = mapped_column(String(3), default="MOI")
    note: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    search_text: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __mapper_args__ = {"version_id_col": version}


class ServiceLine(Base):
    """Dòng tiền của phiếu dịch vụ (D6): phí công (PHI) hoặc vật tư theo mã TD (VAT_TU) xuất từ một kho."""

    __tablename__ = "service_lines"
    __table_args__ = (
        UniqueConstraint("ticket_id", "line_no", name="uq_service_lines_no"),
        CheckConstraint("kind in ('PHI','VAT_TU')", name="ck_service_lines_kind"),
        CheckConstraint("(kind = 'VAT_TU') = (product_code is not null and warehouse_code is not null)", name="ck_service_lines_part"),
        CheckConstraint("qty > 0", name="ck_service_lines_qty"),
        CheckConstraint("unit_price >= 0 and amount >= 0 and vat_amount >= 0", name="ck_service_lines_money"),
        CheckConstraint(_in("vat_rate", VAT_RATES).replace("'", ""), name="ck_service_lines_vat"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("service_tickets.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(8))
    product_code: Mapped[str | None] = mapped_column(ForeignKey("products.code"))
    warehouse_code: Mapped[str | None] = mapped_column(ForeignKey("warehouses.code"))
    description: Mapped[str] = mapped_column(Text, default="")
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit_price: Mapped[int] = mapped_column(BigInteger)
    vat_rate: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[int] = mapped_column(BigInteger)
    vat_amount: Mapped[int] = mapped_column(BigInteger)
    # Phiếu xuất kho đã ghi sổ cho dòng vật tư; null khi chưa hoàn thành hoặc đã mở lại.
    stock_doc_id: Mapped[int | None] = mapped_column(ForeignKey("stock_docs.id"))

    ticket: Mapped["ServiceTicket"] = relationship(back_populates="lines")


QUOTE_STATUSES = ('NHAP', 'DA_GUI', 'CHAP_NHAN', 'TU_CHOI', 'DA_HUY')


class Quote(Base):
    __tablename__ = 'quotes'
    __table_args__ = (
        CheckConstraint(_in('status', QUOTE_STATUSES), name='ck_quotes_status'),
        CheckConstraint('subtotal >= 0 and vat_total >= 0 and total = subtotal + vat_total', name='ck_quotes_money'),
        Index('ix_quotes_status_valid', 'status', 'valid_until'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    customer_code: Mapped[str] = mapped_column(ForeignKey('customers.code'), index=True)
    contact_name: Mapped[str] = mapped_column(String(200), default='')
    valid_until: Mapped[date] = mapped_column(Date, server_default=func.timezone('Asia/Ho_Chi_Minh', func.now()).cast(Date) + 7)
    status: Mapped[str] = mapped_column(String(16), default='NHAP')
    note: Mapped[str] = mapped_column(Text, default='')
    cancel_reason: Mapped[str] = mapped_column(Text, default='')
    replaced_by_id: Mapped[int | None] = mapped_column(ForeignKey('quotes.id'))
    subtotal: Mapped[int] = mapped_column(BigInteger, default=0)
    vat_total: Mapped[int] = mapped_column(BigInteger, default=0)
    total: Mapped[int] = mapped_column(BigInteger, default=0)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, default=1)

    lines: Mapped[list['QuoteLine']] = relationship(back_populates='quote', cascade='all, delete-orphan', order_by='QuoteLine.line_no')
    __mapper_args__ = {'version_id_col': version}


class QuoteLine(Base):
    __tablename__ = 'quote_lines'
    __table_args__ = (
        CheckConstraint('qty > 0', name='ck_quote_lines_qty'),
        CheckConstraint('unit_price >= 0 and amount >= 0 and vat_amount >= 0', name='ck_quote_lines_money'),
        CheckConstraint(_in('vat_rate', VAT_RATES).replace("'", ''), name='ck_quote_lines_vat'),
    )

    quote_id: Mapped[int] = mapped_column(ForeignKey('quotes.id'), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_code: Mapped[str | None] = mapped_column(ForeignKey('products.code'))
    description: Mapped[str] = mapped_column(Text)
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit: Mapped[str] = mapped_column(String(32))
    unit_price: Mapped[int] = mapped_column(BigInteger)
    vat_rate: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[int] = mapped_column(BigInteger)
    vat_amount: Mapped[int] = mapped_column(BigInteger)

    quote: Mapped['Quote'] = relationship(back_populates='lines')


SALES_ORDER_STATUSES = ('NHAP', 'DA_CHOT', 'DA_GIAO', 'DA_HUY')


class SalesOrder(Base):
    __tablename__ = 'sales_orders'
    __table_args__ = (
        CheckConstraint(_in('status', SALES_ORDER_STATUSES), name='ck_sales_orders_status'),
        CheckConstraint('subtotal >= 0 and vat_total >= 0 and total = subtotal + vat_total', name='ck_sales_orders_money'),
        CheckConstraint('receivable is null or receivable >= 0', name='ck_sales_orders_receivable'),
        Index('uq_sales_orders_active_quote', 'quote_id', unique=True,
              postgresql_where=text("quote_id is not null and status <> 'DA_HUY'")),
        Index('ix_sales_orders_status_created', 'status', 'created_at'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    customer_code: Mapped[str] = mapped_column(ForeignKey('customers.code'), index=True)
    quote_id: Mapped[int | None] = mapped_column(ForeignKey('quotes.id'))
    contact_name: Mapped[str] = mapped_column(String(200), default='')
    delivery_address: Mapped[str] = mapped_column(Text, default='')
    note: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(16), default='NHAP')
    cancel_reason: Mapped[str] = mapped_column(Text, default='')
    subtotal: Mapped[int] = mapped_column(BigInteger, default=0)
    vat_total: Mapped[int] = mapped_column(BigInteger, default=0)
    total: Mapped[int] = mapped_column(BigInteger, default=0)
    receivable: Mapped[int | None] = mapped_column(BigInteger)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, default=1)

    lines: Mapped[list['SalesOrderLine']] = relationship(back_populates='order', cascade='all, delete-orphan',
                                                        order_by='SalesOrderLine.line_no')
    __mapper_args__ = {'version_id_col': version}


class SalesOrderLine(Base):
    __tablename__ = 'sales_order_lines'
    __table_args__ = (
        CheckConstraint("kind in ('HANG','KHAC')", name='ck_sales_order_lines_kind'),
        CheckConstraint("(kind = 'HANG' and product_code is not null) or "
                        "(kind = 'KHAC' and product_code is null and warehouse_code is null)",
                        name='ck_sales_order_lines_product_warehouse'),
        CheckConstraint('qty > 0', name='ck_sales_order_lines_qty'),
        CheckConstraint('unit_price >= 0 and amount >= 0 and vat_amount >= 0', name='ck_sales_order_lines_money'),
        CheckConstraint(_in('vat_rate', VAT_RATES).replace("'", ''), name='ck_sales_order_lines_vat'),
        CheckConstraint("kind = 'HANG' or stock_doc_id is null", name='ck_sales_order_lines_stock_doc'),
    )

    order_id: Mapped[int] = mapped_column(ForeignKey('sales_orders.id'), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))
    product_code: Mapped[str | None] = mapped_column(ForeignKey('products.code'))
    warehouse_code: Mapped[str | None] = mapped_column(ForeignKey('warehouses.code'))
    description: Mapped[str] = mapped_column(Text)
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit: Mapped[str] = mapped_column(String(32))
    unit_price: Mapped[int] = mapped_column(BigInteger)
    vat_rate: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[int] = mapped_column(BigInteger)
    vat_amount: Mapped[int] = mapped_column(BigInteger)
    stock_doc_id: Mapped[int | None] = mapped_column(ForeignKey('stock_docs.id'))

    order: Mapped['SalesOrder'] = relationship(back_populates='lines')


SALES_RECEIPT_STATUSES = ('CHO_XAC_NHAN', 'DA_XAC_NHAN', 'TU_CHOI', 'DA_HUY')
SALES_RECEIPT_METHODS = ('TIEN_MAT', 'CHUYEN_KHOAN')


class SalesReceipt(Base):
    __tablename__ = 'sales_receipts'
    __table_args__ = (
        CheckConstraint(_in('method', SALES_RECEIPT_METHODS), name='ck_sales_receipts_method'),
        CheckConstraint(_in('status', SALES_RECEIPT_STATUSES), name='ck_sales_receipts_status'),
        CheckConstraint('amount > 0', name='ck_sales_receipts_amount'),
        Index('ix_sales_receipts_order_status', 'order_id', 'status'),
        Index('ix_sales_receipts_status_reported', 'status', 'reported_at'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('sales_orders.id'))
    method: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(BigInteger)
    received_at: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(16), default='CHO_XAC_NHAN')
    reason: Mapped[str] = mapped_column(Text, default='')
    reported_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {'version_id_col': version}
