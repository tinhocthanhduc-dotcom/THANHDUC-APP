"""Giữ nguyên ảnh trong mô tả khi AI viết lại sản phẩm / bài đang có trên web (TUẤN 25/11/2026).

AI không thấy ảnh: trước khi gửi, tách các khối ảnh ra khỏi HTML và ghi lại chỗ của chúng
(đứng ngay trước tiêu đề H2 thứ mấy, hay nằm trong mục sau H2 thứ mấy). AI viết xong thì đặt
lại đúng các khối ảnh cũ vào chỗ tương ứng của bài mới. Chỉ dùng thư viện chuẩn (app cũng import).
"""
from __future__ import annotations

import re

_IMG = re.compile(r"<img\b[^>]*>", re.I)
_SRC = re.compile(r"""\ssrc\s*=\s*["']([^"']+)["']""", re.I)
# Thứ tự quan trọng: khối Gutenberg, [caption], <figure>, đoạn <p> chỉ có ảnh, rồi ảnh lẻ (kèm <a> bọc ngoài).
_KHOI = re.compile(
    r"<!--\s*wp:image\b.*?<!--\s*/wp:image\s*-->"
    r"|\[caption\b[^\]]*\].*?\[/caption\]"
    r"|<figure\b[^>]*>.*?</figure\s*>"
    r"|<p\b[^>]*>(?:\s|&nbsp;|<br\s*/?>|<a\b[^>]*>|</a\s*>|<img\b[^>]*>)*</p\s*>"
    r"|(?:<a\b[^>]*>\s*)?<img\b[^>]*>(?:\s*</a\s*>)?",
    re.I | re.S,
)
_H2_MO = re.compile(r"<h2\b", re.I)
_H2_DONG = re.compile(r"</h2\s*>", re.I)
_HET_KHOI = re.compile(r"</(?:p|ul|ol|table)\s*>", re.I)


def nguon_anh(html: str) -> list[str]:
    """Đường dẫn ảnh (src) trong HTML theo thứ tự, bỏ trùng."""
    ra: list[str] = []
    for the in _IMG.findall(html or ""):
        m = _SRC.search(the)
        if m and m.group(1).strip() not in ra:
            ra.append(m.group(1).strip())
    return ra


def tach_anh(html: str) -> tuple[str, list[dict]]:
    """Bỏ mọi khối ảnh khỏi HTML. Trả (html không ảnh, các nhóm ảnh).
    Mỗi nhóm: {"the": khối HTML gốc, "h2": số H2 đứng trước, "truoc_h2": True nếu ngay sau nhóm là một H2}.
    Các ảnh liền nhau (chỉ cách khoảng trắng) gộp một nhóm để giữ thứ tự."""
    html = html or ""
    khop = [m for m in _KHOI.finditer(html) if _IMG.search(m.group(0))]
    if not khop:
        return html, []
    nhom: list[dict] = []
    for m in khop:
        if nhom and not html[nhom[-1]["het"]:m.start()].strip():
            nhom[-1]["het"] = m.end()
        else:
            nhom.append({"dau": m.start(), "het": m.end()})
    ra, cuoi, ds = [], 0, []
    for g in nhom:
        ra.append(html[cuoi:g["dau"]])
        cuoi = g["het"]
        ds.append({"the": html[g["dau"]:g["het"]].strip(), "h2": len(_H2_MO.findall(html, 0, g["dau"])),
                   "truoc_h2": bool(_H2_MO.match(html[g["het"]:].lstrip()))})
    ra.append(html[cuoi:])
    con = "".join(ra)
    con = re.sub(r"\n{3,}", "\n\n", con)
    return con.strip(), ds


def _vi_tri(html: str, g: dict) -> int:
    h2 = [m.start() for m in _H2_MO.finditer(html)]
    n = int(g.get("h2") or 0)
    if g.get("truoc_h2") and n < len(h2):
        return h2[n]
    if n > len(h2):
        return len(html)
    dau = 0
    if n > 0:
        dong = _H2_DONG.search(html, h2[n - 1])
        dau = dong.end() if dong else h2[n - 1]
    het = h2[n] if n < len(h2) else len(html)
    k = _HET_KHOI.search(html, dau, het)
    if k:
        return k.end()
    return het if n == 0 else dau


def dat_lai_anh(html: str, ds: list[dict]) -> str:
    """Đặt lại các nhóm ảnh đã tách vào bài mới, theo chỗ đã ghi. Thiếu mục thì đặt cuối bài."""
    html = html or ""
    if not ds:
        return html
    cho = sorted(((_vi_tri(html, g), i, g["the"]) for i, g in enumerate(ds)), key=lambda x: (x[0], x[1]))
    for pos, _, the in reversed(cho):
        html = html[:pos] + "\n" + the + "\n" + html[pos:]
    return html


def anh_bi_mat(cu: list[str], html: str) -> list[str]:
    """Ảnh có trong mô tả cũ mà bài mới không còn."""
    con = set(nguon_anh(html))
    return [s for s in cu or [] if s not in con]
