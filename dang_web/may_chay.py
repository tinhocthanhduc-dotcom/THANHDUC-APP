"""Máy đăng web chạy nền (lệnh `cho-viec`, container dang-web-may).

Nhận việc từ màn Đăng web trong app qua thư mục chung (xem hang_doi.py):
soạn (kiểm trùng → AI viết → xử lý ảnh) rồi chờ anh xem trước; anh bấm Đăng thì đăng nháp lên web.
Database app chỉ đọc. Mọi thứ đăng lên web đều là Bản nháp (bài viết lại giữ nguyên trạng thái trên web).
"""
from __future__ import annotations

import re
import time
import traceback
from pathlib import Path

from . import anh as anh_mod
from . import dang
from . import hang_doi as hd
from .cau_hinh import CauHinh
from .viet_bai import ChoTraLoi
from .so_ten import _ten
from .tren_web import CayDanhMuc, gon_danh_muc
from .wordpress import LoiWeb, WordPress, bai_co_the_trung, link_sua, slug, sp_co_the_trung, tim_danh_muc



SO_ANH_TRONG_BAI = 3  # tối đa số ảnh chèn vào thân mô tả / thân bài
SO_ANH_BAI_VIET = 4  # bài viết: ảnh đầu làm ảnh đại diện, tối đa 3 ảnh sau chèn vào thân bài


def in_(s: str = "") -> None:
    print(f"[{hd.bay_gio():%H:%M:%S}] {s}", flush=True)


def ten_ai(ch: CauHinh) -> str:
    """Tên loại AI 'tự động' đang dùng, để màn app ghi rõ (trống nếu chưa có khóa)."""
    ncc = ch.ai_nha_cung_cap
    if ncc == "mau":
        return ""
    if not ncc:
        ncc = "claude" if ch.anthropic_api_key else ("openai" if ch.openai_api_key else "")
    if ncc == "openai" and ch.openai_api_key:
        return f"ChatGPT ({ch.openai_model})"
    if ncc == "claude" and ch.anthropic_api_key:
        return f"Claude ({ch.ai_model})"
    return ""


