"""Lược đồ dữ liệu chung. Mọi thay đổi cấu trúc phải đi kèm một file trong migrations/versions."""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PG_UUID
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
    phone: Mapped[str] = mapped_column(Text, default='', server_default='')
    token_version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Kho phụ trách: chỉ lọc Việc của tôi/chuông của thủ kho, không chặn quyền. Rỗng = mọi kho.
    warehouse_codes: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list, server_default='{}')


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
    __table_args__ = (
        CheckConstraint("(kind in ('KHO','LOI') and holder_id is null) or (kind = 'THO' and holder_id is not null)", name="ck_warehouses_holder"),
        CheckConstraint("not supplier_hold or kind='LOI'", name='ck_warehouses_supplier_hold'),
        CheckConstraint("not recovered_only or kind='LOI'", name='ck_warehouses_recovered_only'),
        Index("uq_warehouses_active_holder", "holder_id", unique=True, postgresql_where="kind = 'THO' and active"),
    )

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    address: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    kind: Mapped[str] = mapped_column(String(3), default="KHO", server_default="KHO")
    holder_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    supplier_hold: Mapped[bool] = mapped_column(Boolean,default=False,server_default=text('false'))
    recovered_only: Mapped[bool] = mapped_column(Boolean,default=False,server_default=text('false'))
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
    source_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
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
SERVICE_STATUSES = ("MOI", "DA_GIAO", "DA_NHAN", "DA_HEN", "DANG_LAM", "CHO_LINH_KIEN", "TAM_DUNG", "HOAN_THANH", "DA_DONG", "KHONG_LAM_DUOC", "DA_HUY")
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
        CheckConstraint("(warranty_of_id is null and warranty_basis is null) or (kind = 'BAO_HANH' and warranty_basis in ('PHIEU_TRUOC','MAY_CON_HAN','HET_HAN'))", name='ck_service_tickets_warranty'),
        CheckConstraint("warranty_of_id is null or warranty_of_id <> id", name='ck_service_tickets_warranty_self'),
        CheckConstraint("appointment_slot in ('SANG','CHIEU','GIO') and appointment_at is not null or appointment_slot is null", name='ck_service_tickets_slot'),
        Index("ix_service_tickets_open", "status", "due_at"),
        CheckConstraint("hold_reason in ('','CHO_KHACH_DUYET','CHO_KY_THUAT_KHAC','CHO_DOI_TAC','HEN_NGAY_KHAC','KHAC','SUA_XONG_CHO_TRA')", name="ck_service_tickets_hold_reason"),
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
    appointment_slot: Mapped[str | None] = mapped_column(String(5))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hold_reason: Mapped[str] = mapped_column(String(24), default="", server_default="")
    status_reason: Mapped[str] = mapped_column(Text, default="")
    # Thợ xin chuyển việc: lý do chờ điều phối duyệt (giao thợ khác) hoặc từ chối.
    transfer_request: Mapped[str] = mapped_column(Text, default="")
    warranty_of_id: Mapped[int | None] = mapped_column(ForeignKey('service_tickets.id', name='fk_service_tickets_warranty_of'), index=True)
    warranty_basis: Mapped[str | None] = mapped_column(String(16))
    # Tiền (D6): THU_TIEN hoặc MIEN_PHI kèm lý do. Tổng chỉ ghi khi hoàn thành (khóa giá), null là chưa khóa.
    billing: Mapped[str | None] = mapped_column(String(8))
    free_reason: Mapped[str] = mapped_column(Text, default="")
    # Thẻ B: thợ ghi lỗi thực tế gặp và cách đã xử lý khi hoàn thành.
    fault_found: Mapped[str] = mapped_column(Text, default="", server_default="")
    fix_done: Mapped[str] = mapped_column(Text, default="", server_default="")
    subtotal: Mapped[int | None] = mapped_column(BigInteger)
    vat_total: Mapped[int | None] = mapped_column(BigInteger)
    total: Mapped[int | None] = mapped_column(BigInteger)
    receivable: Mapped[int | None] = mapped_column(BigInteger)
    priced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    repaired_by: Mapped[int | None] = mapped_column(ForeignKey('users.id', name='fk_service_tickets_repaired_by'))
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


VISIT_OUTCOMES = ('XONG', 'HEN_LAI', 'KHONG_GAP_KHACH', 'CHO_LINH_KIEN', 'TAM_DUNG',
                  'KHONG_LAM_DUOC', 'CHUYEN_THO', 'HUY')


class ServiceVisit(Base):
    """Một lần đến khách; chỉ được chốt lượt mở một lần, không xóa."""
    __tablename__ = 'service_visits'
    __table_args__ = (
        CheckConstraint(_in('outcome', VISIT_OUTCOMES), name='ck_service_visits_outcome'),
        CheckConstraint('(ended_at is null) = (outcome is null)', name='ck_service_visits_closed'),
        CheckConstraint('ended_at is null or ended_at >= started_at', name='ck_service_visits_time'),
        CheckConstraint("(outcome <> 'KHONG_GAP_KHACH' or next_appointment_at is not null) and (outcome not in ('HEN_LAI','KHONG_GAP_KHACH') or btrim(note) <> '')", name='ck_service_visits_appointment'),
        Index('uq_service_visits_open_ticket', 'ticket_id', unique=True, postgresql_where=text('ended_at is null')),
        Index('ix_service_visits_technician_started', 'technician_id', 'started_at'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey('service_tickets.id'), index=True)
    technician_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(Text, default='', server_default='')
    next_appointment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Thẻ B: vị trí lúc bấm Đã đến khách (trình duyệt cho phép thì có, không thì để trống).
    arrive_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    arrive_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    arrive_accuracy: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    __mapper_args__ = {'version_id_col': version}


class ServiceDraft(Base):
    """Thẻ B: nháp báo cáo hoàn thành của thợ, lưu lên máy chủ để mất sóng/tắt máy không mất."""
    __tablename__ = 'service_drafts'
    ticket_id: Mapped[int] = mapped_column(ForeignKey('service_tickets.id'), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ServiceRecoveredPart(Base):
    """Linh kiện hỏng của khách, tách khỏi hàng tốt; giữ nguyên người đang cầm khi giao lại phiếu."""
    __tablename__ = 'service_recovered_parts'
    __table_args__ = (
        CheckConstraint('qty > 0', name='ck_recovered_parts_qty'),
        CheckConstraint("status in ('CHO_NOP','DA_NHAN','DA_HUY')", name='ck_recovered_parts_status'),
        CheckConstraint("(status = 'DA_NHAN' and warehouse_code is not null and stock_doc_id is not null and received_by is not null and received_at is not null) or (status <> 'DA_NHAN' and warehouse_code is null and stock_doc_id is null and received_by is null and received_at is null)", name='ck_recovered_parts_receipt'),
        CheckConstraint("(status = 'DA_HUY') = (btrim(void_reason) <> '')", name='ck_recovered_parts_reason'),
        Index('ix_recovered_parts_holder_status', 'holder_id', 'status'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey('service_tickets.id'), index=True)
    product_code: Mapped[str] = mapped_column(ForeignKey('products.code'))
    qty: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text)
    holder_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(8), default='CHO_NOP')
    warehouse_code: Mapped[str | None] = mapped_column(ForeignKey('warehouses.code'))
    stock_doc_id: Mapped[int | None] = mapped_column(ForeignKey('stock_docs.id'), unique=True)
    received_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str] = mapped_column(Text, default='', server_default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    __mapper_args__ = {'version_id_col': version}


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
        CheckConstraint("custody_location in ('TAI_KHACH','THO_GIU','KHO_KY_THUAT','DOI_TAC')", name="ck_equipment_custody_location"),
        CheckConstraint("(custody_location = 'THO_GIU') = (custody_holder_id is not null)", name="ck_equipment_custody_holder"),
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
    custody_location: Mapped[str] = mapped_column(String(16), default='TAI_KHACH', server_default='TAI_KHACH')
    custody_holder_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    custody_ticket_id: Mapped[int | None] = mapped_column(ForeignKey('service_tickets.id', name='fk_equipment_custody_ticket', use_alter=True), index=True)
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


FULFILLMENTS = ('TAI_QUAY','KHACH_LAY_SAU','THO_GIAO','VAN_CHUYEN')
PAYMENT_TERMS = ('TRA_NGAY','DAT_COC','CONG_NO')


SALES_ORDER_STATUSES = ('NHAP', 'DA_CHOT', 'GIAO_MOT_PHAN', 'DA_GIAO', 'DA_HUY')


class SalesOrder(Base):
    __tablename__ = 'sales_orders'
    __table_args__ = (
        CheckConstraint(_in('status', SALES_ORDER_STATUSES), name='ck_sales_orders_status'),
        CheckConstraint('subtotal >= 0 and vat_total >= 0 and total = subtotal + vat_total', name='ck_sales_orders_money'),
        CheckConstraint('receivable is null or receivable >= 0', name='ck_sales_orders_receivable'),
        CheckConstraint(_in('fulfillment', FULFILLMENTS), name='ck_sales_orders_fulfillment'),
        CheckConstraint(_in('payment_term', PAYMENT_TERMS), name='ck_sales_orders_payment_term'),
        CheckConstraint('due_days between 0 and 180', name='ck_sales_orders_due_days'),
        CheckConstraint("payment_term <> 'DAT_COC' or (deposit_amount is not null and deposit_amount > 0 and deposit_amount <= total)", name='ck_sales_orders_deposit'),
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
    promised_date: Mapped[date | None] = mapped_column(Date)
    delivery_assignee_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    delivery_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    service_ticket_id: Mapped[int | None] = mapped_column(ForeignKey('service_tickets.id'))
    fulfillment: Mapped[str] = mapped_column(String(16), default='TAI_QUAY', server_default='TAI_QUAY')
    payment_term: Mapped[str] = mapped_column(String(16), default='TRA_NGAY', server_default='TRA_NGAY')
    due_days: Mapped[int] = mapped_column(Integer, default=30, server_default=text('30'))
    deposit_amount: Mapped[int | None] = mapped_column(BigInteger)
    credit_override_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    credit_override_reason: Mapped[str] = mapped_column(Text, default='', server_default='')
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
        CheckConstraint('closed_qty >= 0 and closed_qty <= qty', name='ck_sales_order_lines_closed_qty'),
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
    closed_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0, server_default=text('0'))
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
        CheckConstraint('(order_id is not null) <> (ticket_id is not null)', name='ck_sales_receipts_source'),
        Index('ix_sales_receipts_order_status', 'order_id', 'status'),
        Index('ix_sales_receipts_ticket_status', 'ticket_id', 'status'),
        Index('ix_sales_receipts_status_reported', 'status', 'reported_at'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey('sales_orders.id'))
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey('service_tickets.id'))
    cash_doc_id: Mapped[int | None] = mapped_column(ForeignKey('cash_docs.id'))
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


