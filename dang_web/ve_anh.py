"""Vẽ ảnh minh họa cho bài viết bằng Codex trên Z2 (TUẤN duyệt mẫu D4/4, D5/5 ngày 02/11/2026).

App ghi yêu cầu vào thư mục ve-anh/<id>/yeu-cau.json; Codex (scripts/codex_dang_web.py) hỏi yêu cầu qua cửa
/ai/dang-web/ve-anh, vẽ bằng công cụ image_gen của gói ChatGPT (không tốn phí API) rồi gửi ảnh về.
Bài viết; sản phẩm chỉ dùng cho ảnh trong mô tả (đại diện và album luôn là ảnh thật). Chỉ thư viện chuẩn + Pillow khi nhận ảnh.
"""
from __future__ import annotations

import io
import random
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
SO_ANH = 2  # TUẤN 05/11: chỉ cần vẽ 2 ảnh để chọn (trước 3)
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


def dan_y_bai(html: str, toi_da: int = 1800) -> str:
    """Dàn ý bài để Codex vẽ sát nội dung (TUẤN 02/11): các tiêu đề H1–H3 theo thứ tự, dưới mỗi tiêu đề là câu đầu
    của đoạn ngay sau nó (TUẤN 05/11: chỉ có tiêu đề thì ảnh vẽ chung chung), cuối cùng là đoạn mở bài."""
    html = html or ""
    dong = []
    for m in re.finditer(r"<h([1-3])[^>]*>(.*?)</h\1>", html, re.I | re.S):
        cap, chu = m.group(1), _chu(m.group(2))
        if not chu:
            continue
        thut = "  " * max(int(cap) - 2, 0)
        dong.append(thut + f"H{cap}: {chu[:150]}")
        sau = re.match(r"\s*<(p|ul|ol)[^>]*>(.*?)</\1>", html[m.end():], re.I | re.S)
        y = _chu(sau.group(2)) if sau else ""
        if y and cap != "1":
            dong.append(thut + f"  Nội dung: {y[:140]}")
    mo = re.search(r"<p[^>]*>(.*?)</p>", html, re.I | re.S)
    if mo and _chu(mo.group(1)):
        dong.append(f"Mở bài: {_chu(mo.group(1))[:300]}")
    return "\n".join(dong)[:toi_da]


# TUẤN 05/11: lần nào cũng ra "thợ cầm hộp mực cạnh máy in". Mỗi ảnh nhận một hướng ý tưởng khác nhau (lấy ngẫu nhiên,
# tránh hướng của lần vẽ trước) để Vẽ lại cho ra ý mới thật, và cấm hẳn cảnh rập khuôn đó.
HUONG_BAI = (
    "Cận cảnh chi tiết: đúng bộ phận, linh kiện hoặc dấu hiệu lỗi mà bài nói tới (ví dụ bản in bị sọc mờ, tờ giấy kẹt "
    "trong khay, trục từ trầy, đèn báo lỗi), chụp macro, chỉ thấy bàn tay hoặc không có người.",
    "Góc nhìn của khách hàng: nhân viên văn phòng đang gặp đúng tình huống của bài (cầm bản in lỗi, nhìn máy báo lỗi, "
    "cân nhắc lựa chọn), máy in chỉ là hậu cảnh mờ.",
    "So sánh trực quan các lựa chọn mà bài bàn tới, đặt cạnh nhau trên bàn, chụp từ trên xuống (flat lay), bố cục cân đối.",
    "Kết quả sau khi xử lý xong: bản in sắc nét đặt trên bàn, văn phòng làm việc trơn tru, cảm giác yên tâm.",
    "Một thao tác cụ thể trong một mục H2 (tháo, lắp, vệ sinh, kiểm tra một bộ phận cụ thể), chụp cận tay đang làm "
    "đúng bước đó, nền xưởng sửa chữa.",
    "Cảnh tư vấn tại quầy cửa hàng hoặc tại văn phòng khách: nhân viên đang giải thích, chỉ vào sản phẩm hoặc bản in "
    "mẫu, khách lắng nghe.",
    "Tĩnh vật: dụng cụ, linh kiện, vật tư liên quan tới bài xếp gọn trên bàn gỗ hoặc bàn làm việc, ánh sáng cửa sổ.",
    "Toàn cảnh không gian: góc văn phòng nhỏ ở Việt Nam có máy in đang chạy, người đang làm việc, nhìn rộng và có chiều sâu.",
)
HUONG_SP = (
    "Cảnh văn phòng ở Việt Nam đang dùng loại sản phẩm này hằng ngày, nhìn rộng, sản phẩm không chiếm hết khung.",
    "Kết quả mà sản phẩm mang lại: bản in, tài liệu, công việc trơn tru; sản phẩm chỉ ở hậu cảnh.",
    "Thợ lắp đặt hoặc bảo trì tại văn phòng khách, chụp cận tay đang thao tác.",
    "Cảnh tư vấn tại cửa hàng: nhân viên giới thiệu cho khách.",
    "Tĩnh vật: vật tư, phụ kiện đi kèm xếp gọn trên bàn làm việc, ánh sáng tự nhiên.",
)
CAM_RAP_KHUON = ("Tuyệt đối không vẽ cảnh rập khuôn: một người thợ đứng hoặc ngồi cầm hộp mực bên cạnh máy in để bàn. "
                 "Hai ảnh phải khác nhau rõ ràng về bối cảnh, góc máy, cỡ cảnh (cận / trung / toàn) và nhân vật.")


