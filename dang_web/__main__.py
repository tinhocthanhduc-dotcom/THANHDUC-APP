"""Lệnh chạy:  python -m dang_web <lệnh>   (trên Z2 dùng scripts/dang-web.sh <lệnh>)

  kiem-tra                         kiểm tra kết nối app, web, Claude, ảnh
  san-pham --ma TD-0001,TD-0002    soạn và đăng nháp các mã này
  san-pham --tu-dong --so 3        tự chọn 3 mã đã có ảnh trong anh-goc mà chưa lên web
  bai-viet --chu-de "..."          viết một bài và đăng nháp
  bai-viet --tu-dong --so 2        lấy 2 chủ đề đầu tiên chưa viết trong chu-de.txt
  cho-viec                         chạy nền, nhận việc từ màn Đăng web trong app
Thêm --thu để chỉ soạn và lưu bản xem trước, không đăng.
"""
from __future__ import annotations

import argparse
import re
import sys
import traceback

from . import anh as anh_mod
from . import dang
from .cau_hinh import CauHinh
from .du_lieu import dem_san_pham, ket_noi, lay_san_pham
from .viet_bai import ChoTraLoi
from .wordpress import LoiWeb, WordPress, bai_co_the_trung, slug, sp_co_the_trung, tim_danh_muc

MA_RE = re.compile(r"^(TD-\d{4,})", re.I)


def in_(s: str = "") -> None:
    print(s, flush=True)


def _wp(ch: CauHinh) -> WordPress:
    if not ch.wp_user or not ch.wp_app_password:
        raise RuntimeError("Chưa đặt WP_USER và WP_APP_PASSWORD trong deploy/.env.")
    return WordPress(ch.wp_url, ch.wp_user, ch.wp_app_password)


def _nguoi_viet(ch: CauHinh):
    from .viet_bai import chon_nguoi_viet

    return chon_nguoi_viet(ch)


def _chon_nguoi_viet(ch: CauHinh, args):
    from .viet_bai import NguoiVietChatGPTWeb, NguoiVietMau

    if getattr(args, "khong_ai", False):
        return NguoiVietMau()
    if getattr(args, "chatgpt_web", False):
        return NguoiVietChatGPTWeb(ch.thu_muc / "chatgpt")
    return _nguoi_viet(ch)


def _bao_trung(giong: list[dict]) -> None:
    in_("   CÓ THỂ TRÙNG với bài đang có trên web (chưa gọi AI, chưa tốn tiền):")
    for g in giong[:5]:
        in_(f"     • {g['ten']} [{g['trang_thai']}] {g['link']}")
    in_("     Xem các link trên. Vẫn muốn đăng thì chạy lại lệnh, thêm --van-dang")


def _bao_cho_tra_loi(e) -> None:
    ten_lenh = e.file_lenh.name
    ten_tl = e.file_tra_loi.name
    in_("   CHỜ ANH: câu lệnh cho ChatGPT đã soạn. Làm 3 bước:")
    in_(f"     1) cat ~/THANHDUC-WEB/chatgpt/{ten_lenh}   → bôi đen, chép, dán vào chatgpt.com")
    in_(f"     2) nano ~/THANHDUC-WEB/chatgpt/{ten_tl}   → dán câu trả lời, Ctrl+O, Enter, Ctrl+X")
    in_("     3) chạy lại đúng lệnh vừa rồi")


