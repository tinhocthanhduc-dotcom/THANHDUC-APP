"""Xử lý ảnh sản phẩm ngay trên Z2: xoay đúng chiều, tách nền, nền trắng, vuông, đóng logo, nén WebP."""
from __future__ import annotations

import io
import os
import re
from collections import deque
from pathlib import Path

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

DUOI_ANH = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")
LOGO_MAC_DINH = Path(__file__).with_name("logo-thanh-duc.png")

try:  # Ảnh HEIC chụp từ iPhone
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover - thiếu thư viện thì bỏ qua HEIC
    pass

_rembg_session = None


def _so_thu_tu(path: Path, code: str) -> tuple[int, str]:
    rest = path.stem[len(code):].lstrip("-_ ")
    m = re.match(r"(\d+)", rest)
    return (int(m.group(1)) if m else (0 if rest == "" else 999), path.name.lower())


def tim_anh(thu_muc: Path, code: str) -> list[Path]:
    """Ảnh của một mã: TD-0123.jpg, TD-0123-2.jpg, TD-0123_3.png hoặc thư mục TD-0123/. Ảnh không số đứng đầu."""
    code = code.upper()
    ket_qua: list[Path] = []
    if not thu_muc.exists():
        return ket_qua
    for p in thu_muc.iterdir():
        if p.is_file() and p.suffix.lower() in DUOI_ANH:
            stem = p.stem.upper()
            if stem == code or re.match(re.escape(code) + r"[-_ ]", stem):
                ket_qua.append(p)
    ket_qua.sort(key=lambda p: _so_thu_tu(p, code))
    thu_muc_ma = thu_muc / code
    if thu_muc_ma.is_dir():
        ket_qua += sorted(
            (p for p in thu_muc_ma.iterdir() if p.is_file() and p.suffix.lower() in DUOI_ANH),
            key=lambda p: p.name.lower(),
        )
    return ket_qua


# Mô hình tách nền: isnet-general-use viền sắc, ít sót nền hơn u2net (tải ~180 MB lần đầu vào /data/.mo-hinh).
MO_HINH_MAC_DINH = "isnet-general-use"


def _tach_nen(im: Image.Image) -> Image.Image:
    global _rembg_session
    import rembg

    if _rembg_session is None:
        _rembg_session = rembg.new_session(os.environ.get("WEB_MO_HINH_TACH_NEN") or MO_HINH_MAC_DINH)
    return rembg.remove(im, session=_rembg_session, post_process_mask=True)


def _vung_chinh(mat_na: Image.Image, canh: int = 200, toi_thieu: float = 0.04) -> Image.Image:
    """Giữ các mảng lớn của mặt nạ (sản phẩm), bỏ mảng nhỏ rời rạc: vết bẩn, dây cáp, góc bàn sót lại.
    Mảng giữ lại khi rộng ít nhất `toi_thieu` lần mảng lớn nhất. Trả về mặt nạ cùng cỡ ảnh gốc."""
    w, h = mat_na.size
    k = min(1.0, canh / max(w, h))
    nw, nh = max(1, round(w * k)), max(1, round(h * k))
    nho = mat_na.resize((nw, nh), Image.Resampling.BOX).point(lambda v: 255 if v >= 64 else 0)
    px = nho.load()
    nhan = [[0] * nw for _ in range(nh)]
    mang: list[list[tuple[int, int]]] = []
    for y in range(nh):
        for x in range(nw):
            if px[x, y] and not nhan[y][x]:
                so = len(mang) + 1
                nhan[y][x] = so
                q, o = deque([(x, y)]), []
                while q:
                    cx, cy = q.popleft()
                    o.append((cx, cy))
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ax, ay = cx + dx, cy + dy
                        if 0 <= ax < nw and 0 <= ay < nh and px[ax, ay] and not nhan[ay][ax]:
                            nhan[ay][ax] = so
                            q.append((ax, ay))
                mang.append(o)
    if len(mang) <= 1:
        return mat_na
    lon = max(len(o) for o in mang)
    giu = Image.new("L", (nw, nh), 0)
    gp = giu.load()
    for o in mang:
        if len(o) >= toi_thieu * lon:
            for x, y in o:
                gp[x, y] = 255
    giu = giu.filter(ImageFilter.MaxFilter(3)).resize((w, h), Image.Resampling.NEAREST)
    return ImageChops.multiply(mat_na, giu)


def _len_nen_trang(im: Image.Image) -> Image.Image:
    im = im.convert("RGBA")
    nen = Image.new("RGBA", im.size, (255, 255, 255, 255))
    nen.alpha_composite(im)
    return nen.convert("RGB")


