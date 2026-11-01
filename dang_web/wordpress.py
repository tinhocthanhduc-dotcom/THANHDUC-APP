"""Nói chuyện với thanhduc.vn qua REST API (WordPress + WooCommerce), đăng nhập bằng Application Password.

Mọi thứ tạo ra đều ở trạng thái Bản nháp (draft); người duyệt mở wp-admin bấm Đăng.
"""
from __future__ import annotations

import base64
import html
import re
import unicodedata

import httpx

USER_AGENT = "ThanhDucDangWeb/1.0 (+https://quanlythanhduc.com)"


class LoiWeb(RuntimeError):
    def __init__(self, thong_bao: str, status: int | None = None, ma: str = ""):
        super().__init__(thong_bao)
        self.status = status
        self.ma = ma


def bo_dau(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def chuan_ten(s: str) -> str:
    return re.sub(r"\s+", " ", bo_dau(html.unescape(s)).lower()).strip()


def slug(s: str, toi_da: int = 80) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", bo_dau(s).lower()).strip("-")
    return s[:toi_da].rstrip("-")


class WordPress:
    def __init__(self, url: str, user: str, app_password: str, client: httpx.Client | None = None):
        if not url:
            raise LoiWeb("Chưa đặt WP_URL.")
        self.url = url.rstrip("/")
        token = base64.b64encode(f"{user}:{app_password}".encode()).decode()
        self._headers = {"Authorization": f"Basic {token}", "User-Agent": USER_AGENT}
        self.client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True)

    # ---- nền ----
    def _goi(self, method: str, duong: str, **kw) -> httpx.Response:
        headers = {**self._headers, **kw.pop("headers", {})}
        try:
            r = self.client.request(method, f"{self.url}/wp-json/{duong.lstrip('/')}", headers=headers, **kw)
        except httpx.HTTPError as e:
            raise LoiWeb(f"Không nối được tới {self.url}: {e}") from e
        if r.status_code >= 400:
            ma, tin = "", r.text[:300]
            try:
                j = r.json()
                ma, tin = j.get("code", ""), j.get("message", tin)
            except ValueError:
                pass
            raise LoiWeb(f"Web trả lỗi {r.status_code} ({ma}): {tin}", r.status_code, ma)
        return r

    def _json(self, method: str, duong: str, **kw):
        return self._goi(method, duong, **kw).json()

    def _tat_ca(self, duong: str, params: dict | None = None) -> list[dict]:
        ket_qua, trang = [], 1
        while True:
            r = self._goi("GET", duong, params={**(params or {}), "per_page": 100, "page": trang})
            ket_qua += r.json()
            if trang >= int(r.headers.get("X-WP-TotalPages", "1") or 1):
                return ket_qua
            trang += 1

    # ---- kiểm tra ----
    def toi(self) -> dict:
        return self._json("GET", "wp/v2/users/me", params={"context": "edit"})

    def dem_san_pham(self) -> int:
        r = self._goi("GET", "wc/v3/products", params={"per_page": 1, "status": "any"})
        return int(r.headers.get("X-WP-Total", "0") or 0)

    # ---- sản phẩm ----
    def tim_sp_theo_sku(self, sku: str) -> dict | None:
        ds = self._json("GET", "wc/v3/products", params={"sku": sku, "status": "any"})
        return ds[0] if ds else None

    def danh_muc_sp(self) -> list[dict]:
        return self._tat_ca("wc/v3/products/categories")

    def tao_sp(self, payload: dict) -> dict:
        payload = {**payload, "status": "draft"}
        return self._json("POST", "wc/v3/products", json=payload)

    # ---- bài viết ----
    def chuyen_muc_bai(self) -> list[dict]:
        return self._tat_ca("wp/v2/categories")

    def tao_bai(self, payload: dict) -> dict:
        payload = {**payload, "status": "draft"}
        return self._json("POST", "wp/v2/posts", json=payload)

    # ---- ảnh ----
    def tai_anh(self, data: bytes, ten_file: str, mime: str, alt: str = "", tieu_de: str = "") -> dict:
        media = self._json(
            "POST",
            "wp/v2/media",
            content=data,
            headers={"Content-Type": mime, "Content-Disposition": f'attachment; filename="{ten_file}"'},
        )
        if alt or tieu_de:
            media = self._json("POST", f"wp/v2/media/{media['id']}", json={"alt_text": alt, "title": tieu_de or alt})
        return media


def link_sua(wp_url: str, post_id: int) -> str:
    return f"{wp_url.rstrip('/')}/wp-admin/post.php?post={post_id}&action=edit"


def tim_danh_muc(ten_nhom: str, danh_muc: list[dict], bang_map: dict[str, str] | None = None) -> dict | None:
    """Ghép nhóm hàng trong app với danh mục WooCommerce: theo bảng nhom-danh-muc.json trước, rồi trùng tên (bỏ dấu)."""
    if not ten_nhom:
        return None
    muc_tieu = ten_nhom
    for k, v in (bang_map or {}).items():
        if chuan_ten(k) == chuan_ten(ten_nhom):
            muc_tieu = v
            break
    can = chuan_ten(muc_tieu)
    for dm in danh_muc:
        if chuan_ten(dm.get("name", "")) == can or dm.get("slug") == slug(muc_tieu):
            return dm
    return None