# ---------------- kiem-tra ----------------
def lenh_kiem_tra(ch: CauHinh, args) -> int:
    loi = 0
    in_("1) Database app (chỉ đọc)")
    try:
        with ket_noi(ch.database_url) as conn:
            in_(f"   OK: {dem_san_pham(conn)} mã hàng đang dùng")
    except Exception as e:
        loi += 1
        in_(f"   LỖI: {e}")

    in_(f"2) Website {ch.wp_url}")
    try:
        wp = _wp(ch)
        me = wp.toi()
        vai_tro = ", ".join(me.get("roles", []))
        in_(f"   OK: đăng nhập là {me.get('name')} ({vai_tro})")
        if not set(me.get("roles", [])) & {"administrator", "shop_manager", "editor"}:
            in_("   CHÚ Ý: tài khoản này có thể không đủ quyền đăng sản phẩm.")
        in_(f"   WooCommerce: {wp.dem_san_pham()} sản phẩm, {len(wp.danh_muc_sp())} danh mục")
    except LoiWeb as e:
        loi += 1
        in_(f"   LỖI: {e}")
        if e.status == 401:
            in_("   Gợi ý: kiểm tra WP_USER (tên đăng nhập) và WP_APP_PASSWORD; một số hosting chặn header Authorization.")
    except Exception as e:
        loi += 1
        in_(f"   LỖI: {e}")

    in_("3) AI viết bài")
    try:
        nv = _nguoi_viet(ch)
        in_(f"   Model: {nv.model}")
        if not hasattr(nv, "client"):
            in_("   OK: không dùng AI (mẫu cố định, miễn phí)")
        else:
            m = nv.client.models.retrieve(nv.model)
            in_(f"   OK: {getattr(m, 'display_name', None) or getattr(m, 'id', None) or nv.model}")
    except Exception as e:
        loi += 1
        in_(f"   LỖI: {e}")

    in_("4) Ảnh")
    ch.tao_thu_muc()
    so = sum(1 for p in ch.anh_goc.rglob("*") if p.is_file() and p.suffix.lower() in anh_mod.DUOI_ANH)
    in_(f"   Thư mục anh-goc có {so} ảnh. Logo: {'có' if anh_mod.LOGO_MAC_DINH.exists() else 'THIẾU'}")
    if ch.tach_nen:
        try:
            import rembg  # noqa: F401

            in_("   Tách nền: sẵn sàng (lần đầu sẽ tải mô hình ~170 MB)")
        except Exception as e:
            in_(f"   Tách nền: không dùng được ({e}); ảnh vẫn xử lý nhưng giữ nền gốc.")
    in_()
    in_("TẤT CẢ OK" if loi == 0 else f"CÓ {loi} MỤC LỖI, xem bên trên.")
    return 0 if loi == 0 else 1


# ---------------- san-pham ----------------
def chon_tu_dong(ch: CauHinh, trang_thai: dict, so: int) -> list[str]:
    ma = set()
    if ch.anh_goc.exists():
        for p in ch.anh_goc.iterdir():
            m = MA_RE.match(p.name if p.is_dir() else p.stem)
            if m and (p.is_dir() or p.suffix.lower() in anh_mod.DUOI_ANH):
                ma.add(m.group(1).upper())
    con = sorted(m for m in ma if m not in trang_thai["san_pham"] and m not in trang_thai.get("nghi_trung", {}))
    return con[: max(0, so) * 3]  # lấy dư, vì có mã đã có trên web sẽ bị bỏ qua


