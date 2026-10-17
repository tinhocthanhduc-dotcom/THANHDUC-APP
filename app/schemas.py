from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
