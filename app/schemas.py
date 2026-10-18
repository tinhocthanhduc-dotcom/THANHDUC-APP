from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


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


class StockMinLevelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_qty: Decimal = Field(ge=0, max_digits=14, decimal_places=3)
    # 0 là phiên bản của mức chưa được đặt; bản ghi đầu tiên sẽ có version=1.
    version: int = Field(ge=0)


ServiceKind = Literal["SUA_TAI_CHO", "MANG_VE", "BAO_HANH", "MUC_DINH_KY"]
ServicePriority = Literal["KHAN", "GAP", "THUONG"]
ServiceStatus = Literal["MOI", "DA_GIAO", "DA_NHAN", "DA_HEN", "DANG_LAM", "CHO_LINH_KIEN", "HOAN_THANH", "DA_DONG", "KHONG_LAM_DUOC", "DA_HUY"]


class ServiceTicketIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    customer_code: str = Field(min_length=1, max_length=16)
    site_id: int | None = Field(default=None, ge=1)
    serial: str | None = Field(default=None, max_length=64)
    kind: ServiceKind
    priority: ServicePriority = "THUONG"
    symptom: str = Field(min_length=3, max_length=4000)


class ServiceAssign(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    assignee_id: int = Field(ge=1)
    version: int | None = Field(default=None, ge=1)
    note: str = Field(default="", max_length=2000)


class ServiceTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    to_status: ServiceStatus
    version: int | None = Field(default=None, ge=1)
    note: str = Field(default="", max_length=2000)
    appointment_at: AwareDatetime | None = None


class ServiceReason(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(default="", max_length=2000)
    version: int | None = Field(default=None, ge=1)


class ServiceStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    from_status: str | None
    to_status: str
    actor_id: int
    assignee_id: int | None
    note: str
    at: datetime


class ServiceTicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str
    customer_code: str
    site_id: int | None
    serial: str | None
    kind: ServiceKind
    priority: ServicePriority
    symptom: str
    status: ServiceStatus
    assignee_id: int | None
    appointment_at: datetime | None
    due_at: datetime | None
    status_reason: str
    transfer_request: str
    billing: Literal["THU_TIEN", "MIEN_PHI"] | None
    free_reason: str
    subtotal: int | None
    vat_total: int | None
    total: int | None
    receivable: int | None
    priced_at: datetime | None
    created_by: int
    created_at: datetime
    version: int


class ServiceLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["PHI", "VAT_TU"]
    product_code: str | None = Field(default=None, max_length=16)
    warehouse_code: str | None = Field(default=None, max_length=16)
    description: str = Field(default="", max_length=500)
    qty: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit_price: int = Field(strict=True, ge=0, le=10_000_000_000)
    vat_rate: Literal[0, 5, 8, 10] = 0


class ServiceLineOut(ServiceLineIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    line_no: int
    amount: int
    vat_amount: int
    stock_doc_id: int | None


class ServiceCharges(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    billing: Literal["THU_TIEN", "MIEN_PHI"]
    free_reason: str = Field(default="", max_length=2000)
    lines: list[ServiceLineIn] = Field(default_factory=list, max_length=200)
    version: int | None = Field(default=None, ge=1)


class ServiceTicketDetail(ServiceTicketOut):
    steps: list[ServiceStepOut]
    lines: list[ServiceLineOut]


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity: str
    entity_id: str
    tag: str
    filename: str
    content_type: str
    size: int
    width: int | None
    height: int | None
    hidden: bool
    hidden_reason: str
    created_by: int
    created_at: datetime
    version: int


class CustomerSiteIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    address: str = Field(default="", max_length=2000)
    contact_name: str = Field(default="", max_length=200)
    contact_phone: str = Field(default="", max_length=32)
    note: str = Field(default="", max_length=2000)
    active: bool = True


class CustomerSitePatch(CustomerSiteIn):
    version: int = Field(ge=1)
    name: str = Field(default="", min_length=1, max_length=200)


class CustomerSiteOut(CustomerSiteIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_code: str
    version: int
    created_by: int
    created_at: datetime
    updated_at: datetime


class EquipmentFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_code: str = Field(min_length=1, max_length=16)
    customer_code: str | None = Field(default=None, min_length=1, max_length=16)
    site_id: int | None = Field(default=None, ge=1)
    warranty_until: date | None = None
    condition: Literal["MOI", "CU"] = "MOI"
    note: str = Field(default="", max_length=2000)
    active: bool = True


    @field_validator('customer_code', 'site_id', 'warranty_until', mode='before')
    @classmethod
    def blank_optional(cls, value):
        return None if value == '' else value


class EquipmentIn(EquipmentFields):
    serial: str = Field(min_length=1, max_length=64)

    @field_validator('serial', mode='before')
    @classmethod
    def normalize_serial(cls, value):
        return ''.join(value.split()).upper() if isinstance(value, str) else value


class EquipmentPatch(EquipmentFields):
    version: int = Field(ge=1)
    product_code: str = Field(default="", min_length=1, max_length=16)


class EquipmentOut(EquipmentFields):
    model_config = ConfigDict(from_attributes=True)

    serial: str
    version: int
    created_by: int
    created_at: datetime
    updated_at: datetime


class QuoteLineIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    product_code: str | None = Field(default=None, max_length=16)
    description: str = Field(default='', max_length=1000)
    qty: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit: str = Field(default='', max_length=32)
    unit_price: int | None = Field(default=None, strict=True, ge=0, le=9_223_372_036_854_775_807)
    vat_rate: int = Field(default=0, strict=True)

    @field_validator('vat_rate')
    @classmethod
    def allowed_vat(cls, value):
        from .models import VAT_RATES
        if value not in VAT_RATES:
            raise ValueError('VAT phải là 0, 5, 8 hoặc 10%.')
        return value

    @field_validator('product_code', mode='before')
    @classmethod
    def blank_product(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


class QuoteIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    customer_code: str = Field(min_length=1, max_length=16)
    contact_name: str = Field(default='', max_length=200)
    valid_until: date | None = None
    note: str = Field(default='', max_length=4000)
    lines: list[QuoteLineIn] = Field(default_factory=list, max_length=200)


class QuoteVersion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(strict=True, ge=1)


class QuoteUpdate(QuoteVersion):
    customer_code: str | None = Field(default=None, min_length=1, max_length=16)
    contact_name: str | None = Field(default=None, max_length=200)
    valid_until: date | None = None
    note: str | None = Field(default=None, max_length=4000)
    lines: list[QuoteLineIn] = Field(max_length=200)


class QuoteCancel(QuoteVersion):
    reason: str = Field(default='', max_length=2000)


class QuoteLineOut(QuoteLineIn):
    model_config = ConfigDict(from_attributes=True)
    line_no: int
    unit_price: int
    amount: int
    vat_amount: int


class QuoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    number: str
    customer_code: str
    contact_name: str
    valid_until: date
    status: str
    note: str
    cancel_reason: str
    replaced_by_id: int | None
    subtotal: int
    vat_total: int
    total: int
    created_by: int
    created_at: datetime
    version: int
    lines: list[QuoteLineOut]


class SalesOrderLineIn(QuoteLineIn):
    kind: Literal['HANG', 'KHAC']
    warehouse_code: str | None = Field(default=None, max_length=16)

    @field_validator('warehouse_code', mode='before')
    @classmethod
    def blank_warehouse(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


class SalesOrderIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    customer_code: str = Field(min_length=1, max_length=16)
    contact_name: str = Field(default='', max_length=200)
    delivery_address: str | None = Field(default=None, max_length=2000)
    note: str = Field(default='', max_length=4000)
    lines: list[SalesOrderLineIn] = Field(default_factory=list, max_length=200)


class SalesOrderVersion(QuoteVersion):
    pass


class SalesOrderCancel(SalesOrderVersion):
    reason: str = Field(default='', max_length=2000)


class SalesOrderFromQuote(QuoteVersion):
    """version là phiên bản báo giá; warehouses ánh xạ line_no sang kho xuất."""
    warehouses: dict[Annotated[int, Field(ge=1)], Annotated[str, Field(max_length=16)]] = Field(default_factory=dict, max_length=200)
    contact_name: str | None = Field(default=None, max_length=200)
    delivery_address: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=4000)


class SalesOrderUpdate(SalesOrderVersion):
    contact_name: str | None = Field(default=None, max_length=200)
    delivery_address: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=4000)
    lines: list[SalesOrderLineIn] = Field(max_length=200)


class SalesOrderLineOut(SalesOrderLineIn):
    model_config = ConfigDict(from_attributes=True)
    line_no: int
    unit_price: int
    amount: int
    vat_amount: int
    stock_doc_id: int | None


class SalesOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    number: str
    customer_code: str
    quote_id: int | None
    contact_name: str
    delivery_address: str
    note: str
    status: str
    cancel_reason: str
    subtotal: int
    vat_total: int
    total: int
    receivable: int | None
    confirmed_at: datetime | None
    confirmed_by: int | None
    delivered_at: datetime | None
    delivered_by: int | None
    created_by: int
    created_at: datetime
    version: int
    lines: list[SalesOrderLineOut]


class SalesReceiptReport(QuoteVersion):
    method: Literal['TIEN_MAT', 'CHUYEN_KHOAN']
    amount: int = Field(strict=True, gt=0, le=9_223_372_036_854_775_807)
    received_at: date | None = None
    note: str = Field(default='', max_length=4000)


class SalesReceiptDecision(QuoteVersion):
    reason: str = Field(default='', max_length=2000)


class SalesReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    number: str
    order_id: int | None
    ticket_id: int | None
    cash_doc_id: int | None
    method: str
    amount: int
    received_at: date
    note: str
    status: str
    reason: str
    reported_by: int
    reported_at: datetime
    decided_by: int | None
    decided_at: datetime | None
    version: int


class SalesReceiptBalance(BaseModel):
    receivable: int | None
    paid: int
    pending: int
    remaining: int


class SalesOrderReceipts(BaseModel):
    balance: SalesReceiptBalance
    receipts: list[SalesReceiptOut]


class SalesReceivableOut(BaseModel):
    order: SalesOrderOut
    balance: SalesReceiptBalance
    days: int


class FundIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=300)
    kind: Literal['TIEN_MAT', 'NGAN_HANG']
    bank_name: str = Field(default='', max_length=300)
    account_no: str = Field(default='', max_length=100)
    note: str = Field(default='', max_length=2000)
    active: bool = True


class FundPatch(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    version: int = Field(strict=True, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=300)
    kind: Literal['TIEN_MAT', 'NGAN_HANG'] | None = None
    bank_name: str | None = Field(default=None, max_length=300)
    account_no: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=2000)
    active: bool | None = None


class CashCategoryIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=300)
    direction: Literal['THU', 'CHI']
    note: str = Field(default='', max_length=2000)
    active: bool = True


class CashCategoryPatch(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    version: int = Field(strict=True, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=300)
    direction: Literal['THU', 'CHI'] | None = None
    note: str | None = Field(default=None, max_length=2000)
    active: bool | None = None


class CashDocIn(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    kind: Literal['PT','PC','CQ']
    doc_date: date
    fund_code: str = Field(min_length=1, max_length=32)
    to_fund_code: str | None = Field(default=None, max_length=32)
    category_code: str | None = Field(default=None, max_length=32)
    amount: int = Field(strict=True, gt=0, le=9_223_372_036_854_775_807)
    customer_code: str | None = Field(default=None, max_length=16)
    supplier_code: str | None = Field(default=None, max_length=16)
    partner_name: str = Field(default='', max_length=300)
    description: str = Field(default='', max_length=4000)
    is_opening: bool = False
    source_type: str | None = Field(default=None, max_length=64)
    source_id: str | None = Field(default=None, max_length=64)

    @field_validator('to_fund_code','category_code','customer_code','supplier_code','source_type','source_id',mode='before')
    @classmethod
    def empty_to_none(cls,value):
        return None if isinstance(value,str) and not value.strip() else value


class CashDocPatch(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    version: int = Field(strict=True, ge=1)
    kind: Literal['PT','PC','CQ'] | None = None
    doc_date: date | None = None
    fund_code: str | None = Field(default=None,min_length=1,max_length=32)
    to_fund_code: str | None = Field(default=None,max_length=32)
    category_code: str | None = Field(default=None,max_length=32)
    amount: int | None = Field(default=None,strict=True,gt=0,le=9_223_372_036_854_775_807)
    customer_code: str | None = Field(default=None,max_length=16)
    supplier_code: str | None = Field(default=None,max_length=16)
    partner_name: str | None = Field(default=None,max_length=300)
    description: str | None = Field(default=None,max_length=4000)
    is_opening: bool | None = None


class CashDocCancel(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    version: int = Field(strict=True,ge=1)
    reason: str = Field(min_length=1,max_length=2000)


class ServiceReceivableOut(ServiceTicketOut):
    paid: int
    pending: int
    remaining: int
