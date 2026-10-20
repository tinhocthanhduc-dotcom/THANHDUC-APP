"""Cấp số chứng từ theo loại và năm, ví dụ PN-26-00001. Không trùng, không nhảy số."""
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from .errors import BusinessError

PREFIXES = {
    "CXL": "Hồ sơ chờ xử lý",
    "PN": "Phiếu nhập kho",
    "PX": "Phiếu xuất kho",
    "CK": "Phiếu chuyển kho",
    "KK": "Phiếu kiểm kê",
    "BG": "Báo giá",
    "DB": "Đơn bán",
    "GH": "Phiếu giao hàng",
    "DM": "Đơn mua",
    "TH": "Phiếu trả hàng",
    "HT": "Đề nghị hoàn tiền",
    "TN": "Trả nhà cung cấp",
    "BT": "Báo thu",
    "DV": "Phiếu dịch vụ",
    "TK": "Thợ trả hàng",
    "YC": "Yêu cầu linh kiện",
    "PT": "Phiếu thu",
    "PC": "Phiếu chi",
    "CQ": "Chuyển quỹ",
}


def lock_counter(session: Session, prefix: str, doc_date: date) -> None:
    """Khóa bộ đếm trước các khóa nghiệp vụ, chưa cấp hoặc tiêu tốn số.

    Luồng gộp nhiều thao tác phải khóa theo cùng thứ tự với luồng thông thường.
    Khởi tạo ở 0 vẫn để next_number cấp số 1 và được rollback cùng giao dịch.
    """
    if prefix not in PREFIXES:
        raise BusinessError("UNKNOWN_DOC_TYPE", "Loại chứng từ không tồn tại.", {"prefix": prefix})
    params = {"p": prefix, "y": doc_date.year}
    session.execute(text(
        "insert into doc_counters (prefix, year, last) values (:p, :y, 0) "
        "on conflict (prefix, year) do nothing"), params)
    session.execute(text(
        "select last from doc_counters where prefix = :p and year = :y for update"), params)


def next_number(session: Session, prefix: str, doc_date: date) -> str:
    """Tăng bộ đếm trong transaction của người gọi.

    Dòng bộ đếm bị khóa tới khi người gọi commit, nên hai người lập phiếu cùng lúc phải chờ nhau
    và nhận hai số liền nhau. Nếu người gọi rollback thì số cũng được trả lại, không bị nhảy số.
    Không commit ở đây: số chỉ có giá trị khi chứng từ dùng nó được lưu cùng lúc.
    """
    if prefix not in PREFIXES:
        raise BusinessError("UNKNOWN_DOC_TYPE", "Loại chứng từ không tồn tại.", {"prefix": prefix})
    n = session.execute(
        text(
            "insert into doc_counters (prefix, year, last) values (:p, :y, 1) "
            "on conflict (prefix, year) do update set last = doc_counters.last + 1 returning last"
        ),
        {"p": prefix, "y": doc_date.year},
    ).scalar_one()
    return f"{prefix}-{doc_date.year % 100:02d}-{n:05d}"
