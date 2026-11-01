"""So tên sản phẩm / bài viết (bỏ dấu, mã máy, mức giống). Chỉ dùng thư viện chuẩn để app và máy đăng web cùng dùng."""
from __future__ import annotations

import html
import re
import unicodedata


def bo_dau(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def chuan_ten(s: str) -> str:
    return re.sub(r"\s+", " ", bo_dau(html.unescape(s)).lower()).strip()


def slug(s: str, toi_da: int = 80) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", bo_dau(s).lower()).strip("-")
    return s[:toi_da].rstrip("-")


# ---------- kiểm tra trùng ----------
TU_BO_QUA = {
    "may", "in", "muc", "hop", "cho", "va", "cua", "cach", "cac", "nhung", "mot", "khi", "bi", "loi", "la", "co",
    "khong", "de", "tai", "thanh", "duc", "chinh", "hang", "gia", "re", "moi", "cu", "the", "nao", "voi", "tu", "o",
}


def tu_khoa(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", chuan_ten(s)) if t not in TU_BO_QUA and len(t) > 1]


def ma_model(s: str) -> set[str]:
    """Mã máy / mã mực: từ có cả số, hoặc số từ 3 chữ số (326, lbp2900, m211dw, cf217a)."""
    return {t for t in tu_khoa(s) if any(c.isdigit() for c in t) and len(t) >= 3}


def _ty_le_tu_chung(a: str, b: str) -> float:
    ta, tb = set(tu_khoa(a)), set(tu_khoa(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def do_giong(a: str, b: str) -> float:
    """Sản phẩm, 0..1: trùng mã máy/mã mực coi như giống (0.9); không có mã thì so tỉ lệ từ chung."""
    if ma_model(a) & ma_model(b):
        return 0.9
    return _ty_le_tu_chung(a, b)


HANG_MAY = {
    "canon", "hp", "brother", "epson", "samsung", "ricoh", "xerox", "fuji", "kyocera", "lexmark", "panasonic",
    "toshiba", "sharp", "konica", "oki", "pantum", "lbp", "mf", "laserjet", "deskjet", "ecotank", "dcp", "hl",
}


def _tu_noi_dung(s: str) -> set[str]:
    """Từ nói về nội dung bài (bỏ hãng máy và mã máy)."""
    ma = ma_model(s)
    return {t for t in tu_khoa(s) if t not in HANG_MAY and t not in ma}


def do_giong_bai(a: str, b: str) -> float:
    """Bài viết: cùng máy chưa đủ (cùng máy nhưng khác lỗi), phần nội dung cũng phải giống."""
    ta, tb = _tu_noi_dung(a), _tu_noi_dung(b)
    chung = len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0.0
    return 0.5 + 0.5 * chung if ma_model(a) & ma_model(b) else chung


def cau_tim(s: str) -> str:
    ma = sorted(ma_model(s), key=len, reverse=True)
    if ma:
        return ma[0]
    return " ".join(tu_khoa(s)[:3])


def _ten(item: dict) -> str:
    t = item.get("name") or item.get("title") or ""
    if isinstance(t, dict):
        t = t.get("raw") or t.get("rendered") or ""
    return html.unescape(t)


def tim_gan_giong(ds: list[dict], ten: str, nguong: float = 0.7, ham=do_giong) -> list[dict]:
    ra = []
    for it in ds:
        diem = ham(ten, _ten(it))
        if diem >= nguong:
            ra.append({"id": it.get("id"), "ten": _ten(it), "link": it.get("permalink") or it.get("link") or "",
                       "trang_thai": it.get("status", ""), "diem": round(diem, 2)})
    return sorted(ra, key=lambda x: -x["diem"])


# ---------- so Hàng hóa trong app với sản phẩm trên web (tab Hàng hóa) ----------
NGUONG_HANG = 0.7
_TACH_MA = re.compile(r"\b([A-Za-z]{1,3})[\s-]+(\d{2,}[A-Za-z0-9]*)\b")


def gon_ma(s: str) -> str:
    """DR-2385, DR 2385 → DR2385 để so với web ghi liền (không ghép tên hãng: HP 107a giữ nguyên)."""
    return _TACH_MA.sub(lambda m: m.group(0) if m.group(1).lower() in HANG_MAY else m.group(1) + m.group(2), s or "")


# Cùng một món nhưng gọi khác nhau trên app và web (bỏ dấu). Rỗng = bỏ qua từ đó.
DONG_NGHIA = {"trong": "drum", "cum": "", "toner": "", "cartridge": "", "catridge": "", "say": "fuser", "lo": "", "gom": ""}


def _noi_dung_hang(s: str) -> set[str]:
    return {DONG_NGHIA.get(t, t) for t in _tu_noi_dung(s)} - {""}


def diem_hang(a: str, b: str) -> float:
    """Chặt hơn do_giong: cùng mã máy nhưng phần còn lại khác hẳn (khay giấy M402 và bao lụa M402) thì không tính."""
    a, b = gon_ma(a), gon_ma(b)
    if ma_model(a) & ma_model(b):
        ta, tb = _noi_dung_hang(a), _noi_dung_hang(b)
        if not ta or not tb or len(ta & tb) / min(len(ta), len(tb)) >= 0.5:
            return 0.9
        return 0.6
    return _ty_le_tu_chung(a, b) if min(len(tu_khoa(a)), len(tu_khoa(b))) >= 2 else 0.0


def so_khop_hang(hang: list[tuple[str, str]], web: list[dict], toi_da: int = 3) -> dict[str, dict]:
    """hang: (mã TD, tên trong app). web: {id, ten, sku, ...}.
    Trả {mã: {"kieu": "sku" | "giong", "web": [...]}}; mã không có trong kết quả là chưa thấy trên web."""
    ma_app = {c.upper() for c, _ in hang}
    theo_sku: dict[str, list[dict]] = {}
    tu_do: list[dict] = []  # sản phẩm web chưa gắn mã TD nào của app
    for w in web:
        sku = str(w.get("sku") or "").strip().upper()
        if sku in ma_app:
            theo_sku.setdefault(sku, []).append(w)
        else:
            tu_do.append(w)
    chi_so: dict[str, list[int]] = {}
    for i, w in enumerate(tu_do):
        for m in ma_model(gon_ma(w.get("ten", ""))):
            chi_so.setdefault(m, []).append(i)
    ra: dict[str, dict] = {}
    for code, ten in hang:
        code = code.upper()
        if code in theo_sku:
            ra[code] = {"kieu": "sku", "web": [dict(w, diem=1.0) for w in theo_sku[code]]}
            continue
        ma = ma_model(gon_ma(ten))
        ung_vien = sorted({i for m in ma for i in chi_so.get(m, [])}) or range(len(tu_do))
        giong = []
        for i in ung_vien:
            d = diem_hang(ten, tu_do[i].get("ten", ""))
            if d >= NGUONG_HANG:
                giong.append(dict(tu_do[i], diem=round(d, 2)))
        if giong:
            giong.sort(key=lambda x: -x["diem"])
            ra[code] = {"kieu": "giong", "web": giong[:toi_da]}
    return ra