class Fund(Base):
    __tablename__ = 'funds'
    __table_args__ = (
        CheckConstraint("kind in ('TIEN_MAT','NGAN_HANG')", name='ck_funds_kind'),
        CheckConstraint("kind <> 'NGAN_HANG' or length(trim(account_no)) > 0", name='ck_funds_account'),
    )
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(16))
    bank_name: Mapped[str] = mapped_column(String(300), default='')
    account_no: Mapped[str] = mapped_column(String(100), default='')
    note: Mapped[str] = mapped_column(Text, default='')
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text('true'))
    search_text: Mapped[str] = mapped_column(Text, default='')
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {'version_id_col': version}


class CashCategory(Base):
    __tablename__ = 'cash_categories'
    __table_args__ = (CheckConstraint("direction in ('THU','CHI')", name='ck_cash_categories_direction'),)
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    direction: Mapped[str] = mapped_column(String(3))
    note: Mapped[str] = mapped_column(Text, default='')
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text('true'))
    search_text: Mapped[str] = mapped_column(Text, default='')
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {'version_id_col': version}


class FinanceMapping(Base):
    __tablename__ = 'finance_mappings'
    __table_args__ = (
        CheckConstraint("key in ('THU_TIEN_MAT','THU_CHUYEN_KHOAN','DANH_MUC_BAN_HANG','DANH_MUC_DICH_VU','DANH_MUC_MUA_HANG','DANH_MUC_HOAN_TIEN_BAN','DANH_MUC_LUONG')", name='ck_finance_mappings_key'),
        CheckConstraint("(key in ('THU_TIEN_MAT','THU_CHUYEN_KHOAN') and category_code is null) or "
                        "(key in ('DANH_MUC_BAN_HANG','DANH_MUC_DICH_VU','DANH_MUC_MUA_HANG','DANH_MUC_HOAN_TIEN_BAN','DANH_MUC_LUONG') and fund_code is null)", name='ck_finance_mappings_target'),
    )
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    fund_code: Mapped[str | None] = mapped_column(ForeignKey('funds.code'))
    category_code: Mapped[str | None] = mapped_column(ForeignKey('cash_categories.code'))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {'version_id_col': version}