def _vien_la_nen_trang(im: Image.Image, nguong: int = 242, ty_le: float = 0.85) -> bool:
    """Viền ảnh phần lớn là trắng (ảnh đã tách nền, hoặc chụp trên nền trắng)."""
    nho = im.convert("L").resize((64, 64))
    px = nho.load()
    vien = [px[x, y] for x in range(64) for y in (0, 63)] + [px[x, y] for y in range(1, 63) for x in (0, 63)]
    return sum(v >= nguong for v in vien) >= ty_le * len(vien)


def _mat_na_khac_trang(im: Image.Image, nguong: int = 28) -> Image.Image:
    """Điểm khác nền trắng rõ rệt; bóng mờ nhạt và hạt nhiễu lẻ không tính."""
    trang = Image.new("RGB", im.size, (255, 255, 255))
    m = ImageChops.difference(im.convert("RGB"), trang).convert("L").point(lambda v: 255 if v > nguong else 0)
    return m.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))


def _cat_vien_trang(im: Image.Image, nguong: int = 28) -> Image.Image:
    """Cắt phần nền trắng thừa quanh sản phẩm (bỏ qua bóng mờ, vết bẩn nhỏ ở góc)."""
    bbox = _vung_chinh(_mat_na_khac_trang(im, nguong)).getbbox()
    return im.crop(bbox) if bbox else im


def _lam_trang_nen(im: Image.Image, nguong: int = 246) -> Image.Image:
    """Nền gần trắng (xám nhạt do chụp) thành trắng tinh để ảnh liền với nền web."""
    rgb = im.convert("RGB")
    sang = ImageChops.darker(ImageChops.darker(*rgb.split()[:2]), rgb.split()[2])  # kênh tối nhất
    trang = sang.point(lambda v: 255 if v >= nguong else 0)
    return Image.composite(Image.new("RGB", rgb.size, (255, 255, 255)), rgb, trang)


def _lam_net(im: Image.Image) -> Image.Image:
    """Tăng tương phản nhẹ và làm nét vừa phải cho ảnh chụp điện thoại."""
    im = ImageEnhance.Contrast(im).enhance(1.06)
    return im.filter(ImageFilter.UnsharpMask(radius=1.6, percent=55, threshold=3))


def _de_logo(mat_na: Image.Image, hop: tuple[int, int, int, int]) -> float:
    """Tỷ lệ phần ô logo bị sản phẩm che."""
    o = mat_na.crop(hop)
    hist = o.histogram()
    return sum(hist[128:]) / max(1, o.width * o.height)


def xu_ly(
    nguon: Path | bytes | Image.Image,
    *,
    tach_nen: bool = True,
    kich_thuoc: int = 1200,
    dinh_dang: str = "webp",
    logo: Path | None = LOGO_MAC_DINH,
    le: float = 0.04,
    ty_le_logo: float = 0.13,
) -> bytes:
    """Trả về ảnh vuông kich_thuoc x kich_thuoc, nền trắng, logo góc dưới phải."""
    if isinstance(nguon, Image.Image):
        im = nguon
    elif isinstance(nguon, (bytes, bytearray)):
        im = Image.open(io.BytesIO(nguon))
    else:
        im = Image.open(nguon)
    im = ImageOps.exif_transpose(im)
    im.load()
    rong_logo = max(48, int(kich_thuoc * ty_le_logo)) if logo and Path(logo).exists() else 0

    san_pham = mat_na = None
    if tach_nen:
        rgba = _tach_nen(im.convert("RGB")).convert("RGBA")
        a = _vung_chinh(rgba.getchannel("A").point(lambda v: 0 if v < 24 else v))
        bbox = a.getbbox()
        # Tách được vật rõ ràng (≥3% ảnh) thì dùng; không thì coi như ảnh chụp thường.
        if bbox and (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) >= 0.03 * im.width * im.height:
            rgba.putalpha(a)
            rgba = rgba.crop(bbox)
            mat_na = rgba.getchannel("A")
            san_pham = _len_nen_trang(rgba)
    if san_pham is None:
        im = _len_nen_trang(im)
        if _vien_la_nen_trang(im):
            san_pham = _lam_trang_nen(_cat_vien_trang(im))
            mat_na = _mat_na_khac_trang(san_pham)

    if san_pham is not None:
        # Sản phẩm trên nền trắng: cắt sát, đặt giữa khung, lề mỏng; nếu che logo thì thu nhỏ một chút.
        san_pham = _lam_net(san_pham)
        canh = int(kich_thuoc * 0.03)
        hop_logo = (kich_thuoc - rong_logo - canh, kich_thuoc - rong_logo - canh, kich_thuoc - canh, kich_thuoc - canh)
        vung = int(kich_thuoc * (1 - 2 * le))
        for _ in range(4):  # thu nhỏ tối đa ~14%, quá nữa thì chấp nhận logo đè mép sản phẩm
            sp = ImageOps.contain(san_pham, (vung, vung), Image.Resampling.LANCZOS)
            mn = ImageOps.contain(mat_na, (vung, vung), Image.Resampling.BILINEAR) if mat_na is not None else None
            x, y = (kich_thuoc - sp.width) // 2, (kich_thuoc - sp.height) // 2
            if not rong_logo or mn is None:
                break
            day = Image.new("L", (kich_thuoc, kich_thuoc), 0)
            day.paste(mn, (x, y))
            if _de_logo(day, hop_logo) <= 0.06 or _ == 3:
                break
            vung = int(vung * 0.95)
        khung = Image.new("RGB", (kich_thuoc, kich_thuoc), (255, 255, 255))
        khung.paste(sp, (x, y))
    else:
        # Ảnh chụp có nền / ảnh banner: cắt vuông giữa ảnh cho đầy khung, không để viền trắng lệch.
        khung = ImageOps.fit(im, (kich_thuoc, kich_thuoc), Image.Resampling.LANCZOS, centering=(0.5, 0.5))

    if rong_logo:
        lg = Image.open(logo).convert("RGBA")
        lg = ImageOps.contain(lg, (rong_logo, rong_logo), Image.Resampling.LANCZOS)
        alpha = lg.getchannel("A").point(lambda v: int(v * 0.9))
        lg.putalpha(alpha)
        canh = int(kich_thuoc * 0.03)
        khung = khung.convert("RGBA")
        khung.alpha_composite(lg, (kich_thuoc - lg.width - canh, kich_thuoc - lg.height - canh))
        khung = khung.convert("RGB")

    out = io.BytesIO()
    if dinh_dang == "jpg":
        khung.save(out, "JPEG", quality=85, optimize=True, progressive=True)
    else:
        khung.save(out, "WEBP", quality=82, method=6)
    return out.getvalue()


