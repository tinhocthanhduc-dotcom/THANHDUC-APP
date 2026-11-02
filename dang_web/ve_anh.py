"""Vẽ ảnh minh họa cho bài viết bằng Codex trên Z2 (TUẤN duyệt mẫu D4/4, D5/5 ngày 02/11/2026).

App ghi yêu cầu vào thư mục ve-anh/<id>/yeu-cau.json; Codex (scripts/codex_dang_web.py) hỏi yêu cầu qua cửa
/ai/dang-web/ve-anh, vẽ bằng công cụ image_gen của gói ChatGPT (không tốn phí API) rồi gửi ảnh về.
Chỉ dùng cho bài viết: ảnh sản phẩm phải là ảnh thật. Chỉ thư viện chuẩn + Pillow khi nhận ảnh.
"""
from __future__ import annotations

import io
import re
import secrets
from datetime import datetime
from html import unescape
from pathlib import Path

from . import hang_doi as hd

CHO = "cho"
DANG_VE = "dang_ve"
XONG = "xong"
LOI = "loi"
DANG_CHAY = (CHO, DANG_VE)
SO_ANH = 3  # TUẤN 02/11: mỗi lần vẽ 3 ảnh để chọn
TOI_DA_BYTE = 20 * 1024 * 1024
CANH_TOI_DA = 1800
KET_PHUT = 30  # đang vẽ quá lâu (Codex tắt giữa chừng) thì báo lỗi để anh vẽ lại
TEN_ANH = ("anh-1.jpg", "anh-2.jpg", "anh-3.jpg", "anh-4.jpg")


def thu_muc(goc) -> Path:
    return Path(goc) / "ve-anh"


def _file(goc, yc_id: str) -> Path:
    if not hd.ID_RE.match(yc_id or ""):
        raise ValueError("Mã yêu cầu không hợp lệ")
    return thu_muc(goc) / yc_id / "yeu-cau.json"


def file_anh(goc, yc_id: str, ten: str) -> Path | None:
    if ten not in TEN_ANH:
        return None
    try:
        return _file(goc, yc_id).parent / ten
    except ValueError:
        return None


def _chu(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html or "")).split())


def dan_y_bai(html: str, toi_da: int = 1200) -> str:
    """Dàn ý bài để Codex vẽ sát nội dung (TUẤN 02/11): các tiêu đề H1–H3 theo thứ tự + đoạn mở bài."""
    dong = []
    for cap, trong in re.findall(r"<h([1-3])[^>]*>(.*?)</h\1>", html or "", re.I | re.S):
        chu = _chu(trong)
        if chu:
            dong.append(("  " * max(int(cap) - 2, 0)) + f"H{cap}: {chu[:150]}")
    mo = re.search(r"<p[^>]*>(.*?)</p>", html or "", re.I | re.S)
    if mo and _chu(mo.group(1)):
        dong.append(f"Mở bài: {_chu(mo.group(1))[:300]}")
    return "\n".join(dong)[:toi_da]


def lenh_ve(chu_de: str, tom_tat: str = "", dan: str = "", so: int = SO_ANH, dan_y: str = "") -> str:
    phan = [f"Dùng công cụ tạo ảnh image_gen vẽ {so} ảnh minh họa KHÁC NHAU, khung ngang 16:9, cho một bài viết trên website "
            "của cửa hàng máy in, mực in, sửa máy in Thành Đức ở TP. Hồ Chí Minh.",
            f"Tiêu đề bài viết: {chu_de.strip()[:200]}."]
    if tom_tat.strip():
        phan.append(f"Tóm tắt bài: {' '.join(tom_tat.split())[:500]}")
    if dan_y.strip():
        phan.append("Dàn ý bài (các tiêu đề H1, H2, H3 theo thứ tự):\n" + dan_y.strip()[:1200])
        phan.append(f"Đọc kỹ dàn ý rồi vẽ đúng nội dung bài, không vẽ chung chung: ảnh 1 thể hiện ý chính của tiêu đề bài; "
                    f"các ảnh còn lại mỗi ảnh minh họa một mục H2 khác nhau (chọn {max(so - 1, 1)} mục tiêu biểu nhất). "
                    "Vẽ đúng loại máy, linh kiện, thao tác hoặc tình huống mà mục đó nói tới (ví dụ máy in laser hay "
                    "phun màu, hộp mực, trục từ, kẹt giấy, nạp mực, thợ sửa tại văn phòng khách).")
    else:
        phan.append("Mỗi ảnh vẽ đúng loại máy, linh kiện, thao tác hoặc tình huống mà tiêu đề nói tới, không vẽ chung chung.")
    if dan.strip():
        phan.append(f"Yêu cầu thêm: {' '.join(dan.split())[:300]}")
    phan.append("Phong cách ảnh chụp thật, ánh sáng tự nhiên, văn phòng hoặc xưởng sửa chữa ở Việt Nam, người trong ảnh là người "
                "Việt Nam. Không có chữ, số, logo, tên hãng hay watermark trong ảnh.")
    phan.append("Lưu từng ảnh vào thư mục hiện tại với tên " + ", ".join(f"anh-{i}.png" for i in range(1, so + 1))
                + ". Không tạo hay sửa file nào khác, không chạy lệnh nào khác.")
    return "\n".join(phan)


