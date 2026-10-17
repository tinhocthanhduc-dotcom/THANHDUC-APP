from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContactIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    phones: list[str] = Field(default_factory=list, max_length=10)
    note: str = Field(default="", max_length=1000)


class Confirmation(BaseModel):
    """Xác nhận khi trùng số điện thoại với khách khác: vẫn tạo riêng, kèm lý do."""

    mode: Literal["CREATE_SEPARATE"]
    reason: str = Field(min_length=3, max_length=500)


class CustomerIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["CA_NHAN", "CONG_TY"] = "CA_NHAN"
    name: str = Field(min_length=1, max_length=300)
    tax_code: str = ""
    phones: list[str] = Field(default_factory=list, max_length=10)
    email: str = Field(default="", max_length=200)
    address: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=2000)
    contacts: list[ContactIn] = Field(default_factory=list, max_length=50)
    allow_debt: bool = False
    credit_limit: int = Field(default=0, ge=0)
    is_test: bool = False


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    customer: CustomerIn
    confirmation: Confirmation | None = None


class CustomerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: int
    kind: Literal["CA_NHAN", "CONG_TY"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=300)
    tax_code: str | None = None
    phones: list[str] | None = Field(default=None, max_length=10)
    email: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=2000)
    contacts: list[ContactIn] | None = Field(default=None, max_length=50)
    allow_debt: bool | None = None
    credit_limit: int | None = Field(default=None, ge=0)
    status: Literal["DANG_DUNG", "NGUNG"] | None = None
    confirmation: Confirmation | None = None
    reason: str = Field(default="", max_length=500)


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    phones: list[str]
    note: str


class CustomerOut(BaseModel):
    code: str
    kind: str
    name: str
    tax_code: str
    phones: list[str]
    email: str
    address: str
    note: str
    contacts: list[ContactOut]
    allow_debt: bool
    credit_limit: int
    status: str
    old_codes: list[str]
    is_test: bool
    version: int


class Warning(BaseModel):
    code: str
    customer: str


class CustomerResult(BaseModel):
    customer: CustomerOut
    warnings: list[Warning] = Field(default_factory=list)


class ProductIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(pattern=r"^TD-\d{4,}$")
    name: str = Field(min_length=1, max_length=300)
    group_name: str = Field(default="", max_length=100)
    unit: str = Field(default="cái", min_length=1, max_length=32)
    sale_price: int = Field(default=0, ge=0)
    is_test: bool = False


class ProductPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: int
    name: str | None = Field(default=None, min_length=1, max_length=300)
    group_name: str | None = Field(default=None, max_length=100)
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    sale_price: int | None = Field(default=None, ge=0)
    active: bool | None = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    group_name: str
    unit: str
    sale_price: int
    active: bool
    is_test: bool
    version: int


class UserIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(pattern=r"^[a-z0-9._-]{3,64}$")
    full_name: str = Field(min_length=1, max_length=200)
    password: str
    roles: list[str] = Field(min_length=1)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    roles: list[str]
    active: bool


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class WarehouseIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=16, pattern=r"^K[0-9]+$")
    name: str = Field(min_length=1, max_length=300)
    address: str = Field(default="", max_length=2000)
    active: bool = True


class WarehousePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: int = Field(ge=1)
    name: str = Field(default="", min_length=1, max_length=300)
    address: str = Field(default="", max_length=2000)
    active: bool = True


class WarehouseOut(WarehouseIn):
    model_config = ConfigDict(from_attributes=True)
    version: int


class SupplierIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=300)
    tax_code: str = ""
    phones: list[str] = Field(default_factory=list, max_length=10)
    address: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=2000)
    active: bool = True


class SupplierPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: int = Field(ge=1)
    name: str = Field(default="", min_length=1, max_length=300)
    tax_code: str = ""
    phones: list[str] = Field(default_factory=list, max_length=10)
    address: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=2000)
    active: bool = True


class SupplierOut(SupplierIn):
    model_config = ConfigDict(from_attributes=True)
    code: str
    version: int


StockKind = Literal["PN", "PX", "CK", "KK"]
StockStatus = Literal["NHAP", "DA_GHI", "DA_HUY"]


class StockDocLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_code: str = Field(min_length=1, max_length=16)
    qty: Decimal = Field(ge=0, max_digits=14, decimal_places=3)
    unit_cost: int | None = Field(default=None, strict=True, ge=0, le=9223372036854775807)
    serial: str | None = Field(default=None, max_length=200)
    note: str = Field(default="", max_length=2000)


class StockDocIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: StockKind
    warehouse_code: str = Field(min_length=1, max_length=16)
    to_warehouse_code: str | None = Field(default=None, max_length=16)
    doc_date: date
    supplier_code: str | None = Field(default=None, max_length=16)
    customer_code: str | None = Field(default=None, max_length=16)
    reason: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=2000)
    lines: list[StockDocLineIn] = Field(min_length=1, max_length=1000)


class StockDocPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: int = Field(ge=1)
    kind: StockKind | None = None
    warehouse_code: str | None = Field(default=None, min_length=1, max_length=16)
    to_warehouse_code: str | None = Field(default=None, max_length=16)
    doc_date: date | None = None
    supplier_code: str | None = Field(default=None, max_length=16)
    customer_code: str | None = Field(default=None, max_length=16)
    reason: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=2000)
    lines: list[StockDocLineIn] | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="before")
    @classmethod
    def reject_null_fields(cls, data):
        if isinstance(data, dict):
            for key in ("kind", "warehouse_code", "doc_date", "reason", "note", "lines"):
                if key in data and data[key] is None:
                    raise ValueError("Trường này không được để null: " + key)
        return data


class StockDocCancel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=2000)
    version: int | None = Field(default=None, ge=1)


class StockDocPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int | None = Field(default=None, ge=1)


class StockDocLineOut(StockDocLineIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doc_id: int
    line_no: int


class StockDocOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str
    kind: StockKind
    warehouse_code: str
    to_warehouse_code: str | None
    doc_date: date
    supplier_code: str | None
    customer_code: str | None
    reason: str
    note: str
    status: StockStatus
    created_by: int
    created_at: datetime
    posted_by: int | None
    posted_at: datetime | None
    void_reason: str
    version: int
    lines: list[StockDocLineOut]
