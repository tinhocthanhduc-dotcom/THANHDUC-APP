"""Nhập một ít khách từ Sheet cũ để thử nghiệm (không chuyển toàn bộ dữ liệu cũ).

Xuất tab khách của Danh bạ khách ra CSV (Tệp > Tải xuống > CSV), rồi chạy:
    python -m scripts.import_sample khach.csv --limit 50
Cột đọc được: MA_KHACH, LOAI, TEN, MST, DIEN_THOAI, EMAIL, DIA_CHI (thiếu cột nào thì bỏ qua cột đó).
Mọi khách nhập vào được đánh dấu is_test để xóa sạch trước khi chạy thật (scripts.clear_test_data).
Dòng lỗi hoặc trùng được bỏ qua và in ra lý do; không dòng nào được sửa tự động.
"""
import argparse
import csv
import re
import sys

from sqlalchemy import text
from sqlalchemy.orm import Session

from app import audit
from app.customers import _check_conflicts, _next_code, _search_text, _set_children, snapshot
from app.db import get_session
from app.errors import BusinessError
from app.models import Customer
from app.text import phones, tax_code

KTD = re.compile(r"^KTD-(\d{6})$")


def _bump_sequence(session: Session, at_least: int) -> None:
    used = session.execute(
        text(
            "select greatest(:n, (select coalesce(max(substring(code from 5)::int), 0) from customers where code like 'KTD-%'), "
            "(select case when is_called then last_value else 0 end from ktd_seq))"
        ),
        {"n": at_least},
    ).scalar_one()
    if used > 0:
        session.execute(text("select setval('ktd_seq', :v, true)"), {"v": used})


def import_rows(session: Session, rows, limit: int) -> tuple[list[str], list[tuple[int, str, str]]]:
    created, skipped = [], []
    rows = list(rows)
    # Mã KTD cấp mới phải nằm sau mọi mã KTD cũ trong file, kể cả dòng chưa tới lượt.
    old_max = max((int(m.group(1)) for r in rows if (m := KTD.match((r.get("MA_KHACH") or "").strip()))), default=0)
    _bump_sequence(session, old_max)
    session.commit()
    for line, row in enumerate(rows, start=2):
        if len(created) >= limit:
            break
        name = (row.get("TEN") or "").strip()
        old = (row.get("MA_KHACH") or "").strip()
        try:
            if not name:
                raise BusinessError("MISSING_NAME", "Thiếu tên")
            kind = "CONG_TY" if (row.get("LOAI") or "").strip().upper() == "CONG_TY" else "CA_NHAN"
            own = phones(row.get("DIEN_THOAI") or "")
            tax = tax_code(row.get("MST") or "")
            if kind == "CA_NHAN" and not own:
                raise BusinessError("MISSING_PHONE", "Khách cá nhân không có số điện thoại")
            _check_conflicts(session, kind, tax, own, None, None, None)
            code = old if KTD.match(old) and not session.get(Customer, old) else _next_code(session)
            c = Customer(
                code=code, kind=kind, name=name, tax_code=tax, email=(row.get("EMAIL") or "").strip(),
                address=(row.get("DIA_CHI") or "").strip(), note="", allow_debt=False, credit_limit=0,
                status="DANG_DUNG", old_codes=[old] if old and old != code else [], is_test=True,
            )
            _set_children(c, own, [])
            c.search_text = _search_text(c)
            session.add(c)
            session.flush()
            audit.record(session, None, "customer.import_sample", "customer", c.code, None, snapshot(c), f"dòng {line}")
            session.commit()
            created.append(c.code)
        except BusinessError as e:
            session.rollback()
            skipped.append((line, name or old, e.code))
    return created, skipped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_file")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()
    with open(args.csv_file, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    created, skipped = import_rows(next(get_session()), rows, args.limit)
    print(f"Đã nhập {len(created)} khách thử nghiệm.")
    for line, name, code in skipped:
        print(f"  Bỏ qua dòng {line} ({name}): {code}", file=sys.stderr)


if __name__ == "__main__":
    main()
