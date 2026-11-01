"""Nói chuyện với thanhduc.vn qua REST API (WordPress + WooCommerce), đăng nhập bằng Application Password.

Mọi thứ tạo ra đều ở trạng thái Bản nháp (draft); người duyệt mở wp-admin bấm Đăng.
"""
from __future__ import annotations

import base64
import re

import httpx

from .so_ten import (  # noqa: F401  (giữ tên cũ cho chỗ khác import từ đây)
    HANG_MAY, TU_BO_QUA, _ten, _tu_noi_dung, _ty_le_tu_chung, bo_dau, cau_tim, chuan_ten, do_giong, do_giong_bai,
    ma_model, slug, tim_gan_giong, tu_khoa,
)

USER_AGENT = "ThanhDucDangWeb/1.0 (+https://quanlythanhduc.com)"


class LoiWeb(RuntimeError):
    def __init__(self, thong_bao: str, status: int | None = None, ma: str = ""):
        super().__init__(thong_bao)
        self.status = status
        self.ma = ma


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

    def sua_sp(self, sp_id: int, payload: dict) -> dict:
        """Ghi đè nội dung sản phẩm đã có (Viết lại). Giữ nguyên trạng thái và mã SKU trên web."""
        payload = {k: v for k, v in payload.items() if k not in ("status", "sku", "type")}
        return self._json("PUT", f"wc/v3/products/{int(sp_id)}", json=payload)

    def tat_ca_sp(self) -> list[dict]:
        """Mọi sản phẩm trên web (cả nháp), chỉ lấy các trường cần cho tab Trên web."""
        return self._tat_ca("wc/v3/products", {"status": "any", "_fields": "id,name,sku,status,permalink,categories,regular_price,price,images"})

    def lay_sp(self, sp_id: int) -> dict:
        return self._json("GET", f"wc/v3/products/{int(sp_id)}")

    def doi_danh_muc_sp(self, cap: list[tuple[int, list[int]]]) -> int:
        """Chỉ đổi danh mục (không đụng nội dung, giá, trạng thái); WooCommerce nhận tối đa 100 dòng mỗi lần."""
        so = 0
        for i in range(0, len(cap), 100):
            phan = [{"id": int(sp_id), "categories": [{"id": int(c)} for c in dm]} for sp_id, dm in cap[i:i + 100]]
            kq = self._json("POST", "wc/v3/products/batch", json={"update": phan})
            so += sum(1 for x in kq.get("update") or [] if isinstance(x, dict) and x.get("id") and not x.get("error"))
        return so

    def lay_sp_theo_ids(self, ids: list[int]) -> list[dict]:
        ra = []
        for i in range(0, len(ids), 100):
            phan = ids[i:i + 100]
            ra += self._json("GET", "wc/v3/products", params={"include": ",".join(map(str, phan)), "status": "any", "per_page": 100})
        return ra

    # ---- bài viết ----
    def sua_bai(self, bai_id: int, payload: dict) -> dict:
        payload = {k: v for k, v in payload.items() if k != "status"}
        return self._json("POST", f"wp/v2/posts/{int(bai_id)}", json=payload)

    def lay_bai_theo_ids(self, ids: list[int]) -> list[dict]:
        ra = []
        for i in range(0, len(ids), 100):
            phan = ids[i:i + 100]
            ra += self._json("GET", "wp/v2/posts", params={
                "include": ",".join(map(str, phan)), "status": "publish,draft,pending,future,private",
                "context": "edit", "per_page": 100})
        return ra

    def tat_ca_bai(self) -> list[dict]:
        """Mọi bài viết trên web (cả nháp, chờ duyệt, hẹn giờ)."""
        return self._tat_ca("wp/v2/posts", {"status": "publish,draft,pending,future,private", "context": "edit",
                                            "_fields": "id,title,status,link,categories,date,featured_media,content"})

    def lay_bai(self, bai_id: int) -> dict:
        return self._json("GET", f"wp/v2/posts/{int(bai_id)}", params={"context": "edit"})

    def doi_chuyen_muc_bai(self, bai_id: int, cm: list[int]) -> dict:
        return self._json("POST", f"wp/v2/posts/{int(bai_id)}", json={"categories": [int(c) for c in cm]})

    def lay_anh_theo_ids(self, ids: list[int]) -> dict[int, dict]:
        """Nhiều ảnh một lượt (100 ảnh mỗi lần gọi): id → {src: ảnh gốc, nho: ảnh nhỏ cho danh sách}."""
        ra: dict[int, dict] = {}
        ids = sorted({int(i) for i in ids if int(i) > 0})
        for i in range(0, len(ids), 100):
            for m in self._json("GET", "wp/v2/media", params={"include": ",".join(map(str, ids[i:i + 100])), "per_page": 100,
                                                              "_fields": "id,source_url,media_details"}):
                co = ((m.get("media_details") or {}).get("sizes") or {})
                nho = next((co[k].get("source_url") for k in ("medium", "thumbnail", "medium_large") if isinstance(co.get(k), dict)), "")
                ra[int(m["id"])] = {"src": m.get("source_url") or "", "nho": nho or m.get("source_url") or ""}
        return ra

    def lay_anh(self, media_id: int) -> dict:
        return self._json("GET", f"wp/v2/media/{int(media_id)}")

    def tai_ve(self, url: str, toi_da: int = 15 * 1024 * 1024) -> bytes:
        """Tải ảnh đang có trên web về để xử lý lại (chỉ ảnh của chính trang web này)."""
        if not url.startswith(self.url + "/"):
            raise LoiWeb("Chỉ tải ảnh nằm trên chính trang web.")
        try:
            r = self.client.get(url, headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError as e:
            raise LoiWeb(f"Không tải được ảnh {url}: {e}") from e
        if r.status_code >= 400 or len(r.content) > toi_da:
            raise LoiWeb(f"Không tải được ảnh {url} ({r.status_code}).")
        return r.content

    def chuyen_muc_bai(self) -> list[dict]:
        return self._tat_ca("wp/v2/categories")

    def tao_bai(self, payload: dict) -> dict:
        payload = {**payload, "status": "draft"}
        return self._json("POST", "wp/v2/posts", json=payload)

    # ---- ảnh ----
    def xoa_anh(self, media_id: int) -> None:
        """Xóa hẳn ảnh khỏi thư viện Media (cả các cỡ thu nhỏ WordPress tự tạo)."""
        self._goi("DELETE", f"wp/v2/media/{int(media_id)}", params={"force": "true"})

    def anh_con_dung(self, ten_file: str, bo_sp_id: int) -> bool:
        """Ảnh còn nằm trong nội dung sản phẩm, bài viết hay trang nào khác không (tìm theo tên file)."""
        goc = re.sub(r"\.[a-z0-9]+$", "", ten_file.rsplit("/", 1)[-1], flags=re.I)
        if len(goc) < 4:
            return True  # tên quá ngắn, không dám chắc: giữ lại
        for duong, them in (("wc/v3/products", {"status": "any"}), ("wp/v2/posts", {"status": "any"}),
                            ("wp/v2/pages", {"status": "any"})):
            for it in self._json("GET", duong, params={"search": goc, "per_page": 20, "_fields": "id", **them}):
                if not (duong.startswith("wc/") and int(it.get("id", 0)) == int(bo_sp_id)):
                    return True
        return False

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


_IMG_RE = re.compile(r"<img\b[^>]*>", re.I)


def anh_trong_bai(html: str, toi_da: int = 8) -> list[dict]:
    """Ảnh trong thân bài: [{id: số media (0 nếu không rõ), src}] theo thứ tự xuất hiện, bỏ trùng."""
    ra, thay = [], set()
    for the in _IMG_RE.findall(html or ""):
        m = re.search(r'\ssrc=["\']([^"\']+)["\']', the, re.I)
        src = m.group(1).strip() if m else ""
        if not src.startswith(("http://", "https://")) or src in thay:
            continue
        thay.add(src)
        mid = re.search(r"wp-image-(\d+)", the)
        ra.append({"id": int(mid.group(1)) if mid else 0, "src": src})
        if len(ra) >= toi_da:
            break
    return ra


def thay_anh_trong_bai(html: str, cu: str, moi: str, id_cu: int = 0, id_moi: int = 0) -> str:
    """Đổi một ảnh trong thân bài sang ảnh mới (bỏ srcset/sizes cũ để trình duyệt không lấy lại ảnh cũ)."""
    def doi(m):
        the = m.group(0)
        if cu not in the:
            return the
        the = re.sub(r'\s(srcset|sizes)=("[^"]*"|\'[^\']*\')', "", the, flags=re.I).replace(cu, moi)
        if id_cu and id_moi:
            the = the.replace(f"wp-image-{id_cu}", f"wp-image-{id_moi}")
        return the
    html = _IMG_RE.sub(doi, html or "")
    if id_cu and id_moi:
        html = re.sub(rf'("id":\s*){id_cu}\b', rf"\g<1>{id_moi}", html)  # khối ảnh Gutenberg <!-- wp:image {"id":123} -->
    return html


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


def _tim(wp: "WordPress", duong: str, ten: str, them: dict) -> list[dict]:
    q = cau_tim(ten)
    if not q:
        return []
    return wp._json("GET", duong, params={"search": q, "per_page": 30, **them})


def sp_co_the_trung(wp: "WordPress", ten: str) -> list[dict]:
    return tim_gan_giong(_tim(wp, "wc/v3/products", ten, {"status": "any"}), ten)


def bai_co_the_trung(wp: "WordPress", ten: str) -> list[dict]:
    return tim_gan_giong(_tim(wp, "wp/v2/posts", ten, {"status": "publish,draft,pending,future,private", "context": "edit"}), ten, ham=do_giong_bai)
