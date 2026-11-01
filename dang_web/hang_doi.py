"""Hàng việc dùng chung giữa app (màn Đăng web) và máy đăng web (container dang-web-may).

Chỉ dùng thư viện chuẩn, để app import được mà không cần thư viện ảnh hay AI.
Mỗi việc là một thư mục <gốc>/viec/<id>/ chứa viec.json và ảnh gốc anh-1.jpg, anh-2.jpg…
App chỉ đổi việc đang chờ người (NGUOI_LAM) sang trạng thái máy làm (MAY_LAM) và ngược lại;
máy đăng web chỉ nhận việc ở MAY_LAM. Mọi lần đọc-sửa-ghi đều khóa chung một file.
"""
from __future__ import annotations

import fcntl
import json
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

GIO_VN = timezone(timedelta(hours=7))

CHO_SOAN = "cho_soan"
DANG_SOAN = "dang_soan"
CO_THE_TRUNG = "co_the_trung"
CHO_DAN = "cho_dan"
XEM_TRUOC = "xem_truoc"
CHO_DANG = "cho_dang"
DANG_DANG = "dang_dang"
DA_DANG = "da_dang"
LOI = "loi"
BO = "bo"

MAY_LAM = (CHO_SOAN, CHO_DANG)
DANG_CHAY = (CHO_SOAN, DANG_SOAN, CHO_DANG, DANG_DANG)
NGUOI_LAM = (CO_THE_TRUNG, CHO_DAN, XEM_TRUOC, LOI)
XONG = (DA_DANG, BO)

NHAN = {
    CHO_SOAN: "Chờ soạn",
    DANG_SOAN: "Đang soạn",
    CO_THE_TRUNG: "Có thể trùng",
    CHO_DAN: "Chờ dán ChatGPT",
    XEM_TRUOC: "Xem trước",
    CHO_DANG: "Chờ đăng",
    DANG_DANG: "Đang đăng",
    DA_DANG: "Đã đăng nháp",
    LOI: "Lỗi",
    BO: "Đã bỏ",
}

LOAI = ("san_pham", "bai_viet")
CACH_VIET = {
    "tu_dong": "AI tự động",
    "chatgpt_web": "ChatGPT web (dán tay)",
    "mau": "Mẫu có sẵn, không AI",
}

ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
TEN_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,150}$")
NHIP_SONG_GIAY = 120  # quá thời gian này không thấy nhịp thì coi như máy đăng web đang tắt


def bay_gio() -> datetime:
    return datetime.now(GIO_VN)


def _luc() -> str:
    return bay_gio().isoformat(timespec="seconds")


def thu_muc_viec(goc) -> Path:
    return Path(goc) / "viec"


def file_trang_thai(goc) -> Path:
    return Path(goc) / "da-dang.json"


def file_web(goc) -> Path:
    """Trạng thái bài trên web (Nháp/Đang bán, tên, link) máy đăng web lấy về lần gần nhất."""
    return Path(goc) / "trang-thai-web.json"


def file_sp_web(goc) -> Path:
    """Toàn bộ sản phẩm trên web (id, tên, mã SKU, trạng thái, link) lấy về lần anh bấm Cập nhật từ web gần nhất."""
    return Path(goc) / "san-pham-web.json"


def file_bai_web(goc) -> Path:
    """Toàn bộ bài viết trên web và chuyên mục, lấy về lần anh bấm Cập nhật từ web gần nhất."""
    return Path(goc) / "bai-web.json"


def file_xac_nhan(goc) -> Path:
    """Anh xác nhận ở tab Hàng hóa: {mã: {"dung": id web} hoặc {"khong": [id web, ...]}}."""
    return Path(goc) / "xac-nhan-web.json"


def file_danh_muc(goc) -> Path:
    return Path(goc) / "danh-muc-web.json"


def file_lich(goc) -> Path:
    return Path(goc) / "lich.json"


def file_chu_de(goc) -> Path:
    return Path(goc) / "chu-de.txt"


def doc_json(path: Path, mac_dinh):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, UnicodeDecodeError):
        return mac_dinh


def ghi_json(path: Path, d) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(3)}.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


@contextmanager
def khoa(goc):
    d = thu_muc_viec(goc)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".khoa", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def kiem_ghi_duoc(goc) -> str | None:
    """None nếu ghi được vào thư mục làm việc; không thì câu báo lỗi dễ hiểu."""
    try:
        d = thu_muc_viec(goc)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f".thu-{secrets.token_hex(3)}"
        p.write_text("ok")
        p.unlink()
        return None
    except OSError as e:
        return f"Chưa ghi được vào thư mục làm việc ({e.strerror or e})."


