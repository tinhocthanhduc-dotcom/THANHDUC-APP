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

LOAI = ("san_pham", "bai_viet", "mang_xa_hoi")
KENH_MXH = ("facebook", "zalo", "google")  # bài mạng xã hội: anh đăng tay, bấm Đã đăng từng kênh
CACH_VIET = {
    "tu_dong": "AI tự động",
    "chatgpt_web": "ChatGPT web (dán tay)",
    "codex": "Codex trên máy chủ (gói ChatGPT, không tốn API)",
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


def file_ghi_chu_chu_de(goc) -> Path:
    return Path(goc) / "chu-de-ghi-chu.json"


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


CODEX_SONG_GIAY = 30 * 60  # Codex trên Z2 hỏi việc mỗi ~30 giây; quá 30 phút không thấy thì coi như tắt
CODEX_CHO_PHUT = 60  # việc Codex quá lâu không ai làm thì chuyển sang AI tự động (API) nếu có khóa
CODEX_CHAM_GIAY = 3 * 60  # quá 3 phút chưa hỏi việc: bảng sức khỏe báo "chậm" (thường là đang làm một việc dài)


def ghi_nhip_codex(goc, **them) -> None:
    """Mỗi lần Codex gọi cửa AI. them: dang_nhap (True/False), ban (phiên bản codex), loi (lỗi gần nhất)."""
    p = thu_muc_viec(goc) / "_nhip_codex.json"
    d = doc_json(p, {})
    d = d if isinstance(d, dict) else {}
    luc = _luc()
    d["luc"] = luc
    if "dang_nhap" in them:
        d["dang_nhap"] = them["dang_nhap"]
        d["kiem_luc"] = luc
    if them.get("ban"):
        d["ban"] = str(them["ban"])[:60]
    if them.get("loi"):
        d["loi"] = {"luc": luc, "ly_do": str(them["loi"])[:300]}
    ghi_json(p, d)


def suc_khoe_codex(goc) -> dict:
    """Bảng sức khỏe Codex cho màn Đăng web: còn chạy không, còn đăng nhập ChatGPT không, việc chờ, lỗi gần nhất."""
    d = doc_json(thu_muc_viec(goc) / "_nhip_codex.json", None)
    d = d if isinstance(d, dict) else {}
    ra = {"co": bool(d.get("luc")), "dang_nhap": d.get("dang_nhap"), "ban": d.get("ban", ""), "loi": d.get("loi") or None,
          "song": codex_song(goc), "giay": None, "luc": ""}
    try:
        luc = datetime.fromisoformat(d["luc"])
        ra["giay"] = int((bay_gio() - luc).total_seconds())
        ra["luc"] = luc.strftime("%H:%M %d/%m")
    except (KeyError, TypeError, ValueError):
        pass
    ra["cham"] = ra["song"] and ra["giay"] is not None and ra["giay"] > CODEX_CHAM_GIAY
    ra["cho_viec"] = len(viec_cho_codex(goc))
    try:
        from . import ve_anh
        ra["cho_ve"] = sum(1 for x in ve_anh.ds(goc) if x.get("trang_thai") in ve_anh.DANG_CHAY)
    except Exception:  # noqa: BLE001 — bảng sức khỏe không được làm hỏng màn
        ra["cho_ve"] = 0
    if ra["loi"]:
        try:
            ra["loi"]["gio"] = datetime.fromisoformat(ra["loi"]["luc"]).strftime("%H:%M %d/%m")
        except (KeyError, TypeError, ValueError):
            ra["loi"]["gio"] = ""
    return ra


def codex_song(goc) -> bool:
    d = doc_json(thu_muc_viec(goc) / "_nhip_codex.json", None)
    try:
        return bool(d) and (bay_gio() - datetime.fromisoformat(d["luc"])).total_seconds() < CODEX_SONG_GIAY
    except (KeyError, TypeError, ValueError):
        return False


def viec_cho_codex(goc) -> list[dict]:
    """Việc đang chờ câu trả lời và được giao cho Codex (cũ trước)."""
    ra = [v for v in ds(goc) if v.get("trang_thai") == CHO_DAN and v.get("cach_viet") == "codex" and v.get("lenh_chatgpt")]
    return sorted(ra, key=lambda v: v.get("cap_nhat", ""))


def chuyen_codex_qua_han(goc, co_api: bool) -> int:
    """Codex tắt hoặc kẹt: việc chờ Codex quá CODEX_CHO_PHUT chuyển sang AI tự động (có khóa API)
    hoặc sang dán tay ChatGPT web (không có khóa). Không bao giờ mất việc."""
    if codex_song(goc):
        return 0
    han = bay_gio() - timedelta(minutes=CODEX_CHO_PHUT)
    so = 0
    with khoa(goc):
        for v in ds(goc):
            if v.get("trang_thai") != CHO_DAN or v.get("cach_viet") != "codex":
                continue
            try:
                if datetime.fromisoformat(v.get("cap_nhat", "")) > han:
                    continue
            except ValueError:
                pass
            if co_api:
                v.update(cach_viet="tu_dong", trang_thai=CHO_SOAN, lenh_chatgpt="", file_tra_loi="",
                         loi="Codex trên máy chủ không trả lời; chuyển sang AI tự động (API).")
            else:
                v.update(cach_viet="chatgpt_web", loi="Codex trên máy chủ không trả lời. Dán tay câu lệnh vào ChatGPT web.")
            v["cap_nhat"] = _luc()
            ghi_json(_file(goc, v["id"]), v)
            so += 1
    return so


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


# Lấy lại đúng 1 sản phẩm / bài viết khi anh mở màn Sửa ảnh hoặc Sửa bằng AI (TUẤN duyệt đợt 1 ngày 02/11/2026).
# Danh sách Trên web vẫn chỉ làm mới cả loạt khi anh bấm "Cập nhật từ web".
LOAI_MOT = ("san_pham", "bai_viet")
MOI_GIAY = 120  # vừa lấy trong 2 phút thì không lấy lại (tránh mở lại màn là lấy liên tục)


def _file_mot(goc, loai: str, web_id: int) -> Path:
    if loai not in LOAI_MOT:
        raise ValueError("loai")
    return thu_muc_viec(goc) / "_lam-moi-mot" / f"{loai}-{int(web_id)}"


def yeu_cau_lam_moi_mot(goc, loai: str, web_id: int) -> None:
    p = _file_mot(goc, loai, web_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_luc())


def dang_cho_lam_moi_mot(goc, loai: str, web_id: int) -> bool:
    try:
        return _file_mot(goc, loai, web_id).exists()
    except (ValueError, TypeError):
        return False


def lay_yeu_cau_lam_moi_mot(goc) -> list[tuple[str, int]]:
    """Máy đăng web lấy các yêu cầu (cũ trước). File chỉ xóa sau khi làm xong (xong_lam_moi_mot) để màn biết đang chờ."""
    d = thu_muc_viec(goc) / "_lam-moi-mot"
    if not d.exists():
        return []
    ra = []
    for p in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime):
        loai, _, so = p.name.rpartition("-")
        if loai in LOAI_MOT and so.isdigit():
            ra.append((loai, int(so)))
        else:
            p.unlink(missing_ok=True)
    return ra