def tao(goc, *, chu_de: str, wp_id: int | None = None, viec_id: str = "", tom_tat: str = "", dan: str = "", dan_y: str = "") -> str:
    yc_id = f"{hd.bay_gio():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    luc = hd.bay_gio().isoformat(timespec="seconds")
    d = {"id": yc_id, "loai": "bai_viet", "wp_id": wp_id, "viec_id": viec_id, "chu_de": chu_de[:200], "dan": dan[:300],
         "lenh": lenh_ve(chu_de, tom_tat, dan, dan_y=dan_y), "so_anh": SO_ANH, "trang_thai": CHO, "anh": [], "loi": "", "tao_luc": luc,
         "cap_nhat": luc}
    p = _file(goc, yc_id)
    p.parent.mkdir(parents=True)
    with hd.khoa(goc):
        hd.ghi_json(p, d)
    return yc_id


def doc(goc, yc_id: str) -> dict | None:
    try:
        d = hd.doc_json(_file(goc, yc_id), None)
    except ValueError:
        return None
    if d and d.get("trang_thai") == DANG_VE and _qua_han(d):
        d = dict(d, trang_thai=LOI, loi="Codex trên máy chủ không gửi ảnh về. Bấm vẽ lại.")
    return d


def _qua_han(d: dict) -> bool:
    try:
        return (hd.bay_gio() - datetime.fromisoformat(d.get("cap_nhat", ""))).total_seconds() > KET_PHUT * 60
    except ValueError:
        return True


def ds(goc) -> list[dict]:
    d = thu_muc(goc)
    if not d.exists():
        return []
    ra = [x for p in d.iterdir() if p.is_dir() and hd.ID_RE.match(p.name) and (x := doc(goc, p.name))]
    return sorted(ra, key=lambda x: x.get("tao_luc", ""), reverse=True)


def moi_nhat(goc, *, wp_id: int | None = None, viec_id: str = "") -> dict | None:
    for d in ds(goc):
        if (wp_id and d.get("wp_id") == wp_id) or (viec_id and d.get("viec_id") == viec_id):
            return d
    return None


def sua(goc, yc_id: str, ham, chi_khi: tuple[str, ...] | None = None) -> dict | None:
    p = _file(goc, yc_id)
    with hd.khoa(goc):
        d = hd.doc_json(p, None)
        if d is None or (chi_khi and d.get("trang_thai") not in chi_khi):
            return None
        ham(d)
        d["cap_nhat"] = hd.bay_gio().isoformat(timespec="seconds")
        hd.ghi_json(p, d)
        return d


def nhan_cho_codex(goc) -> list[dict]:
    """Codex lấy các yêu cầu đang chờ (và yêu cầu đang vẽ đã kẹt quá lâu), chuyển sang đang vẽ."""
    ra = []
    for d in sorted(ds(goc), key=lambda x: x.get("tao_luc", "")):
        goc_tt = hd.doc_json(_file(goc, d["id"]), {}).get("trang_thai")
        if goc_tt == CHO or (goc_tt == DANG_VE and d.get("trang_thai") == LOI):
            kq = sua(goc, d["id"], lambda x: x.update(trang_thai=DANG_VE, anh=[], loi=""), (CHO, DANG_VE))
            if kq:
                ra.append(kq)
    return ra


def chuan_anh(data: bytes) -> bytes:
    """Ảnh Codex gửi: mở bằng Pillow (chặn file lạ), thu về tối đa 1800px, lưu lại JPEG sạch."""
    from PIL import Image, ImageOps

    if not data or len(data) > TOI_DA_BYTE:
        raise ValueError("Ảnh trống hoặc quá lớn.")
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as exc:  # noqa: BLE001 — mọi lỗi đọc ảnh đều là "không phải ảnh"
        raise ValueError("Không phải file ảnh.") from exc
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((CANH_TOI_DA, CANH_TOI_DA))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


def nhan_anh(goc, yc_id: str, data: bytes) -> str:
    sach = chuan_anh(data)
    ten_moi = {}

    def ham(d):
        anh = list(d.get("anh") or [])
        if len(anh) >= len(TEN_ANH):
            raise ValueError("Đã đủ ảnh.")
        ten = TEN_ANH[len(anh)]
        (_file(goc, yc_id).parent / ten).write_bytes(sach)
        d["anh"] = anh + [ten]
        ten_moi["ten"] = ten

    if sua(goc, yc_id, ham, (DANG_VE,)) is None:
        raise LookupError("Yêu cầu này không còn chờ ảnh.")
    return ten_moi["ten"]


def ket_thuc(goc, yc_id: str, ly_do: str = "") -> dict | None:
    def ham(d):
        if d.get("anh"):
            d.update(trang_thai=XONG, loi="")
        else:
            d.update(trang_thai=LOI, loi=("Codex không vẽ được" + (f": {ly_do[:200]}" if ly_do else "") + ". Bấm vẽ lại."))

    return sua(goc, yc_id, ham, (DANG_VE,))