def lenh_san_pham(ch: CauHinh, args) -> int:
    ch.tao_thu_muc()
    trang_thai = dang.doc_trang_thai(ch.file_trang_thai)
    if args.tu_dong:
        codes = chon_tu_dong(ch, trang_thai, args.so)
        gioi_han = args.so
    else:
        codes = [c.strip().upper() for c in (args.ma or "").replace(";", ",").split(",") if c.strip()]
        gioi_han = len(codes)
    if not codes:
        in_("Không có mã nào để làm. Bỏ ảnh tên TD-xxxx.jpg vào thư mục anh-goc hoặc dùng --ma.")
        return 0

    with ket_noi(ch.database_url) as conn:
        hang = lay_san_pham(conn, codes)
    wp = None if args.thu else _wp(ch)
    nv = _chon_nguoi_viet(ch, args)
    danh_muc = wp.danh_muc_sp() if wp else []
    bang_nhom = dang.doc_bang_nhom(ch)
    tach_nen = ch.tach_nen and not args.khong_tach_nen

    xong = loi = cho = trung = 0
    tong_phi = 0
    for code in codes:
        if xong >= gioi_han:
            break
        in_(f"— {code}")
        sp = hang.get(code)
        if not sp:
            in_("   bỏ qua: không có trong danh mục app (hoặc đã ngừng dùng)")
            continue
        if code in trang_thai["san_pham"] and not args.lam_lai and not args.thu:
            in_(f"   bỏ qua: đã đăng trước đó ({trang_thai['san_pham'][code].get('link', '')})")
            continue
        try:
            if wp:
                co_san = wp.tim_sp_theo_sku(code)
                if co_san and not args.lam_lai:
                    in_(f"   bỏ qua: web đã có sản phẩm mã {code} ({co_san.get('status')}) {co_san.get('permalink', '')}")
                    trang_thai["san_pham"][code] = {"id": co_san["id"], "link": co_san.get("permalink", ""), "co_san": True}
                    dang.ghi_trang_thai(ch.file_trang_thai, trang_thai)
                    continue
            if wp and not args.van_dang:
                giong = sp_co_the_trung(wp, sp.name)
                if giong:
                    _bao_trung(giong)
                    dang.ghi_ket_qua(ch.ket_qua, dang.dong_ket_qua("Sản phẩm", code, "CÓ THỂ TRÙNG",
                                                               [f"{g['ten']} ({g['trang_thai']}) {g['link']}" for g in giong]))
                    trung += 1
                    trang_thai["nghi_trung"][code] = [g["link"] for g in giong]
                    dang.ghi_trang_thai(ch.file_trang_thai, trang_thai)
                    continue
            files = anh_mod.tim_anh(ch.anh_goc, code)
            if not files and not args.khong_can_anh:
                in_("   bỏ qua: chưa có ảnh trong anh-goc (thêm --khong-can-anh nếu vẫn muốn đăng)")
                continue
            in_(f"   {len(files)} ảnh, AI đang viết…")
            kq = nv.viet_san_pham(sp, dang.doc_ghi_chu(ch, code), len(files), ch.hotline)
            d = kq.du_lieu
            tong_phi += kq.chi_phi_vnd
            ten_anh = slug(f"{d.get('slug') or d['ten_web']}", 60) or code.lower()
            in_("   xử lý ảnh…")
            anh_ds = dang.xu_ly_anh(ch, ten_anh, files, tach_nen)
            chi_tiet = [f"Tên web: {d['ten_web']}", f"Chi phí AI: {kq.mo_ta_chi_phi}"]
            chi_tiet += [f"Cần kiểm tra: {x}" for x in d.get("can_kiem_tra", [])] + kq.ghi_chu
            if args.thu:
                p = dang.ghi_xem_truoc(ch, code, d["ten_web"], d, [a for a, _ in anh_ds])
                in_(f"   XEM TRƯỚC: {p}")
                dang.ghi_ket_qua(ch.ket_qua, dang.dong_ket_qua("Sản phẩm (thử)", code, "chưa đăng", chi_tiet))
                xong += 1
                continue
            anh = dang.tai_anh_len_day_du(wp, ch, anh_ds, d["alt_anh"], files, tach_nen)
            dm = tim_danh_muc(sp.group_name, danh_muc, bang_nhom)
            if not dm:
                chi_tiet.append(f"Chưa gán danh mục: nhóm '{sp.group_name}' không khớp danh mục nào trên web")
            payload = dang.payload_san_pham(sp, d, ch, [m["id"] for m in anh], dm)
            payload["description"] = dang.chen_anh(payload["description"], anh[:3])
            moi = wp.tao_sp(payload)
            link = dang.link_bai(ch, moi["id"])
            trang_thai["san_pham"][code] = {"id": moi["id"], "link": link, "luc": dang.bay_gio().isoformat(timespec="minutes")}
            dang.ghi_trang_thai(ch.file_trang_thai, trang_thai)
            dang.ghi_ket_qua(ch.ket_qua, dang.dong_ket_qua("Sản phẩm", code, "BẢN NHÁP", [f"Duyệt: {link}"] + chi_tiet))
            in_(f"   ĐÃ ĐĂNG NHÁP: {link}")
            xong += 1
        except ChoTraLoi as e:
            cho += 1
            _bao_cho_tra_loi(e)
        except Exception as e:
            loi += 1
            in_(f"   LỖI: {e}")
            if args.chi_tiet:
                traceback.print_exc()
            dang.ghi_ket_qua(ch.ket_qua, dang.dong_ket_qua("Sản phẩm", code, "LỖI", [str(e)]))
    in_()
    phi = f"{tong_phi:,}".replace(",", ".")
    in_(f"Xong {xong} mã, lỗi {loi}" + (f", chờ dán ChatGPT {cho}" if cho else "") + (f", có thể trùng {trung}" if trung else "") + f". Chi phí AI ước tính ~{phi}đ.")
    in_(f"Kết quả ghi trong ~/THANHDUC-WEB/ket-qua/{dang.bay_gio():%Y-%m-%d}.md")
    return 0 if loi == 0 else 1


