"""Ảnh mạng xã hội có chữ (TUẤN duyệt mẫu M1–M3, D1–D2 ngày 07/11/2026).

Máy ghép ảnh thật và chữ vào khung Thành Đức bằng Pillow, chữ dùng font Be Vietnam Pro (đủ dấu tiếng Việt).
Không để AI vẽ chữ vì AI hay sai dấu. Có khung PNG anh làm bằng Canva (thư mục mau-anh/ trong thư mục đăng web,
tên <mẫu>-<cỡ>.png, ví dụ truoc_sau-vuong.png) thì đặt khung đó lên trên ảnh; chưa có thì dùng khung dựng sẵn.
Vùng ảnh và vùng chữ cố định theo VUNG để khung Canva chừa đúng chỗ.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

MAU = {
    "san_pham": "Sản phẩm",
    "truoc_sau": "Trước và sau",
    "meo": "Mẹo sửa lỗi",
    "uu_dai": "Ưu đãi",
}
CO = {"vuong": (1080, 1080), "doc": (1080, 1350)}
CO_NHAN = {"vuong": "Vuông 1080×1080", "doc": "Dọc 1080×1350"}

XANH = (11, 78, 162)
XANH_NHAT = (232, 240, 251)
CHU_DAM = (16, 35, 63)
TRANG = (255, 255, 255)

THU_MUC_FONT = Path(__file__).with_name("font")
FONT_DAM = THU_MUC_FONT / "BeVietnamPro-ExtraBold.ttf"
FONT_VUA = THU_MUC_FONT / "BeVietnamPro-SemiBold.ttf"
LOGO = Path(__file__).with_name("logo-thanh-duc.png")
LE = 64  # lề hai bên
DAI_TREN = 150  # dải logo phía trên
DAI_DUOI = {"vuong": 250, "doc": 300}  # dải chữ xanh phía dưới


def vung(co: str) -> dict[str, tuple[int, int, int, int]]:
    """Vùng (trái, trên, phải, dưới) của logo, ảnh và chữ cho mỗi cỡ; khung Canva chừa trống đúng các vùng này."""
    W, H = CO[co]
    duoi = H - DAI_DUOI[co]
    return {"logo": (LE, 36, W - LE, DAI_TREN - 20), "anh": (LE, DAI_TREN, W - LE, duoi - 40), "chu": (0, duoi, W, H)}


def _font(p: Path, co: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(p), co)
    except OSError:  # thiếu file font (không nên xảy ra): vẫn ra ảnh, chữ dùng font mặc định của Pillow
        return ImageFont.load_default(co)


def _xuong_dong(ve: ImageDraw.ImageDraw, chu: str, font, rong: int) -> list[str]:
    dong, hien = [], ""
    for tu in chu.split():
        thu = f"{hien} {tu}".strip()
        if ve.textlength(thu, font=font) <= rong or not hien:
            hien = thu
        else:
            dong.append(hien)
            hien = tu
    if hien:
        dong.append(hien)
    return dong


def vua_khung(chu: str, rong: int, cao: int, lon: int, nho: int, toi_da_dong: int = 3, file_font: Path = FONT_DAM):
    """Cỡ chữ lớn nhất (từ `lon` xuống `nho`) để chữ xuống dòng vừa ô rong×cao, tối đa `toi_da_dong` dòng.
    Nhỏ nhất vẫn chưa vừa thì cắt bớt và thêm dấu …"""
    ve = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    chu = " ".join((chu or "").split())
    for co in range(lon, nho - 1, -2):
        f = _font(file_font, co)
        dong = _xuong_dong(ve, chu, f, rong)
        cao_dong = int(co * 1.22)
        if len(dong) <= toi_da_dong and cao_dong * len(dong) <= cao and all(ve.textlength(d, font=f) <= rong for d in dong):
            return f, dong, cao_dong
    f = _font(file_font, nho)
    dong = _xuong_dong(ve, chu, f, rong)[:toi_da_dong]
    if dong:
        while dong[-1] and ve.textlength(dong[-1] + "…", font=f) > rong:
            dong[-1] = dong[-1][:-1]
        dong[-1] = dong[-1].rstrip() + "…"
    return f, dong, int(nho * 1.22)


def _mo(p: Path | bytes) -> Image.Image:
    im = Image.open(io.BytesIO(p) if isinstance(p, bytes) else p)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        nen = Image.new("RGBA", im.size, TRANG + (255,))
        nen.alpha_composite(im)
        im = nen
    return im.convert("RGB")


def _bo_goc(im: Image.Image, r: int) -> Image.Image:
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width - 1, im.height - 1), r, fill=255)
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def _dan(nen: Image.Image, im: Image.Image, hop: tuple[int, int, int, int], phu: bool, r: int = 24) -> None:
    """Đặt ảnh vào hộp: phu=True cắt cho kín hộp, False thì thu vừa hộp trên nền xanh nhạt."""
    x0, y0, x1, y1 = hop
    w, h = x1 - x0, y1 - y0
    if phu:
        lop = ImageOps.fit(im, (w, h), Image.Resampling.LANCZOS)
    else:
        lop = Image.new("RGB", (w, h), XANH_NHAT)
        nho = ImageOps.contain(im, (int(w * 0.86), int(h * 0.86)), Image.Resampling.LANCZOS)
        lop.paste(nho, ((w - nho.width) // 2, (h - nho.height) // 2))
    lop = _bo_goc(lop, r)
    nen.alpha_composite(lop, (x0, y0))


def _nhan(nen: Image.Image, chu: str, x: int, y: int) -> None:
    ve = ImageDraw.Draw(nen)
    f = _font(FONT_DAM, 34)
    rong = int(ve.textlength(chu, font=f))
    ve.rounded_rectangle((x, y, x + rong + 36, y + 56), 12, fill=(0, 0, 0, 150))
    ve.text((x + 18, y + 7), chu, font=f, fill=TRANG)


def ghep(mau: str, co: str, chu: str, anh: list[Path | bytes] | None = None, hotline: str = "",
         thu_muc_khung: Path | None = None, web: str = "thanhduc.vn") -> bytes:
    """Ra ảnh JPEG cỡ CO[co] theo mẫu. anh: Sản phẩm/Mẹo dùng ảnh đầu; Trước và sau dùng 2 ảnh đầu; Ưu đãi không cần ảnh."""
    if mau not in MAU or co not in CO:
        raise ValueError("Mẫu hoặc cỡ ảnh không hợp lệ")
    W, H = CO[co]
    vg = vung(co)
    anh = [a for a in (anh or []) if a]
    uu_dai = mau == "uu_dai"
    nen = Image.new("RGBA", (W, H), (XANH if uu_dai else TRANG) + (255,))
    ve = ImageDraw.Draw(nen)
    x0, y0, x1, y1 = vg["anh"]
    if mau == "truoc_sau":
        if len(anh) < 2:
            raise ValueError("Mẫu Trước và sau cần 2 ảnh: trước khi sửa và sau khi sửa.")
        giua = (x0 + x1) // 2
        _dan(nen, _mo(anh[0]), (x0, y0, giua - 8, y1), True)
        _dan(nen, _mo(anh[1]), (giua + 8, y0, x1, y1), True)
        _nhan(nen, "TRƯỚC", x0 + 20, y1 - 76)
        _nhan(nen, "SAU", giua + 28, y1 - 76)
    elif mau in ("san_pham", "meo") and anh:
        _dan(nen, _mo(anh[0]), vg["anh"], mau == "meo")
    elif uu_dai:
        # Ưu đãi: không ảnh, chữ lớn giữa khung xanh (dòng chữ anh nhập, không ghi giá).
        f, dong, cao = vua_khung(chu, x1 - x0 - 40, y1 - y0, 112, 56, 4)
        y = y0 + (y1 - y0 - cao * len(dong)) // 2
        for d in dong:
            ve.text((W // 2, y), d, font=f, fill=TRANG, anchor="ma")
            y += cao
    else:  # Sản phẩm/Mẹo chưa có ảnh: nền xanh nhạt, chữ nằm ở dải dưới
        ve.rounded_rectangle(vg["anh"], 24, fill=XANH_NHAT)
    khung = (thu_muc_khung / f"{mau}-{co}.png") if thu_muc_khung else None
    if khung and khung.is_file():
        lop = Image.open(khung).convert("RGBA")
        nen.alpha_composite(lop.resize((W, H), Image.Resampling.LANCZOS) if lop.size != (W, H) else lop)
    else:
        lx0, ly0, lx1, ly1 = vg["logo"]
        if LOGO.exists():
            lg = ImageOps.contain(Image.open(LOGO).convert("RGBA"), (ly1 - ly0, ly1 - ly0), Image.Resampling.LANCZOS)
            nen.alpha_composite(lg, (lx0, ly0))
            tx = lx0 + lg.width + 18
        else:
            tx = lx0
        ve.text((tx, (ly0 + ly1) // 2), "TIN HỌC THÀNH ĐỨC", font=_font(FONT_DAM, 38), fill=TRANG if uu_dai else XANH, anchor="lm")
        if not uu_dai:
            ve.rectangle(vg["chu"], fill=XANH + (255,))
    cx0, cy0, cx1, cy1 = vg["chu"]
    duoi = f"Gọi {hotline} · {web}" if hotline else web
    f2 = _font(FONT_VUA, 36)
    if uu_dai:
        ve.text((W // 2, cy0 + (cy1 - cy0) // 2), duoi, font=_font(FONT_VUA, 44), fill=TRANG, anchor="mm")
    else:
        f, dong, cao = vua_khung(chu, cx1 - cx0 - 2 * LE, cy1 - cy0 - 120, 76, 40, 2)
        y = cy0 + 36
        for d in dong:
            ve.text((LE, y), d, font=f, fill=TRANG)
            y += cao
        ve.text((LE, cy1 - 40), duoi, font=f2, fill=TRANG, anchor="ls")
    out = io.BytesIO()
    nen.convert("RGB").save(out, "JPEG", quality=90, optimize=True, progressive=True)
    return out.getvalue()