class MayDangWeb:
    def __init__(self, ch: CauHinh, wp: WordPress | None = None, nguoi_viet=None, ket_noi_db=None):
        self.ch = ch
        self.goc = ch.thu_muc
        self._wp = wp
        self._nguoi_viet = nguoi_viet  # dùng trong kiểm thử; thật thì chọn theo cách viết của từng việc
        self._ket_noi_db = ket_noi_db
        self._danh_muc: list[dict] | None = None

    # ---------- phụ ----------
    @property
    def co_wp(self) -> bool:
        return self._wp is not None or bool(self.ch.wp_user and self.ch.wp_app_password)

    def wp(self) -> WordPress:
        if self._wp is None:
            if not (self.ch.wp_user and self.ch.wp_app_password):
                raise RuntimeError("Chưa đặt WP_USER và WP_APP_PASSWORD trong deploy/.env trên Z2.")
            self._wp = WordPress(self.ch.wp_url, self.ch.wp_user, self.ch.wp_app_password)
        return self._wp

    def danh_muc(self) -> list[dict]:
        if self._danh_muc is None:
            self._danh_muc = self.wp().danh_muc_sp()
            hd.ghi_json(hd.file_danh_muc(self.goc), sorted({d.get("name", "") for d in self._danh_muc if d.get("name")}))
        return self._danh_muc

    def lay_san_pham(self, code: str):
        if self._ket_noi_db:
            return self._ket_noi_db(code)
        from .du_lieu import ket_noi, lay_san_pham

        with ket_noi(self.ch.database_url) as conn:
            return lay_san_pham(conn, [code]).get(code)

    def nguoi_viet(self, v: dict):
        if self._nguoi_viet is not None:
            return self._nguoi_viet
        from .viet_bai import NguoiVietChatGPTWeb, NguoiVietMau, chon_nguoi_viet

        cach = v.get("cach_viet")
        if cach == "mau":
            return NguoiVietMau()
        if cach == "chatgpt_web":
            return NguoiVietChatGPTWeb(hd.thu_muc_mot_viec(self.goc, v["id"]))
        if not ten_ai(self.ch):
            raise RuntimeError("Chưa có khóa AI (OPENAI_API_KEY hoặc ANTHROPIC_API_KEY). Chọn cách viết ChatGPT web hoặc Mẫu có sẵn.")
        return chon_nguoi_viet(self.ch)

    def _cap_nhat(self, v: dict, trang_thai: str, **them) -> None:
        def ham(x):
            x.update(them)
            x["trang_thai"] = trang_thai

        hd.sua(self.goc, v["id"], ham)

    def _anh_goc(self, v: dict) -> list[Path]:
        d = hd.thu_muc_mot_viec(self.goc, v["id"])
        files = [d / f for f in v.get("anh_goc", []) if (d / f).exists()]
        if not files and v["loai"] == "san_pham":
            files = anh_mod.tim_anh(self.ch.anh_goc, v["ma"])
        return files

    # ---------- soạn ----------
    def soan(self, v: dict) -> None:
        if v.get("kieu") == "sua_web":
            self._soan_sua_web(v)
        elif v.get("kieu") == "xep_danh_muc":
            self._soan_xep(v)
        elif v["loai"] == "san_pham":
            self._soan_san_pham(v)
        else:
            self._soan_bai(v)

    def _kiem_trung(self, v: dict, ham, ten: str) -> bool:
        """True nếu đã chuyển việc sang Có thể trùng."""
        if v.get("van_dang") or v.get("wp_id") or not self.co_wp:
            return False
        giong = ham(self.wp(), ten)
        if not giong:
            return False
        for g in giong:
            if g.get("id"):
                g["link_sua"] = link_sua(self.ch.wp_url, g["id"])
        self._cap_nhat(v, hd.CO_THE_TRUNG, trung=giong[:5], trung_sku=False)
        dang.ghi_ket_qua(self.ch.ket_qua, dang.dong_ket_qua(
            "Sản phẩm" if v["loai"] == "san_pham" else "Bài viết", v.get("ma") or v.get("chu_de", ""), "CÓ THỂ TRÙNG",
            [f"{g['ten']} ({g['trang_thai']}) {g['link']}" for g in giong]))
        return True

    def _goi_ai(self, v: dict, nv, goi):
        """Gọi AI; với ChatGPT web thì dừng chờ anh dán, câu trả lời sai dạng thì xin dán lại."""
        try:
            return goi()
        except ChoTraLoi as e:
            self._cap_nhat(v, hd.CHO_DAN, lenh_chatgpt=e.file_lenh.read_text(encoding="utf-8"),
                           file_tra_loi=e.file_tra_loi.name)
            return None
        except Exception as e:
            ten = v.get("file_tra_loi")
            if v.get("cach_viet") == "chatgpt_web" and ten:
                p = hd.thu_muc_mot_viec(self.goc, v["id"]) / ten
                if p.exists():
                    p.unlink()
                    self._cap_nhat(v, hd.CHO_DAN, loi=f"Câu trả lời chưa đúng dạng ({e}). Dán lại đúng khối JSON ChatGPT trả về.")
                    return None
            raise

    def _soan_san_pham(self, v: dict) -> None:
        sp = self.lay_san_pham(v["ma"])
        if not sp:
            raise RuntimeError(f"Không thấy mã {v['ma']} trong Hàng hóa (hoặc đã ngừng dùng).")
        if self.co_wp and not v.get("wp_id") and not v.get("van_dang"):
            co_san = self.wp().tim_sp_theo_sku(sp.code)
            if co_san:
                g = {"id": co_san["id"], "ten": co_san.get("name", ""), "link": co_san.get("permalink", ""),
                     "trang_thai": co_san.get("status", ""), "link_sua": link_sua(self.ch.wp_url, co_san["id"])}
                self._cap_nhat(v, hd.CO_THE_TRUNG, trung=[g], trung_sku=True)
                return
        if self._kiem_trung(v, sp_co_the_trung, sp.name):
            return
        files = self._anh_goc(v)  # không có ảnh vẫn soạn được: lên web dạng chữ, bổ sung ảnh sau bằng Sửa bằng AI
        nv = self.nguoi_viet(v)
        kq = self._goi_ai(v, nv, lambda: nv.viet_san_pham(sp, v.get("ghi_chu") or dang.doc_ghi_chu(self.ch, sp.code),
                                                          len(files), self.ch.hotline))
        if kq is None:
            return
        d = kq.du_lieu
        ten_anh = slug(d.get("slug") or d["ten_web"], 60) or sp.code.lower()
        anh_ds = dang.xu_ly_anh(self.ch, ten_anh, files, self.ch.tach_nen and v.get("tach_nen", True), logo=not v.get("da_co_logo"))
        dm = None
        try:
            dm = tim_danh_muc(sp.group_name, self.danh_muc(), dang.doc_bang_nhom(self.ch)) if self.co_wp else None
        except LoiWeb:
            pass
        self._cap_nhat(
            v, hd.XEM_TRUOC,
            ten=sp.name, noi_dung=d, anh_web=[a for a, _ in anh_ds], anh_nguon=[str(f) for f in files],
            chi_phi=kq.mo_ta_chi_phi, model=getattr(nv, "model", ""), ghi_chu_ai=kq.ghi_chu,
            can_kiem_tra=d.get("can_kiem_tra", []), gia=dang.gia_web(sp, self.ch),
            danh_muc=(dm or {}).get("name") or sp.group_name, loi="",
        )

    def _soan_bai(self, v: dict) -> None:
        cd = v["chu_de"]
        if self._kiem_trung(v, bai_co_the_trung, cd):
            return
        nv = self.nguoi_viet(v)
        kq = self._goi_ai(v, nv, lambda: nv.viet_bai(cd, v.get("ghi_chu") or dang.doc_ghi_chu(self.ch, slug(cd, 60)), self.ch.hotline))
        if kq is None:
            return
        d = kq.du_lieu
        files = self._anh_goc(v)[:SO_ANH_BAI_VIET]
        anh_ds = dang.xu_ly_anh(self.ch, slug(d.get("slug") or d["tieu_de"], 60), files, False,
                                logo=not v.get("da_co_logo")) if files else []
        self._cap_nhat(
            v, hd.XEM_TRUOC,
            ten=d["tieu_de"], noi_dung=d, anh_web=[a for a, _ in anh_ds], anh_nguon=[str(f) for f in files],
            chi_phi=kq.mo_ta_chi_phi, model=getattr(nv, "model", ""), ghi_chu_ai=kq.ghi_chu,
            can_kiem_tra=d.get("can_kiem_tra", []), loi="",
        )

    # ---------- đăng ----------
    def _anh_len_web(self, v: dict, alt: str, tach_nen: bool) -> list[dict]:
        ds = [(t, (self.ch.anh_da_xu_ly / t).read_bytes()) for t in v.get("anh_web", []) if (self.ch.anh_da_xu_ly / t).exists()]
        nguon = [Path(p) for p in v.get("anh_nguon", [])]
        return dang.tai_anh_len_day_du(self.wp(), self.ch, ds, alt, nguon, tach_nen, logo=not v.get("da_co_logo")) if ds else []

    def dang(self, v: dict) -> None:
        if v.get("kieu") == "sua_web":
            return self._dang_sua_web(v)
        if v.get("kieu") == "xep_danh_muc":
            return self._dang_xep(v)
        wp = self.wp()
        d = v["noi_dung"]
        if v["loai"] == "san_pham":
            sp = self.lay_san_pham(v["ma"])
            if not sp:
                raise RuntimeError(f"Không thấy mã {v['ma']} trong Hàng hóa (hoặc đã ngừng dùng).")
            anh = self._anh_len_web(v, d.get("alt_anh") or d["ten_web"], self.ch.tach_nen and v.get("tach_nen", True))
            ids = [m["id"] for m in anh]
            dm = tim_danh_muc(v.get("danh_muc") or sp.group_name, self.danh_muc(), dang.doc_bang_nhom(self.ch))
            payload = dang.payload_san_pham(sp, d, self.ch, ids, dm)
            payload["description"] = dang.chen_anh(payload["description"], anh[:SO_ANH_TRONG_BAI])
            gia = re.sub(r"\D", "", str(v.get("gia") or ""))
            if gia and int(gia) > 0:
                payload["regular_price"] = gia
            else:
                payload.pop("regular_price", None)
            if not ids:
                payload.pop("images", None)
            moi = wp.sua_sp(v["wp_id"], payload) if v.get("wp_id") else wp.tao_sp(payload)
            khoa_tt, khoa = "san_pham", sp.code
            ten = payload["name"]
        else:
            payload = {
                "title": d["tieu_de"],
                "slug": slug(d.get("slug") or d["tieu_de"]),
                "content": d["noi_dung_html"],
                "excerpt": d.get("tom_tat", ""),
                "meta": {
                    "_yoast_wpseo_title": d.get("seo_title", ""),
                    "_yoast_wpseo_metadesc": d.get("seo_description", ""),
                    "_yoast_wpseo_focuskw": d.get("tu_khoa_chinh", ""),
                },
            }
            if self.ch.chuyen_muc_bai:
                cm = tim_danh_muc(self.ch.chuyen_muc_bai, wp.chuyen_muc_bai())
                if cm:
                    payload["categories"] = [cm["id"]]
            anh = self._anh_len_web(v, d.get("alt_anh") or d["tieu_de"], False)
            if anh:
                payload["featured_media"] = anh[0]["id"]  # ảnh đầu làm ảnh đại diện, các ảnh sau chèn vào thân bài
                payload["content"] = dang.chen_anh(payload["content"], anh[1:1 + SO_ANH_TRONG_BAI])
            moi = wp.sua_bai(v["wp_id"], payload) if v.get("wp_id") else wp.tao_bai(payload)
            khoa_tt, khoa = "bai_viet", v["chu_de"]
            ten = d["tieu_de"]
        sua = link_sua(self.ch.wp_url, moi["id"])
        xem = moi.get("permalink") or moi.get("link") or ""
        luc = hd.bay_gio().isoformat(timespec="minutes")

        def ghi(tt):
            tt[khoa_tt][khoa] = {"id": moi["id"], "link": sua, "xem": xem, "ten": ten, "luc": luc, "viec": v["id"]}
            tt["nghi_trung"].pop(khoa, None)

        hd.sua_trang_thai(self.goc, ghi)
        self._cap_nhat(v, hd.DA_DANG, wp_id=moi["id"], link_sua=sua, link_xem=xem, trang_thai_web=moi.get("status", "draft"),
                       dang_luc=luc, loi="")
        dang.ghi_ket_qua(self.ch.ket_qua, dang.dong_ket_qua(
            "Sản phẩm" if v["loai"] == "san_pham" else "Bài viết", khoa,
            "VIẾT LẠI" if v.get("wp_id") else "BẢN NHÁP", [f"Duyệt: {sua}", f"Chi phí AI: {v.get('chi_phi', '')}"]))

    # ---------- làm mới trạng thái trên web ----------
    def lam_moi_web(self) -> None:
        wp = self.wp()
        tt = hd.doc_trang_thai(self.goc)
        ra = {"luc": hd.bay_gio().isoformat(timespec="minutes"), "san_pham": {}, "bai_viet": {}}
        for khoa, ham in (("san_pham", wp.lay_sp_theo_ids), ("bai_viet", wp.lay_bai_theo_ids)):
            ids = sorted({int(x["id"]) for x in tt[khoa].values() if isinstance(x, dict) and x.get("id")})
            if not ids:
                continue
            for it in ham(ids):
                ten = it.get("name") or it.get("title") or ""
                if isinstance(ten, dict):
                    ten = ten.get("raw") or ten.get("rendered") or ""
                ra[khoa][str(it["id"])] = {"ten": ten, "trang_thai": it.get("status", ""),
                                           "xem": it.get("permalink") or it.get("link") or ""}
        hd.ghi_json(hd.file_web(self.goc), ra)
        # Tab Trên web: mọi sản phẩm, bài viết và danh mục trên web (chỉ khi anh bấm Cập nhật từ web).
        dm_sp = gon_danh_muc(wp.danh_muc_sp())
        sp_web = [{"id": it["id"], "ten": _ten(it), "sku": str(it.get("sku") or ""), "trang_thai": it.get("status", ""),
                   "xem": it.get("permalink") or "", "sua": link_sua(self.ch.wp_url, it["id"]),
                   "dm": [c["id"] for c in it.get("categories") or [] if isinstance(c, dict) and c.get("id")],
                   "gia": str(it.get("regular_price") or it.get("price") or ""),
                   "anh": next((m.get("src", "") for m in it.get("images") or [] if isinstance(m, dict)), ""),
                   "so_anh": len(it.get("images") or [])} for it in wp.tat_ca_sp() if it.get("id")]
        hd.ghi_json(hd.file_sp_web(self.goc), {"luc": ra["luc"], "ds": sp_web, "danh_muc": dm_sp})
        bai_web = [{"id": it["id"], "ten": _ten(it), "trang_thai": it.get("status", ""), "xem": it.get("link") or "",
                    "sua": link_sua(self.ch.wp_url, it["id"]), "dm": [int(c) for c in it.get("categories") or [] if str(c).isdigit()],
                    "ngay": str(it.get("date") or "")[:10], "co_anh": bool(it.get("featured_media"))} for it in wp.tat_ca_bai() if it.get("id")]
        hd.ghi_json(hd.file_bai_web(self.goc), {"luc": ra["luc"], "ds": bai_web, "danh_muc": gon_danh_muc(wp.chuyen_muc_bai())})
        in_(f"Cập nhật từ web: {len(sp_web)} sản phẩm, {len(bai_web)} bài viết")
        self._danh_muc = None
        self.danh_muc()

    # ---------- sửa bài / sản phẩm đang có trên web ----------
    def _sua_ds_web(self, loai: str, web_id: int, **them) -> None:
        """Ghi luôn thay đổi vào danh sách đã lấy về để tab Trên web thấy ngay, khỏi đợi lần Cập nhật từ web sau."""
        f = hd.file_sp_web(self.goc) if loai == "san_pham" else hd.file_bai_web(self.goc)
        with hd.khoa(self.goc):
            d = hd.doc_json(f, {})
            if not isinstance(d, dict):
                return
            for it in d.get("ds") or []:
                if isinstance(it, dict) and it.get("id") == web_id:
                    it.update(them)
            hd.ghi_json(f, d)

    def _cay(self, loai: str) -> CayDanhMuc:
        f = hd.file_sp_web(self.goc) if loai == "san_pham" else hd.file_bai_web(self.goc)
        d = hd.doc_json(f, {})
        return CayDanhMuc(d.get("danh_muc") or [] if isinstance(d, dict) else [])

    @staticmethod
    def _hien_tai(loai: str, it: dict) -> dict:
        if loai == "san_pham":
            meta = {m.get("key"): m.get("value") for m in it.get("meta_data") or [] if isinstance(m, dict)}
            anh = [m for m in it.get("images") or [] if isinstance(m, dict)]
            return {"ten_web": _ten(it), "slug": it.get("slug", ""), "mo_ta_ngan_html": it.get("short_description") or "",
                    "mo_ta_html": it.get("description") or "", "seo_title": str(meta.get("_yoast_wpseo_title") or ""),
                    "seo_description": str(meta.get("_yoast_wpseo_metadesc") or ""), "tu_khoa_chinh": str(meta.get("_yoast_wpseo_focuskw") or ""),
                    "alt_anh": (anh[0].get("alt") if anh else "") or _ten(it), "can_kiem_tra": [],
                    "danh_muc": ", ".join(c.get("name", "") for c in it.get("categories") or [] if isinstance(c, dict))}

        def raw(k):
            x = it.get(k) or ""
            return (x.get("raw") or x.get("rendered") or "") if isinstance(x, dict) else str(x)

        yoast = it.get("yoast_head_json") or {}
        meta = it.get("meta") if isinstance(it.get("meta"), dict) else {}
        return {"tieu_de": _ten(it), "slug": it.get("slug", ""), "tom_tat": raw("excerpt"), "noi_dung_html": raw("content"),
                "seo_title": str(meta.get("_yoast_wpseo_title") or yoast.get("title") or ""),
                "seo_description": str(meta.get("_yoast_wpseo_metadesc") or yoast.get("description") or ""),
                "tu_khoa_chinh": str(meta.get("_yoast_wpseo_focuskw") or ""), "alt_anh": _ten(it), "the": [], "can_kiem_tra": []}

    def _anh_tu_web(self, v: dict, loai: str, it: dict) -> list[Path]:
        """Không có ảnh mới thì tải ảnh đang có trên web về để xử lý lại (nền trắng, logo)."""
        wp = self.wp()
        if loai == "san_pham":
            urls = [m.get("src", "") for m in it.get("images") or [] if isinstance(m, dict)][:6]
        else:
            urls = [wp.lay_anh(it["featured_media"]).get("source_url", "")] if it.get("featured_media") else []
        thu_muc = hd.thu_muc_mot_viec(self.goc, v["id"])
        ra = []
        for i, u in enumerate(u for u in urls if u):
            p = thu_muc / f"web-{i + 1}{Path(u.split('?')[0]).suffix.lower() or '.jpg'}"
            p.write_bytes(wp.tai_ve(u))
            ra.append(p)
        return ra

    def _soan_sua_web(self, v: dict) -> None:
        loai, web_id, sua = v["loai"], int(v["wp_id"]), v.get("sua") or {}
        wp = self.wp()
        it = wp.lay_sp(web_id) if loai == "san_pham" else wp.lay_bai(web_id)
        ht = self._hien_tai(loai, it)
        sp = self.lay_san_pham(v["ma"]) if v.get("ma") else None
        d, kq, nv = dict(ht), None, None
        if sua.get("noi_dung") or sua.get("seo"):
            if v.get("cach_viet") == "mau":
                raise RuntimeError("Sửa bằng AI cần AI tự động hoặc ChatGPT web.")
            nv = self.nguoi_viet(v)
            ghi_chu = v.get("ghi_chu") or ""
            kq = self._goi_ai(v, nv, (lambda: nv.sua_san_pham(ht, sp, ghi_chu, self.ch.hotline)) if loai == "san_pham"
                              else (lambda: nv.sua_bai(ht, ghi_chu, self.ch.hotline)))
            if kq is None:
                return
            moi = kq.du_lieu
            noi = ("ten_web", "mo_ta_ngan_html", "mo_ta_html", "alt_anh") if loai == "san_pham" else ("tieu_de", "tom_tat", "noi_dung_html", "alt_anh")
            if sua.get("noi_dung"):
                d.update({k: moi.get(k, d.get(k, "")) for k in noi})
            if sua.get("seo"):
                d.update({k: moi.get(k, "") for k in ("seo_title", "seo_description", "tu_khoa_chinh")})
            d["can_kiem_tra"] = moi.get("can_kiem_tra", [])
        anh_ds, files = [], []
        if sua.get("anh"):
            files = self._anh_goc(v) if (v.get("anh_goc") or v.get("ma")) else []
            if not files:
                files = self._anh_tu_web(v, loai, it)
            if not files:
                raise RuntimeError("Trên web chưa có ảnh để xử lý lại. Chụp hoặc chọn ảnh mới ở màn Sửa bằng AI.")
            tach = loai == "san_pham" and self.ch.tach_nen and v.get("tach_nen", True)
            files = files[:6 if loai == "san_pham" else SO_ANH_BAI_VIET]
            anh_ds = dang.xu_ly_anh(self.ch, slug(ht.get("slug") or ht.get("ten_web") or ht.get("tieu_de"), 60) or f"web-{web_id}",
                                    files, tach, logo=not v.get("da_co_logo"))
        cay = self._cay(loai)
        dm_id = v.get("danh_muc_id")
        self._cap_nhat(
            v, hd.XEM_TRUOC, noi_dung=d, anh_web=[a for a, _ in anh_ds], anh_nguon=[str(f) for f in files],
            chi_phi=kq.mo_ta_chi_phi if kq else "0đ", model=getattr(nv, "model", "") if nv else "không dùng AI",
            ghi_chu_ai=kq.ghi_chu if kq else [], can_kiem_tra=d.get("can_kiem_tra", []),
            gia=(dang.gia_web(sp, self.ch) if sp else re.sub(r"\D", "", str(it.get("regular_price") or ""))) if loai == "san_pham" else "",
            danh_muc=cay.duong(dm_id) if dm_id else "", link_xem=it.get("permalink") or it.get("link") or "",
            link_sua=link_sua(self.ch.wp_url, web_id), ten=ht.get("ten_web") or ht.get("tieu_de") or v.get("ten", ""), loi="",
        )

    def _dang_sua_web(self, v: dict) -> None:
        wp, loai, web_id, sua, d = self.wp(), v["loai"], int(v["wp_id"]), v.get("sua") or {}, v["noi_dung"]
        dm_id = v.get("danh_muc_id")
        anh = self._anh_len_web(v, d.get("alt_anh") or d.get("ten_web") or d.get("tieu_de") or "", False) if sua.get("anh") else []
        payload: dict = {}
        if loai == "san_pham":
            if sua.get("noi_dung"):
                payload.update(name=d["ten_web"], description=d["mo_ta_html"], short_description=d["mo_ta_ngan_html"])
            if sua.get("seo"):
                payload["meta_data"] = dang.meta_seo(d)
            if anh:
                payload["images"] = [{"id": m["id"]} for m in anh]
                payload["description"] = dang.chen_anh(payload.get("description", d.get("mo_ta_html", "")), anh[:SO_ANH_TRONG_BAI])
            gia = re.sub(r"\D", "", str(v.get("gia") or ""))
            if gia and int(gia) > 0:
                payload["regular_price"] = gia
            if dm_id:
                payload["categories"] = [{"id": int(dm_id)}]
            moi = wp.sua_sp(web_id, payload)
        else:
            if sua.get("noi_dung"):
                payload.update(title=d["tieu_de"], content=d["noi_dung_html"], excerpt=d.get("tom_tat", ""))
            if sua.get("seo"):
                payload["meta"] = {"_yoast_wpseo_title": d.get("seo_title", ""), "_yoast_wpseo_metadesc": d.get("seo_description", ""),
                                   "_yoast_wpseo_focuskw": d.get("tu_khoa_chinh", "")}
            if anh:
                payload["featured_media"] = anh[0]["id"]
                payload["content"] = dang.chen_anh(payload.get("content", d.get("noi_dung_html", "")), anh[1:1 + SO_ANH_TRONG_BAI])
            if dm_id:
                payload["categories"] = [int(dm_id)]
            moi = wp.sua_bai(web_id, payload)
        luc = hd.bay_gio().isoformat(timespec="minutes")
        doi = {"ten": _ten(moi) or v.get("ten", "")}
        if dm_id:
            doi["dm"] = [int(dm_id)]
        if anh and loai == "san_pham":
            doi.update(anh=anh[0].get("url", ""), so_anh=len(anh))
        self._sua_ds_web(loai, web_id, **doi)
        self._cap_nhat(v, hd.DA_DANG, link_sua=link_sua(self.ch.wp_url, web_id), link_xem=moi.get("permalink") or moi.get("link") or "",
                       trang_thai_web=moi.get("status", ""), dang_luc=luc, loi="")
        dang.ghi_ket_qua(self.ch.ket_qua, dang.dong_ket_qua("Sản phẩm" if loai == "san_pham" else "Bài viết", v.get("ten", str(web_id)),
                                                            "SỬA BẰNG AI", [f"Duyệt: {link_sua(self.ch.wp_url, web_id)}"]))

    # ---------- xếp danh mục bằng AI ----------
    def _soan_xep(self, v: dict) -> None:
        loai, muc = v["loai"], [m for m in v.get("muc") or [] if isinstance(m, dict) and m.get("id")]
        cay = self._cay(loai)
        lua = cay.lua_chon()
        if not lua:
            raise RuntimeError("Chưa có danh mục trên web. Bấm Cập nhật từ web trước.")
        if not ten_ai(self.ch) and self._nguoi_viet is None:
            raise RuntimeError("Xếp danh mục cần khóa AI (OPENAI_API_KEY hoặc ANTHROPIC_API_KEY).")
        from .viet_bai import chon_nguoi_viet

        nv = self._nguoi_viet or chon_nguoi_viet(self.ch)
        kq = nv.xep_danh_muc(loai, muc, [p for _, p in lua])
        theo_duong = {p: i for i, p in lua}
        goi_y = {int(x.get("id", 0)): x for x in kq.du_lieu.get("ket_qua") or [] if isinstance(x, dict)}
        de_xuat = []
        for m in muc:
            g = goi_y.get(int(m["id"])) or {}
            dm_id = theo_duong.get(str(g.get("danh_muc") or "").strip())
            de_xuat.append({"id": int(m["id"]), "ten": m.get("ten", ""), "dm_id": dm_id, "chac": bool(g.get("chac")) and bool(dm_id)})
        self._cap_nhat(v, hd.XEM_TRUOC, de_xuat=de_xuat, chi_phi=kq.mo_ta_chi_phi, model=getattr(nv, "model", ""), loi="")

    def _dang_xep(self, v: dict) -> None:
        wp, loai = self.wp(), v["loai"]
        ghi = [(int(x["id"]), int(x["dm_id"])) for x in v.get("ghi") or [] if x.get("id") and x.get("dm_id")]
        if loai == "san_pham":
            so = wp.doi_danh_muc_sp([(i, [dm]) for i, dm in ghi])
        else:
            so = 0
            for i, dm in ghi:
                wp.doi_chuyen_muc_bai(i, [dm])
                so += 1
        for i, dm in ghi:
            self._sua_ds_web(loai, i, dm=[dm])
        self._cap_nhat(v, hd.DA_DANG, so_da_ghi=so, dang_luc=hd.bay_gio().isoformat(timespec="minutes"), loi="")
        in_(f"Xếp danh mục: ghi {so} {'sản phẩm' if loai == 'san_pham' else 'bài viết'}")

    # ---------- lịch tự soạn ----------
    def kiem_lich(self) -> int:
        lich = hd.doc_lich(self.goc)
        now = hd.bay_gio()
        if not lich.get("bat") or now.weekday() == 6 or lich.get("lan_cuoi") == now.date().isoformat():
            return 0
        if now.strftime("%H:%M") < str(lich.get("gio") or "07:00"):
            return 0
        hd.ghi_lich(self.goc, {"lan_cuoi": now.date().isoformat()})
        tt = hd.doc_trang_thai(self.goc)
        dang_co = {(v.get("ma") or v.get("chu_de")) for v in hd.ds(self.goc) if v.get("trang_thai") not in hd.XONG}
        bo_qua = set(tt["nghi_trung"]) | dang_co
        cach = "tu_dong" if ten_ai(self.ch) else "mau"
        so = 0
        ma = hd.ma_co_anh(self.goc)
        for code in sorted(m for m in ma if m not in tt["san_pham"] and m not in bo_qua)[: int(lich.get("so_sp") or 0)]:
            hd.tao_viec(self.goc, "san_pham", {"ma": code, "ten": code, "cach_viet": cach, "nguon": "lich"})
            so += 1
        if cach == "tu_dong":
            for cd in [c for c in hd.doc_chu_de(self.goc) if c not in tt["bai_viet"] and c not in bo_qua][: int(lich.get("so_bai") or 0)]:
                hd.tao_viec(self.goc, "bai_viet", {"chu_de": cd, "ten": cd, "cach_viet": cach, "nguon": "lich"})
                so += 1
        in_(f"Lịch tự soạn: tạo {so} việc")
        return so

    # ---------- vòng chạy ----------
    def nhip(self, dang_lam: str | None = None) -> None:
        hd.ghi_nhip(self.goc, {"ai": ten_ai(self.ch), "co_wp": self.co_wp, "dang_lam": dang_lam, "web": self.ch.wp_url})

    def lam_mot_viec(self) -> bool:
        try:
            with dang.khoa(self.ch.thu_muc):  # lệnh tay scripts/dang-web.sh đang chạy thì đợi
                v = hd.nhan_viec(self.goc)
                if not v:
                    return False
                buoc = "soan" if v["trang_thai"] == hd.DANG_SOAN else "dang"
                in_(f"{'Soạn' if buoc == 'soan' else 'Đăng'} {v['loai']} {v.get('ma') or v.get('chu_de')}")
                self.nhip(v["id"])
                try:
                    self.soan(v) if buoc == "soan" else self.dang(v)
                except Exception as e:
                    in_(f"  LỖI: {e}")
                    traceback.print_exc()
                    self._cap_nhat(v, hd.LOI, loi=str(e)[:600], buoc_loi=buoc)
                return True
        except RuntimeError as e:
            if "Đang có một lần chạy khác" in str(e):
                return False
            raise

    def chay(self, mot_lan: bool = False, nghi: float = 3.0) -> None:
        self.ch.tao_thu_muc()
        so = hd.phuc_hoi(self.goc)
        in_(f"Máy đăng web bắt đầu. AI: {ten_ai(self.ch) or 'chưa có khóa'}; web: {'có' if self.co_wp else 'CHƯA đặt WP_USER/WP_APP_PASSWORD'}"
            + (f"; phục hồi {so} việc dở" if so else ""))
        while True:
            self.nhip()
            try:
                if hd.lay_yeu_cau_lam_moi(self.goc) and self.co_wp:
                    self.lam_moi_web()
                self.kiem_lich()
            except Exception as e:
                in_(f"  LỖI làm mới/lịch: {e}")
            co_viec = self.lam_mot_viec()
            if mot_lan:
                return
            if not co_viec:
                time.sleep(nghi)
