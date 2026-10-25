"""Nhập danh mục hàng (mã TD) và mức tồn tối thiểu từ file CSV đã duyệt. Không đụng tồn kho, không đụng giá vốn.

    python -m scripts.import_products --csv danh-muc.csv [--user giamdoc] [--apply]

Cột: MA_HANG, TEN, DVT, NHOM, BAO_HANH_THANG, TRANG_THAI (DANG_BAN/NGUNG_BAN), GIA_BAN,
và MUC_TON_<MÃ KHO> cho từng kho (ví dụ MUC_TON_K58, MUC_TON_K145; trống = không đặt).

Chạy thử (không --apply) không ghi gì. Mã chưa có → TAO_MOI. Mã đã có → CAP_NHAT nếu tên, ĐVT, nhóm, bảo hành,
trạng thái hoặc giá bán khác, kèm danh sách trường đổi; giống hệt → DA_CO. Nhóm hàng chưa có thì tạo.
Mức tồn tối thiểu chỉ ghi khi khác mức hiện tại.
"""
import argparse
import csv
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import products, stock_min_levels  # noqa: E402
from app.db import engine  # noqa: E402
from app.errors import BusinessError  # noqa: E402
from app.models import Product, ProductGroup, StockMinLevel, User, Warehouse  # noqa: E402
from app.schemas import ProductIn, ProductPatch, StockMinLevelIn  # noqa: E402

CODE = re.compile(r"^TD-\d{4,}$")


def read_csv(path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        return [{(k or "").strip().upper(): (v or "").strip() for k, v in row.items()} for row in reader]


def _int(raw: str, default: int = 0) -> int:
    s = re.sub(r"[.,\s]", "", raw or "")
    if not s:
        return default
    if not s.isdigit():
        raise ValueError(f"Số không hợp lệ: {raw}")
    return int(s)


def _qty(raw: str) -> Decimal | None:
    s = (raw or "").strip().replace(" ", "")
    if not s:
        return None
    try:
        return Decimal(s.replace(",", "."))
    except InvalidOperation:
        raise ValueError(f"Mức tồn không hợp lệ: {raw}")


def _min_columns(rows: list[dict]) -> list[str]:
    cols = set()
    for r in rows:
        cols.update(k for k in r if k.startswith("MUC_TON_"))
    return sorted(cols)


def import_rows(session: Session, user: User, rows: list[dict], *, apply: bool = False) -> list[dict]:
    report = []
    groups = {g.lower() for g in products.groups(session)}
    warehouses = {w.code for w in session.scalars(select(Warehouse).where(Warehouse.kind == "KHO"))}
    seen: set[str] = set()
    for n, row in enumerate(rows, start=2):  # dòng 1 là tiêu đề
        code = (row.get("MA_HANG") or "").upper()
        result = {"row": n, "code": code, "status": ""}
        report.append(result)
        try:
            if not CODE.match(code):
                raise ValueError("Mã hàng phải có dạng TD-0001.")
            if code in seen:
                raise ValueError("Mã hàng lặp lại trong file.")
            seen.add(code)
            name = row.get("TEN", "")
            if not name:
                raise ValueError("Thiếu tên hàng.")
            unit = row.get("DVT", "") or "cái"
            group = " ".join((row.get("NHOM") or "").split())
            months = _int(row.get("BAO_HANH_THANG", ""), 0)
            if not 0 <= months <= 60:
                raise ValueError("Bảo hành phải từ 0 đến 60 tháng.")
            status = (row.get("TRANG_THAI") or "DANG_BAN").upper()
            if status not in ("DANG_BAN", "NGUNG_BAN"):
                raise ValueError("TRANG_THAI phải là DANG_BAN hoặc NGUNG_BAN.")
            active = status == "DANG_BAN"
            price = _int(row.get("GIA_BAN", ""), 0)
            mins = {}
            for col in (k for k in row if k.startswith("MUC_TON_")):
                q = _qty(row[col])
                if q is None:
                    continue
                wh = col[len("MUC_TON_"):]
                if wh not in warehouses:
                    raise ValueError(f"Kho {wh} không có hoặc không phải kho hàng.")
                mins[wh] = q

            if group and group.lower() not in groups:
                result["new_group"] = group
                if apply:
                    products.create_group(session, user, group)
                groups.add(group.lower())

            p = session.get(Product, code)
            if p is None:
                result.update(status="TAO_MOI", name=name, unit=unit, group=group, warranty_months=months, active=active)
                if apply:
                    p = products.create(session, user, ProductIn(code=code, name=name, group_name=group, unit=unit, warranty_months=months, sale_price=price))
                    if not active:
                        products.update(session, user, code, ProductPatch(version=p.version, active=False))
            else:
                changes = {}
                if p.name != name:
                    changes["name"] = [p.name, name]
                if p.unit != unit:
                    changes["unit"] = [p.unit, unit]
                if p.group_name != group:
                    changes["group_name"] = [p.group_name, group]
                if p.warranty_months != months:
                    changes["warranty_months"] = [p.warranty_months, months]
                if p.active != active:
                    changes["active"] = [p.active, active]
                if price and p.sale_price != price:
                    changes["sale_price"] = [p.sale_price, price]
                if changes:
                    result.update(status="CAP_NHAT", changes=changes)
                    if apply:
                        products.update(session, user, code, ProductPatch(version=p.version, **{k: v[1] for k, v in changes.items()}))
                else:
                    result["status"] = "DA_CO"

            min_changes = {}
            for wh, q in mins.items():
                level = session.get(StockMinLevel, (wh, code))
                if level is not None and level.min_qty == q:
                    continue
                min_changes[wh] = str(q)
                if apply:
                    stock_min_levels.set_level(session, user, wh, code, StockMinLevelIn(min_qty=q, version=level.version if level else 0))
            if min_changes:
                result["min_levels"] = min_changes
        except (BusinessError, ValueError) as exc:
            session.rollback()
            result.update(status="LOI", message=getattr(exc, "message", str(exc)), code_error=getattr(exc, "code", ""))
    return report


def summary(report: list[dict]) -> dict:
    out: dict[str, int] = {}
    for r in report:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--user", default="", help="tên đăng nhập; mặc định giám đốc đầu tiên đang hoạt động (cần quyền kho và bảo hành)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    with Session(engine()) as session:
        if args.user:
            user = session.scalar(select(User).where(User.username == args.user.strip().lower(), User.active.is_(True)))
        else:
            user = session.scalar(select(User).where(User.active.is_(True), User.roles.contains(["director"])).order_by(User.id).limit(1))
        if user is None:
            print("Không tìm thấy tài khoản giám đốc đang hoạt động.", file=sys.stderr)
            return 1
        rep = import_rows(session, user, read_csv(args.csv), apply=args.apply)
    print(json.dumps({"apply": args.apply, "tong_ket": summary(rep), "dong": rep}, ensure_ascii=False, indent=2))
    return 0 if not any(r["status"] == "LOI" for r in rep) else 2


if __name__ == "__main__":
    sys.exit(main())