# ---------------- bai-viet ----------------
def doc_chu_de(ch: CauHinh) -> list[str]:
    try:
        dong = ch.file_chu_de.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [d.strip() for d in dong if d.strip() and not d.strip().startswith("#")]


def lenh_bai_viet(ch: CauHinh, args) -> int:
    ch.tao_thu_muc()
    trang_thai = dang.doc_trang_thai(ch.file_trang_thai)
    if args.tu_dong:
        chu_de = [c for c in doc_chu_de(ch) if c not in trang_thai["bai_viet"] and c not in trang_thai["nghi_trung"]][: args.so]
    else:
        chu_de = [args.chu_de] if args.chu_de else []
    if not chu_de:
        in_("Không có chủ đề. Ghi mỗi dòng một chủ đề vào ~/THANHDUC-WEB/chu-de.txt hoặc dùng --chu-de.")
        return 0
    wp = None if args.thu else _wp(ch)
    nv = _chon_nguoi_viet(ch, args)
    chuyen_muc = None
    if wp and ch.chuyen_muc_bai:
        chuyen_muc = tim_danh_muc(ch.chuyen_muc_bai, wp.chuyen_muc_bai())
    xong = loi = cho = trung = 0
    for cd in chu_de:
        in_(f"— {cd}")
        try:
            if wp and not args.van_dang:
                giong = bai_co_the_trung(wp, cd)
                if giong:
                    _bao_trung(giong)
                    dang.ghi_ket_qua(ch.ket_qua, dang.dong_ket_qua("Bài viết", cd, "CÓ THỂ TRÙNG",
                                                               [f"{g['ten']} ({g['trang_thai']}) {g['link']}" for g in giong]))
                    trung += 1
                    trang_thai["nghi_trung"][cd] = [g["link"] for g in giong]
                    dang.ghi_trang_thai(ch.file_trang_thai, trang_thai)
                    continue
            kq = nv.viet_bai(cd, dang.doc_ghi_chu(ch, slug(cd, 60)), ch.hotline)
            d = kq.du_lieu
            chi_tiet = [f"Tiêu đề: {d['tieu_de']}", f"Chi phí AI: {kq.mo_ta_chi_phi}"]
            chi_tiet += [f"Cần kiểm tra: {x}" for x in d.get("can_kiem_tra", [])] + kq.ghi_chu
            files = [anh_mod.Path(args.anh)] if getattr(args, "anh", None) else anh_mod.tim_anh(ch.anh_bai, slug(cd, 60).upper())
            anh_ds = dang.xu_ly_anh(ch, slug(d.get("slug") or d["tieu_de"], 60), files, False) if files else []
            if args.thu:
                p = dang.ghi_xem_truoc(ch, slug(d["tieu_de"], 60), d["tieu_de"], d, [a for a, _ in anh_ds])
                in_(f"   XEM TRƯỚC: {p}")
                dang.ghi_ket_qua(ch.ket_qua, dang.dong_ket_qua("Bài viết (thử)", cd, "chưa đăng", chi_tiet))
                xong += 1
                continue
            payload = {
                "title": d["tieu_de"],
                "slug": slug(d.get("slug") or d["tieu_de"]),
                "content": d["noi_dung_html"],
                "excerpt": d["tom_tat"],
                "meta": {
                    "_yoast_wpseo_title": d["seo_title"],
                    "_yoast_wpseo_metadesc": d["seo_description"],
                    "_yoast_wpseo_focuskw": d["tu_khoa_chinh"],
                },
            }
            if chuyen_muc:
                payload["categories"] = [chuyen_muc["id"]]
            if anh_ds:
                payload["featured_media"] = dang.tai_anh_len(wp, ch, anh_ds[:1], d["alt_anh"], files[:1], False)[0]
            moi = wp.tao_bai(payload)
            link = dang.link_bai(ch, moi["id"])
            trang_thai["bai_viet"][cd] = {"id": moi["id"], "link": link, "luc": dang.bay_gio().isoformat(timespec="minutes")}
            dang.ghi_trang_thai(ch.file_trang_thai, trang_thai)
            chi_tiet.append(f"SEO title: {d['seo_title']} | SEO description: {d['seo_description']} (dán vào ô Yoast nếu web chưa tự nhận)")
            dang.ghi_ket_qua(ch.ket_qua, dang.dong_ket_qua("Bài viết", cd, "BẢN NHÁP", [f"Duyệt: {link}"] + chi_tiet))
            in_(f"   ĐÃ ĐĂNG NHÁP: {link}")
            xong += 1
        except ChoTraLoi as e:
            cho += 1
            _bao_cho_tra_loi(e)
        except Exception as e:
            loi += 1
            in_(f"   LỖI: {e}")
            if args.chi_tiet:
                traceback.print_exc()
            dang.ghi_ket_qua(ch.ket_qua, dang.dong_ket_qua("Bài viết", cd, "LỖI", [str(e)]))
    in_()
    in_(f"Xong {xong} bài, lỗi {loi}" + (f", chờ dán ChatGPT {cho}" if cho else "") + (f", có thể trùng {trung}" if trung else "") + ".")
    return 0 if loi == 0 else 1