class CashDoc(Base):
    __tablename__ = 'cash_docs'
    __table_args__ = (
        CheckConstraint("kind in ('PT','PC','CQ')", name='ck_cash_docs_kind'),
        CheckConstraint("status in ('NHAP','DA_GHI','DA_HUY')", name='ck_cash_docs_status'),
        CheckConstraint('amount > 0', name='ck_cash_docs_amount'),
        CheckConstraint("(kind='CQ' and to_fund_code is not null and to_fund_code <> fund_code) or (kind<>'CQ' and to_fund_code is null)", name='ck_cash_docs_transfer'),
        CheckConstraint("((kind='CQ' or is_opening) and category_code is null) or (kind in ('PT','PC') and not is_opening and category_code is not null)", name='ck_cash_docs_category'),
        CheckConstraint("not is_opening or kind <> 'CQ'", name='ck_cash_docs_opening'),
        CheckConstraint('customer_code is null or supplier_code is null', name='ck_cash_docs_partner'),
        CheckConstraint("(source_type is null and source_id is null) or (source_type is not null and source_id is not null and length(trim(source_type))>0 and length(trim(source_id))>0)", name='ck_cash_docs_source'),
        Index('uq_cash_docs_active_source','source_type','source_id',unique=True,
              postgresql_where=text("source_type is not null and source_id is not null and status <> 'DA_HUY'")),
        Index('ix_cash_docs_date','doc_date','id'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    kind: Mapped[str] = mapped_column(String(2))
    doc_date: Mapped[date] = mapped_column(Date)
    fund_code: Mapped[str] = mapped_column(ForeignKey('funds.code'))
    to_fund_code: Mapped[str | None] = mapped_column(ForeignKey('funds.code'))
    category_code: Mapped[str | None] = mapped_column(ForeignKey('cash_categories.code'))
    amount: Mapped[int] = mapped_column(BigInteger)
    customer_code: Mapped[str | None] = mapped_column(ForeignKey('customers.code'))
    supplier_code: Mapped[str | None] = mapped_column(ForeignKey('suppliers.code'))
    partner_name: Mapped[str] = mapped_column(String(300), default='')
    description: Mapped[str] = mapped_column(Text, default='')
    is_opening: Mapped[bool] = mapped_column(Boolean, default=False)
    source_type: Mapped[str | None] = mapped_column(String(64))
    source_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(8), default='NHAP')
    void_reason: Mapped[str] = mapped_column(Text, default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    posted_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {'version_id_col': version}


class FundBalance(Base):
    __tablename__ = 'fund_balances'
    __table_args__ = (CheckConstraint('balance >= 0',name='ck_fund_balances_nonnegative'),)
    fund_code: Mapped[str] = mapped_column(ForeignKey('funds.code'), primary_key=True)
    balance: Mapped[int] = mapped_column(BigInteger, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {'version_id_col': version}


class FundLedger(Base):
    __tablename__ = 'fund_ledger'
    __table_args__ = (
        CheckConstraint('amount <> 0',name='ck_fund_ledger_amount'),
        CheckConstraint('balance_after >= 0',name='ck_fund_ledger_balance'),
        UniqueConstraint('doc_id','fund_code','is_reversal',name='uq_fund_ledger_move'),
        Index('ix_fund_ledger_statement','fund_code','entry_date','id'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey('funds.code'))
    entry_date: Mapped[date] = mapped_column(Date)
    doc_id: Mapped[int] = mapped_column(ForeignKey('cash_docs.id'))
    amount: Mapped[int] = mapped_column(BigInteger)
    balance_after: Mapped[int] = mapped_column(BigInteger)
    is_reversal: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DayClosing(Base):
    __tablename__ = 'day_closings'
    __table_args__ = (CheckConstraint("status in ('DA_CHOT','DA_MO')",name='ck_day_closings_status'),)
    day: Mapped[date] = mapped_column(Date,primary_key=True)
    status: Mapped[str] = mapped_column(String(8))
    closed_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    unlocked_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unlock_reason: Mapped[str] = mapped_column(Text,default='')
    version: Mapped[int] = mapped_column(Integer,default=1,nullable=False)
    __mapper_args__ = {'version_id_col': version}


class CashCorrection(Base):
    __tablename__ = 'cash_corrections'
    __table_args__ = (
        CheckConstraint("status in ('CHO_DUYET','DA_DUYET','TU_CHOI')",name='ck_cash_corrections_status'),
        CheckConstraint('new_amount > 0',name='ck_cash_corrections_amount'),
        Index('uq_cash_corrections_pending','doc_id',unique=True,postgresql_where=text("status='CHO_DUYET'")),
    )
    id: Mapped[int] = mapped_column(BigInteger,primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey('cash_docs.id'))
    reason: Mapped[str] = mapped_column(Text)
    new_amount: Mapped[int] = mapped_column(BigInteger)
    new_fund_code: Mapped[str] = mapped_column(ForeignKey('funds.code'))
    new_category_code: Mapped[str] = mapped_column(ForeignKey('cash_categories.code'))
    new_description: Mapped[str] = mapped_column(Text,default='')
    status: Mapped[str] = mapped_column(String(12),default='CHO_DUYET')
    requested_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decide_reason: Mapped[str] = mapped_column(Text,default='')
    new_doc_id: Mapped[int | None] = mapped_column(ForeignKey('cash_docs.id'))
    version: Mapped[int] = mapped_column(Integer,default=1,nullable=False)
    __mapper_args__ = {'version_id_col': version}


PURCHASE_ORDER_STATUSES = ('NHAP', 'DA_DAT', 'NHAN_MOT_PHAN', 'DA_NHAN', 'DA_HUY')


class PurchaseOrder(Base):
    __tablename__ = 'purchase_orders'
    __table_args__ = (
        CheckConstraint(_in('status', PURCHASE_ORDER_STATUSES), name='ck_purchase_orders_status'),
        CheckConstraint('subtotal >= 0 and vat_total >= 0 and total >= 0', name='ck_purchase_orders_money'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    supplier_code: Mapped[str] = mapped_column(ForeignKey('suppliers.code'))
    order_date: Mapped[date] = mapped_column(Date)
    expected_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default='NHAP')
    note: Mapped[str] = mapped_column(Text, default='')
    subtotal: Mapped[int] = mapped_column(BigInteger, default=0)
    vat_total: Mapped[int] = mapped_column(BigInteger, default=0)
    total: Mapped[int] = mapped_column(BigInteger, default=0)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ordered_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str] = mapped_column(Text, default='')
    close_reason: Mapped[str] = mapped_column(Text, default='', server_default=text("''"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    lines: Mapped[list['PurchaseOrderLine']] = relationship(back_populates='order', cascade='all, delete-orphan', order_by='PurchaseOrderLine.line_no')
    __mapper_args__ = {'version_id_col': version}


class PurchaseOrderLine(Base):
    __tablename__ = 'purchase_order_lines'
    __table_args__ = (
        UniqueConstraint('order_id', 'line_no', name='uq_purchase_order_lines_number'),
        CheckConstraint('qty > 0', name='ck_purchase_order_lines_qty'),
        CheckConstraint('unit_cost >= 0 and amount >= 0 and vat_amount >= 0', name='ck_purchase_order_lines_money'),
        CheckConstraint(_in('vat_rate', VAT_RATES).replace("'", ''), name='ck_purchase_order_lines_vat'),
        CheckConstraint('received_qty >= 0 and received_qty <= qty', name='ck_purchase_order_lines_received'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('purchase_orders.id'), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_code: Mapped[str] = mapped_column(ForeignKey('products.code'))
    warehouse_code: Mapped[str] = mapped_column(ForeignKey('warehouses.code'))
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit_cost: Mapped[int] = mapped_column(BigInteger)
    vat_rate: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(BigInteger)
    vat_amount: Mapped[int] = mapped_column(BigInteger)
    received_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0, server_default=text('0'))
    order: Mapped['PurchaseOrder'] = relationship(back_populates='lines')


class PurchaseReceipt(Base):
    __tablename__ = 'purchase_receipts'
    __table_args__ = (
        CheckConstraint("status in ('DA_GHI','DA_HUY')", name='ck_purchase_receipts_status'),
        CheckConstraint('amount >= 0 and vat_amount >= 0 and total >= 0', name='ck_purchase_receipts_money'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('purchase_orders.id'), index=True)
    stock_doc_id: Mapped[int] = mapped_column(ForeignKey('stock_docs.id'), unique=True)
    warehouse_code: Mapped[str] = mapped_column(ForeignKey('warehouses.code'))
    receipt_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(BigInteger)
    vat_amount: Mapped[int] = mapped_column(BigInteger)
    total: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(8), default='DA_GHI')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    void_reason: Mapped[str] = mapped_column(Text, default='')
    voided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    lines: Mapped[list['PurchaseReceiptLine']] = relationship(back_populates='receipt', cascade='all, delete-orphan', order_by='PurchaseReceiptLine.id')
    __mapper_args__ = {'version_id_col': version}


class PurchaseReceiptLine(Base):
    __tablename__ = 'purchase_receipt_lines'
    __table_args__ = (
        CheckConstraint('qty > 0', name='ck_purchase_receipt_lines_qty'),
        CheckConstraint('unit_cost >= 0 and amount >= 0 and vat_amount >= 0', name='ck_purchase_receipt_lines_money'),
        CheckConstraint(_in('vat_rate', VAT_RATES).replace("'", ''), name='ck_purchase_receipt_lines_vat'),
        UniqueConstraint('receipt_id', 'order_line_id', name='uq_purchase_receipt_lines_order_line'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey('purchase_receipts.id'), index=True)
    order_line_id: Mapped[int] = mapped_column(ForeignKey('purchase_order_lines.id'), index=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit_cost: Mapped[int] = mapped_column(BigInteger)
    vat_rate: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(BigInteger)
    vat_amount: Mapped[int] = mapped_column(BigInteger)
    receipt: Mapped['PurchaseReceipt'] = relationship(back_populates='lines')


class SupplierPayment(Base):
    __tablename__ = 'supplier_payments'
    __table_args__ = (
        CheckConstraint("status in ('DA_GHI','DA_HUY')", name='ck_supplier_payments_status'),
        CheckConstraint('amount > 0', name='ck_supplier_payments_amount'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    supplier_code: Mapped[str] = mapped_column(ForeignKey('suppliers.code'), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey('purchase_orders.id'), index=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey('funds.code'))
    amount: Mapped[int] = mapped_column(BigInteger)
    paid_at: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(8), default='DA_GHI')
    cash_doc_id: Mapped[int] = mapped_column(ForeignKey('cash_docs.id'), unique=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    void_reason: Mapped[str] = mapped_column(Text, default='')
    voided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {'version_id_col': version}


class SupplierOpeningDebt(Base):
    __tablename__ = 'supplier_opening_debts'
    __table_args__ = (CheckConstraint('amount >= 0', name='ck_supplier_opening_debts_amount'),)
    supplier_code: Mapped[str] = mapped_column(ForeignKey('suppliers.code'), primary_key=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    as_of: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {'version_id_col': version}


class PurchaseReturn(Base):
    __tablename__ = 'purchase_returns'
    __table_args__ = (
        CheckConstraint("status in ('DA_GHI','DA_HUY')", name='ck_purchase_returns_status'),
        CheckConstraint('amount >= 0 and vat_amount >= 0 and total >= 0', name='ck_purchase_returns_money'),
        CheckConstraint("length(trim(reason)) > 0", name='ck_purchase_returns_reason'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey('purchase_receipts.id'), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('purchase_orders.id'), index=True)
    supplier_code: Mapped[str] = mapped_column(ForeignKey('suppliers.code'), index=True)
    warehouse_code: Mapped[str] = mapped_column(ForeignKey('warehouses.code'))
    stock_doc_id: Mapped[int] = mapped_column(ForeignKey('stock_docs.id'), unique=True)
    return_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    amount: Mapped[int] = mapped_column(BigInteger)
    vat_amount: Mapped[int] = mapped_column(BigInteger)
    total: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(8), default='DA_GHI')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    void_reason: Mapped[str] = mapped_column(Text, default='')
    voided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    lines: Mapped[list['PurchaseReturnLine']] = relationship(back_populates='return_doc', cascade='all, delete-orphan', order_by='PurchaseReturnLine.id')
    __mapper_args__ = {'version_id_col': version}


class PurchaseReturnLine(Base):
    __tablename__ = 'purchase_return_lines'
    __table_args__ = (
        CheckConstraint('qty > 0', name='ck_purchase_return_lines_qty'),
        CheckConstraint('unit_cost >= 0 and amount >= 0 and vat_amount >= 0', name='ck_purchase_return_lines_money'),
        CheckConstraint(_in('vat_rate', VAT_RATES).replace("'", ''), name='ck_purchase_return_lines_vat'),
        UniqueConstraint('return_id', 'receipt_line_id', name='uq_purchase_return_lines_receipt_line'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    return_id: Mapped[int] = mapped_column(ForeignKey('purchase_returns.id'), index=True)
    receipt_line_id: Mapped[int] = mapped_column(ForeignKey('purchase_receipt_lines.id'), index=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit_cost: Mapped[int] = mapped_column(BigInteger)
    vat_rate: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(BigInteger)
    vat_amount: Mapped[int] = mapped_column(BigInteger)
    return_doc: Mapped['PurchaseReturn'] = relationship(back_populates='lines')


class DailyDigest(Base):
    """Ảnh chụp Tổng quan đã lưu; mỗi ngày giữ bản mới nhất."""

    __tablename__ = 'daily_digests'

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {'version_id_col': version}


class PartRequest(Base):
    """KT3: yêu cầu cấp linh kiện, không xóa."""
    __tablename__ = "part_requests"
    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_part_requests_qty"),
        CheckConstraint("btrim(reason) <> ''", name="ck_part_requests_reason"),
        CheckConstraint("status in ('CHO_CAP','DA_CAP','HET_HANG','DA_HUY')", name="ck_part_requests_status"),
        Index("ix_part_requests_ticket_id", "ticket_id"),
        Index("ix_part_requests_queue", "status", "created_at", "id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    # Không gắn phiếu = thợ xin hàng mang theo, cấp vào kho riêng của người yêu cầu.
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("service_tickets.id"))
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    product_code: Mapped[str] = mapped_column(ForeignKey("products.code"))
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(8), default="CHO_CAP")
    from_warehouse_code: Mapped[str | None] = mapped_column(ForeignKey("warehouses.code"))
    to_warehouse_code: Mapped[str | None] = mapped_column(ForeignKey("warehouses.code"))
    stock_doc_id: Mapped[int | None] = mapped_column(ForeignKey("stock_docs.id"))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {"version_id_col": version}


class EquipmentCustody(Base):
    """KT4: lịch sử nhận và bàn giao máy khách, tách khỏi tồn kho công ty."""
    __tablename__ = 'equipment_custody'
    __table_args__ = (
        CheckConstraint("location in ('TAI_KHACH','THO_GIU','KHO_KY_THUAT','DOI_TAC')", name='ck_custody_location'),
        CheckConstraint("(location = 'THO_GIU') = (holder_id is not null)", name='ck_custody_holder'),
        CheckConstraint("location <> 'DOI_TAC' or btrim(partner_name) <> ''", name='ck_custody_partner'),
        CheckConstraint("status in ('CHO_NHAN','DA_NHAN','DA_HUY')", name='ck_custody_status'),
        CheckConstraint("accessories <@ ARRAY['DAY_NGUON','KHAY_GIAY','HOP_MUC','DAY_USB','KHAC']::varchar[]", name='ck_custody_accessories'),
        CheckConstraint("(status = 'CHO_NHAN' and confirmed_by is null and confirmed_at is null) or (status <> 'CHO_NHAN' and confirmed_by is not null and confirmed_at is not null)", name='ck_custody_confirmation'),
        Index('uq_custody_pending_serial', 'serial', unique=True, postgresql_where=text("status = 'CHO_NHAN'")),
        Index('ix_custody_serial_id', 'serial', 'id'),
        Index('ix_custody_ticket_id', 'ticket_id'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    serial: Mapped[str] = mapped_column(ForeignKey('equipment.serial'))
    ticket_id: Mapped[int] = mapped_column(ForeignKey('service_tickets.id'))
    location: Mapped[str] = mapped_column(String(16))
    holder_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    partner_name: Mapped[str] = mapped_column(String(300), default='')
    expected_back: Mapped[date | None] = mapped_column(Date)
    accessories: Mapped[list[str]] = mapped_column(ARRAY(String(16)))
    accessories_note: Mapped[str] = mapped_column(Text, default='')
    condition_note: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(8))
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {'version_id_col': version}


class FormSubmission(Base):
    __tablename__ = "form_submissions"
    __table_args__ = (
        CheckConstraint("status in ('DANG_XU_LY','XONG')", name="ck_form_submissions_status"),
        CheckConstraint("(status = 'DANG_XU_LY' and finished_at is null and result_url = '') or (status = 'XONG' and finished_at is not null and result_url <> '')",
                        name="ck_form_submissions_result"),
        Index("ix_form_submissions_created_at", "created_at"),
    )

    key: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    result_url: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StockReservation(Base):
    __tablename__ = 'stock_reservations'
    __table_args__ = (
        ForeignKeyConstraint(['order_id','order_line_id'], ['sales_order_lines.order_id','sales_order_lines.line_no']),
        CheckConstraint('qty > 0', name='ck_stock_reservations_qty'),
        CheckConstraint("status <> 'DA_NHA' or length(trim(reason)) > 0", name='ck_stock_reservations_release_reason'),
        CheckConstraint("status in ('GIU','DA_XUAT','DA_NHA')", name='ck_stock_reservations_status'),
        CheckConstraint("(status = 'GIU' and closed_by is null and closed_at is null) or "
                        "(status <> 'GIU' and closed_by is not null and closed_at is not null)", name='ck_stock_reservations_closed'),
        Index('ix_stock_reservations_active_stock','warehouse_code','product_code', postgresql_where=text("status = 'GIU'")),
        Index('uq_stock_reservations_active_line','order_id','order_line_id','warehouse_code',unique=True, postgresql_where=text("status = 'GIU'")),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('sales_orders.id'))
    order_line_id: Mapped[int] = mapped_column(Integer)
    warehouse_code: Mapped[str] = mapped_column(ForeignKey('warehouses.code'))
    product_code: Mapped[str] = mapped_column(ForeignKey('products.code'))
    qty: Mapped[Decimal] = mapped_column(Numeric(14,3))
    status: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(Text, default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SalesDelivery(Base):
    __tablename__ = 'sales_deliveries'
    __table_args__ = (
        CheckConstraint("status in ('DA_GIAO','DA_HUY')", name='ck_sales_deliveries_status'),
        CheckConstraint('subtotal >= 0 and vat_total >= 0 and total = subtotal + vat_total', name='ck_sales_deliveries_money'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('sales_orders.id'), index=True)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    receiver_name: Mapped[str] = mapped_column(String(200), default='')
    carrier_name: Mapped[str] = mapped_column(String(200), default='', server_default='')
    tracking_code: Mapped[str] = mapped_column(String(100), default='', server_default='')
    note: Mapped[str] = mapped_column(Text, default='')
    subtotal: Mapped[int] = mapped_column(BigInteger)
    vat_total: Mapped[int] = mapped_column(BigInteger)
    total: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default='DA_GIAO')
    void_reason: Mapped[str] = mapped_column(Text, default='')
    voided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    lines: Mapped[list['SalesDeliveryLine']] = relationship(order_by='SalesDeliveryLine.order_line_no', cascade='all, delete-orphan')
    __mapper_args__ = {'version_id_col': version}


class SalesDeliveryLine(Base):
    __tablename__ = 'sales_delivery_lines'
    __table_args__ = (
        ForeignKeyConstraint(['order_id','order_line_no'], ['sales_order_lines.order_id','sales_order_lines.line_no']),
        CheckConstraint('qty > 0', name='ck_sales_delivery_lines_qty'),
        CheckConstraint('amount >= 0 and vat_amount >= 0', name='ck_sales_delivery_lines_money'),
    )
    delivery_id: Mapped[int] = mapped_column(ForeignKey('sales_deliveries.id'), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('sales_orders.id'))
    order_line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(14,3))
    amount: Mapped[int] = mapped_column(BigInteger)
    vat_amount: Mapped[int] = mapped_column(BigInteger)
    warehouse_code: Mapped[str | None] = mapped_column(ForeignKey('warehouses.code'))
    stock_doc_id: Mapped[int | None] = mapped_column(ForeignKey('stock_docs.id'))


class SalesDeliveryAttempt(Base):
    __tablename__ = 'sales_delivery_attempts'
    __table_args__ = (
        CheckConstraint("result = 'KHONG_GIAO_DUOC'", name='ck_sales_delivery_attempts_result'),
        CheckConstraint("reason in ('KHACH_VANG','KHACH_TU_CHOI','SAI_DIA_CHI','HEN_LAI','KHAC','DE_NGHI_HUY')", name='ck_sales_delivery_attempts_reason'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('sales_orders.id'), index=True)
    assignee_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    result: Mapped[str] = mapped_column(String(20), default='KHONG_GIAO_DUOC')
    reason: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SalesReturn(Base):
    __tablename__ = 'sales_returns'
    __table_args__ = (
        CheckConstraint("status in ('DA_NHAN','DA_HUY')", name='ck_sales_returns_status'),
        CheckConstraint('subtotal >= 0 and vat_total >= 0 and total = subtotal + vat_total', name='ck_sales_returns_money'),
        CheckConstraint('length(trim(reason)) > 0', name='ck_sales_returns_reason'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('sales_orders.id'), index=True)
    returned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    reason: Mapped[str] = mapped_column(Text)
    subtotal: Mapped[int] = mapped_column(BigInteger, default=0)
    vat_total: Mapped[int] = mapped_column(BigInteger, default=0)
    total: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16), default='DA_NHAN')
    void_reason: Mapped[str] = mapped_column(Text, default='')
    voided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    lines: Mapped[list['SalesReturnLine']] = relationship(cascade='all, delete-orphan', order_by='SalesReturnLine.line_no')
    __mapper_args__ = {'version_id_col': version}


class SalesReturnLine(Base):
    __tablename__ = 'sales_return_lines'
    __table_args__ = (
        ForeignKeyConstraint(['order_id','order_line_no'], ['sales_order_lines.order_id','sales_order_lines.line_no']),
        ForeignKeyConstraint(['delivery_id','order_line_no'], ['sales_delivery_lines.delivery_id','sales_delivery_lines.order_line_no']),
        CheckConstraint('qty > 0', name='ck_sales_return_lines_qty'),
        CheckConstraint("condition in ('TOT','LOI')", name='ck_sales_return_lines_condition'),
        CheckConstraint('amount >= 0 and vat_amount >= 0 and unit_cost >= 0 and cost_amount >= 0', name='ck_sales_return_lines_money'),
    )
    return_id: Mapped[int] = mapped_column(ForeignKey('sales_returns.id'), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('sales_orders.id'))
    order_line_no: Mapped[int] = mapped_column(Integer)
    delivery_id: Mapped[int] = mapped_column(ForeignKey('sales_deliveries.id'))
    qty: Mapped[Decimal] = mapped_column(Numeric(14,3))
    condition: Mapped[str] = mapped_column(String(3))
    warehouse_code: Mapped[str | None] = mapped_column(ForeignKey('warehouses.code'))
    amount: Mapped[int] = mapped_column(BigInteger)
    vat_amount: Mapped[int] = mapped_column(BigInteger)
    unit_cost: Mapped[int] = mapped_column(BigInteger)
    cost_amount: Mapped[int] = mapped_column(BigInteger)
    stock_doc_id: Mapped[int | None] = mapped_column(ForeignKey('stock_docs.id'))


class SalesRefund(Base):
    __tablename__ = 'sales_refunds'
    __table_args__ = (
        CheckConstraint("status in ('CHO_DUYET','DA_CHI','TU_CHOI')", name='ck_sales_refunds_status'),
        CheckConstraint("method in ('TIEN_MAT','CHUYEN_KHOAN')", name='ck_sales_refunds_method'),
        CheckConstraint('amount > 0', name='ck_sales_refunds_amount'),
        CheckConstraint('length(trim(reason)) > 0', name='ck_sales_refunds_reason'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey('sales_orders.id'), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    method: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default='CHO_DUYET')
    requested_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cash_doc_id: Mapped[int | None] = mapped_column(ForeignKey('cash_docs.id'))
    version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {'version_id_col': version}


class TechnicianReturn(Base):
    """Yêu cầu trả hàng tốt từ kho thợ, chỉ thủ kho xác nhận ghi CK."""
    __tablename__ = 'technician_returns'
    __table_args__ = (
        CheckConstraint("status in ('CHO_NHAN','DA_NHAN','DA_HUY')", name='ck_technician_returns_status'),
        CheckConstraint("(status = 'DA_HUY') = (btrim(void_reason) <> '')", name='ck_technician_returns_reason'),
        CheckConstraint("warehouse_code <> to_warehouse_code", name='ck_technician_returns_warehouse'),
        Index('ix_technician_returns_queue', 'status', 'created_at'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey('service_tickets.id'), index=True)
    technician_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    warehouse_code: Mapped[str] = mapped_column(ForeignKey('warehouses.code'))
    to_warehouse_code: Mapped[str] = mapped_column(ForeignKey('warehouses.code'))
    status: Mapped[str] = mapped_column(String(8), default='CHO_NHAN')
    stock_doc_id: Mapped[int | None] = mapped_column(ForeignKey('stock_docs.id'), unique=True)
    defect_stock_doc_id: Mapped[int | None] = mapped_column(ForeignKey('stock_docs.id'), unique=True)
    note: Mapped[str] = mapped_column(Text, default='')
    void_reason: Mapped[str] = mapped_column(Text, default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    received_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    lines: Mapped[list['TechnicianReturnLine']] = relationship(order_by='TechnicianReturnLine.line_no')
    __mapper_args__ = {'version_id_col': version}


class TechnicianReturnLine(Base):
    __tablename__ = 'technician_return_lines'
    __table_args__ = (
        CheckConstraint('qty > 0', name='ck_technician_return_lines_qty'),
        CheckConstraint('received_qty is null or (received_qty >= 0 and received_qty <= qty)', name='ck_technician_return_lines_received'),
        UniqueConstraint('return_id', 'product_code', 'condition', name='uq_technician_return_line_condition'),
        CheckConstraint("condition in ('TOT','HU_HONG')", name='ck_technician_return_condition'),
        CheckConstraint("(condition = 'TOT' and damage_reason = '') or (condition = 'HU_HONG' and length(btrim(damage_reason)) >= 3)", name='ck_technician_return_damage_reason'),
        CheckConstraint("received_condition in ('TOT','HU_HONG')", name='ck_technician_return_received_condition'),
        CheckConstraint("(received_qty is null) = (received_condition is null)", name='ck_technician_return_received_state'),
        CheckConstraint("(received_condition is distinct from 'HU_HONG' and received_damage_reason = '') or (received_condition = 'HU_HONG' and length(btrim(received_damage_reason)) >= 3)", name='ck_technician_return_received_reason'),
    )
    return_id: Mapped[int] = mapped_column(ForeignKey('technician_returns.id'), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_code: Mapped[str] = mapped_column(ForeignKey('products.code'))
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    received_qty: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    condition: Mapped[str] = mapped_column(String(8), default='TOT', server_default='TOT')
    damage_reason: Mapped[str] = mapped_column(Text, default='', server_default='')
    received_condition: Mapped[str | None] = mapped_column(String(8))
    received_damage_reason: Mapped[str] = mapped_column(Text, default='', server_default='')


class PendingItem(Base):
    """Hồ sơ đi kèm sổ kho LOI; không tự thay đổi tồn."""
    __tablename__ = 'pending_items'
    __table_args__ = (
        CheckConstraint('qty_in > 0',name='ck_pending_items_qty_in'),
        CheckConstraint('qty_left >= 0 and qty_left <= qty_in',name='ck_pending_items_qty_left'),
        CheckConstraint('unit_cost >= 0',name='ck_pending_items_cost'),
        CheckConstraint("group_code in ('HANG_LOI','CHO_BH_NCC','THANH_LY','THU_HOI','CHUA_PHAN_LOAI')",name='ck_pending_items_group'),
        CheckConstraint("source in ('THU_HOI','THO_BAO_HU','KHACH_TRA','CHUYEN_TAY','KIEM_KE','TON_CU')",name='ck_pending_items_source'),
        CheckConstraint("(status='DANG_CHO' and qty_left>0) or (status in ('XONG','DA_HUY') and qty_left=0)",name='ck_pending_items_status'),
        Index('ix_pending_items_stock','warehouse_code','product_code','status'),
        Index('ix_pending_items_group','group_code','status'),
    )
    id: Mapped[int] = mapped_column(BigInteger,primary_key=True)
    number: Mapped[str] = mapped_column(Text,unique=True)
    warehouse_code: Mapped[str] = mapped_column(ForeignKey('warehouses.code'))
    product_code: Mapped[str] = mapped_column(ForeignKey('products.code'))
    qty_in: Mapped[Decimal] = mapped_column(Numeric(14,3))
    qty_left: Mapped[Decimal] = mapped_column(Numeric(14,3))
    unit_cost: Mapped[int] = mapped_column(BigInteger)
    group_code: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str] = mapped_column(Text,default='')
    stock_doc_id: Mapped[int | None] = mapped_column(ForeignKey('stock_docs.id'))
    supplier_code: Mapped[str | None] = mapped_column(ForeignKey('suppliers.code'))
    reason: Mapped[str] = mapped_column(Text,default='')
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(Text,default='DANG_CHO')
    version: Mapped[int] = mapped_column(Integer,default=1)
    __mapper_args__ = {'version_id_col': version}


class PendingDisposition(Base):
    __tablename__='pending_dispositions'
    __table_args__=(
        CheckConstraint("kind in ('SUA_XONG','GUI_NCC','NCC_TRA_VE','NCC_KHONG_NHAN','HUY','TRA_NCC','THANH_LY')",name='ck_pending_dispositions_kind'),
        CheckConstraint("status in ('CHO_DUYET','DA_GHI','TU_CHOI','DA_HUY')",name='ck_pending_dispositions_status'),
        CheckConstraint('qty>0',name='ck_pending_dispositions_qty'),
        CheckConstraint('cost_value>=0',name='ck_pending_dispositions_cost'),
        CheckConstraint('amount>=0 and vat_amount>=0 and total=amount+vat_amount and vat_rate in (0,5,8,10)',name='ck_pending_dispositions_money'),
        CheckConstraint("(kind='THANH_LY') = (delivery_id is not null and order_line_no is not null)",name='ck_pending_dispositions_delivery'),
        Index('ix_pending_dispositions_item_status','item_id','status'),
    )
    id: Mapped[int]=mapped_column(BigInteger,primary_key=True)
    number: Mapped[str]=mapped_column(Text,unique=True)
    item_id: Mapped[int]=mapped_column(ForeignKey('pending_items.id'))
    kind: Mapped[str]=mapped_column(Text)
    qty: Mapped[Decimal]=mapped_column(Numeric(14,3))
    to_warehouse_code: Mapped[str | None]=mapped_column(ForeignKey('warehouses.code'))
    supplier_code: Mapped[str | None]=mapped_column(ForeignKey('suppliers.code'))
    stock_doc_id: Mapped[int | None]=mapped_column(ForeignKey('stock_docs.id'),unique=True)
    cost_value: Mapped[int]=mapped_column(BigInteger,default=0)
    status: Mapped[str]=mapped_column(Text,default='CHO_DUYET')
    reason: Mapped[str]=mapped_column(Text,default='')
    requested_by: Mapped[int]=mapped_column(ForeignKey('users.id'))
    requested_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
    decided_by: Mapped[int | None]=mapped_column(ForeignKey('users.id'))
    decided_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str]=mapped_column(Text,default='')
    void_reason: Mapped[str]=mapped_column(Text,default='')
    version: Mapped[int]=mapped_column(Integer,default=1)
    voided_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    amount: Mapped[int]=mapped_column(BigInteger,default=0,server_default='0')
    vat_rate: Mapped[int]=mapped_column(Integer,default=0,server_default='0')
    vat_amount: Mapped[int]=mapped_column(BigInteger,default=0,server_default='0')
    total: Mapped[int]=mapped_column(BigInteger,default=0,server_default='0')
    delivery_id: Mapped[int | None]=mapped_column(ForeignKey('sales_deliveries.id'),index=True)
    order_line_no: Mapped[int | None]=mapped_column(Integer)
    __mapper_args__={'version_id_col':version}


class PendingItemMove(Base):
    __tablename__='pending_item_moves'
    __table_args__=(CheckConstraint('qty>0',name='ck_pending_item_moves_qty'),UniqueConstraint('disposition_id','item_id',name='uq_pending_item_moves_disposition_item'),)
    id: Mapped[int]=mapped_column(BigInteger,primary_key=True)
    item_id: Mapped[int]=mapped_column(ForeignKey('pending_items.id'))
    disposition_id: Mapped[int]=mapped_column(ForeignKey('pending_dispositions.id'))
    stock_doc_id: Mapped[int]=mapped_column(ForeignKey('stock_docs.id'))
    qty: Mapped[Decimal]=mapped_column(Numeric(14,3))


class Employee(Base):
    __tablename__ = 'employees'
    __table_args__ = (
        CheckConstraint("position in ('Kỹ thuật','Kinh doanh','Kho','Kế toán','Điều phối','Lễ tân','Quản lý','Khác')", name='ck_employees_position'),
        CheckConstraint('end_date is null or end_date >= start_date', name='ck_employees_dates'),
        CheckConstraint("id_number = '' or id_number ~ '^([0-9]{9}|[0-9]{12})$'", name='ck_employees_id_number'),
        Index('uq_employees_id_number', 'id_number', unique=True, postgresql_where=text("id_number <> ''")),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), unique=True, nullable=False)
    position: Mapped[str] = mapped_column(Text)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    birth_date: Mapped[date | None] = mapped_column(Date)
    id_number: Mapped[str] = mapped_column(Text, default='', server_default='')
    id_issued_on: Mapped[date | None] = mapped_column(Date)
    address: Mapped[str] = mapped_column(Text, default='', server_default='')
    emergency_name: Mapped[str] = mapped_column(Text, default='', server_default='')
    emergency_phone: Mapped[str] = mapped_column(Text, default='', server_default='')
    note: Mapped[str] = mapped_column(Text, default='', server_default='')
    version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    __mapper_args__ = {'version_id_col': version}


class EmployeePay(Base):
    __tablename__ = 'employee_pay'
    __table_args__ = (CheckConstraint("bank_account = '' or bank_account ~ '^[0-9]{6,20}$'", name='ck_employee_pay_bank_account'),)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), primary_key=True)
    bank_name: Mapped[str] = mapped_column(Text, default='', server_default='')
    bank_account: Mapped[str] = mapped_column(Text, default='', server_default='')
    bank_holder: Mapped[str] = mapped_column(Text, default='', server_default='')
    version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {'version_id_col': version}


class EmployeeSalaryVersion(Base):
    __tablename__ = 'employee_salary_versions'
    __table_args__ = (
        CheckConstraint('base_salary between 0 and 1000000000000 and meal_allowance between 0 and 1000000000000 and fuel_allowance between 0 and 1000000000000 and phone_allowance between 0 and 1000000000000 and other_allowance between 0 and 1000000000000', name='ck_employee_salary_money'),
        CheckConstraint('standard_days between 1 and 31', name='ck_employee_salary_days'),
        CheckConstraint("length(trim(reason)) > 0", name='ck_employee_salary_reason'),
        Index('uq_employee_salary_effective', 'employee_id', 'effective_from', unique=True, postgresql_where=text('replaced_at is null')),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'))
    effective_from: Mapped[date] = mapped_column(Date)
    base_salary: Mapped[int] = mapped_column(BigInteger)
    meal_allowance: Mapped[int] = mapped_column(BigInteger, default=0, server_default='0')
    fuel_allowance: Mapped[int] = mapped_column(BigInteger, default=0, server_default='0')
    phone_allowance: Mapped[int] = mapped_column(BigInteger, default=0, server_default='0')
    other_allowance: Mapped[int] = mapped_column(BigInteger, default=0, server_default='0')
    standard_days: Mapped[Decimal] = mapped_column(Numeric(4,1), default=26, server_default='26')
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    replaces_id: Mapped[int | None] = mapped_column(ForeignKey('employee_salary_versions.id'))
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AttendanceRequest(Base):
    __tablename__ = 'attendance_requests'
    __table_args__ = (
        CheckConstraint("kind in ('BO_SUNG_VAO','BO_SUNG_RA','NGHI_PHEP','NGHI_KHONG_LUONG')", name='ck_attendance_request_kind'),
        CheckConstraint("status in ('CHO_DUYET','DA_DUYET','TU_CHOI','DA_HUY')", name='ck_attendance_request_status'),
        CheckConstraint("part in ('CA_NGAY','SANG','CHIEU')", name='ck_attendance_request_part'),
        CheckConstraint("(kind in ('BO_SUNG_VAO','BO_SUNG_RA') and punch_at is not null and from_date is null and to_date is null) or (kind in ('NGHI_PHEP','NGHI_KHONG_LUONG') and punch_at is null and from_date is not null and to_date is not null and from_date <= to_date and (part='CA_NGAY' or from_date=to_date))", name='ck_attendance_request_dates'),
        CheckConstraint('length(trim(reason)) > 0', name='ck_attendance_request_reason'),
        Index('ix_attendance_request_queue', 'status', 'created_at'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    kind: Mapped[str] = mapped_column(Text)
    punch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    from_date: Mapped[date | None] = mapped_column(Date)
    to_date: Mapped[date | None] = mapped_column(Date)
    part: Mapped[str] = mapped_column(Text, default='CA_NGAY', server_default='CA_NGAY')
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default='CHO_DUYET', server_default='CHO_DUYET')
    decided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str] = mapped_column(Text, default='', server_default='')
    version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __mapper_args__ = {'version_id_col': version}


class AttendancePunch(Base):
    __tablename__ = 'attendance_punches'
    __table_args__ = (
        CheckConstraint("kind in ('IN','OUT')", name='ck_attendance_punch_kind'),
        CheckConstraint("source in ('APP','BO_SUNG') and ((source='APP' and request_id is null) or (source='BO_SUNG' and request_id is not null))", name='ck_attendance_punch_source'),
        CheckConstraint('(lat is null and lng is null) or (lat is not null and lng is not null and lat between -90 and 90 and lng between -180 and 180)', name='ck_attendance_punch_location'),
        CheckConstraint('accuracy_m is null or accuracy_m >= 0', name='ck_attendance_punch_accuracy'),
        Index('ix_attendance_punch_user_at','user_id','at'),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    kind: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lat: Mapped[Decimal | None] = mapped_column(Numeric(10,7))
    lng: Mapped[Decimal | None] = mapped_column(Numeric(10,7))
    accuracy_m: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text)
    request_id: Mapped[int | None] = mapped_column(ForeignKey('attendance_requests.id'), unique=True)
    off_hours: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AttendanceHoliday(Base):
    __tablename__ = 'attendance_holidays'
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class AttendanceMonthLock(Base):
    __tablename__ = 'attendance_month_locks'
    __table_args__ = (CheckConstraint('extract(day from month) = 1', name='ck_attendance_lock_month'),)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    locked_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    unlocked_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unlock_reason: Mapped[str] = mapped_column(Text, default='', server_default='')


class KpiTarget(Base):
    __tablename__ = 'kpi_targets'
    __table_args__ = (
        CheckConstraint('extract(day from month) = 1', name='ck_kpi_target_month'),
        CheckConstraint('target >= 0', name='ck_kpi_target_value'),
        Index('uq_kpi_target_person', 'month', 'user_id', 'metric_key', unique=True, postgresql_where=text('user_id is not null')),
        Index('uq_kpi_target_common', 'month', 'metric_key', unique=True, postgresql_where=text('user_id is null')),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    month: Mapped[date] = mapped_column(Date)
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    metric_key: Mapped[str] = mapped_column(String(48))
    target: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    set_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    __mapper_args__ = {'version_id_col': version}


class CommissionRule(Base):
    __tablename__ = 'commission_rules'
    __table_args__ = (
        CheckConstraint("target in ('KINH_DOANH','THO')",name='ck_commission_rule_target'),
        CheckConstraint('pct between 0 and 50 and fixed_amount >= 0',name='ck_commission_rule_money'),
        CheckConstraint("target <> 'KINH_DOANH' or (fixed_amount=0 and service_kind is null)",name='ck_commission_rule_sales'),
        CheckConstraint("length(trim(reason))>0",name='ck_commission_rule_reason'),
        CheckConstraint(_in('service_kind',SERVICE_KINDS),name='ck_commission_rule_kind'),
        Index('uq_commission_rule_effective','target',text('coalesce(user_id,0)'),text("coalesce(service_kind,'')"),'effective_from',unique=True,postgresql_where=text('replaced_at is null')),
    )
    id: Mapped[int] = mapped_column(BigInteger,primary_key=True)
    target: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    service_kind: Mapped[str | None] = mapped_column(String(24))
    pct: Mapped[Decimal] = mapped_column(Numeric(5,2))
    fixed_amount: Mapped[int] = mapped_column(BigInteger)
    effective_from: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now())
    replaces_id: Mapped[int | None] = mapped_column(ForeignKey('commission_rules.id'),unique=True)
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommissionMonth(Base):
    __tablename__ = 'commission_months'
    __table_args__ = (CheckConstraint('extract(day from month)=1',name='ck_commission_month_first'),)
    month: Mapped[date] = mapped_column(Date,primary_key=True)
    locked_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total: Mapped[int] = mapped_column(BigInteger)


class CommissionLine(Base):
    __tablename__ = 'commission_lines'
    __table_args__ = (
        CheckConstraint('extract(day from month)=1',name='ck_commission_line_month'),
        CheckConstraint("(source='DON_BAN' and order_id is not null and ticket_id is null) or (source='PHIEU_DV' and ticket_id is not null and order_id is null) or (source='DIEU_CHINH' and order_id is null and ticket_id is null and length(trim(note))>0)",name='ck_commission_line_source'),
        CheckConstraint('pct between 0 and 50 and fixed_amount>=0 and paid_ratio between 0 and 1',name='ck_commission_line_values'),
        CheckConstraint('amount=entitled-previous and amount<>0',name='ck_commission_line_amount'),
        Index('uq_commission_line_order','month','order_id',unique=True,postgresql_where=text("source='DON_BAN'")),
        Index('uq_commission_line_ticket','month','ticket_id',unique=True,postgresql_where=text("source='PHIEU_DV'")),
        Index('ix_commission_line_user_month','user_id','month'),
    )
    id: Mapped[int] = mapped_column(BigInteger,primary_key=True)
    month: Mapped[date] = mapped_column(Date)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    source: Mapped[str] = mapped_column(String(16))
    order_id: Mapped[int | None] = mapped_column(ForeignKey('sales_orders.id'))
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey('service_tickets.id'))
    rule_id: Mapped[int | None] = mapped_column(ForeignKey('commission_rules.id'))
    base: Mapped[int] = mapped_column(BigInteger)
    pct: Mapped[Decimal] = mapped_column(Numeric(5,2))
    fixed_amount: Mapped[int] = mapped_column(BigInteger)
    paid_ratio: Mapped[Decimal] = mapped_column(Numeric(7,6))
    entitled: Mapped[int] = mapped_column(BigInteger)
    previous: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[int] = mapped_column(BigInteger)
    note: Mapped[str] = mapped_column(Text,default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now())


class PayrollInput(Base):
    __tablename__='payroll_inputs'
    __table_args__=(CheckConstraint('extract(day from month)=1',name='ck_payroll_input_month'),
        CheckConstraint('bonus>=0 and social_insurance>=0 and income_tax>=0 and other_deduction>=0',name='ck_payroll_input_values'))
    month: Mapped[date]=mapped_column(Date,primary_key=True)
    employee_id: Mapped[int]=mapped_column(ForeignKey('employees.id'),primary_key=True)
    bonus: Mapped[int]=mapped_column(BigInteger,default=0,server_default='0')
    social_insurance: Mapped[int]=mapped_column(BigInteger,default=0,server_default='0')
    income_tax: Mapped[int]=mapped_column(BigInteger,default=0,server_default='0')
    other_deduction: Mapped[int]=mapped_column(BigInteger,default=0,server_default='0')
    note: Mapped[str]=mapped_column(Text,default='',server_default='')
    version: Mapped[int]=mapped_column(Integer,default=1,server_default='1')
    updated_by: Mapped[int]=mapped_column(ForeignKey('users.id'))
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    __mapper_args__={'version_id_col':version}


class PayrollMonth(Base):
    __tablename__='payroll_months'
    __table_args__=(CheckConstraint('extract(day from month)=1',name='ck_payroll_month_first'),)
    month: Mapped[date]=mapped_column(Date,primary_key=True)
    approved_by: Mapped[int]=mapped_column(ForeignKey('users.id'))
    approved_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    total_net: Mapped[int]=mapped_column(BigInteger)


class PayrollLine(Base):
    __tablename__='payroll_lines'
    __table_args__=(UniqueConstraint('month','employee_id',name='uq_payroll_line_employee'),
        CheckConstraint('extract(day from month)=1',name='ck_payroll_line_month'),
        CheckConstraint('net>=0 and cong>=0',name='ck_payroll_line_values'),
        CheckConstraint('gross=base_pay+allowances+commission+bonus',name='ck_payroll_line_gross'),
        CheckConstraint('net=gross-advance-social_insurance-income_tax-other_deduction',name='ck_payroll_line_net'),
        CheckConstraint("(paid_at is null and paid_by is null and cash_doc_id is null) or (paid_at is not null and paid_by is not null and ((net=0 and cash_doc_id is null) or (net>0 and cash_doc_id is not null)))",name='ck_payroll_line_paid'))
    id: Mapped[int]=mapped_column(BigInteger,primary_key=True)
    month: Mapped[date]=mapped_column(Date)
    employee_id: Mapped[int]=mapped_column(ForeignKey('employees.id'))
    user_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    salary_version_id: Mapped[int]=mapped_column(ForeignKey('employee_salary_versions.id'))
    base_salary: Mapped[int]=mapped_column(BigInteger)
    standard_days: Mapped[Decimal]=mapped_column(Numeric(4,1))
    cong: Mapped[Decimal]=mapped_column(Numeric(5,1))
    base_pay: Mapped[int]=mapped_column(BigInteger)
    allowances: Mapped[int]=mapped_column(BigInteger)
    commission: Mapped[int]=mapped_column(BigInteger)
    bonus: Mapped[int]=mapped_column(BigInteger)
    advance: Mapped[int]=mapped_column(BigInteger)
    social_insurance: Mapped[int]=mapped_column(BigInteger)
    income_tax: Mapped[int]=mapped_column(BigInteger)
    other_deduction: Mapped[int]=mapped_column(BigInteger)
    gross: Mapped[int]=mapped_column(BigInteger)
    net: Mapped[int]=mapped_column(BigInteger)
    note: Mapped[str]=mapped_column(Text,default='')
    paid_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    paid_by: Mapped[int | None]=mapped_column(ForeignKey('users.id'))
    cash_doc_id: Mapped[int | None]=mapped_column(ForeignKey('cash_docs.id'),unique=True)


class SalaryAdvance(Base):
    __tablename__='salary_advances'
    __table_args__=(CheckConstraint('amount>0',name='ck_salary_advance_amount'),
        CheckConstraint("status in ('CHO_DUYET','DA_DUYET','TU_CHOI','DA_HUY','DA_CHI')",name='ck_salary_advance_status'),
        CheckConstraint("length(trim(reason))>0",name='ck_salary_advance_reason'),
        CheckConstraint("(status='DA_CHI' and cash_doc_id is not null and paid_at is not null and paid_by is not null) or (status<>'DA_CHI' and cash_doc_id is null and paid_at is null and paid_by is null and payroll_line_id is null)",name='ck_salary_advance_paid'),
        Index('ix_salary_advance_user_status','user_id','status'))
    id: Mapped[int]=mapped_column(BigInteger,primary_key=True)
    number: Mapped[str]=mapped_column(String(32),unique=True)
    user_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    employee_id: Mapped[int]=mapped_column(ForeignKey('employees.id'))
    amount: Mapped[int]=mapped_column(BigInteger)
    reason: Mapped[str]=mapped_column(Text)
    status: Mapped[str]=mapped_column(String(16),default='CHO_DUYET')
    requested_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    decided_by: Mapped[int | None]=mapped_column(ForeignKey('users.id'))
    decided_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str]=mapped_column(Text,default='')
    paid_by: Mapped[int | None]=mapped_column(ForeignKey('users.id'))
    paid_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    cash_doc_id: Mapped[int | None]=mapped_column(ForeignKey('cash_docs.id'),unique=True)
    payroll_line_id: Mapped[int | None]=mapped_column(ForeignKey('payroll_lines.id'))
    version: Mapped[int]=mapped_column(Integer,default=1,server_default='1')
    __mapper_args__={'version_id_col':version}


class PushKey(Base):
    """Khóa VAPID của máy chủ cho Web Push; tự tạo một lần, sao lưu cùng database."""
    __tablename__ = 'push_keys'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_key: Mapped[str] = mapped_column(Text)
    private_pem: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PushSubscription(Base):
    """Một trình duyệt/điện thoại đã bật thông báo; seen là các việc đã báo (JSON), null = chưa ghi nhớ lần nào."""
    __tablename__ = 'push_subscriptions'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(Text)
    auth: Mapped[str] = mapped_column(Text)
    seen: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationRead(Base):
    """Người dùng đã xem thông báo nào (mã việc dạng nhóm:id); chỉ để đánh dấu Mới/Đã xem, không đụng nghiệp vụ."""
    __tablename__ = 'notification_reads'
    __table_args__ = (UniqueConstraint('user_id', 'item_id', name='uq_notification_reads_user_item'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    item_id: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
