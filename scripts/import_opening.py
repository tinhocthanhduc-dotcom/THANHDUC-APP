"""Nhập tồn đầu kỳ của một kho từ file kiểm kê, tạo phiếu KK nháp.

    python -m scripts.import_opening --warehouse K58 --date 2026-12-31 --user thu.kho ton_dau_ky_58.csv

Cột: MA_HANG, SO_LUONG (số lẻ dùng dấu phẩy hoặc chấm, tối đa 3 số lẻ), DON_GIA (đồng, ví dụ 1.250.000).
File có bất kỳ dòng lỗi hoặc mã hàng chưa có trong danh mục thì không tạo phiếu nào và in đủ danh sách lỗi.
Phiếu tạo ra ở trạng thái nháp; Kho mở trên web kiểm lại rồi bấm Ghi sổ.
"""
import argparse
import csv
import sys
from datetime import date

from sqlalchemy import select

from app.db import get_session
from app.errors import BusinessError
from app.models import User
from app.stock_opening import create_opening

ERRORS = {
    "MISSING_CODE": "thiếu mã hàng",
    "DUPLICATE_CODE": "mã hàng lặp lại",
    "BAD_QTY": "số lượng sai",
    "BAD_UNIT_COST": "đơn giá sai hoặc trống",
    "UNKNOWN_PRODUCT": "mã hàng chưa có trong danh mục",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_file")
    ap.add_argument("--warehouse", required=True)
    ap.add_argument("--date", required=True, type=date.fromisoformat)
    ap.add_argument("--user", required=True, help="tên đăng nhập của thủ kho, ghi vào nhật ký")
    args = ap.parse_args()
    with open(args.csv_file, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    session = next(get_session())
    user = session.scalars(select(User).where(User.username == args.user.strip().lower(), User.active.is_(True))).first()
    if user is None:
        print("Không tìm thấy tài khoản đang hoạt động: " + args.user, file=sys.stderr)
        return 1
    try:
        docs = create_opening(session, user, args.warehouse.strip().upper(), args.date, rows)
    except BusinessError as e:
        print(e.message, file=sys.stderr)
        for err in e.details.get("errors", []):
            where = f"dòng {err['row']}: " if "row" in err else ""
            print(f"  {where}{err.get('code', '')} {ERRORS.get(err['error'], err['error'])}", file=sys.stderr)
        for number in e.details.get("docs", []):
            print("  " + number, file=sys.stderr)
        return 1
    for d in docs:
        print(f"Đã tạo phiếu nháp {d.number} ({len(d.lines)} dòng). Mở trên web để kiểm lại rồi ghi sổ.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
