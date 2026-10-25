"""Nhập danh bạ khách hàng và nhà cung cấp thật từ file gộp (SALE + CARE + Finance).

    python -m scripts.import_customers --csv khach-hang-gop.csv [--ncc nha-cung-cap.csv] [--user giamdoc] [--apply]

Chạy thử (không --apply) không ghi gì, chỉ in báo cáo từng dòng. Chạy lại nhiều lần không tạo trùng:
khách đã có trong app (theo mã cũ, MST hoặc số điện thoại) chỉ được bổ sung mã cũ, địa chỉ, người liên hệ còn thiếu.

Cột file khách (thiếu cột nào thì coi là trống):
  MA_CU_SALE, MA_CU_CARE, LOAI (CA_NHAN/CONG_TY), TEN, CONG_TY, MST, DIEN_THOAI, EMAIL, DIA_CHI,
  NGUOI_LIEN_HE, HAN_MUC_NO, SO_NGAY_NO, GHI_CHU, XU_LY (trống = nhập; BO = bỏ; GOP:<STT> = gộp vào dòng STT đó).
Cột file nhà cung cấp: MA_CU_FINANCE, TEN, MST, DIEN_THOAI, EMAIL, DIA_CHI, NGUOI_LIEN_HE, SO_TAI_KHOAN, GHI_CHU.

Trạng thái từng dòng: TAO_MOI, DA_CO (khớp mã cũ), GOP_MST, GOP_SDT (cùng số với khách đã có hoặc dòng trước trong file), GOP_THEO_FILE, BO_QUA, LOI.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import audit, customers, inventory_catalogs  # noqa: E402
from app.db import engine  # noqa: E402
from app.errors import BusinessError  # noqa: E402
from app.models import Customer, CustomerContact, CustomerPhone, Supplier, User  # noqa: E402
from app.schemas import ContactIn, CustomerCreate, CustomerIn, SupplierIn  # noqa: E402
from app.text import fold, phones, tax_code  # noqa: E402

GOP = re.compile(r"^GOP\s*:\s*(\d+)$", re.I)


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


def _old_codes(row: dict) -> list[str]:
    return [c for c in (row.get("MA_CU_SALE", ""), row.get("MA_CU_CARE", "")) if c]


def _int(raw: str) -> int:
    s = re.sub(r"\D", "", raw or "")
    return int(s) if s else 0


def _find_by_old_code(session: Session, codes: list[str]) -> Customer | None:
    for code in codes:
        c = session.scalars(select(Customer).where(Customer.old_codes.contains([code]), Customer.status != "DA_GOP")).first()
        if c:
            return c
    return None


def _owner_of_phones(session: Session, nums: list[str]) -> Customer | None:
    if not nums:
        return None
    return session.scalars(
        select(Customer).join(CustomerPhone).where(CustomerPhone.phone.in_(nums), Customer.status != "DA_GOP").order_by(Customer.code)
    ).first()


def _attach(session: Session, user: User, target: Customer, row: dict, codes: list[str], own: list[str], apply: bool, status: str) -> dict:
    """Gắn mã cũ, số điện thoại, địa chỉ, người liên hệ còn thiếu vào khách đã có. Không đổi tên, không đổi loại."""
    changes = {}
    missing_codes = [c for c in codes if c not in (target.old_codes or [])]
    if missing_codes:
        changes["old_codes"] = missing_codes
    have = {p.phone for p in target.phones}
    new_phones = [p for p in own if p not in have]
    if new_phones:
        changes["phones"] = new_phones
    name = row.get("TEN", "")
    if fold(name) != fold(target.name) and name and target.kind == "CONG_TY" and not any(fold(x.name) == fold(name) for x in target.contacts):
        changes["contact"] = name
    if not target.address and row.get("DIA_CHI"):
        changes["address"] = row["DIA_CHI"]
    if not target.email and row.get("EMAIL"):
        changes["email"] = row["EMAIL"]
    result = {"status": status, "customer_code": target.code, "existing_name": target.name, "changes": changes}
    if apply and changes:
        before = customers.snapshot(target)
        if missing_codes:
            target.old_codes = list(target.old_codes or []) + missing_codes
        for p in new_phones:
            # Số mới của người liên hệ thì is_contact, số của chính khách thì không.
            target.phones.append(CustomerPhone(phone=p, is_contact=bool(changes.get("contact"))))
        if changes.get("contact"):
            target.contacts.append(CustomerContact(name=name, phones=new_phones, note="Từ file gộp danh bạ"))
        if changes.get("address"):
            target.address = row["DIA_CHI"]
        if changes.get("email"):
            target.email = row["EMAIL"]
        target.search_text = customers._search_text(target)
        session.flush()
        audit.record(session, user, "customer.import", "customer", target.code, before, customers.snapshot(target), "gộp từ file danh bạ")
        session.commit()
    return result


def import_customers(session: Session, user: User, rows: list[dict], *, apply: bool = False) -> list[dict]:
    report = []
    created_by_line: dict[int, str] = {}  # STT trong file -> mã khách đã tạo (để GOP:<STT>)
    seen_phones: dict[str, int] = {}  # số đã dùng ở dòng trước trong cùng file (chạy thử không có khách trong DB để tra)
    deferred = []  # dòng GOP:<n> xử lý sau khi mọi dòng khác đã tạo
    for n, row in enumerate(rows, start=1):
        result = {"row": n, "name": row.get("TEN", ""), "old_codes": _old_codes(row), "status": ""}
        report.append(result)
        xu_ly = (row.get("XU_LY") or "").strip()
        if xu_ly.upper() == "BO":
            result["status"] = "BO_QUA"
            continue
        if GOP.match(xu_ly):
            deferred.append((n, row, result, int(GOP.match(xu_ly).group(1))))
            continue
        try:
            _import_one(session, user, n, row, result, apply, created_by_line, seen_phones)
        except (BusinessError, ValueError) as exc:
            session.rollback()
            result.update(status="LOI", message=getattr(exc, "message", str(exc)), code=getattr(exc, "code", ""))
    for n, row, result, target_line in deferred:
        try:
            own = phones(row.get("DIEN_THOAI", ""))
            target = None
            if target_line in created_by_line:
                target = session.get(Customer, created_by_line[target_line])
            elif 1 <= target_line <= len(rows):
                target = _find_by_old_code(session, _old_codes(rows[target_line - 1])) or _owner_of_phones(session, phones(rows[target_line - 1].get("DIEN_THOAI", "")))
            if target is None:
                raise ValueError(f"Dòng {target_line} để gộp vào chưa có trong app (bị lỗi hoặc bị bỏ).")
            result.update(_attach(session, user, target, row, _old_codes(row), own, apply, "GOP_THEO_FILE"))
        except (BusinessError, ValueError) as exc:
            session.rollback()
            result.update(status="LOI", message=getattr(exc, "message", str(exc)), code=getattr(exc, "code", ""))
    return report


def _import_one(session, user, n, row, result, apply, created_by_line, seen_phones):
    name = row.get("TEN", "")
    if not name:
        raise ValueError("Thiếu tên.")
    kind = "CONG_TY" if (row.get("LOAI") or "").strip().upper() == "CONG_TY" else "CA_NHAN"
    codes = _old_codes(row)
    own = phones(row.get("DIEN_THOAI", ""))
    tax = tax_code(row.get("MST", ""))

    existing = _find_by_old_code(session, codes)
    if existing:
        result.update(_attach(session, user, existing, row, codes, own, apply, "DA_CO"))
        created_by_line[n] = existing.code
        return
    if tax:
        by_tax = session.scalars(select(Customer).where(Customer.tax_code == tax, Customer.status != "DA_GOP")).first()
        if by_tax:
            result.update(_attach(session, user, by_tax, row, codes, own, apply, "GOP_MST"))
            created_by_line[n] = by_tax.code
            return
    owner = _owner_of_phones(session, own)
    if owner:
        # Cùng số: cá nhân trùng cá nhân = cùng người; công ty/cá nhân lẫn nhau = người liên hệ của công ty.
        result.update(_attach(session, user, owner, row, codes, own, apply, "GOP_SDT"))
        created_by_line[n] = owner.code
        return
    for p in own:
        if p in seen_phones:
            # Chạy thử: dòng trước trong file sẽ sở hữu số này, dòng này sẽ được gộp vào đó khi chạy thật.
            result.update(status="GOP_SDT", other_row=seen_phones[p], phone=p, note="gộp vào dòng %d khi chạy thật" % seen_phones[p])
            return
    if kind == "CA_NHAN" and not own:
        raise BusinessError("MISSING_PHONE", "Khách cá nhân cần ít nhất một số điện thoại.")
    if kind == "CONG_TY" and not own and not tax:
        raise BusinessError("MISSING_IDENTITY", "Khách công ty cần MST hoặc số điện thoại.")
    contacts = []
    contact_name = row.get("NGUOI_LIEN_HE", "")
    if contact_name and fold(contact_name) != fold(name):
        contacts.append(ContactIn(name=contact_name, phones=[]))
    limit = _int(row.get("HAN_MUC_NO", ""))
    note_parts = [row.get("GHI_CHU", "")]
    if row.get("CONG_TY") and fold(row["CONG_TY"]) != fold(name):
        note_parts.append("Công ty: " + row["CONG_TY"])
    if _int(row.get("SO_NGAY_NO", "")):
        note_parts.append(f"Số ngày được nợ (Finance): {_int(row['SO_NGAY_NO'])}")
    body = CustomerIn(
        kind=kind, name=name, tax_code=tax, phones=own, email=row.get("EMAIL", "")[:200], address=row.get("DIA_CHI", ""),
        note="\n".join(p for p in note_parts if p)[:2000], contacts=contacts, allow_debt=limit > 0, credit_limit=limit,
    )
    for p in own:
        seen_phones.setdefault(p, n)
    result.update(status="TAO_MOI", kind=kind, phones=own, tax_code=tax, credit_limit=limit)
    if apply:
        c, _ = customers.create(session, user, CustomerCreate(customer=body))
        if codes:
            before = customers.snapshot(c)
            c.old_codes = codes
            c.search_text = customers._search_text(c)
            session.flush()
            audit.record(session, user, "customer.import", "customer", c.code, before, customers.snapshot(c), "mã cũ từ file danh bạ")
            session.commit()
        result["customer_code"] = c.code
        created_by_line[n] = c.code


def import_suppliers(session: Session, user: User, rows: list[dict], *, apply: bool = False) -> list[dict]:
    report = []
    for n, row in enumerate(rows, start=1):
        result = {"row": n, "name": row.get("TEN", ""), "old_code": row.get("MA_CU_FINANCE", ""), "status": ""}
        report.append(result)
        try:
            name = row.get("TEN", "")
            if not name:
                raise ValueError("Thiếu tên.")
            tax = tax_code(row.get("MST", ""))
            nums = phones(row.get("DIEN_THOAI", ""))
            existing = None
            if tax:
                existing = session.scalars(select(Supplier).where(Supplier.tax_code == tax)).first()
            if existing is None:
                for s in session.scalars(select(Supplier)):
                    if fold(s.name) == fold(name) or (nums and set(nums) & set(s.phones or [])):
                        existing = s
                        break
            if existing:
                result.update(status="DA_CO", supplier_code=existing.code)
                continue
            note_parts = [row.get("GHI_CHU", "")]
            if row.get("NGUOI_LIEN_HE"):
                note_parts.append("Liên hệ: " + row["NGUOI_LIEN_HE"])
            if row.get("EMAIL"):
                note_parts.append("Email: " + row["EMAIL"])
            if row.get("SO_TAI_KHOAN"):
                note_parts.append("Số tài khoản: " + row["SO_TAI_KHOAN"])
            if row.get("MA_CU_FINANCE"):
                note_parts.append("Mã Finance cũ: " + row["MA_CU_FINANCE"])
            body = SupplierIn(name=name, tax_code=tax, phones=nums, address=row.get("DIA_CHI", ""), note="\n".join(p for p in note_parts if p)[:2000])
            result.update(status="TAO_MOI", tax_code=tax, phones=nums)
            if apply:
                s = inventory_catalogs.create(session, user, Supplier, body)
                result["supplier_code"] = s.code
        except (BusinessError, ValueError) as exc:
            session.rollback()
            result.update(status="LOI", message=getattr(exc, "message", str(exc)), code=getattr(exc, "code", ""))
    return report


def summary(report: list[dict]) -> dict:
    out: dict[str, int] = {}
    for r in report:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="file khách hàng đã gộp (CSV UTF-8)")
    ap.add_argument("--ncc", help="file nhà cung cấp (CSV UTF-8)")
    ap.add_argument("--user", default="", help="tên đăng nhập ghi vào nhật ký; mặc định giám đốc đầu tiên đang hoạt động")
    ap.add_argument("--apply", action="store_true", help="ghi thật; không có thì chỉ chạy thử")
    args = ap.parse_args(argv)
    if not args.csv and not args.ncc:
        ap.error("cần --csv hoặc --ncc")
    out = {"apply": args.apply}
    with Session(engine()) as session:
        if args.user:
            user = session.scalar(select(User).where(User.username == args.user.strip().lower(), User.active.is_(True)))
        else:
            user = session.scalar(select(User).where(User.active.is_(True), User.roles.contains(["director"])).order_by(User.id).limit(1))
        if user is None:
            print("Không tìm thấy tài khoản giám đốc đang hoạt động.", file=sys.stderr)
            return 1
        if args.csv:
            rep = import_customers(session, user, read_csv(args.csv), apply=args.apply)
            out["khach_hang"] = {"tong_ket": summary(rep), "dong": rep}
        if args.ncc:
            rep = import_suppliers(session, user, read_csv(args.ncc), apply=args.apply)
            out["nha_cung_cap"] = {"tong_ket": summary(rep), "dong": rep}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