def chon_huong(so: int, bo: list[str] | tuple[str, ...] = (), san_pham: bool = False, rng: random.Random | None = None) -> list[str]:
    """Chọn ngẫu nhiên `so` hướng ý tưởng khác nhau, ưu tiên hướng chưa dùng ở lần vẽ trước."""
    kho = list(HUONG_SP if san_pham else HUONG_BAI)
    moi = [h for h in kho if h not in set(bo)]
    if len(moi) < so:
        moi = kho
    return (rng or random).sample(moi, so)


def _phan_huong(huong: list[str]) -> list[str]:
    return ["Ý tưởng cho từng ảnh (mỗi ảnh một ý, không lặp lại nhau):"] + [f"- Ảnh {i}: {h}" for i, h in enumerate(huong, 1)]


def lenh_ve(chu_de: str, tom_tat: str = "", dan: str = "", so: int = SO_ANH, dan_y: str = "", huong: list[str] | None = None) -> str:
    huong = huong or chon_huong(so)
    phan = [f"Dùng công cụ tạo ảnh image_gen vẽ {so} ảnh minh họa KHÁC NHAU, khung ngang 16:9, cho một bài viết trên website "
            "của cửa hàng máy in, mực in, sửa máy in Thành Đức ở TP. Hồ Chí Minh.",
            f"Tiêu đề bài viết: {chu_de.strip()[:200]}."]
    if tom_tat.strip():
        phan.append(f"Tóm tắt bài: {' '.join(tom_tat.split())[:500]}")
    if dan_y.strip():
        phan.append("Dàn ý bài (các tiêu đề H1, H2, H3 theo thứ tự, kèm ý chính dưới mỗi tiêu đề):\n" + dan_y.strip()[:1800])
        phan.append("Đọc kỹ tiêu đề và dàn ý trước khi vẽ. Ảnh 1 thể hiện ý chính của tiêu đề bài; ảnh 2 minh họa một mục H2 "
                    "cụ thể khác (chọn mục có hình ảnh rõ nhất). Vẽ đúng loại máy, linh kiện, thao tác hoặc tình huống mà bài "
                    "nói tới (máy in laser hay phun màu, hộp mực, trục từ, kẹt giấy, nạp mực...), không vẽ chung chung.")
    else:
        phan.append("Mỗi ảnh vẽ đúng loại máy, linh kiện, thao tác hoặc tình huống mà tiêu đề nói tới, không vẽ chung chung.")
    phan += _phan_huong(huong)
    phan.append("Ghép từng ý tưởng ở trên với nội dung bài để ra cảnh cụ thể; nếu một ý tưởng không hợp bài thì đổi sang "
                "cảnh khác nhưng vẫn phải khác hẳn ảnh còn lại.")
    phan.append(CAM_RAP_KHUON)
    if dan.strip():
        phan.append(f"Yêu cầu thêm (ưu tiên hơn các ý trên): {' '.join(dan.split())[:300]}")
    phan.append("Phong cách ảnh chụp thật, ánh sáng tự nhiên, văn phòng hoặc xưởng sửa chữa ở Việt Nam, người trong ảnh là người "
                "Việt Nam. Không có chữ, số, logo, tên hãng hay watermark trong ảnh.")
    phan.append("Lưu từng ảnh vào thư mục hiện tại với tên " + ", ".join(f"anh-{i}.png" for i in range(1, so + 1))
                + ". Không tạo hay sửa file nào khác, không chạy lệnh nào khác.")
    return "\n".join(phan)


