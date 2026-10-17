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
SERVICE_STATUSES = ("MOI", "DA_GIAO", "DA_NHAN", "DA_HEN", "DANG_LAM", "CHO_LINH_KIEN", "HOAN_THANH", "KHONG_LAM_DUOC", "DA_HUY")


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
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    customer_code: Mapped[str] = mapped_column(ForeignKey("customers.code"), index=True)
    # Địa điểm và máy của D1; khóa ngoại thêm khi bảng D1 có trên main.
    site_id: Mapped[int | None] = mapped_column(BigInteger)
    serial: Mapped[str | None] = mapped_column(String(64))
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
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, default=1)

    steps: Mapped[list["ServiceStep"]] = relationship(order_by="ServiceStep.id", viewonly=True)

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