def xong_lam_moi_mot(goc, loai: str, web_id: int) -> None:
    _file_mot(goc, loai, web_id).unlink(missing_ok=True)


def moi_lay(it: dict) -> bool:
    """Mục này vừa được lấy lại từ web trong MOI_GIAY giây."""
    try:
        return (bay_gio() - datetime.fromisoformat(str(it.get("lay_luc") or ""))).total_seconds() < MOI_GIAY
    except ValueError:
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


# ---------------- ghép mã TD cho sản phẩm trên web (TUẤN duyệt mẫu D6/6, D7 ngày 02/11/2026) ----------------
# Ghép = xác nhận "Đúng là nó" trong xac-nhan-web.json (mã → web_id), mỗi mã chỉ một sản phẩm web và ngược lại.
# "Không có trong app" lưu riêng theo web_id trong bo-ghep.json.
def file_bo_ghep(goc) -> Path:
    return Path(goc) / "bo-ghep.json"


def doc_bo_ghep(goc) -> set[int]:
    d = doc_json(file_bo_ghep(goc), {})
    return {int(k) for k in (d if isinstance(d, dict) else {}) if str(k).isdigit()}


def dat_bo_ghep(goc, web_id: int, bo: bool) -> None:
    with khoa(goc):
        d = doc_json(file_bo_ghep(goc), {})
        d = d if isinstance(d, dict) else {}
        if bo:
            d[str(int(web_id))] = _luc()
        else:
            d.pop(str(int(web_id)), None)
        ghi_json(file_bo_ghep(goc), d)


