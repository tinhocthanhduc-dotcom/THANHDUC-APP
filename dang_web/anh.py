"""Xử lý ảnh sản phẩm ngay trên Z2: xoay đúng chiều, tách nền, nền trắng, vuông, đóng logo, nén WebP."""
from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

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


def _tach_nen(im: Image.Image) -> Image.Image:
    global _rembg_session
    import rembg

    if _rembg_session is None:
        _rembg_session = rembg.new_session("u2net")
    return rembg.remove(im, session=_rembg_session)


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


def _cat_vien_trang(im: Image.Image, nguong: int = 14) -> Image.Image:
    """Cắt phần nền trắng thừa quanh sản phẩm."""
    trang = Image.new("RGB", im.size, (255, 255, 255))
    diff = ImageChops.difference(im, trang).convert("L").point(lambda v: 255 if v > nguong else 0)
    bbox = diff.getbbox()
    return im.crop(bbox) if bbox else im


def xu_ly(
    nguon: Path | bytes | Image.Image,
    *,
    tach_nen: bool = True,
    kich_thuoc: int = 1200,
    dinh_dang: str = "webp",
    logo: Path | None = LOGO_MAC_DINH,
    le: float = 0.05,
    ty_le_logo: float = 0.14,
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
    if tach_nen:
        im = _tach_nen(im.convert("RGB"))
    im = _len_nen_trang(im)

    if _vien_la_nen_trang(im):
        # Sản phẩm trên nền trắng: cắt sát rồi đặt giữa khung, chừa lề mỏng để sản phẩm to, đều mọi ảnh.
        im = _cat_vien_trang(im)
        vung = int(kich_thuoc * (1 - 2 * le))
        im = ImageOps.contain(im, (vung, vung), Image.Resampling.LANCZOS)
        khung = Image.new("RGB", (kich_thuoc, kich_thuoc), (255, 255, 255))
        khung.paste(im, ((kich_thuoc - im.width) // 2, (kich_thuoc - im.height) // 2))
    else:
        # Ảnh chụp có nền / ảnh banner: cắt vuông giữa ảnh cho đầy khung, không để viền trắng lệch.
        khung = ImageOps.fit(im, (kich_thuoc, kich_thuoc), Image.Resampling.LANCZOS, centering=(0.5, 0.5))

    if logo and Path(logo).exists():
        lg = Image.open(logo).convert("RGBA")
        rong = max(48, int(kich_thuoc * ty_le_logo))
        lg = ImageOps.contain(lg, (rong, rong), Image.Resampling.LANCZOS)
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


def mime(dinh_dang: str) -> str:
    return "image/jpeg" if dinh_dang == "jpg" else "image/webp"