def lenh_ve_sp(ten: str, dan_y: str = "", dan: str = "", so: int = SO_ANH, huong: list[str] | None = None) -> str:
    """Ảnh cho phần mô tả sản phẩm (TUẤN 02/11): không thay ảnh thật của máy, chỉ minh họa nội dung mô tả."""
    huong = huong or chon_huong(so, san_pham=True)
    phan = [f"Dùng công cụ tạo ảnh image_gen vẽ {so} ảnh minh họa KHÁC NHAU, khung ngang 16:9, để chèn vào PHẦN MÔ TẢ của một "
            "sản phẩm trên website cửa hàng máy in, mực in, sửa máy in Thành Đức ở TP. Hồ Chí Minh.",
            f"Tên sản phẩm: {ten.strip()[:200]}."]
    if dan_y.strip():
        phan.append("Các mục trong mô tả (tiêu đề H2, H3):\n" + dan_y.strip()[:1200])
    phan.append("Ảnh minh họa cách dùng hoặc lợi ích của sản phẩm theo nội dung mô tả. Không vẽ cận cảnh làm như ảnh chụp "
                "chính sản phẩm để bán (khách có thể hiểu nhầm mẫu mã).")
    phan += _phan_huong(huong)
    phan.append(CAM_RAP_KHUON)
    if dan.strip():
        phan.append(f"Yêu cầu thêm (ưu tiên hơn các ý trên): {' '.join(dan.split())[:300]}")
    phan.append("Phong cách ảnh chụp thật, ánh sáng tự nhiên, người trong ảnh là người Việt Nam. Không có chữ, số, logo, tên hãng "
                "hay watermark trong ảnh.")
    phan.append("Lưu từng ảnh vào thư mục hiện tại với tên " + ", ".join(f"anh-{i}.png" for i in range(1, so + 1))
                + ". Không tạo hay sửa file nào khác, không chạy lệnh nào khác.")
    return "\n".join(phan)


def tao(goc, *, chu_de: str, wp_id: int | None = None, viec_id: str = "", tom_tat: str = "", dan: str = "", dan_y: str = "",
        loai: str = "bai_viet") -> str:
    yc_id = f"{hd.bay_gio():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    luc = hd.bay_gio().isoformat(timespec="seconds")
    sp = loai == "san_pham"
    cu = moi_nhat(goc, wp_id=wp_id, viec_id=viec_id, loai="san_pham" if sp else "bai_viet")
    huong = chon_huong(SO_ANH, (cu or {}).get("huong") or [], san_pham=sp)  # Vẽ lại: tránh hướng ý tưởng lần trước
    d = {"id": yc_id, "loai": "san_pham" if sp else "bai_viet", "wp_id": wp_id, "viec_id": viec_id, "chu_de": chu_de[:200], "dan": dan[:300],
         "lenh": lenh_ve_sp(chu_de, dan_y, dan, huong=huong) if sp else lenh_ve(chu_de, tom_tat, dan, dan_y=dan_y, huong=huong),
         "huong": huong, "so_anh": SO_ANH, "trang_thai": CHO, "anh": [], "loi": "", "tao_luc": luc, "cap_nhat": luc}
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


def moi_nhat(goc, *, wp_id: int | None = None, viec_id: str = "", loai: str = "bai_viet") -> dict | None:
    for d in ds(goc):
        if (wp_id and d.get("wp_id") == wp_id and d.get("loai", "bai_viet") == loai) or (viec_id and d.get("viec_id") == viec_id):
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