def ghep_ma(goc, ma: str, web_id: int) -> None:
    """Ghép mã TD với sản phẩm web: bỏ mã cũ đang ghép vào sản phẩm này (anh chọn lại), rồi ghi mã mới."""
    with khoa(goc):
        d = doc_xac_nhan(goc)
        for code, x in list(d.items()):
            if code != ma and isinstance(x, dict) and str(x.get("dung", "")) == str(int(web_id)):
                d.pop(code)
        d[ma] = {"dung": int(web_id), "luc": _luc()}
        ghi_json(file_xac_nhan(goc), d)


def bo_ghep_web(goc, web_id: int) -> int:
    """Bỏ mọi mã đang ghép vào sản phẩm web này. Trả số mã đã bỏ."""
    with khoa(goc):
        d = doc_xac_nhan(goc)
        bo = [c for c, x in d.items() if isinstance(x, dict) and str(x.get("dung", "")) == str(int(web_id))]
        for c in bo:
            d.pop(c)
        if bo:
            ghi_json(file_xac_nhan(goc), d)
        return len(bo)


# TUẤN 08/11 (Đợt 4 rút gọn): bài viết chuyển sang app Dịch vụ SEO, lịch chỉ còn soạn sản phẩm (so_bai bỏ).
LICH_MAC_DINH = {"bat": False, "gio": "07:00", "so_sp": 2, "lan_cuoi": ""}


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
        gc = doc_ghi_chu_chu_de(goc)
        if gc.pop(chu_de.strip(), None) is not None:
            ghi_json(file_ghi_chu_chu_de(goc), gc)


# TUẤN 07/11: mỗi chủ đề có "Ghi chú thật" (việc đã làm ở phường nào, máy gì...) để bài không giống nhau.
def doc_ghi_chu_chu_de(goc) -> dict[str, str]:
    d = doc_json(file_ghi_chu_chu_de(goc), {})
    return {str(k): str(v) for k, v in d.items() if str(v).strip()} if isinstance(d, dict) else {}


def ghi_ghi_chu_chu_de(goc, chu_de: str, ghi_chu: str) -> None:
    chu_de, ghi_chu = " ".join(chu_de.split()), ghi_chu.strip()[:1000]
    if not chu_de:
        return
    with khoa(goc):
        d = doc_ghi_chu_chu_de(goc)
        if ghi_chu:
            d[chu_de] = ghi_chu
        else:
            d.pop(chu_de, None)
        ghi_json(file_ghi_chu_chu_de(goc), d)


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


def ma_chua_len_web(goc, hang: list[tuple[str, str]]) -> list[str]:
    """Mã (giữ thứ tự) chưa có trên web, cùng cách màn Đăng web xếp "Chưa có": bỏ mã app đã đăng, mã đã ghép
    (Đúng là nó), mã trùng SKU, và mã có sản phẩm web gần giống chưa bị anh bấm "Không phải".
    Chưa có danh sách web (chưa bấm Cập nhật từ web) thì chỉ bỏ mã app đã đăng."""
    from .so_ten import so_khop_hang

    da_dang = doc_trang_thai(goc)["san_pham"]
    hang = [(c.upper(), t) for c, t in hang if c.upper() not in da_dang]
    web = doc_json(file_sp_web(goc), {})
    ds_web = [w for w in (web.get("ds") or []) if isinstance(w, dict) and w.get("id")] if isinstance(web, dict) else []
    if not ds_web:
        return [c for c, _ in hang]
    xac = doc_xac_nhan(goc)
    khop = so_khop_hang(hang, ds_web)
    ra = []
    for code, _ in hang:
        x = xac.get(code) if isinstance(xac.get(code), dict) else {}
        if x.get("dung"):
            continue
        k = khop.get(code) or {}
        if k.get("kieu") == "sku":
            continue
        khong = set(x.get("khong") or [])
        if any(w.get("id") not in khong for w in k.get("web") or []):
            continue
        ra.append(code)
    return ra


def co_anh_goc(goc, code: str) -> bool:
    try:
        return code.upper() in ma_co_anh(goc)
    except OSError:
        return False
