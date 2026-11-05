"""Thêm bộ danh mục thu chi chuẩn cho Thành Đức; chỉ thêm cái còn thiếu, không sửa, không xóa.

    python -m scripts.them_danh_muc_thu_chi [--user giamdoc] [--apply]

Chạy thử (không --apply) không ghi gì. Mã đã có hoặc đã có danh mục cùng tên, cùng chiều → DA_CO (bỏ qua).
Ô bảng ghép tài chính (lương, mua hàng, hoàn tiền, chi phí thợ, bán hàng, dịch vụ) đang TRỐNG thì ghép vào
danh mục tương ứng; ô đã ghép thì giữ nguyên. Không đụng phiếu thu chi, quỹ hay số tiền nào.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import finance_catalogs, finance_mappings  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import CashCategory, FinanceMapping, User  # noqa: E402
from app.schemas import CashCategoryIn  # noqa: E402
from app.text import fold  # noqa: E402

# (mã, tên, chiều, ô bảng ghép nếu có)
BO_DANH_MUC = [
    ("THU-BAN-HANG", "Thu bán hàng", "THU", "DANH_MUC_BAN_HANG"),
    ("THU-DICH-VU", "Thu dịch vụ", "THU", "DANH_MUC_DICH_VU"),
    ("THU-THANH-LY", "Thanh lý đồ cũ, phế liệu", "THU", None),
    ("THU-LAI-NH", "Lãi ngân hàng", "THU", None),
    ("THU-KHAC", "Thu khác", "THU", None),
    ("CHI-MUA-HANG", "Chi trả nhà cung cấp", "CHI", "DANH_MUC_MUA_HANG"),
    ("CHI-HOAN-TIEN", "Hoàn tiền bán hàng", "CHI", "DANH_MUC_HOAN_TIEN_BAN"),
    ("CHI-LUONG", "Chi lương, tạm ứng", "CHI", "DANH_MUC_LUONG"),
    ("CHI-PHI-THO", "Hoàn chi phí cho thợ", "CHI", "DANH_MUC_CHI_PHI_THO"),
    ("CHI-MAT-BANG", "Thuê mặt bằng", "CHI", None),
    ("CHI-DIEN-NUOC", "Điện nước", "CHI", None),
    ("CHI-INTERNET-DT", "Internet và điện thoại", "CHI", None),
    ("CHI-XANG-XE", "Xăng xe, gửi xe", "CHI", None),
    ("CHI-VAN-CHUYEN", "Vận chuyển, ship hàng", "CHI", None),
    ("CHI-VPP", "Văn phòng phẩm", "CHI", None),
    ("CHI-DUNG-CU", "Sửa chữa, mua sắm dụng cụ", "CHI", None),
    ("CHI-QUANG-CAO", "Quảng cáo, website", "CHI", None),
    ("CHI-PHI-NH", "Phí ngân hàng", "CHI", None),
    ("CHI-THUE-PHI", "Thuế, lệ phí", "CHI", None),
    ("CHI-KHAC", "Chi khác", "CHI", None),
]


def run(session: Session, user: User, *, apply: bool = False) -> dict:
    co_san = list(session.scalars(select(CashCategory)))
    ma_co = {c.code: c for c in co_san}
    ten_co = {(fold(c.name), c.direction): c for c in co_san}
    danh_muc, ghep = [], []
    chon = {}  # ô ghép → mã danh mục sẽ dùng
    for code, name, direction, key in BO_DANH_MUC:
        cu = ma_co.get(code) or ten_co.get((fold(name), direction))
        if cu is not None and cu.direction == direction:
            danh_muc.append({"ma": cu.code, "ten": cu.name, "chieu": direction, "trang_thai": "DA_CO"})
            if key and cu.active:
                chon[key] = cu.code
            continue
        if cu is not None:  # trùng mã nhưng khác chiều: không đụng
            danh_muc.append({"ma": code, "ten": name, "chieu": direction, "trang_thai": "TRUNG_MA_KHAC_CHIEU"})
            continue
        if apply:
            finance_catalogs.create(session, user, CashCategory, CashCategoryIn(code=code, name=name, direction=direction))
        danh_muc.append({"ma": code, "ten": name, "chieu": direction, "trang_thai": "THEM_MOI"})
        if key:
            chon[key] = code
    for key, code in chon.items():
        row = session.scalar(select(FinanceMapping).where(FinanceMapping.key == key))
        if row is None:
            continue
        if row.category_code:
            ghep.append({"o": finance_mappings.KEYS[key], "ma": row.category_code, "trang_thai": "GIU_NGUYEN"})
            continue
        if apply:
            finance_mappings.set_mapping(session, user, key, category_code=code, version=row.version)
        ghep.append({"o": finance_mappings.KEYS[key], "ma": code, "trang_thai": "GHEP_MOI"})
    if apply:
        session.commit()
    tong = {}
    for r in danh_muc:
        tong[r["trang_thai"]] = tong.get(r["trang_thai"], 0) + 1
    return {"apply": apply, "tong_ket": tong, "danh_muc": danh_muc, "bang_ghep": ghep}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", default="", help="tên đăng nhập; mặc định giám đốc đầu tiên đang hoạt động")
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
        print(json.dumps(run(session, user, apply=args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