def tao_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="dang-web", description="Soạn và đăng nháp sản phẩm, bài viết lên thanhduc.vn")
    sub = ap.add_subparsers(dest="lenh", required=True)
    sub.add_parser("kiem-tra", help="kiểm tra kết nối")
    sub.add_parser("cho-viec", help="chạy nền, nhận việc từ màn Đăng web trong app")

    sp = sub.add_parser("san-pham", help="đăng nháp sản phẩm")
    sp.add_argument("--ma", help="các mã cách nhau dấu phẩy, ví dụ TD-0001,TD-0002")
    sp.add_argument("--tu-dong", action="store_true", help="tự chọn mã đã có ảnh mà chưa đăng")
    sp.add_argument("--so", type=int, default=3)
    sp.add_argument("--khong-tach-nen", action="store_true")
    sp.add_argument("--khong-can-anh", action="store_true")
    sp.add_argument("--lam-lai", action="store_true", help="đăng lại dù đã đăng trước đó")
    sp.add_argument("--khong-ai", action="store_true", help="không dùng AI, điền mẫu cố định (miễn phí)")

    bv = sub.add_parser("bai-viet", help="viết và đăng nháp bài viết")
    bv.add_argument("--chu-de")
    bv.add_argument("--tu-dong", action="store_true")
    bv.add_argument("--so", type=int, default=2)
    bv.add_argument("--anh", help="ảnh đại diện (đường dẫn trong container, ví dụ /data/anh-bai/x.jpg)")

    for p in (sp, bv):
        p.add_argument("--van-dang", action="store_true", help="bỏ qua cảnh báo có thể trùng bài trên web")
        p.add_argument("--chatgpt-web", action="store_true", help="không dùng API: soạn câu lệnh để dán vào ChatGPT web")
        p.add_argument("--thu", action="store_true", help="chỉ soạn, lưu bản xem trước, không đăng")
        p.add_argument("--chi-tiet", action="store_true", help="in chi tiết lỗi")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = tao_parser().parse_args(argv)
    ch = CauHinh.tu_moi_truong()
    try:
        if args.lenh == "kiem-tra":
            return lenh_kiem_tra(ch, args)
        if args.lenh == "cho-viec":
            from .may_chay import MayDangWeb

            MayDangWeb(ch).chay()
            return 0
        with dang.khoa(ch.thu_muc):
            if args.lenh == "san-pham":
                if not args.tu_dong and not args.ma:
                    in_("Cần --ma TD-xxxx hoặc --tu-dong.")
                    return 2
                return lenh_san_pham(ch, args)
            return lenh_bai_viet(ch, args)
    except RuntimeError as e:
        in_(f"LỖI: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
