"""UI0: đọc số tiền người dùng gõ (540.000, 540000, 540.000 đ) thành int VND."""
import re

from ..errors import BusinessError

_GROUPED = re.compile(r"\d{1,3}(?:\.\d{3})+|\d+")
MAX_MONEY = 10**12


def parse_money(raw, field: str = "Số tiền", allow_zero: bool = True) -> int:
    s = str(raw or "").strip()
    for suffix in ("đồng", "đ", "₫", "VND", "vnd"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    s = s.replace(" ", "")
    if not s:
        raise BusinessError("MONEY_REQUIRED", f"{field}: hãy nhập số tiền.")
    if s.startswith("-"):
        raise BusinessError("MONEY_NEGATIVE", f"{field} không được là số âm.")
    if not _GROUPED.fullmatch(s):
        raise BusinessError("MONEY_FORMAT", f"{field} chỉ gồm chữ số, ví dụ 540.000.")
    value = int(s.replace(".", ""))
    if value == 0 and not allow_zero:
        raise BusinessError("MONEY_ZERO", f"{field} phải lớn hơn 0.")
    if value > MAX_MONEY:
        raise BusinessError("MONEY_TOO_LARGE", f"{field} quá lớn, hãy kiểm tra lại.")
    return value