# ---------------- việc ----------------
def _file(goc, viec_id: str) -> Path:
    if not ID_RE.match(viec_id or ""):
        raise ValueError("Mã việc không hợp lệ")
    return thu_muc_viec(goc) / viec_id / "viec.json"


def thu_muc_mot_viec(goc, viec_id: str) -> Path:
    return _file(goc, viec_id).parent


def tao_viec(goc, loai: str, du_lieu: dict, anh: list[tuple[str, bytes]] | None = None) -> str:
    if loai not in LOAI:
        raise ValueError("Loại việc không hợp lệ")
    viec_id = f"{bay_gio():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    d = thu_muc_viec(goc) / viec_id
    d.mkdir(parents=True)
    ten_anh = []
    for i, (ten, data) in enumerate(anh or [], 1):
        duoi = Path(ten).suffix.lower().lstrip(".")
        duoi = duoi if duoi in ("jpg", "jpeg", "png", "webp", "heic", "heif") else "jpg"
        f = f"anh-{i}.{duoi}"
        (d / f).write_bytes(data)
        ten_anh.append(f)
    v = {
        "cach_viet": "tu_dong",
        "ghi_chu": "",
        "van_dang": False,
        "tach_nen": True,
        "wp_id": None,
        "nguon": "app",
        **du_lieu,
        "id": viec_id,
        "loai": loai,
        "anh_goc": ten_anh,
        "trang_thai": CHO_SOAN,
        "tao_luc": _luc(),
        "cap_nhat": _luc(),
    }
    with khoa(goc):
        ghi_json(d / "viec.json", v)
    return viec_id


def doc(goc, viec_id: str) -> dict | None:
    try:
        p = _file(goc, viec_id)
    except ValueError:
        return None
    return doc_json(p, None)


def ds(goc) -> list[dict]:
    d = thu_muc_viec(goc)
    if not d.exists():
        return []
    ra = []
    for p in d.iterdir():
        if p.is_dir() and ID_RE.match(p.name):
            v = doc_json(p / "viec.json", None)
            if v:
                ra.append(v)
    return sorted(ra, key=lambda v: v.get("tao_luc", ""), reverse=True)


def sua(goc, viec_id: str, ham, chi_khi: tuple[str, ...] | None = None) -> dict | None:
    """Đọc-sửa-ghi có khóa. chi_khi: chỉ sửa khi việc đang ở một trong các trạng thái này; không thì None."""
    p = _file(goc, viec_id)
    with khoa(goc):
        v = doc_json(p, None)
        if v is None or (chi_khi and v.get("trang_thai") not in chi_khi):
            return None
        ham(v)
        v["cap_nhat"] = _luc()
        ghi_json(p, v)
        return v


def nhan_viec(goc) -> dict | None:
    """Máy đăng web lấy việc cũ nhất đang chờ máy, chuyển sang đang làm."""
    with khoa(goc):
        cho = [v for v in ds(goc) if v.get("trang_thai") in MAY_LAM]
        if not cho:
            return None
        v = min(cho, key=lambda x: x.get("cap_nhat", ""))
        v["trang_thai"] = DANG_SOAN if v["trang_thai"] == CHO_SOAN else DANG_DANG
        v["cap_nhat"] = _luc()
        ghi_json(_file(goc, v["id"]), v)
        return v


def phuc_hoi(goc) -> int:
    """Lúc máy đăng web khởi động: việc bị dừng giữa chừng. Đang soạn thì soạn lại;
    đang đăng thì báo lỗi để anh xem web trước (có thể đã tạo bài rồi)."""
    so = 0
    with khoa(goc):
        for v in ds(goc):
            if v.get("trang_thai") == DANG_SOAN:
                v["trang_thai"] = CHO_SOAN
            elif v.get("trang_thai") == DANG_DANG:
                v["trang_thai"] = LOI
                v["buoc_loi"] = "dang"
                v["loi"] = "Máy đăng web bị dừng giữa lúc đăng. Mở tab Đã đăng hoặc wp-admin xem đã có bài chưa rồi mới thử lại."
            else:
                continue
            v["cap_nhat"] = _luc()
            ghi_json(_file(goc, v["id"]), v)
            so += 1
    return so


# ---------------- trạng thái đã đăng (da-dang.json) ----------------
def doc_trang_thai(goc) -> dict:
    d = doc_json(file_trang_thai(goc), {})
    if not isinstance(d, dict):
        d = {}
    for k in ("san_pham", "bai_viet", "nghi_trung"):
        d.setdefault(k, {})
    return d


def sua_trang_thai(goc, ham) -> dict:
    with khoa(goc):
        d = doc_trang_thai(goc)
        ham(d)
        ghi_json(file_trang_thai(goc), d)
        return d