def giu_nguyen(nguon: Path | bytes, *, canh_toi_da: int = 1600, dinh_dang: str = "webp") -> bytes:
    """Ảnh "giữ nguyên" (banner, ảnh thông số): chỉ xoay đúng chiều, thu nhỏ nếu quá lớn, nén lại; không cắt, không tách nền, không logo."""
    im = Image.open(io.BytesIO(nguon) if isinstance(nguon, (bytes, bytearray)) else nguon)
    im = ImageOps.exif_transpose(im)
    im.load()
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        nen = Image.new("RGB", im.size, (255, 255, 255))
        nen.paste(im, mask=im.split()[-1])
        im = nen
    else:
        im = im.convert("RGB")
    im.thumbnail((canh_toi_da, canh_toi_da), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    if dinh_dang == "jpg":
        im.save(out, "JPEG", quality=88, optimize=True, progressive=True)
    else:
        im.save(out, "WEBP", quality=85, method=6)
    return out.getvalue()


def mime(dinh_dang: str) -> str:
    return "image/jpeg" if dinh_dang == "jpg" else "image/webp"


# ---------- Sửa ảnh kiểu mới (TUẤN duyệt mẫu D2/2 ngày 02/11/2026) ----------
# Không tách nền: chỉ đặt ảnh vào đúng khung web (vuông cho sản phẩm, ngang 16:9 cho bài viết), căn giữa,
# xoay chỉnh góc, phóng to/thu nhỏ, lật, đóng logo. Màn sửa ảnh xem trước bằng CSS theo đúng công thức dưới đây.
KHUNG_BAI = (1200, 675)


def chuan_bien(b: dict | None) -> dict:
    """Thông số anh chỉnh trên màn: z phóng (1 = vừa khít khung), r độ xoay (chiều kim đồng hồ),
    x, y dời tâm ảnh theo tỷ lệ khung, q số lần xoay 90°, f lật ngang, tu = để máy tự căn giữa."""
    b = b if isinstance(b, dict) else {}

    def so(k, mac, lo, hi):
        try:
            v = float(b.get(k, mac))
        except (TypeError, ValueError):
            return mac
        return mac if v != v else max(lo, min(hi, v))  # v != v: NaN

    return {"z": so("z", 1.0, 0.2, 5.0), "r": so("r", 0.0, -45.0, 45.0), "x": so("x", 0.0, -1.5, 1.5),
            "y": so("y", 0.0, -1.5, 1.5), "q": int(so("q", 0, 0, 3)), "f": bool(b.get("f")), "tu": bool(b.get("tu"))}


def _mo_rgb(nguon: Path | bytes | Image.Image) -> Image.Image:
    im = nguon if isinstance(nguon, Image.Image) else Image.open(io.BytesIO(nguon) if isinstance(nguon, (bytes, bytearray)) else nguon)
    im = ImageOps.exif_transpose(im)
    im.load()
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        nen = Image.new("RGB", im.size, (255, 255, 255))
        nen.paste(im, mask=im.split()[-1])
        return nen
    return im.convert("RGB")


def mau_nen(im: Image.Image) -> tuple[int, int, int]:
    """Màu viền ảnh (trung vị các điểm sát mép) để tô phần khung trống khi thu nhỏ hoặc xoay; gần trắng thì trắng hẳn."""
    nho = im.copy()
    nho.thumbnail((200, 200))
    w, h = nho.size
    d = max(1, int(min(w, h) * 0.03))
    px = [nho.getpixel((x, y)) for y in range(h) for x in range(w) if x < d or y < d or x >= w - d or y >= h - d]
    mau = tuple(sorted(p[k] for p in px)[len(px) // 2] for k in range(3))
    return (255, 255, 255) if min(mau) >= 235 else mau  # type: ignore[return-value]


def vung_san_pham(im: Image.Image, nen: tuple[int, int, int], nguong: int = 32) -> tuple[int, int, int, int] | None:
    """Khung bao phần khác màu nền (sản phẩm); bỏ đốm nhỏ lẻ."""
    nho = im.copy()
    nho.thumbnail((400, 400))
    k = im.width / nho.width
    diff = ImageChops.difference(nho, Image.new("RGB", nho.size, nen)).convert("L").point(lambda v: 255 if v > nguong else 0)
    diff = diff.filter(ImageFilter.MedianFilter(5))
    hop = diff.getbbox()
    if not hop or (hop[2] - hop[0]) * (hop[3] - hop[1]) < 0.01 * nho.width * nho.height:
        return None
    return tuple(int(round(v * k)) for v in hop)  # type: ignore[return-value]


def tu_can_giua(iw: int, ih: int, hop, W: int, H: int, day: float = 0.9) -> dict:
    """z, x, y để khung bao sản phẩm nằm giữa và chiếm `day` khung (cùng công thức với nút Căn giữa tự động trên màn)."""
    s0 = min(W / iw, H / ih)
    if not hop:
        return {"z": 1.0, "x": 0.0, "y": 0.0}
    bw, bh = max(1, hop[2] - hop[0]), max(1, hop[3] - hop[1])
    s = day * min(W / bw, H / bh)
    cx, cy = (hop[0] + hop[2]) / 2 - iw / 2, (hop[1] + hop[3]) / 2 - ih / 2
    return {"z": max(0.2, min(5.0, s / s0)), "x": -cx * s / W, "y": -cy * s / H}


def dat_khung(nguon: Path | bytes | Image.Image, W: int, H: int, bien: dict | None = None, *, logo: Path | None = None,
              logo_goc: str = "phai", dinh_dang: str = "webp", ty_le_logo: float = 0.13) -> bytes:
    """Đặt ảnh vào khung W×H theo thông số chỉnh trên màn Sửa ảnh. Không tách nền, không đổi màu."""
    b = chuan_bien(bien)
    im = _mo_rgb(nguon)
    if b["q"]:
        im = im.rotate(-90 * b["q"], expand=True)
    if b["f"]:
        im = ImageOps.mirror(im)
    nen = mau_nen(im)
    if b["tu"]:
        b.update(tu_can_giua(im.width, im.height, vung_san_pham(im, nen), W, H), r=0.0)
    s = min(W / im.width, H / im.height) * b["z"]
    nw, nh = max(1, round(im.width * s)), max(1, round(im.height * s))
    lop = im.resize((nw, nh), Image.Resampling.LANCZOS).convert("RGBA")
    if b["r"]:
        lop = lop.rotate(-b["r"], resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(0, 0, 0, 0))
    khung = Image.new("RGB", (W, H), nen)
    cx, cy = W / 2 + b["x"] * W, H / 2 + b["y"] * H
    khung.paste(lop, (round(cx - lop.width / 2), round(cy - lop.height / 2)), lop)
    if logo and Path(logo).exists():
        rong = max(48, int(min(W, H) * ty_le_logo))
        lg = ImageOps.contain(Image.open(logo).convert("RGBA"), (rong, rong), Image.Resampling.LANCZOS)
        lg.putalpha(lg.getchannel("A").point(lambda v: int(v * 0.9)))
        canh = int(min(W, H) * 0.03)
        x = canh if logo_goc == "trai" else W - lg.width - canh
        khung = khung.convert("RGBA")
        khung.alpha_composite(lg, (x, H - lg.height - canh))
        khung = khung.convert("RGB")
    out = io.BytesIO()
    if dinh_dang == "jpg":
        khung.save(out, "JPEG", quality=86, optimize=True, progressive=True)
    else:
        khung.save(out, "WEBP", quality=84, method=6)
    return out.getvalue()
