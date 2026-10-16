"""Lược đồ dữ liệu chung. Mọi thay đổi cấu trúc phải đi kèm một file trong migrations/versions."""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
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
