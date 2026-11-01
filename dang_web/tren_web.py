"""Danh mục trên web (cây cha/con, đường dẫn tên, chưa phân loại) dùng chung cho app và máy đăng web.

Chỉ dùng thư viện chuẩn: container app không có httpx/Pillow.
"""
from __future__ import annotations

import html
import re
import unicodedata

TEN_CHUA_PHAN_LOAI = {"chua phan loai", "uncategorized", "khong phan loai"}


def _gap(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip().lower()


def la_chua_phan_loai(dm: dict) -> bool:
    return dm.get("slug") == "uncategorized" or _gap(dm.get("ten", "")) in TEN_CHUA_PHAN_LOAI


class CayDanhMuc:
    """Cây danh mục: dm là danh sách {id, ten, cha, slug} (cha = 0 nếu là gốc)."""

    def __init__(self, dm: list[dict]):
        self.theo_id = {int(d["id"]): d for d in dm if isinstance(d, dict) and str(d.get("id", "")).isdigit()}

    def to_tien(self, i: int) -> list[int]:
        """Chính nó rồi lần lên cha (chặn vòng lặp nếu dữ liệu web lỗi)."""
        ra, dang = [], int(i)
        while dang in self.theo_id and dang not in ra:
            ra.append(dang)
            dang = int(self.theo_id[dang].get("cha") or 0)
        return ra

    def duong(self, i: int) -> str:
        return " › ".join(self.theo_id[x]["ten"] for x in reversed(self.to_tien(i))) or "?"

    def chua_phan_loai(self, ids: list[int]) -> bool:
        that = [i for i in ids if i in self.theo_id and not la_chua_phan_loai(self.theo_id[i])]
        return not that

    def thu_tu(self) -> list[tuple[int, int]]:
        """(id, độ sâu) theo cây, cùng cấp xếp theo tên; bỏ danh mục 'chưa phân loại'."""
        con: dict[int, list[int]] = {}
        for i, d in self.theo_id.items():
            if la_chua_phan_loai(d):
                continue
            cha = int(d.get("cha") or 0)
            con.setdefault(cha if cha in self.theo_id else 0, []).append(i)
        ra: list[tuple[int, int]] = []

        def di(cha: int, sau: int):
            for i in sorted(con.get(cha, []), key=lambda x: _gap(self.theo_id[x]["ten"])):
                if any(i == x for x, _ in ra):
                    continue
                ra.append((i, sau))
                di(i, sau + 1)

        di(0, 0)
        return ra

    def dem(self, ds: list[dict], khoa: str = "dm") -> tuple[dict[int, int], int]:
        """Số mục trong từng danh mục (tính cả danh mục con) và số mục chưa phân loại."""
        nhom: dict[int, set] = {}
        chua = 0
        for it in ds:
            ids = [int(x) for x in it.get(khoa) or [] if str(x).isdigit()]
            if self.chua_phan_loai(ids):
                chua += 1
                continue
            for i in ids:
                for t in self.to_tien(i):
                    nhom.setdefault(t, set()).add(it.get("id"))
        return {i: len(s) for i, s in nhom.items()}, chua

    def thuoc(self, it: dict, dm: int, khoa: str = "dm") -> bool:
        return any(dm in self.to_tien(int(x)) for x in it.get(khoa) or [] if str(x).isdigit())

    def lua_chon(self) -> list[tuple[int, str]]:
        """Danh sách (id, đường dẫn) cho ô chọn danh mục."""
        return [(i, self.duong(i)) for i, _ in self.thu_tu()]


def gon_danh_muc(ds: list[dict]) -> list[dict]:
    """Từ danh mục WooCommerce/WordPress (id, name, parent, slug) sang dạng gọn lưu file."""
    return [{"id": d["id"], "ten": html.unescape(d.get("name", "")), "cha": int(d.get("parent") or 0), "slug": d.get("slug", "")}
            for d in ds if isinstance(d, dict) and d.get("id")]
