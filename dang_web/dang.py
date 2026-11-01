"""Ghép các bước: lấy hàng → ảnh → AI viết → đăng nháp; ghi kết quả để người duyệt xem."""
from __future__ import annotations

import fcntl
import html
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import anh as anh_mod
from .cau_hinh import CauHinh
from .du_lieu import SanPham
from .wordpress import LoiWeb, WordPress, link_sua, slug

GIO_VN = timezone(timedelta(hours=7))


def bay_gio() -> datetime:
    return datetime.now(GIO_VN)


# ---------- trạng thái: mã nào / chủ đề nào đã đăng ----------
def doc_trang_thai(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        d = {}
    d.setdefault("san_pham", {})
    d.setdefault("bai_viet", {})
    d.setdefault("nghi_trung", {})  # mã/chủ đề bị nghi trùng: lịch tự động bỏ qua, chạy tay với --van-dang
    return d


def ghi_trang_thai(path: Path, d: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


@contextmanager
def khoa(thu_muc: Path):
    """Chặn hai lần chạy cùng lúc (lịch tự động trùng lúc anh chạy tay)."""
    thu_muc.mkdir(parents=True, exist_ok=True)
    f = open(thu_muc / ".dang-chay", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise RuntimeError("Đang có một lần chạy khác; đợi xong rồi chạy lại.")
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def ghi_ket_qua(thu_muc: Path, dong: list[str]) -> Path:
    thu_muc.mkdir(parents=True, exist_ok=True)
    p = thu_muc / f"{bay_gio():%Y-%m-%d}.md"
    with p.open("a", encoding="utf-8") as f:
        f.write("\n".join(dong) + "\n\n")
    return p


# ---------- giá, payload ----------
def gia_web(sp: SanPham, ch: CauHinh) -> str:
    if not ch.dang_gia or sp.sale_price <= 0:
        return ""
    gia = sp.sale_price * (100 + ch.vat_phan_tram) / 100
    return str(int(round(gia, -3)) if ch.vat_phan_tram else int(gia))


def meta_seo(d: dict) -> list[dict]:
    """Yoast SEO đọc các khóa này. WooCommerce cho ghi qua meta_data."""
    return [
        {"key": "_yoast_wpseo_title", "value": d["seo_title"]},
        {"key": "_yoast_wpseo_metadesc", "value": d["seo_description"]},
        {"key": "_yoast_wpseo_focuskw", "value": d["tu_khoa_chinh"]},
    ]


def payload_san_pham(sp: SanPham, d: dict, ch: CauHinh, anh_ids: list[int], danh_muc: dict | None) -> dict:
    p = {
        "name": d["ten_web"].strip() or sp.name,
        "slug": slug(d.get("slug") or d["ten_web"]),
        "type": "simple",
        "status": "draft",
        "sku": sp.code,
        "description": d["mo_ta_html"],
        "short_description": d["mo_ta_ngan_html"],
        "images": [{"id": i} for i in anh_ids],
        "meta_data": meta_seo(d),
    }
    gia = gia_web(sp, ch)
    if gia:
        p["regular_price"] = gia
    if danh_muc:
        p["categories"] = [{"id": danh_muc["id"]}]
    return p


def doc_ghi_chu(ch: CauHinh, ten: str) -> str:
    p = ch.ghi_chu / f"{ten}.txt"
    try:
        return p.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        return ""


def doc_bang_nhom(ch: CauHinh) -> dict[str, str]:
    try:
        d = json.loads(ch.file_nhom_danh_muc.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in d.items()}
    except (FileNotFoundError, ValueError, AttributeError):
        return {}


# ---------- xem trước (chạy thử, không đăng) ----------
def ghi_xem_truoc(ch: CauHinh, ten: str, tieu_de: str, d: dict, anh_files: list[str]) -> Path:
    ch.xem_truoc.mkdir(parents=True, exist_ok=True)
    anh_html = "".join(f'<img src="../anh-da-xu-ly/{html.escape(a)}" style="width:240px;margin:4px;border:1px solid #ddd">' for a in anh_files)
    kiem = "".join(f"<li>{html.escape(x)}</li>" for x in d.get("can_kiem_tra", []))
    than = d.get("mo_ta_ngan_html", "") + d.get("mo_ta_html", "") + d.get("noi_dung_html", "")
    trang = f"""<!doctype html><meta charset="utf-8"><title>{html.escape(tieu_de)}</title>
<body style="font-family:sans-serif;max-width:860px;margin:24px auto;padding:0 16px;line-height:1.6">
<p style="background:#fff7e6;padding:8px">XEM TRƯỚC — chưa đăng lên web</p>
<p><b>SEO title:</b> {html.escape(d.get('seo_title', ''))}<br><b>SEO description:</b> {html.escape(d.get('seo_description', ''))}<br>
<b>Từ khóa:</b> {html.escape(d.get('tu_khoa_chinh', ''))} · <b>Slug:</b> {html.escape(d.get('slug', ''))}</p>
<div>{anh_html}</div><h1>{html.escape(tieu_de)}</h1>{than}
<h3>Cần kiểm tra</h3><ul>{kiem}</ul></body>"""
    p = ch.xem_truoc / f"{ten}.html"
    p.write_text(trang, encoding="utf-8")
    return p


# ---------- xử lý và tải ảnh ----------
def xu_ly_anh(ch: CauHinh, ten_goc: str, files: list[Path], tach_nen: bool) -> list[tuple[str, bytes]]:
    ch.anh_da_xu_ly.mkdir(parents=True, exist_ok=True)
    duoi = "jpg" if ch.anh_dinh_dang == "jpg" else "webp"
    ra: list[tuple[str, bytes]] = []
    for i, f in enumerate(files, 1):
        data = anh_mod.xu_ly(f, tach_nen=tach_nen, kich_thuoc=ch.anh_kich_thuoc, dinh_dang=ch.anh_dinh_dang)
        ten = f"{ten_goc}{'' if i == 1 else f'-{i}'}.{duoi}"
        (ch.anh_da_xu_ly / ten).write_bytes(data)
        ra.append((ten, data))
    return ra


def tai_anh_len(wp: WordPress, ch: CauHinh, ds: list[tuple[str, bytes]], alt: str, files: list[Path], tach_nen: bool) -> list[int]:
    """Tải ảnh lên thư viện Media. Host không nhận WebP thì tự chuyển sang JPG."""
    ids: list[int] = []
    for i, (ten, data) in enumerate(ds):
        mime = anh_mod.mime("jpg" if ten.endswith(".jpg") else "webp")
        nhan = alt if i == 0 else f"{alt} {i + 1}"
        try:
            m = wp.tai_anh(data, ten, mime, alt=nhan)
        except LoiWeb as e:
            if not ten.endswith(".webp") or e.status not in (400, 415, 500):
                raise
            data = anh_mod.xu_ly(files[i], tach_nen=tach_nen, kich_thuoc=ch.anh_kich_thuoc, dinh_dang="jpg")
            m = wp.tai_anh(data, ten[:-5] + ".jpg", "image/jpeg", alt=nhan)
        ids.append(int(m["id"]))
    return ids


def dong_ket_qua(loai: str, ten: str, trang_thai: str, chi_tiet: list[str]) -> list[str]:
    return [f"## {bay_gio():%H:%M} · {loai} · {ten} · {trang_thai}"] + [f"- {x}" for x in chi_tiet]


def link_bai(ch: CauHinh, post_id: int) -> str:
    return link_sua(ch.wp_url, post_id)