# ---------------- nhịp sống, yêu cầu làm mới, lịch ----------------
def ghi_nhip(goc, thong_tin: dict) -> None:
    ghi_json(thu_muc_viec(goc) / "_nhip.json", {**thong_tin, "luc": _luc()})


def doc_nhip(goc) -> dict | None:
    """Nhịp gần nhất của máy đăng web, kèm khóa 'song' (True nếu vừa thấy)."""
    d = doc_json(thu_muc_viec(goc) / "_nhip.json", None)
    if not isinstance(d, dict):
        return None
    try:
        luc = datetime.fromisoformat(d["luc"])
        d["song"] = (bay_gio() - luc).total_seconds() < NHIP_SONG_GIAY
    except (KeyError, ValueError, TypeError):
        d["song"] = False
    return d


def yeu_cau_lam_moi(goc) -> None:
    p = thu_muc_viec(goc) / "_lam-moi"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_luc())


def dang_cho_lam_moi(goc) -> bool:
    return (thu_muc_viec(goc) / "_lam-moi").exists()


def lay_yeu_cau_lam_moi(goc) -> bool:
    p = thu_muc_viec(goc) / "_lam-moi"
    if p.exists():
        p.unlink(missing_ok=True)
        return True
    return False


def doc_xac_nhan(goc) -> dict:
    d = doc_json(file_xac_nhan(goc), {})
    return d if isinstance(d, dict) else {}


def xac_nhan(goc, ma: str, web_id: int, dung: bool) -> None:
    """Đúng là nó: mã này đã có trên web (sản phẩm web_id). Không phải: bỏ web_id khỏi danh sách gần giống của mã."""
    with khoa(goc):
        d = doc_xac_nhan(goc)
        x = d.get(ma) if isinstance(d.get(ma), dict) else {}
        if dung:
            x = {"dung": int(web_id), "luc": _luc()}
        else:
            khong = [int(i) for i in x.get("khong", []) if str(i).isdigit()]
            x = {"khong": sorted(set(khong) | {int(web_id)}), "luc": _luc()}
        d[ma] = x
        ghi_json(file_xac_nhan(goc), d)


def bo_xac_nhan(goc, ma: str) -> None:
    with khoa(goc):
        d = doc_xac_nhan(goc)
        if d.pop(ma, None) is not None:
            ghi_json(file_xac_nhan(goc), d)


LICH_MAC_DINH = {"bat": False, "gio": "07:00", "so_sp": 2, "so_bai": 1, "lan_cuoi": ""}


def doc_lich(goc) -> dict:
    d = doc_json(file_lich(goc), {})
    return {**LICH_MAC_DINH, **(d if isinstance(d, dict) else {})}


def ghi_lich(goc, d: dict) -> None:
    with khoa(goc):
        ghi_json(file_lich(goc), {**doc_lich(goc), **d})


# ---------------- chủ đề bài viết ----------------
def doc_chu_de(goc) -> list[str]:
    try:
        dong = file_chu_de(goc).read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, UnicodeDecodeError):
        return []
    return [x.strip() for x in dong if x.strip() and not x.strip().startswith("#")]


def them_chu_de(goc, chu_de: str) -> None:
    chu_de = " ".join(chu_de.split())
    if not chu_de:
        return
    with khoa(goc):
        if chu_de in doc_chu_de(goc):
            return
        p = file_chu_de(goc)
        cu = p.read_text(encoding="utf-8") if p.exists() else ""
        if cu and not cu.endswith("\n"):
            cu += "\n"
        p.write_text(cu + chu_de + "\n", encoding="utf-8")


def xoa_chu_de(goc, chu_de: str) -> None:
    with khoa(goc):
        p = file_chu_de(goc)
        if not p.exists():
            return
        dong = p.read_text(encoding="utf-8").splitlines()
        p.write_text("".join(x + "\n" for x in dong if x.strip() != chu_de.strip()), encoding="utf-8")


# ---------------- ảnh bỏ sẵn trong anh-goc ----------------
DUOI_ANH = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")
MA_RE = re.compile(r"^(TD-\d{4,})", re.I)


def ma_co_anh(goc) -> set[str]:
    """Các mã TD có ảnh trong anh-goc (TD-0123.jpg, TD-0123-2.jpg hoặc thư mục TD-0123/)."""
    d = Path(goc) / "anh-goc"
    ra: set[str] = set()
    if not d.exists():
        return ra
    for p in d.iterdir():
        m = MA_RE.match(p.name if p.is_dir() else p.stem)
        if m and (p.is_dir() or p.suffix.lower() in DUOI_ANH):
            ra.add(m.group(1).upper())
    return ra


def co_anh_goc(goc, code: str) -> bool:
    try:
        return code.upper() in ma_co_anh(goc)
    except OSError:
        return False
