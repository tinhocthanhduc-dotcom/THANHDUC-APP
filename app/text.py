"""Chuẩn hóa chữ, số điện thoại, MST. Giữ đúng quy tắc của app Danh bạ khách (customer-directory/core.cjs)."""
import re
import unicodedata

from .errors import BusinessError


def fold(value: str | None) -> str:
    """Bỏ dấu, chữ thường, gộp khoảng trắng: 'Công ty Đức' -> 'cong ty duc'."""
    s = unicodedata.normalize("NFD", str(value or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "d").lower()
    return re.sub(r"\s+", " ", s).strip()


_PHONE_CHARS = re.compile(r"^[+\d ().-]+$")


def phone(value: str) -> str:
    v = str(value).strip()
    if not _PHONE_CHARS.match(v):
        raise BusinessError("INVALID_PHONE", f"Số điện thoại không hợp lệ: {value}")
    v = re.sub(r"[ ().-]", "", v)
    v = re.sub(r"^\+84", "0", v)
    v = re.sub(r"^0084", "0", v)
    # Sheet làm mất số 0 đầu: di động 9 số, máy bàn 2x 10 số.
    if re.fullmatch(r"[35789]\d{8}", v) or re.fullmatch(r"2\d{9}", v):
        v = "0" + v
    if re.fullmatch(r"84\d{9}", v):
        v = "0" + v[2:]
    if not re.fullmatch(r"0\d{9}|02\d{9}", v):
        raise BusinessError("INVALID_PHONE", f"Số điện thoại không hợp lệ: {value}")
    return v


def phones(values: list[str] | str | None) -> list[str]:
    if values is None:
        return []
    items = values if isinstance(values, list) else re.split(r"[;,/\n]", values)
    return sorted({phone(x) for x in items if str(x).strip()})


def tax_code(value: str | None) -> str:
    v = re.sub(r"[ .-]", "", str(value or "")).strip()
    # Sheet làm mất số 0 đầu của MST 10 hoặc 13 số.
    if re.fullmatch(r"\d{9}|\d{12}", v):
        v = "0" + v
    if v and not re.fullmatch(r"\d{10}|\d{13}", v):
        raise BusinessError("INVALID_TAX", f"MST phải có 10 hoặc 13 chữ số: {value}")
    return v


def digits_query(q: str) -> str:
    v = re.sub(r"[ ().-]", "", q or "")
    v = re.sub(r"^\+84", "0", v)
    return re.sub(r"\D", "", v)
