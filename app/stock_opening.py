"""Tồn đầu kỳ (K5): biến file kiểm kê thành phiếu KK nháp để Kho xem lại rồi ghi sổ trên web.

Không ghi sổ ở đây; ghi sổ đi qua stock_posting.post như mọi phiếu khác.
"""
import re
from datetime import date
from decimal import Decimal

from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from . import stock_docs
from .errors import BusinessError
from .models import Product, StockDoc, StockLedger, User, Warehouse
from .schemas import StockDocIn

# Dấu hiệu phiếu đầu kỳ, để chạy lại cùng file không nhân đôi tồn.
OPENING_REASON = "Kiểm kê đầu kỳ"
MAX_LINES = 1000  # bằng giới hạn dòng của một phiếu kho
_QTY = re.compile(r"^\d+([.,]\d{1,3})?$")


def _qty(raw: str) -> Decimal | None:
    s = raw.strip().replace(" ", "")
    return Decimal(s.replace(",", ".")) if _QTY.match(s) else None


def _cost(raw: str) -> int | None:
    # Đơn giá là số nguyên đồng, cho phép dấu chấm hoặc phẩy ngăn hàng nghìn: 1.250.000
    s = re.sub(r"[.,\s]", "", raw.strip())
    return int(s) if s.isdigit() else None


def parse(rows) -> tuple[list[dict], list[dict]]:
    lines, errors, seen = [], [], {}
    for n, row in enumerate(rows, start=2):  # dòng 1 là tiêu đề
        code = (row.get("MA_HANG") or "").strip().upper()
        if not code and not (row.get("SO_LUONG") or "").strip():
            continue
        qty, cost = _qty(row.get("SO_LUONG") or ""), _cost(row.get("DON_GIA") or "")
        if not code:
            errors.append({"row": n, "error": "MISSING_CODE"})
        elif code in seen:
            errors.append({"row": n, "error": "DUPLICATE_CODE", "code": code, "first_row": seen[code]})
        elif qty is None:
            errors.append({"row": n, "error": "BAD_QTY", "code": code})
        elif cost is None:
            errors.append({"row": n, "error": "BAD_UNIT_COST", "code": code})
        else:
            seen[code] = n
            lines.append({"product_code": code, "qty": qty, "unit_cost": cost, "note": f"dòng {n}"})
    return lines, errors


def create_opening(session: Session, user: User, warehouse_code: str, doc_date: date, rows) -> list[StockDoc]:
    lines, errors = parse(rows)
    # Kiểm cả mã ngừng bán ở đây: file nhiều phần thì phần đầu đã lưu trước khi phần sau bị từ chối.
    known = dict(session.execute(
        select(Product.code, Product.active).where(Product.code.in_([x["product_code"] for x in lines]))).all())
    codes = {x["product_code"] for x in lines}
    errors += [{"error": "UNKNOWN_PRODUCT", "code": c} for c in sorted(codes - known.keys())]
    errors += [{"error": "INACTIVE_PRODUCT", "code": c} for c in sorted(c for c in codes & known.keys() if not known[c])]
    if errors:
        raise BusinessError("BAD_OPENING_FILE", "File tồn đầu kỳ có dòng lỗi, chưa tạo phiếu nào.", {"errors": errors})
    if not lines:
        raise BusinessError("EMPTY_OPENING_FILE", "File không có dòng hàng nào.")
    if session.get(Warehouse, warehouse_code) is None:
        raise BusinessError("BAD_WAREHOUSE", "Kho không tồn tại.", {"code": warehouse_code})

    # Khóa theo kho để hai lần chạy cùng lúc không cùng tạo phiếu đầu kỳ.
    session.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": "opening:" + warehouse_code})
    existing = session.scalars(
        select(StockDoc.number).where(
            StockDoc.warehouse_code == warehouse_code, StockDoc.kind == "KK",
            StockDoc.reason == OPENING_REASON, StockDoc.status != "DA_HUY",
        )
    ).all()
    if existing:
        raise BusinessError("OPENING_EXISTS", "Kho này đã có phiếu kiểm kê đầu kỳ.", {"docs": list(existing)}, status=409)
    if session.scalar(select(exists().where(StockLedger.warehouse_code == warehouse_code))):
        raise BusinessError("WAREHOUSE_NOT_EMPTY", "Kho đã có phát sinh nhập xuất, không nhập tồn đầu kỳ được nữa.", status=409)

    docs = []
    chunks = [lines[i:i + MAX_LINES] for i in range(0, len(lines), MAX_LINES)]
    for i, chunk in enumerate(chunks, 1):
        note = f"Nhập từ file, phần {i}/{len(chunks)}" if len(chunks) > 1 else "Nhập từ file"
        body = StockDocIn(kind="KK", warehouse_code=warehouse_code, doc_date=doc_date, reason=OPENING_REASON, note=note, lines=chunk)
        # stock_docs.create commit từng phiếu; khóa advisory hết hiệu lực sau phiếu đầu,
        # nhưng phiếu đầu đã lưu nên lần chạy song song sẽ thấy OPENING_EXISTS.
        docs.append(stock_docs.create(session, user, body, source_kind='STOCK_OPENING'))
    return docs
