import pytest

from app.errors import BusinessError
from app.text import fold, phone, phones, tax_code


def test_fold_removes_accents_and_d():
    assert fold("  Công ty  TNHH Đức Thành ") == "cong ty tnhh duc thanh"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("0903 123 456", "0903123456"),
        ("+84903123456", "0903123456"),
        ("84903123456", "0903123456"),
        ("903123456", "0903123456"),  # Sheet mất số 0 của di động
        ("2838123456", "02838123456"),  # Sheet mất số 0 của máy bàn
        ("(028) 3812-3456", "02838123456"),
    ],
)
def test_phone_normalization(raw, expected):
    assert phone(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "12345", "0903-12"])
def test_phone_invalid(raw):
    with pytest.raises(BusinessError) as e:
        phone(raw)
    assert e.value.code == "INVALID_PHONE"


def test_phones_dedupe_and_split():
    assert phones("0903123456; 903123456 / 0912000111") == ["0903123456", "0912000111"]


@pytest.mark.parametrize(
    "raw, expected",
    [("0301234567", "0301234567"), ("301234567", "0301234567"), ("0301234567-001", "0301234567001"), ("", "")],
)
def test_tax_code(raw, expected):
    assert tax_code(raw) == expected


def test_tax_code_invalid():
    with pytest.raises(BusinessError):
        tax_code("12345")
