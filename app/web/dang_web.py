"""Màn Đăng web (TUẤN duyệt mẫu 01/11/2026): soạn và đăng nháp sản phẩm, bài viết lên thanhduc.vn.

App chỉ ghi việc vào thư mục chung (dang_web/hang_doi.py); phần nặng (tách nền, AI, gọi web) do máy đăng
web chạy nền làm ở container riêng, nên app không chậm. Không đụng dữ liệu nghiệp vụ trong database.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from dang_web import hang_doi as hd
from dang_web import ve_anh
from dang_web.so_ten import do_giong_bai, so_khop_hang
from dang_web.tren_web import CayDanhMuc

from . import checked_form, paging, render, web_user
from .. import products as product_svc
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import Product, User
from ..security import has_role
from ..text import fold

router = APIRouter(include_in_schema=False)

# TUẤN duyệt 01/11/2026 (khung 9–13, D6–D7): "Đã đăng" thành "Trên web"; tab Hàng hóa bỏ, gộp vào Trên web › Sản phẩm.
TABS = (("san-pham", "Sản phẩm", "san_pham"), ("bai-viet", "Bài viết", "bai_viet"), ("tren-web", "Trên web", None))
TAB_CU = {"da-dang": "tren-web", "hang-hoa": "tren-web"}
LOC_SP = (("", "Tất cả"), ("khop", "Khớp mã app"), ("khong-ma", "Không có mã TD"), ("nhap", "Nháp"), ("chua-anh", "Chưa có ảnh"))
LOC_APP = (("chua-len", "Mã app chưa lên web"), ("co-the", "Có thể trùng"))
LOC_BAI = (("", "Tất cả"), ("hien", "Đang hiện"), ("nhap", "Nháp"), ("app", "App đã đăng"), ("chua-anh", "Chưa có ảnh đại diện"))
TOI_DA_SOAN_NHIEU = 10
TOI_DA_XEP = 40  # mỗi lượt xếp danh mục bằng AI
# Chỉ đọc: Hàng hóa đang dùng và tồn ở các kho bán (giống dang_web/du_lieu.py).
CAU_HANG = text("""
select p.code, p.name, p.group_name, p.sale_price,
       coalesce(sum(b.qty) filter (where w.kind = 'KHO' and w.active), 0) as ton
from products p
left join stock_balances b on b.product_code = p.code
left join warehouses w on w.code = b.warehouse_code
where p.active and not p.is_test
group by p.code
order by p.code
""")
# Hàng mới nhập (đơn mua, sản xuất, phiếu nhập tay) vào kho bán trong 30 ngày, để gợi ý đăng lên web (mẫu D7/7, 02/11/2026).
# Không tính tồn đầu kỳ, hàng khách trả, linh kiện thu hồi.
CAU_MOI_NHAP = text("""
select l.product_code as code, max(d.doc_date) as ngay
from stock_ledger l
join stock_docs d on d.id = l.doc_id
join warehouses w on w.code = l.warehouse_code
where d.kind = 'PN' and d.status = 'DA_GHI' and l.qty_in > 0 and not l.is_reversal and w.kind = 'KHO'
  and (d.source_kind is null or d.source_kind in ('PURCHASE_RECEIPT', 'SAN_XUAT'))
  and d.doc_date >= :tu
group by l.product_code
""")
MOI_NHAP_NGAY = 30
LOC = (
    ("xem-truoc", "Chờ anh xem trước", (hd.XEM_TRUOC,)),
    ("trung", "Có thể trùng", (hd.CO_THE_TRUNG,)),
    ("cho-dan", "Chờ dán ChatGPT", (hd.CHO_DAN,)),
    ("dang-soan", "Đang soạn", hd.DANG_CHAY),
    ("loi", "Lỗi", (hd.LOI,)),
)
TONE = {hd.XEM_TRUOC: "lam", hd.CO_THE_TRUNG: "cho", hd.CHO_DAN: "cho", hd.LOI: "huy", hd.DA_DANG: "xong", hd.BO: "nhap"}
WEB_TT = {"draft": ("Nháp", "cho"), "publish": ("Đang hiện", "xong"), "pending": ("Chờ duyệt", "cho"),
          "private": ("Riêng tư", "nhap"), "future": ("Hẹn giờ", "lam")}
TOI_DA_ANH = 6
SUA_SP = ("ten_web", "mo_ta_ngan_html", "mo_ta_html", "seo_title", "seo_description", "tu_khoa_chinh", "alt_anh")
SUA_BAI = ("tieu_de", "tom_tat", "noi_dung_html", "seo_title", "seo_description", "tu_khoa_chinh", "alt_anh")


def goc() -> Path:
    return Path(get_settings().web_dir)


def _can_ghi():
    loi = hd.kiem_ghi_duoc(goc())
    if loi:
        raise BusinessError("WEB_DIR", loi + " Trên Z2 chạy scripts/dang-web.sh bat-may rồi chạy lại lệnh cập nhật app.", status=503)


def _viec(viec_id: str) -> dict:
    v = hd.doc(goc(), viec_id)
    if not v:
        raise BusinessError("NOT_FOUND", "Không thấy việc này.", status=404)
    return v


def _khoa_viec(v: dict) -> str:
    return v.get("ma") or v.get("chu_de") or ""


def _cach_mac_dinh(nhip) -> str:
    # Codex trên Z2 (gói ChatGPT, không tốn API) đang chạy thì ưu tiên; không thì AI tự động nếu có khóa API.
    if hd.codex_song(goc()):
        return "codex"
    return "tu_dong" if nhip and nhip.get("ai") else "chatgpt_web"  # TUẤN 01/11: bỏ "Mẫu có sẵn"


def _them_nhan(v: dict) -> dict:
    v = dict(v)
    v["nhan"] = hd.NHAN.get(v.get("trang_thai"), v.get("trang_thai"))
    if v.get("trang_thai") == hd.CHO_DAN and v.get("cach_viet") == "codex":
        v["nhan"] = "Chờ Codex"
    v["tone"] = TONE.get(v.get("trang_thai"), "nhap")
    v["dang_chay"] = v.get("trang_thai") in hd.DANG_CHAY
    return v


# ---------------- danh sách ----------------
def _anh_dau(viec_id) -> str:
    v = hd.doc(goc(), viec_id) if viec_id else None
    return (v.get("anh_web") or [""])[0] if v else ""


@router.get("/dang-web")
def listing(request: Request, tab: str = "san-pham", loc: str = "", q: str = "", trang: str = "1", so: str = "",
            da_luu: str = "", nhom: str = "", ton: str = "", loai: str = "sp", dm: str = "",
            user: User = Depends(web_user), session: Session = Depends(get_session)):
    if tab in TAB_CU:
        query = {"tab": "tren-web", "loai": "sp"}
        if tab == "hang-hoa":
            query["loc"] = "co-the" if loc == "co-the" else "chua-len"
        return RedirectResponse("/dang-web?" + urlencode(query), status_code=303)
    tab = tab if tab in [t[0] for t in TABS] else "san-pham"
    g = goc()
    loi_thu_muc = hd.kiem_ghi_duoc(g)
    nhip = hd.doc_nhip(g)
    viec = [_them_nhan(v) for v in hd.ds(g)]
    con = [v for v in viec if v.get("trang_thai") not in hd.XONG]
    tt = hd.doc_trang_thai(g)
    sp_web, bai_web = _doc_web(hd.file_sp_web(g)), _doc_web(hd.file_bai_web(g))
    dem_tab = {"tren-web": len(sp_web["ds"]) + len(bai_web["ds"])}
    tabs = [dict(label=label, href=f"/dang-web?tab={key}", on=key == tab,
                 n=dem_tab.get(key, 0) if l is None else sum(1 for v in con if v["loai"] == l)) for key, label, l in TABS]
    ctx = dict(tab=tab, tabs=tabs, nhip=nhip, loi_thu_muc=loi_thu_muc, da_luu=da_luu, q=q, loc=loc,
               cach_viet=hd.CACH_VIET, cach_mac_dinh=_cach_mac_dinh(nhip), cho_lam_moi=not loi_thu_muc and hd.dang_cho_lam_moi(g),
               la_giam_doc=has_role(user, "director"), codex_sk=None if loi_thu_muc else hd.suc_khoe_codex(g))
    if tab == "tren-web":
        return _tab_tren_web(request, session, g, ctx, tt, sp_web, bai_web, loai, loc, q.strip(), dm, nhom, ton, trang, so)
    loai = dict((t[0], t[2]) for t in TABS)[tab]
    if loai == "san_pham" and not loi_thu_muc:
        ctx["hang_moi"] = _hang_moi(session, g)
    cua_tab = [v for v in con if v["loai"] == loai]
    tiles = [dict(label=label, n=sum(1 for v in cua_tab if v["trang_thai"] in sts),
                  href=f"/dang-web?{urlencode(dict(tab=tab, loc='' if loc == key else key))}", on=loc == key) for key, label, sts in LOC]
    sts = dict((k, s) for k, _, s in LOC).get(loc)
    rows = [v for v in cua_tab if not sts or v["trang_thai"] in sts]
    gan_day = [v for v in viec if v["loai"] == loai and v.get("trang_thai") == hd.DA_DANG][:5]
    chu_de = []
    if loai == "bai_viet":
        dang_co = {v.get("chu_de") for v in con}
        bai_tren_web = _doc_web(hd.file_bai_web(g))["ds"]
        ghi_chu = hd.doc_ghi_chu_chu_de(g)
        chu_de = [dict(ten=c, xong=c in tt["bai_viet"], dang=c in dang_co, trung=c in tt["nghi_trung"],
                       tren_web=_bai_giong(c, bai_tren_web), ghi_chu=ghi_chu.get(c, "")) for c in hd.doc_chu_de(g)]
    return render(request, "dang_web.jinja", rows=rows, tiles=tiles, gan_day=gan_day, chu_de=chu_de, lich=hd.doc_lich(g),
                  co_chay=any(v["dang_chay"] for v in viec), **ctx)


# ---------------- tab Hàng hóa: mã nào đã có trên web ----------------
_KHOP_CACHE: dict = {}


def _khop(hang: list[tuple[str, str]], ds_web: list[dict], luc: str) -> dict:
    """So tên tốn vài trăm mili giây với vài nghìn mã, nên nhớ kết quả tới khi Hàng hóa hoặc danh sách web đổi."""
    khoa = (luc, len(ds_web), hash(tuple(hang)))
    if _KHOP_CACHE.get("khoa") != khoa:
        _KHOP_CACHE.update(khoa=khoa, kq=so_khop_hang(hang, ds_web))
    return _KHOP_CACHE["kq"]


def _hang_hoa(session, g) -> tuple[list[dict], dict]:
    web = hd.doc_json(hd.file_sp_web(g), {})
    web = web if isinstance(web, dict) else {}
    ds_web = [w for w in web.get("ds") or [] if isinstance(w, dict) and w.get("id")]
    theo_id = {int(w["id"]): w for w in ds_web}
    hang = session.execute(CAU_HANG).all()
    khop = _khop([(h.code, h.name) for h in hang], ds_web, str(web.get("luc", "")))
    xac = hd.doc_xac_nhan(g)
    da_dang = hd.doc_trang_thai(g)["san_pham"]
    co_anh = hd.ma_co_anh(g)
    dang_lam = {v.get("ma") for v in hd.ds(g) if v.get("loai") == "san_pham" and v.get("trang_thai") not in hd.XONG}
    rows = []
    for h in hang:
        x = xac.get(h.code) if isinstance(xac.get(h.code), dict) else {}
        k = khop.get(h.code) or {}
        web_sp, kieu, xac_nhan = None, "chua-co", False
        if x.get("dung"):
            web_sp = theo_id.get(int(x["dung"])) or {"id": int(x["dung"]), "ten": "", "trang_thai": ""}
            kieu, xac_nhan = "da-co", True
        elif k.get("kieu") == "sku":
            web_sp, kieu = k["web"][0], "da-co"
        elif isinstance(da_dang.get(h.code), dict) and da_dang[h.code].get("id"):
            info = da_dang[h.code]
            web_sp = theo_id.get(int(info["id"])) or {"id": info["id"], "ten": info.get("ten", ""), "xem": info.get("xem", ""),
                                                      "sua": info.get("link", ""), "trang_thai": ""}
            kieu = "da-co"
        else:
            khong = set(x.get("khong") or [])
            con_lai = [w for w in k.get("web") or [] if w.get("id") not in khong]
            if con_lai:
                web_sp, kieu = con_lai[0], "co-the"
        rows.append(dict(code=h.code, name=h.name, nhom=h.group_name or "Chưa có nhóm", gia=int(h.sale_price or 0), ton=h.ton,
                         kieu=kieu, web=web_sp, xac_nhan=xac_nhan, co_anh=h.code in co_anh, dang_lam=h.code in dang_lam))
    return rows, {"luc": str(web.get("luc", "")), "so_web": len(ds_web), "da_lay": bool(web)}


def _moi_nhap(session) -> dict[str, object]:
    from datetime import timedelta

    tu = _hom_nay() - timedelta(days=MOI_NHAP_NGAY)
    return {r.code: r.ngay for r in session.execute(CAU_MOI_NHAP, {"tu": tu})}


def _hom_nay():
    from .lists import _today

    return _today()


def _hang_moi(session, g) -> list[dict]:
    """Mã mới nhập 30 ngày, còn tồn, chưa có trên web (không tính mã đã ghép với sản phẩm web), mới nhất trước."""
    if not _doc_web(hd.file_sp_web(g))["da_lay"]:
        return []
    moi = _moi_nhap(session)
    if not moi:
        return []
    rows, _ = _hang_hoa(session, g)
    ra = [dict(r, ngay=moi[r["code"]]) for r in rows if r["code"] in moi and r["kieu"] == "chua-co" and r["ton"] > 0 and not r["dang_lam"]]
    return sorted(ra, key=lambda r: (r["ngay"], r["code"]), reverse=True)


def _doc_web(f: Path) -> dict:
    d = hd.doc_json(f, {})
    d = d if isinstance(d, dict) else {}
    return {"luc": str(d.get("luc", "")), "ds": [x for x in d.get("ds") or [] if isinstance(x, dict) and x.get("id")],
            "danh_muc": [x for x in d.get("danh_muc") or [] if isinstance(x, dict)], "da_lay": bool(d)}


def _ma_theo_web(session, g, tt) -> dict[int, str]:
    """Sản phẩm web nào ứng với mã nào trong app: SKU = mã TD, anh đã xác nhận, hoặc app đã đăng."""
    ma_app = {r[0] for r in session.execute(text("select code from products where active and not is_test"))}
    ra: dict[int, str] = {}
    for code, info in tt["san_pham"].items():
        if isinstance(info, dict) and str(info.get("id", "")).isdigit() and code in ma_app:
            ra[int(info["id"])] = code
    for code, x in hd.doc_xac_nhan(g).items():
        if isinstance(x, dict) and str(x.get("dung", "")).isdigit() and code in ma_app:
            ra[int(x["dung"])] = code
    return ra, ma_app


def _bai_giong(chu_de: str, bai_web: list[dict]) -> dict | None:
    """Chủ đề chờ viết đã có bài gần giống trên web (theo danh sách lấy về lần Cập nhật từ web gần nhất)."""
    tot = max(((do_giong_bai(chu_de, b.get("ten", "")), b) for b in bai_web), key=lambda x: x[0], default=(0, None))
    return tot[1] if tot[0] >= 0.7 else None


def _tab_tren_web(request, session, g, ctx, tt, sp_web, bai_web, loai, loc, q, dm, nhom, ton, trang, so):
    """Mọi sản phẩm / bài viết đang có trên thanhduc.vn theo danh mục (lấy về khi anh bấm Cập nhật từ web)."""
    loai = "bai" if loai == "bai" else "sp"
    web = sp_web if loai == "sp" else bai_web
    cay = CayDanhMuc(web["danh_muc"])
    ctx.update(loai=loai, dm=dm, sp_web=sp_web, bai_web=bai_web, web=web, toi_da_xep=TOI_DA_XEP)
    if loai == "sp" and loc in dict(LOC_APP):  # phần cũ của tab Hàng hóa: mã trong app chưa lên web / có thể trùng
        rows, info = _hang_hoa(session, g)
        dem_app = {k: sum(r["kieu"] == ("chua-co" if k == "chua-len" else "co-the") for r in rows) for k, _ in LOC_APP}
        rows = [r for r in rows if r["kieu"] == ("chua-co" if loc == "chua-len" else "co-the")]
        nhom_dem: dict[str, int] = {}
        for r in rows:
            nhom_dem[r["nhom"]] = nhom_dem.get(r["nhom"], 0) + 1
        if nhom:
            rows = [r for r in rows if r["nhom"] == nhom]
        if ton == "1":
            rows = [r for r in rows if r["ton"] > 0]
        if request.query_params.get("moi") == "1":
            moi = _moi_nhap(session)
            rows = sorted([r for r in rows if r["code"] in moi and r["ton"] > 0], key=lambda r: (moi[r["code"]], r["code"]), reverse=True)
        if q:
            f = fold(q)
            rows = [r for r in rows if f in fold(f"{r['code']} {r['name']}")]
        moi_loc = "1" if request.query_params.get("moi") == "1" else ""
        query = dict(tab="tren-web", loai="sp", loc=loc, q=q, nhom=nhom, ton=ton, moi=moi_loc)
        p = paging.build(trang, so, len(rows), query)
        rows = rows[p["offset"]: p["offset"] + p["size"]]
        return render(request, "dang_web.jinja", rows=rows, p=p, hang_info=info, nhom=nhom, ton=ton, nhom_dem=sorted(nhom_dem.items()),
                      query=query, toi_da_soan_nhieu=TOI_DA_SOAN_NHIEU, che_do="app", moi=moi_loc, loc_chip=_loc_chip(loai, loc, {}, dem_app, dm, q),
                      cats=[], **ctx)
    ds = web["ds"]
    ma_theo_web, ma_app = _ma_theo_web(session, g, tt) if loai == "sp" else ({}, set())
    id_app_dang = {int(x["id"]) for x in tt["bai_viet"].values() if isinstance(x, dict) and str(x.get("id", "")).isdigit()}
    rows = []
    for it in ds:
        r = dict(it)
        ids = [int(x) for x in it.get("dm") or [] if str(x).isdigit()]
        r["chua_pl"] = cay.chua_phan_loai(ids)
        r["dm_ten"] = ", ".join(cay.duong(i) for i in ids if i in cay.theo_id and not cay.chua_phan_loai([i]))
        r["nhan"], r["tone"] = WEB_TT.get(it.get("trang_thai", ""), ("Chưa rõ", "nhap"))
        if loai == "sp":
            sku = str(it.get("sku") or "").strip().upper()
            r["ma"] = sku if sku in ma_app else ma_theo_web.get(int(it["id"]), "")
            r["gia"] = int(it["gia"]) if str(it.get("gia", "")).isdigit() else 0
            r["co_anh"] = bool(it.get("so_anh") or it.get("anh"))
        else:
            r["app"] = int(it["id"]) in id_app_dang
            r["co_anh"] = bool(it.get("co_anh"))
        rows.append(r)
    dem_cat, dem_chua = cay.dem(ds)
    cats = [dict(id=i, ten=cay.theo_id[i]["ten"], sau=sau, n=dem_cat.get(i, 0)) for i, sau in cay.thu_tu() if dem_cat.get(i)]
    if dm == "chua":
        rows = [r for r in rows if r["chua_pl"]]
    elif dm.isdigit():
        rows = [r for r in rows if cay.thuoc(r, int(dm))]
    if q:
        f = fold(q)
        rows = [r for r in rows if f in fold(f"{r['ten']} {r.get('sku', '')} {r.get('ma', '')}")]
    loc_ham = {
        "sp": {"khop": lambda r: r["ma"], "khong-ma": lambda r: not r["ma"], "nhap": lambda r: r["trang_thai"] == "draft",
               "chua-anh": lambda r: not r["co_anh"]},
        "bai": {"hien": lambda r: r["trang_thai"] == "publish", "nhap": lambda r: r["trang_thai"] == "draft", "app": lambda r: r["app"],
                "chua-anh": lambda r: not r["co_anh"]},
    }[loai]
    dem_loc = {k: sum(1 for r in rows if h(r)) for k, h in loc_ham.items()}
    dem_loc[""] = len(rows)
    dem_app = {}
    if loai == "sp":
        hang, _ = _hang_hoa(session, g)
        dem_app = {"chua-len": sum(r["kieu"] == "chua-co" for r in hang), "co-the": sum(r["kieu"] == "co-the" for r in hang)}
    if loc in loc_ham:
        rows = [r for r in rows if loc_ham[loc](r)]
    else:
        loc = ""
    rows.sort(key=lambda r: (r.get("ngay", ""), r["id"]), reverse=True)
    query = dict(tab="tren-web", loai=loai, loc=loc, q=q, dm=dm)
    p = paging.build(trang, so, len(rows), query)
    rows = rows[p["offset"]: p["offset"] + p["size"]]
    return render(request, "dang_web.jinja", rows=rows, p=p, query=query, che_do="web", cats=cats, dem_chua=dem_chua,
                  loc_chip=_loc_chip(loai, loc, dem_loc, dem_app, dm, q), cay_lua=cay.lua_chon(), **ctx)


def _loc_chip(loai, loc, dem_loc, dem_app, dm, q) -> list[dict]:
    ds = []
    for key, label in (LOC_SP if loai == "sp" else LOC_BAI):
        ds.append(dict(label=label, n=dem_loc.get(key), on=loc == key,
                       href="/dang-web?" + urlencode({k: v for k, v in dict(tab="tren-web", loai=loai, loc=key, dm=dm, q=q).items() if v}), tone=""))
    if loai == "sp":
        for key, label in LOC_APP:
            ds.append(dict(label=label, n=dem_app.get(key), on=loc == key, tone="bad" if key == "chua-len" else "warn",
                           href="/dang-web?" + urlencode(dict(tab="tren-web", loai="sp", loc=key))))
    return ds


@router.post("/dang-web/hang-hoa/xac-nhan")
def confirm_web(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    _can_ghi()
    ma = str(form.get("ma", "")).strip().upper()
    if not session.get(Product, ma):
        raise BusinessError("BAD_PRODUCT", "Không thấy mã hàng này.")
    quay_lai = "/dang-web?" + urlencode({k: str(form.get(k, "")) for k in ("tab", "loai", "loc", "q", "nhom", "ton", "trang") if form.get(k)})
    if form.get("hanh_dong") == "bo":
        hd.bo_xac_nhan(goc(), ma)
        return RedirectResponse(quay_lai, status_code=303)
    web_id = str(form.get("web_id", ""))
    if not web_id.isdigit():
        raise BusinessError("BAD_FORM", "Thiếu sản phẩm trên web.")
    hd.xac_nhan(goc(), ma, int(web_id), form.get("hanh_dong") == "dung")
    return RedirectResponse(quay_lai, status_code=303)


@router.post("/dang-web/soan-nhieu")
def create_many(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    """Tick nhiều mã (Trên web › Mã app chưa lên web): mỗi mã một việc; có ảnh trong ~/THANHDUC-WEB/anh-goc thì dùng, không thì đăng chữ."""
    _can_ghi()
    g = goc()
    chon = list(dict.fromkeys(str(m).strip().upper() for m in form.getlist("ma") if str(m).strip()))
    if not chon:
        raise BusinessError("BAD_FORM", "Tick ít nhất một mã.")
    if len(chon) > TOI_DA_SOAN_NHIEU:
        raise BusinessError("BAD_FORM", f"Mỗi lần soạn tối đa {TOI_DA_SOAN_NHIEU} mã.")
    dang_lam = {v.get("ma") for v in hd.ds(g) if v.get("trang_thai") not in hd.XONG}
    cach = _cach_mac_dinh(hd.doc_nhip(g))
    so = 0
    for code in chon:
        sp = session.get(Product, code)
        if not sp or not sp.active or code in dang_lam:
            continue
        hd.tao_viec(g, "san_pham", {"ma": sp.code, "ten": sp.name, "cach_viet": cach})
        so += 1
    return RedirectResponse(f"/dang-web?tab=san-pham&da_luu=tu-chon-{so}", status_code=303)


# ---------------- soạn mới ----------------
def _form_soan(request, session, d: dict, error="", status=200):
    nhip = hd.doc_nhip(goc())
    sp = session.get(Product, d.get("ma", "")) if d.get("ma") else None
    anh_co_san = bool(sp and hd.co_anh_goc(goc(), sp.code))
    codex = hd.codex_song(goc())
    if not d.get("cach_viet"):
        d["cach_viet"] = "codex" if codex else ("tu_dong" if nhip and nhip.get("ai") else "chatgpt_web")
    return render(request, "dang_web_soan.jinja", d=d, sp=sp, anh_co_san=anh_co_san, nhip=nhip, cach_viet=hd.CACH_VIET, codex=codex,
                  error=error, toi_da_anh=TOI_DA_ANH, status=status)


@router.get("/dang-web/soan")
def new_form(request: Request, loai: str = "san-pham", ma: str = "", chu_de: str = "", wp_id: str = "",
             user: User = Depends(web_user), session: Session = Depends(get_session)):
    d = dict(loai="bai_viet" if loai == "bai-viet" else "san_pham", ma=ma.strip().upper(), chu_de=chu_de.strip(),
             wp_id=wp_id if wp_id.isdigit() else "")
    if d["loai"] == "bai_viet" and d["chu_de"]:
        d["ghi_chu"] = hd.doc_ghi_chu_chu_de(goc()).get(" ".join(d["chu_de"].split()), "")
    return _form_soan(request, session, d)


@router.get("/dang-web/tim-hang")
def pick_product(request: Request, q: str = "", user: User = Depends(web_user), session: Session = Depends(get_session)):
    rows = product_svc.search(session, q[:200], active_only=True, limit=15) if q.strip() else []
    return render(request, "dang_web_chon_hang.jinja", rows=rows, q=q)


async def _doc_anh(form) -> list[tuple[str, bytes]]:
    ra = []
    gioi_han = get_settings().max_upload_mb * 1024 * 1024
    for up in form.getlist("anh"):
        if not isinstance(up, UploadFile) or not up.filename:
            continue
        data = await up.read(gioi_han + 1)
        if len(data) > gioi_han:
            raise BusinessError("FILE_TOO_LARGE", f"Ảnh {up.filename} quá lớn (tối đa {get_settings().max_upload_mb} MB).")
        if data:
            ra.append((up.filename, data))
    if len(ra) > TOI_DA_ANH:
        raise BusinessError("PHOTO_LIMIT", f"Tối đa {TOI_DA_ANH} ảnh mỗi sản phẩm.")
    return ra


@router.post("/dang-web/soan")
async def create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    loai = "bai_viet" if form.get("loai") == "bai_viet" else "san_pham"
    d = dict(loai=loai, ma=str(form.get("ma", "")).strip().upper(), chu_de=" ".join(str(form.get("chu_de", "")).split()),
             ghi_chu=str(form.get("ghi_chu", "")).strip()[:3000], cach_viet=str(form.get("cach_viet", "")),
             nen_trang=form.get("nen_trang") == "1", da_co_logo=form.get("da_co_logo") == "1", wp_id=str(form.get("wp_id", "")).strip())
    d["ve_anh"] = form.get("ve_anh") == "1"
    try:
        _can_ghi()
        g = goc()
        if d["cach_viet"] not in hd.CACH_VIET:
            raise BusinessError("BAD_FORM", "Chọn cách viết.")
        if loai == "bai_viet" and d["cach_viet"] == "mau":
            raise BusinessError("BAD_FORM", "Bài viết cần AI hoặc ChatGPT web; mẫu có sẵn chỉ dùng cho sản phẩm.")
        wp_id = int(d["wp_id"]) if d["wp_id"].isdigit() else None
        anh = await _doc_anh(form)
        them = dict(cach_viet=d["cach_viet"], ghi_chu=d["ghi_chu"], wp_id=wp_id, van_dang=bool(wp_id), viet_lai=bool(wp_id),
                    da_co_logo=d["da_co_logo"])
        if loai == "bai_viet" and len(anh) > 4:
            raise BusinessError("PHOTO_LIMIT", "Bài viết tối đa 4 ảnh.")
        if loai == "san_pham":
            sp = session.get(Product, d["ma"]) if d["ma"] else None
            if not sp or not sp.active:
                raise BusinessError("BAD_PRODUCT", "Chọn mã hàng đang dùng trong Hàng hóa.")
            them.update(ma=sp.code, ten=sp.name, tach_nen=not d["nen_trang"])
        else:
            if len(d["chu_de"]) < 8:
                raise BusinessError("BAD_FORM", "Gõ chủ đề bài viết (ít nhất 8 chữ).")
            them.update(chu_de=d["chu_de"][:200], ten=d["chu_de"][:200])
        ve = loai == "bai_viet" and d["ve_anh"] and not anh and not wp_id and hd.codex_song(g)
        if ve:
            them["ve_anh"] = True
        viec_id = hd.tao_viec(g, loai, them, anh)
        # TUẤN 02/11: máy đăng web viết bài xong mới gửi Codex vẽ, kèm dàn ý H1/H2 để ảnh sát nội dung.
    except BusinessError as exc:
        return _form_soan(request, session, d, error=exc.message, status=exc.status)
    return RedirectResponse(f"/dang-web/viec/{viec_id}", status_code=303)


@router.post("/dang-web/tu-chon")
def auto_pick(request: Request, form=Depends(checked_form)):
    """Tự chọn tối đa 3 mã đã có ảnh trong ~/THANHDUC-WEB/anh-goc mà chưa lên web."""
    _can_ghi()
    g = goc()
    tt = hd.doc_trang_thai(g)
    dang_co = {v.get("ma") for v in hd.ds(g) if v.get("trang_thai") not in hd.XONG}
    ma = hd.ma_co_anh(g)
    chon = sorted(m for m in ma if m not in tt["san_pham"] and m not in tt["nghi_trung"] and m not in dang_co)[:3]
    cach = _cach_mac_dinh(hd.doc_nhip(g))
    for code in chon:
        hd.tao_viec(g, "san_pham", {"ma": code, "ten": code, "cach_viet": cach})
    return RedirectResponse(f"/dang-web?tab=san-pham&da_luu={'tu-chon-' + str(len(chon))}", status_code=303)


# ---------------- sửa bằng AI bài / sản phẩm đang có trên web ----------------
def _muc_web(loai: str, web_id: str) -> tuple[dict, CayDanhMuc]:
    web = _doc_web(hd.file_sp_web(goc()) if loai == "san_pham" else hd.file_bai_web(goc()))
    it = next((x for x in web["ds"] if str(x.get("id")) == str(web_id)), None)
    if not it:
        raise BusinessError("NOT_FOUND", "Không thấy trên danh sách web. Bấm Cập nhật từ web rồi thử lại.", status=404)
    return it, CayDanhMuc(web["danh_muc"])


def _xin_lay_lai(loai: str, it: dict) -> bool:
    """Mở màn Sửa ảnh / Sửa bằng AI: nhờ máy đăng web lấy lại đúng mục này từ web (TUẤN duyệt đợt 1 ngày 02/11/2026).
    Trả True khi đang chờ bản mới. Máy tắt, chưa nối web hoặc vừa lấy trong 2 phút thì thôi."""
    g = goc()
    web_id = int(it["id"])
    if hd.dang_cho_lam_moi_mot(g, loai, web_id):
        return True
    nhip = hd.doc_nhip(g)
    if hd.moi_lay(it) or not nhip or not nhip.get("song") or not nhip.get("co_wp") or hd.kiem_ghi_duoc(g):
        return False
    hd.yeu_cau_lam_moi_mot(g, loai, web_id)
    return True


@router.get("/dang-web/lay-lai")
def refresh_one_status(loai: str = "sp", id: str = "", user: User = Depends(web_user)):
    from fastapi.responses import JSONResponse

    loai = _loai_anh(loai)
    return JSONResponse({"cho": id.isdigit() and hd.dang_cho_lam_moi_mot(goc(), loai, int(id))}, headers={"Cache-Control": "no-store"})


def _form_sua(request, session, loai: str, it: dict, cay: CayDanhMuc, d: dict, error="", status=200, cho_lay_lai=False):
    ids = [int(x) for x in it.get("dm") or [] if str(x).isdigit()]
    hien = next((i for i in ids if not cay.chua_phan_loai([i])), None)
    ma = _ma_cua_web(session, it) if loai == "san_pham" else ""
    return render(request, "dang_web_sua.jinja", loai=loai, it=it, cay_lua=cay.lua_chon(), dm_hien=cay.duong(hien) if hien else "",
                  d=d, ma=ma, nhip=hd.doc_nhip(goc()), cach_viet=hd.CACH_VIET, codex=hd.codex_song(goc()), error=error, status=status,
                  cho_lay_lai=cho_lay_lai, toi_da_anh=TOI_DA_ANH if loai == "san_pham" else 4)


def _ma_cua_web(session, it: dict) -> str:
    sku = str(it.get("sku") or "").strip().upper()
    sp = session.get(Product, sku) if sku else None
    if sp and sp.active:
        return sp.code
    ma_theo, _ = _ma_theo_web(session, goc(), hd.doc_trang_thai(goc()))
    return ma_theo.get(int(it["id"]), "")


@router.get("/dang-web/sua-ai")
def edit_web_form(request: Request, loai: str = "sp", id: str = "", user: User = Depends(web_user), session: Session = Depends(get_session)):
    loai = "bai_viet" if loai == "bai" else "san_pham"
    it, cay = _muc_web(loai, id)
    nhip = hd.doc_nhip(goc())
    return _form_sua(request, session, loai, it, cay, dict(noi_dung=True, seo=True, anh=False,
                                                           cach_viet="codex" if hd.codex_song(goc()) else ("tu_dong" if nhip and nhip.get("ai") else "chatgpt_web")),
                     cho_lay_lai=_xin_lay_lai(loai, it))


@router.post("/dang-web/sua-ai")
async def edit_web(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    loai = "bai_viet" if form.get("loai") == "bai_viet" else "san_pham"
    it, cay = _muc_web(loai, str(form.get("id", "")))
    d = dict(noi_dung=form.get("noi_dung") == "1", seo=form.get("seo") == "1", anh=form.get("anh_lai") == "1",
             cach_viet=str(form.get("cach_viet", "")), ghi_chu=str(form.get("ghi_chu", "")).strip()[:3000],
             danh_muc_id=str(form.get("danh_muc_id", "")), da_co_logo=form.get("da_co_logo") == "1", nen_trang=form.get("nen_trang") == "1")
    try:
        _can_ghi()
        if d["cach_viet"] not in ("codex", "tu_dong", "chatgpt_web"):
            raise BusinessError("BAD_FORM", "Chọn Codex, AI tự động hoặc ChatGPT web.")
        anh = await _doc_anh(form)
        if loai == "bai_viet" and len(anh) > 4:
            raise BusinessError("PHOTO_LIMIT", "Bài viết tối đa 4 ảnh.")
        hop_le = dict(cay.lua_chon())
        dm_id = int(d["danh_muc_id"]) if d["danh_muc_id"].isdigit() and int(d["danh_muc_id"]) in hop_le else None
        ids = [int(x) for x in it.get("dm") or [] if str(x).isdigit()]
        doi_dm = dm_id is not None and ids != [dm_id]
        sua = {"noi_dung": d["noi_dung"], "seo": d["seo"], "anh": d["anh"] or bool(anh)}
        if not any(sua.values()) and not doi_dm:
            raise BusinessError("BAD_FORM", "Chọn ít nhất một phần cần sửa, hoặc đổi danh mục.")
        g = goc()
        dang_lam = [v for v in hd.ds(g) if v.get("kieu") == "sua_web" and v.get("loai") == loai
                    and v.get("wp_id") == int(it["id"]) and v.get("trang_thai") not in hd.XONG]
        if dang_lam:
            return RedirectResponse(f"/dang-web/viec/{dang_lam[0]['id']}", status_code=303)
        them = dict(kieu="sua_web", wp_id=int(it["id"]), ten=it.get("ten", ""), ma=_ma_cua_web(session, it) if loai == "san_pham" else "",
                    sua=sua, danh_muc_id=dm_id if doi_dm else None, cach_viet=d["cach_viet"], ghi_chu=d["ghi_chu"],
                    da_co_logo=d["da_co_logo"], tach_nen=not d["nen_trang"], van_dang=True, viet_lai=True)
        if loai == "bai_viet":
            them["chu_de"] = it.get("ten", "")[:200]
        viec_id = hd.tao_viec(g, loai, them, anh)
    except BusinessError as exc:
        return _form_sua(request, session, loai, it, cay, d, error=exc.message, status=exc.status)
    return RedirectResponse(f"/dang-web/viec/{viec_id}", status_code=303)


# ---------- sửa ảnh riêng (không AI). TUẤN duyệt mẫu D2/2 ngày 02/11/2026: bỏ 3 cách xử lý cũ, chỉ đặt ảnh vào đúng
# khung web (vuông cho sản phẩm, ngang 16:9 cho bài viết), căn giữa, xoay, phóng to, lật, đóng logo; nhiều ảnh một lượt. ----------
CHE_DO_ANH = {"tach_nen": "Tách nền", "nen_trang": "Đã nền trắng", "giu": "Giữ nguyên"}  # việc cũ còn ghi che_do
TOI_DA_ANH_BAI = 4  # ảnh mới thêm vào một bài viết


def _loai_anh(loai: str) -> str:
    return "bai_viet" if loai in ("bai", "bai_viet") else "san_pham"


def _anh_cua_muc(loai: str, it: dict) -> list[dict]:
    """Ảnh đang trên web của một sản phẩm/bài viết: key (ổn định trên màn), src, media_id, vai.
    Sản phẩm 3 nhóm (TUẤN duyệt mẫu D8/8 ngày 02/11/2026): dai_dien (ảnh đầu), album (ảnh sau), mo_ta (ảnh nằm trong mô tả).
    Bài viết: dai_dien, bai. Ảnh mô tả/trong bài trùng ảnh đại diện thì đánh dấu trung để anh bỏ."""
    from dang_web.so_ten import cung_anh

    if loai == "san_pham":
        ra = [{"key": f"web-{int(m['id'])}", "src": m.get("src", ""), "media_id": int(m["id"]), "vai": "album"}
              for m in it.get("anhs") or [] if isinstance(m, dict) and str(m.get("id", "")).isdigit() and m.get("src")]
        if ra:
            ra[0]["vai"] = "dai_dien"
        dd = ra[0] if ra else None
        for i, m in enumerate(it.get("anhs_mo_ta") or []):
            if isinstance(m, dict) and m.get("src"):
                mid = int(m.get("id") or 0)
                trung = bool(dd) and ((mid and mid == dd["media_id"]) or cung_anh(m["src"], dd["src"]))
                ra.append({"key": f"mota-{i}", "src": m["src"], "media_id": mid, "vai": "mo_ta", "trung": trung})
        return ra
    ra = []
    if it.get("anh_id") and (it.get("anh_goc") or it.get("anh")):
        ra.append({"key": f"web-{int(it['anh_id'])}", "src": it.get("anh_goc") or it.get("anh"), "media_id": int(it["anh_id"]), "vai": "dai_dien"})
    dd = ra[0] if ra else None
    for i, m in enumerate(it.get("anhs_bai") or []):
        if isinstance(m, dict) and m.get("src"):
            mid = int(m.get("id") or 0)
            trung = bool(dd) and ((mid and mid == dd["media_id"]) or cung_anh(m["src"], dd["src"]))
            ra.append({"key": f"bai-{i}", "src": m["src"], "media_id": mid, "vai": "bai", "trung": trung})
    return ra


def _slug(ten: str, toi_da: int) -> str:
    from dang_web.so_ten import slug

    return slug(ten, toi_da)


def _form_sua_anh(request, session, loai: str, it: dict, d: dict, error="", status=200, cho_lay_lai=False):
    bai = loai == "bai_viet"
    co_ds = ("anhs_bai" in it) if bai else ("anhs" in it)
    g = goc()
    ve = ve_anh.moi_nhat(g, wp_id=int(it["id"]), loai=loai)
    anhs = _anh_cua_muc(loai, it)
    return render(request, "dang_web_sua_anh.jinja", it=it, loai=loai, bai=bai, anhs=anhs, co_ds_anh=co_ds, d=d,
                  ve=_ve_json(ve), codex=hd.codex_song(g), h2=it.get("h2_bai" if bai else "h2_mo_ta") or [],
                  co_mo_ta=("anhs_bai" if bai else "anhs_mo_ta") in it, ten_file=_slug(it.get("ten", ""), 50) or "anh",
                  nhom={v: [m for m in anhs if m["vai"] == v] for v in ("dai_dien", "album", "mo_ta", "bai")},
                  ma="" if bai else _ma_cua_web(session, it), nhip=hd.doc_nhip(goc()),
                  toi_da_anh=TOI_DA_ANH_BAI if bai else TOI_DA_ANH, error=error, status=status, cho_lay_lai=cho_lay_lai)


@router.get("/dang-web/sua-anh")
def edit_photos_form(request: Request, id: str = "", loai: str = "sp", user: User = Depends(web_user), session: Session = Depends(get_session)):
    loai = _loai_anh(loai)
    it, _ = _muc_web(loai, id)
    return _form_sua_anh(request, session, loai, it, dict(logo=True, logo_goc="phai", alt_tat_ca=True), cho_lay_lai=_xin_lay_lai(loai, it))


@router.get("/dang-web/anh-tren-web")
def web_photo(loai: str = "sp", id: str = "", k: str = "", user: User = Depends(web_user)):
    """Chuyển ảnh đang trên web về cùng trang app để màn Sửa ảnh đo được nền và tự căn giữa.
    Chỉ ảnh có trong danh sách đã lấy về của đúng sản phẩm/bài đó, không mở địa chỉ bất kỳ."""
    import urllib.request

    loai = _loai_anh(loai)
    it, _ = _muc_web(loai, id)
    m = next((x for x in _anh_cua_muc(loai, it) if x["key"] == k), None)
    if not m or not str(m["src"]).startswith(("https://", "http://")):
        raise BusinessError("NOT_FOUND", "Không thấy ảnh này.", status=404)
    gioi_han = 15 * 1024 * 1024
    try:
        with urllib.request.urlopen(urllib.request.Request(m["src"], headers={"User-Agent": "ThanhDucApp/1.0"}), timeout=15) as r:
            kieu = r.headers.get_content_type()
            data = r.read(gioi_han + 1)
    except Exception as exc:  # mạng chập chờn: màn vẫn dùng ảnh gốc trên web, chỉ không tự căn giữa được
        raise BusinessError("WEB_PHOTO", f"Không tải được ảnh từ web: {exc}", status=502) from exc
    if not kieu.startswith("image/") or len(data) > gioi_han:
        raise BusinessError("WEB_PHOTO", "Ảnh trên web không hợp lệ.", status=502)
    return Response(data, media_type=kieu, headers={"Cache-Control": "private, max-age=600"})


@router.get("/dang-web/logo.png")
def logo_png(user: User = Depends(web_user)):
    from dang_web.anh import LOGO_MAC_DINH

    if not LOGO_MAC_DINH.exists():
        raise BusinessError("NOT_FOUND", "Chưa có logo.", status=404)
    return FileResponse(LOGO_MAC_DINH, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


def _bien(form, key: str) -> dict:
    import json

    from dang_web.anh import chuan_bien

    try:
        return chuan_bien(json.loads(str(form.get(f"bien_{key}", "") or "{}")))
    except ValueError:
        return chuan_bien({})


@router.post("/dang-web/sua-anh")
async def edit_photos(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    loai = _loai_anh(str(form.get("loai", "sp")))
    bai = loai == "bai_viet"
    it, _ = _muc_web(loai, str(form.get("id", "")))
    d = dict(logo=form.get("logo") == "1", logo_goc="trai" if form.get("logo_goc") == "trai" else "phai", alt_tat_ca=form.get("alt_tat_ca") == "1")
    try:
        _can_ghi()
        if ("anhs_bai" if bai else "anhs") not in it:
            raise BusinessError("BAD_STATE", "Danh sách web cũ chưa có ảnh. Bấm Cập nhật từ web rồi mở lại.", status=409)
        tren_web = {m["key"]: m for m in _anh_cua_muc(loai, it)}
        moi = await _doc_anh(form)
        thu_tu = [str(x) for x in form.getlist("thu_tu")]
        dai_dien = str(form.get("dai_dien", ""))
        hop_le = ("dai_dien", "bai") if bai else ("dai_dien", "album", "mo_ta")
        so_h2 = len(it.get("h2_bai" if bai else "h2_mo_ta") or [])

        def vai_cua(key, mac_dinh):
            v = str(form.get(f"vai_{key}", "") or mac_dinh)
            if bai and v == "dai_dien" and not key.startswith("moi-") and mac_dinh != "dai_dien":
                v = mac_dinh  # ảnh trong bài không chuyển thành ảnh đại diện (chỉ ảnh mới)
            if not bai and mac_dinh == "mo_ta" and v != "mo_ta":
                v = "mo_ta"  # ảnh đang nằm trong mô tả giữ nhóm mô tả (bỏ thì tick Bỏ)
            return v if v in hop_le else mac_dinh

        def vi_tri(key):
            vt = str(form.get(f"vitri_{key}", ""))
            return vt if vt.isdigit() and int(vt) < so_h2 else ""

        ds, bo_trong_bai = [], []
        for key in dict.fromkeys(thu_tu):  # thứ tự trên màn (nút ‹ ›)
            if key in tren_web:
                m = tren_web[key]
                trong = m["vai"] in ("bai", "mo_ta")
                if form.get(f"xoa_{key}") == "1":
                    if trong:
                        bo_trong_bai.append(m["src"])
                    continue
                vai = vai_cua(key, m["vai"])
                if not bai and form.get(f"vai_{key}") is None and not trong:
                    vai = "dai_dien" if (dai_dien == key or (not dai_dien and m["vai"] == "dai_dien")) else "album"
                if bai and m["vai"] == "dai_dien" and (dai_dien.startswith("moi-") or any(
                        str(form.get(f"vai_{k}", "")) == "dai_dien" for k in thu_tu if k.startswith("moi-"))):
                    continue  # ảnh đại diện mới thay ảnh cũ
                ds.append({"nguon": "web", "key": key, "media_id": m["media_id"], "src": m["src"], "vai": vai,
                           "sua": form.get(f"chon_{key}") == "1", "bien": _bien(form, key)})
            elif key.startswith("moi-") and key[4:].isdigit() and 0 < int(key[4:]) <= len(moi):
                n = int(key[4:])
                if bai:
                    vai = "dai_dien" if (dai_dien == key or str(form.get(f"vai_{key}", "")) == "dai_dien") else "them"
                else:
                    vai = "dai_dien" if dai_dien == key else vai_cua(key, "album")
                ds.append({"nguon": "moi", "key": key, "so": n, "sua": True, "bien": _bien(form, key), "vai": vai,
                           "vi_tri": vi_tri(key) if vai in ("mo_ta", "them") else ""})
        if not bai:
            dd = [x for x in ds if x["vai"] == "dai_dien"]
            for x in dd[1:]:
                x["vai"] = "album"  # chỉ 1 ảnh đại diện
            if not dd:
                x = next((x for x in ds if x["vai"] == "album"), None)
                if x:
                    x["vai"] = "dai_dien"  # bỏ ảnh đại diện thì ảnh album đầu lên thay
            ds.sort(key=lambda x: {"dai_dien": 0, "album": 1}.get(x["vai"], 2))
        if bai and not any(x["vai"] == "dai_dien" for x in ds):
            for x in ds:  # bài chưa có ảnh đại diện: ảnh mới đầu tiên làm ảnh đại diện
                if x["nguon"] == "moi":
                    x["vai"] = "dai_dien"
                    break
        if not bai and not any(x["vai"] == "dai_dien" for x in ds):
            raise BusinessError("BAD_FORM", "Sản phẩm cần ít nhất 1 ảnh đại diện hoặc ảnh album: giữ lại ảnh trên web hoặc thêm ảnh mới.")
        if not bai and len([x for x in ds if x["vai"] != "mo_ta"]) > TOI_DA_ANH:
            raise BusinessError("PHOTO_LIMIT", f"Ảnh đại diện + album tối đa {TOI_DA_ANH} ảnh. Bỏ bớt ảnh.")
        if len([x for x in ds if x["nguon"] == "moi"]) > (TOI_DA_ANH_BAI if bai else TOI_DA_ANH):
            raise BusinessError("PHOTO_LIMIT", f"Tối đa {TOI_DA_ANH_BAI if bai else TOI_DA_ANH} ảnh mới một lần. Bỏ bớt ảnh.")
        doi_nhom = any(x["nguon"] == "web" and x["vai"] != tren_web[x["key"]]["vai"] for x in ds)
        ngoai = [k for k, m in tren_web.items() if m["vai"] not in ("bai", "mo_ta")]  # ảnh không nằm trong chữ
        con = [x["key"] for x in ds if x["nguon"] == "web" and x["key"] in ngoai]
        cap_nhat_alt = form.get("alt_tat_ca") == "1"
        if (not any(x["sua"] for x in ds) and not bo_trong_bai and not doi_nhom and con == ngoai
                and not any(x["nguon"] == "moi" for x in ds) and not cap_nhat_alt):
            raise BusinessError("BAD_FORM", "Chưa có gì thay đổi: tick ảnh cần sửa, chỉnh ảnh, đổi thứ tự, bỏ hoặc thêm ảnh.")
        g = goc()
        dang_lam = [v for v in hd.ds(g) if v.get("kieu") == "sua_anh" and v.get("loai") == loai and v.get("wp_id") == int(it["id"])
                    and v.get("trang_thai") not in hd.XONG]
        for v in dang_lam:  # bản đang chờ xem trước hoặc lỗi thì "Chọn lại ảnh" thay nó; đang chạy thì mở việc đó
            if hd.sua(g, v["id"], lambda x: x.update(trang_thai=hd.BO), (hd.XEM_TRUOC, hd.LOI)) is None:
                return RedirectResponse(f"/dang-web/viec/{v['id']}", status_code=303)
        them = dict(kieu="sua_anh", wp_id=int(it["id"]), ten=it.get("ten", ""), ma="" if bai else _ma_cua_web(session, it), anh=ds,
                    bo_trong_bai=bo_trong_bai, ba_nhom=not bai, cap_nhat_alt=cap_nhat_alt, h2=list(it.get("h2_bai" if bai else "h2_mo_ta") or []),
                    logo=d["logo"], logo_goc=d["logo_goc"], chen_mo_ta=False, xoa_cu=not bai, van_dang=True, viet_lai=True)
        if bai:
            them["chu_de"] = it.get("ten", "")[:200]
        viec_id = hd.tao_viec(g, loai, them, moi)
    except BusinessError as exc:
        return _form_sua_anh(request, session, loai, it, d, error=exc.message, status=exc.status)
    return RedirectResponse(f"/dang-web/viec/{viec_id}", status_code=303)


@router.post("/dang-web/xep-danh-muc")
def sort_categories(request: Request, form=Depends(checked_form)):
    """AI đề xuất danh mục cho tối đa TOI_DA_XEP mục chưa phân loại; anh tick đồng ý rồi mới ghi lên web."""
    _can_ghi()
    loai = "bai_viet" if form.get("loai") == "bai" else "san_pham"
    g = goc()
    web = _doc_web(hd.file_sp_web(g) if loai == "san_pham" else hd.file_bai_web(g))
    cay = CayDanhMuc(web["danh_muc"])
    dang_xep = {int(m["id"]) for v in hd.ds(g) if v.get("kieu") == "xep_danh_muc" and v.get("trang_thai") not in hd.XONG
                for m in v.get("muc") or []}
    muc = [{"id": int(x["id"]), "ten": x.get("ten", "")} for x in web["ds"]
           if cay.chua_phan_loai([int(i) for i in x.get("dm") or [] if str(i).isdigit()]) and int(x["id"]) not in dang_xep][:TOI_DA_XEP]
    if not muc:
        raise BusinessError("BAD_STATE", "Không còn mục chưa phân loại (hoặc đang xếp). Bấm Cập nhật từ web nếu vừa đổi trên web.", status=409)
    ten_loai = "sản phẩm" if loai == "san_pham" else "bài viết"
    them = dict(kieu="xep_danh_muc", muc=muc, ten=f"Xếp danh mục · {len(muc)} {ten_loai}", van_dang=True)
    if hd.codex_song(g):
        them["cach_viet"] = "codex"
    if loai == "bai_viet":
        them["chu_de"] = them["ten"]
    viec_id = hd.tao_viec(g, loai, them)
    return RedirectResponse(f"/dang-web/viec/{viec_id}", status_code=303)


# ---------------- một việc ----------------
def _cay(loai: str) -> CayDanhMuc:
    return CayDanhMuc(_doc_web(hd.file_sp_web(goc()) if loai == "san_pham" else hd.file_bai_web(goc()))["danh_muc"])


def _page_viec(request, v, error="", status=200):
    g = goc()
    cay_lua = _cay(v["loai"]).lua_chon() if v.get("kieu") in ("sua_web", "xep_danh_muc") else []
    ve = ve_anh.moi_nhat(g, viec_id=v["id"]) if v.get("ve_anh") else None
    return render(request, "dang_web_viec.jinja", v=_them_nhan(v), nhip=hd.doc_nhip(g), error=error, status=status, ve_ai=_ve_json(ve),
                  codex=hd.codex_song(g),
                  danh_muc=hd.doc_json(hd.file_danh_muc(g), []), cach_viet=hd.CACH_VIET, cay_lua=cay_lua, che_do_anh=CHE_DO_ANH)


@router.get("/dang-web/viec/{viec_id}")
def view(request: Request, viec_id: str, user: User = Depends(web_user)):
    return _page_viec(request, _viec(viec_id))


def _nap_xep(v: dict, form) -> None:
    hop_le = {i for i, _ in _cay(v["loai"]).lua_chon()}
    chon = {str(x) for x in form.getlist("chon")}
    ghi = []
    for x in v.get("de_xuat") or []:
        dm = str(form.get(f"dm_{x['id']}", ""))
        if str(x["id"]) in chon and dm.isdigit() and int(dm) in hop_le:
            ghi.append({"id": x["id"], "dm_id": int(dm)})
    if not ghi:
        raise BusinessError("BAD_FORM", "Tick ít nhất một dòng có danh mục.")
    v["ghi"] = ghi


def _nap_sua_anh(v: dict, form) -> None:
    for i, k in enumerate(v.get("ket_anh") or []):
        k["dung"] = "goc" if form.get(f"dung_{i}") == "goc" else "sau"
    v["chen_mo_ta"] = form.get("chen_mo_ta") == "1"
    v["xoa_cu"] = form.get("xoa_cu") == "1"


def _nap_anh_ai(v: dict, chon: str) -> None:
    """Bài viết mới: ảnh AI anh chọn ở màn xem trước được đặt vào khung 1200×675, đóng logo, làm ảnh đại diện."""
    from dang_web import anh as anh_mod
    from dang_web.so_ten import slug

    v["anh_web"] = [t for t in v.get("anh_web") or [] if not t.startswith("ai-")]
    v["anh_nguon"] = [p for p in v.get("anh_nguon") or [] if "/ve-anh/" not in p]
    if not chon:
        return
    yc_id, _, ten = chon.partition("/")
    g = goc()
    yc = ve_anh.doc(g, yc_id)
    f = ve_anh.file_anh(g, yc_id, ten)
    if not yc or yc.get("viec_id") != v["id"] or ten not in (yc.get("anh") or []) or not f or not f.is_file():
        raise BusinessError("BAD_FORM", "Ảnh AI đã chọn không còn. Tải lại trang rồi chọn lại.")
    ra = f"ai-{slug(v.get('chu_de') or 'bai-viet', 50)}-{yc_id[-6:]}-{ten[4]}.jpg"
    thu = g / "anh-da-xu-ly"
    thu.mkdir(parents=True, exist_ok=True)
    W, H = anh_mod.KHUNG_BAI
    (thu / ra).write_bytes(anh_mod.dat_khung(f, W, H, {}, logo=None if v.get("da_co_logo") else anh_mod.LOGO_MAC_DINH, dinh_dang="jpg"))
    v["anh_web"] = [ra] + v["anh_web"]
    v["anh_nguon"] = [str(f)] + v["anh_nguon"]


def _nap_sua(v: dict, form) -> None:
    if v.get("kieu") == "xep_danh_muc":
        return _nap_xep(v, form)
    if v.get("kieu") == "sua_anh":
        return _nap_sua_anh(v, form)
    d = dict(v.get("noi_dung") or {})
    for k in (SUA_SP if v["loai"] == "san_pham" else SUA_BAI):
        if k in form:
            d[k] = str(form.get(k, "")).strip()
    tieu_de = d.get("ten_web") if v["loai"] == "san_pham" else d.get("tieu_de")
    if not tieu_de:
        raise BusinessError("BAD_FORM", "Tên trên web không được để trống.")
    v["noi_dung"] = d
    if v["loai"] == "bai_viet" and v.get("ve_anh") and not v.get("kieu"):
        _nap_anh_ai(v, str(form.get("anh_ai", "")))
    if v.get("kieu") == "sua_web":
        dm = str(form.get("danh_muc_id", ""))
        hop_le = dict(_cay(v["loai"]).lua_chon())
        v["danh_muc_id"] = int(dm) if dm.isdigit() and int(dm) in hop_le else None
        v["danh_muc"] = hop_le.get(v["danh_muc_id"], "")
        if v["loai"] == "bai_viet":
            return
    if v["loai"] == "san_pham":
        v["gia"] = re.sub(r"\D", "", str(form.get("gia", "")))
        if v.get("kieu") != "sua_web":
            v["danh_muc"] = str(form.get("danh_muc", "")).strip()[:200]


@router.post("/dang-web/viec/{viec_id}")
def act(request: Request, viec_id: str, form=Depends(checked_form)):
    g = goc()
    v = _viec(viec_id)
    hanh_dong = str(form.get("hanh_dong", ""))
    thu_muc = hd.thu_muc_mot_viec(g, viec_id)

    def doi(trang_thai, chi_khi, ham=None):
        def h(x):
            if ham:
                ham(x)
            x["trang_thai"] = trang_thai

        return hd.sua(g, viec_id, h, chi_khi)

    def xoa_tra_loi(x):
        for ten in (x.get("file_tra_loi"), (x.get("file_tra_loi") or "").replace("-tra-loi.txt", ".txt")):
            if ten and hd.TEN_FILE_RE.match(ten):
                (thu_muc / ten).unlink(missing_ok=True)
        x.update(noi_dung=None, lenh_chatgpt="", file_tra_loi="", loi="", van_dang=True)

    try:
        _can_ghi()
        if v.get("kieu") == "ghi_sku" and hanh_dong not in ("thu_lai", "bo"):
            raise BusinessError("BAD_STATE", "Việc ghi mã chỉ thử lại hoặc bỏ được.", status=409)
        if hanh_dong == "dang":
            def ham(x):
                _nap_sua(x, form)
                x["loi"] = ""

            kq = doi(hd.CHO_DANG, (hd.XEM_TRUOC,), ham)
        elif hanh_dong == "viet_lai":
            kq = doi(hd.CHO_SOAN, (hd.XEM_TRUOC, hd.LOI, hd.CHO_DAN), xoa_tra_loi)
        elif hanh_dong == "van_soan":
            kq = doi(hd.CHO_SOAN, (hd.CO_THE_TRUNG,), lambda x: x.update(van_dang=True, trung=[]))
        elif hanh_dong == "viet_de":
            ids = {str(t.get("id")) for t in v.get("trung") or []}
            chon = str(form.get("wp_id", ""))
            if chon not in ids:
                raise BusinessError("BAD_FORM", "Chọn bài trên web cần viết đè.")
            kq = doi(hd.CHO_SOAN, (hd.CO_THE_TRUNG,), lambda x: x.update(van_dang=True, wp_id=int(chon), viet_lai=True, trung=[]))
        elif hanh_dong == "bo_qua_trung":
            khoa = _khoa_viec(v)
            kq = doi(hd.BO, (hd.CO_THE_TRUNG,))
            if kq:
                hd.sua_trang_thai(g, lambda tt: tt["nghi_trung"].__setitem__(khoa, [t.get("link", "") for t in v.get("trung") or []]))
        elif hanh_dong == "dan":
            tra_loi = str(form.get("tra_loi", "")).strip()
            ten = v.get("file_tra_loi") or ""
            if not tra_loi:
                raise BusinessError("BAD_FORM", "Dán câu trả lời của ChatGPT vào ô trước.")
            if not hd.TEN_FILE_RE.match(ten):
                raise BusinessError("BAD_STATE", "Việc này chưa có câu lệnh ChatGPT.")

            def ham(x):
                (thu_muc / ten).write_text(tra_loi, encoding="utf-8")
                x["loi"] = ""

            kq = doi(hd.CHO_SOAN, (hd.CHO_DAN,), ham)
        elif hanh_dong == "thu_lai":
            sau = hd.CHO_DANG if v.get("buoc_loi") == "dang" and (v.get("noi_dung") or v.get("ghi") or v.get("ket_anh")) else hd.CHO_SOAN
            kq = doi(sau, (hd.LOI,), lambda x: x.update(loi=""))
        elif hanh_dong == "bo":
            kq = doi(hd.BO, hd.NGUOI_LAM)
        else:
            raise BusinessError("BAD_FORM", "Thao tác không hợp lệ.")
        if kq is None:
            raise BusinessError("BAD_STATE", "Việc vừa đổi trạng thái, trang đã tải lại.", status=409)
    except BusinessError as exc:
        return _page_viec(request, hd.doc(g, viec_id) or v, error=exc.message, status=exc.status)
    if hanh_dong in ("bo", "bo_qua_trung"):
        return RedirectResponse(f"/dang-web?tab={'bai-viet' if v['loai'] == 'bai_viet' else 'san-pham'}&da_luu=bo", status_code=303)
    return RedirectResponse(f"/dang-web/viec/{viec_id}", status_code=303)


# ---------------- vẽ ảnh minh họa bài viết bằng Codex (TUẤN duyệt mẫu D4/4, D5/5 ngày 02/11/2026) ----------------
def _ve_json(yc: dict | None) -> dict:
    if not yc:
        return {"trang_thai": ""}
    return {"id": yc["id"], "trang_thai": yc["trang_thai"], "loi": yc.get("loi", ""), "tao_luc": yc.get("tao_luc", ""),
            "anh": [{"ten": t, "chon": f"{yc['id']}/{t}", "url": f"/dang-web/ve-anh/{yc['id']}/{t}"} for t in yc.get("anh") or []]}


@router.post("/dang-web/ve-anh")
def draw(request: Request, form=Depends(checked_form)):
    _can_ghi()
    g = goc()
    viec_id = str(form.get("viec_id", ""))
    dan = " ".join(str(form.get("dan", "")).split())[:300]
    if viec_id:
        v = _viec(viec_id)
        if v["loai"] != "bai_viet" or not v.get("ve_anh") or v.get("trang_thai") not in hd.NGUOI_LAM + hd.DANG_CHAY:
            raise BusinessError("BAD_STATE", "Việc này không vẽ ảnh được.", status=409)
        d = v.get("noi_dung") or {}
        ve_lai = f"/dang-web/viec/{viec_id}#tao-anh"
        cu = ve_anh.moi_nhat(g, viec_id=viec_id)
        tham = dict(chu_de=d.get("tieu_de") or v.get("chu_de", ""), viec_id=viec_id, tom_tat=d.get("tom_tat", ""), dan=dan,
                    dan_y=ve_anh.dan_y_bai(d.get("noi_dung_html", "")))
    else:
        loai = _loai_anh(str(form.get("loai", "bai")))
        it, _ = _muc_web(loai, str(form.get("id", "")))
        ve_lai = f"/dang-web/sua-anh?loai={'bai' if loai == 'bai_viet' else 'sp'}&id={int(it['id'])}#tao-anh"
        cu = ve_anh.moi_nhat(g, wp_id=int(it["id"]), loai=loai)
        if loai == "san_pham":  # ảnh cho phần mô tả sản phẩm (TUẤN 02/11)
            dan_y = "\n".join(f"H2: {h}" for h in it.get("h2_mo_ta") or [])
        else:
            dan_y = it.get("dan_y", "")
        tham = dict(chu_de=it.get("ten", ""), wp_id=int(it["id"]), dan=dan, dan_y=dan_y, loai=loai)
    if not hd.codex_song(g):
        raise BusinessError("CODEX_OFF", "Codex trên Z2 đang tắt nên chưa vẽ được ảnh.", status=409)
    if not (cu and cu["trang_thai"] in ve_anh.DANG_CHAY):  # đang vẽ thì chờ lượt đó, không gửi thêm
        ve_anh.tao(g, **tham)
    return RedirectResponse(ve_lai, status_code=303)


@router.get("/dang-web/ve-anh/{yc_id}/trang-thai")
def draw_status(yc_id: str, user: User = Depends(web_user)):
    from fastapi.responses import JSONResponse

    return JSONResponse(_ve_json(ve_anh.doc(goc(), yc_id)), headers={"Cache-Control": "no-store"})


@router.get("/dang-web/ve-anh/{yc_id}/{ten}")
def draw_photo(yc_id: str, ten: str, user: User = Depends(web_user)):
    f = ve_anh.file_anh(goc(), yc_id, ten)
    if not f:
        raise BusinessError("NOT_FOUND", "Không thấy ảnh.", status=404)
    return _gui_file(f)


# ---------------- ảnh ----------------
def _gui_file(p: Path):
    if not p.is_file():
        raise BusinessError("NOT_FOUND", "Không thấy ảnh.", status=404)
    return FileResponse(p, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/dang-web/anh/{viec_id}/{ten}")
def photo(viec_id: str, ten: str, user: User = Depends(web_user)):
    if not hd.ID_RE.match(viec_id) or not hd.TEN_FILE_RE.match(ten):
        raise BusinessError("NOT_FOUND", "Không thấy ảnh.", status=404)
    return _gui_file(hd.thu_muc_mot_viec(goc(), viec_id) / ten)


@router.get("/dang-web/anh-web/{ten}")
def web_photo(ten: str, user: User = Depends(web_user)):
    if not hd.TEN_FILE_RE.match(ten):
        raise BusinessError("NOT_FOUND", "Không thấy ảnh.", status=404)
    return _gui_file(goc() / "anh-da-xu-ly" / ten)


# ---------------- làm mới, chủ đề, lịch ----------------
@router.post("/dang-web/lam-moi")
def refresh(request: Request, form=Depends(checked_form)):
    """Chỉ chạy khi anh bấm nút: nhờ máy đăng web lấy lại trạng thái bài đã đăng và danh sách sản phẩm trên web."""
    _can_ghi()
    hd.yeu_cau_lam_moi(goc())
    loai = "bai" if form.get("loai") == "bai" else "sp"
    return RedirectResponse(f"/dang-web?tab=tren-web&loai={loai}&da_luu=lam-moi", status_code=303)


@router.post("/dang-web/chu-de")
def topics(request: Request, form=Depends(checked_form)):
    _can_ghi()
    g = goc()
    chu_de = " ".join(str(form.get("chu_de", "")).split())[:200]
    hanh_dong = str(form.get("hanh_dong", ""))
    if hanh_dong == "xoa":
        hd.xoa_chu_de(g, chu_de)
        return RedirectResponse("/dang-web?tab=bai-viet&da_luu=xoa-chu-de", status_code=303)
    if hanh_dong == "ghi_chu":
        if chu_de not in hd.doc_chu_de(g):
            raise BusinessError("BAD_FORM", "Chủ đề này không còn trong danh sách.")
        hd.ghi_ghi_chu_chu_de(g, chu_de, str(form.get("ghi_chu", "")))
        return RedirectResponse("/dang-web?tab=bai-viet&da_luu=ghi-chu-chu-de", status_code=303)
    # TUẤN 07/11: dán nhiều chủ đề một lần, mỗi dòng một chủ đề (bỏ số thứ tự, gạch đầu dòng).
    dong = [re.sub(r"^\s*(?:\d+[.)]|[-*•+])\s*", "", x) for x in str(form.get("chu_de", "")).splitlines()]
    dong = [" ".join(x.split())[:200] for x in dong if x.strip()][:50]
    if not dong:
        raise BusinessError("BAD_FORM", "Gõ chủ đề bài viết, mỗi dòng một chủ đề.")
    co_san = set(hd.doc_chu_de(g))
    bai_web = _doc_web(hd.file_bai_web(g))["ds"]
    them = trung_web = da_co = ngan = 0
    for c in dong:
        if len(c) < 8:
            ngan += 1
        elif c in co_san:
            da_co += 1
        elif _bai_giong(c, bai_web):
            trung_web += 1
        else:
            hd.them_chu_de(g, c)
            co_san.add(c)
            them += 1
    if len(dong) == 1 and not them:
        ly_do = ("Chủ đề cần ít nhất 8 chữ." if ngan else "Chủ đề này đã có trong danh sách." if da_co
                 else "Web đã có bài gần giống chủ đề này (xem trong tab Trên web).")
        raise BusinessError("BAD_FORM", ly_do)
    return RedirectResponse(f"/dang-web?tab=bai-viet&da_luu=them-cd-{them}-{trung_web}-{da_co}-{ngan}", status_code=303)


@router.post("/dang-web/lich")
def schedule(request: Request, form=Depends(checked_form), user: User = Depends(web_user)):
    if not has_role(user, "director"):  # Kinh doanh soạn, đăng nháp được; bật tự soạn chỉ Giám đốc
        raise BusinessError("FORBIDDEN", "Chỉ Giám đốc bật hoặc tắt tự soạn.", status=403)
    _can_ghi()
    gio = str(form.get("gio", "07:00"))
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", gio):
        raise BusinessError("BAD_FORM", "Giờ chạy chưa hợp lệ.")

    def so(k, toi_da):
        s = str(form.get(k, "0"))
        return min(toi_da, int(s)) if s.isdigit() else 0

    hd.ghi_lich(goc(), {"bat": form.get("bat") == "1", "gio": gio, "so_sp": so("so_sp", 5), "so_bai": so("so_bai", 3)})
    return RedirectResponse(f"/dang-web?tab={'bai-viet' if form.get('tab') == 'bai-viet' else 'san-pham'}&da_luu=lich", status_code=303)


# ---------------- ghép mã TD cho sản phẩm trên web (TUẤN duyệt mẫu D6/6, D7 ngày 02/11/2026) ----------------
# Chưa đồng bộ giá, còn hàng (TUẤN 02/11: "chưa"). Ghi lên web chỉ đổi ô Mã (SKU).
GHEP_NHOM = (("chac", "Gợi ý chắc (≥ 90%)"), ("xem", "Gợi ý cần xem (70–89%)"), ("khong", "Không có gợi ý"), ("cho-ghi", "Đã ghép · chờ ghi lên web"),
             ("da-co", "Đã có mã TD trên web"))
GHEP_CHAC = 0.9


def _ghep_du_lieu(session, g) -> dict:
    """Mỗi sản phẩm web chưa mang mã TD trong ô SKU: đã ghép (chờ ghi), gợi ý mã giống nhất, hoặc không có gợi ý."""
    web = _doc_web(hd.file_sp_web(g))
    cay = CayDanhMuc(web["danh_muc"])
    hang = session.execute(CAU_HANG).all()
    theo_ma = {h.code: h for h in hang}
    tt = hd.doc_trang_thai(g)
    ma_theo_web, _ = _ma_theo_web(session, g, tt)
    lien_ket: dict[int, str] = {}  # web_id → mã (SKU đúng mã app, hoặc đã ghép / app đã đăng)
    for it in web["ds"]:
        sku = str(it.get("sku") or "").strip().upper()
        if sku in theo_ma:
            lien_ket[int(it["id"])] = sku
    for wid, code in ma_theo_web.items():
        lien_ket.setdefault(wid, code)
    ma_da_dung = set(lien_ket.values())
    bo = hd.doc_bo_ghep(g)
    xac = hd.doc_xac_nhan(g)
    khop = _khop([(h.code, h.name) for h in hang], web["ds"], web["luc"])
    tot: dict[int, tuple[float, int, str]] = {}  # web_id → (điểm, tồn, mã)
    for code, k in khop.items():
        if k.get("kieu") != "giong" or code in ma_da_dung:
            continue
        x = xac.get(code) if isinstance(xac.get(code), dict) else {}
        khong = {int(i) for i in x.get("khong") or [] if str(i).isdigit()}
        ton = float(theo_ma[code].ton or 0) if code in theo_ma else 0.0
        for w in k.get("web") or []:
            wid = int(w["id"])
            if wid in khong or wid in lien_ket:
                continue
            ung = (float(w.get("diem") or 0), ton, code)
            if ung[:2] > tot.get(wid, (0.0, -1.0, ""))[:2]:
                tot[wid] = ung
    rows, dem = [], {k: 0 for k, _ in GHEP_NHOM}
    for it in web["ds"]:
        wid = int(it["id"])
        sku = str(it.get("sku") or "").strip().upper()
        code = lien_ket.get(wid)
        if wid in bo and not code:
            continue
        ids = [int(x) for x in it.get("dm") or [] if str(x).isdigit()]
        r = dict(id=wid, ten=it.get("ten", ""), anh=it.get("anh", ""), sku_cu=str(it.get("sku") or ""), xem=it.get("xem", ""),
                 gia_web=int(it["gia"]) if str(it.get("gia", "")).isdigit() else 0,
                 dm_ten=", ".join(cay.duong(i) for i in ids if i in cay.theo_id and not cay.chua_phan_loai([i])))
        if code and sku == code:  # đã có mã TD trên web; bỏ ghép = xóa ô SKU trên web (TUẤN 02/11/2026)
            r.update(nhom="da-co", ma=code, diem=None, bo_duoc=wid not in ma_theo_web or _ghep_tay(xac, code, wid))
        elif code:
            r.update(nhom="cho-ghi", ma=code, diem=None, bo_duoc=_ghep_tay(xac, code, wid))
        elif wid in tot:
            diem, _, code = tot[wid]
            r.update(nhom="chac" if diem >= GHEP_CHAC else "xem", ma=code, diem=int(round(diem * 100)))
        else:
            r.update(nhom="khong", ma="", diem=None)
        h = theo_ma.get(r["ma"]) if r["ma"] else None
        r.update(ten_app=h.name if h else "", gia_app=int(h.sale_price or 0) if h else 0, ton=h.ton if h else 0)
        dem[r["nhom"]] += 1
        rows.append(r)
    return dict(rows=rows, dem=dem, lien_ket=lien_ket, theo_ma=theo_ma, web=web, so_bo=len(bo), cay=cay)


def _ghep_tay(xac: dict, code: str, wid: int) -> bool:
    """Mã này do anh ghép tay (không phải app tự đăng) nên bỏ ghép được."""
    return isinstance(xac.get(code), dict) and str(xac[code].get("dung", "")) == str(wid)


def _viec_ghi_ma(g):
    return next((v for v in hd.ds(g) if v.get("kieu") == "ghi_sku" and v.get("trang_thai") not in hd.XONG), None)


def _ghep_quay_lai(form) -> str:
    q = {k: str(form.get(k, "")) for k in ("nhom", "q", "trang") if form.get(k)}
    return "/dang-web/ghep-ma" + ("?" + urlencode(q) if q else "")


@router.get("/dang-web/ghep-ma")
def match_codes(request: Request, nhom: str = "", q: str = "", trang: str = "1", so: str = "", da_luu: str = "", loi: str = "",
                user: User = Depends(web_user), session: Session = Depends(get_session)):
    g = goc()
    d = _ghep_du_lieu(session, g)
    if nhom not in dict(GHEP_NHOM):
        nhom = next((k for k, _ in GHEP_NHOM if d["dem"][k]), "chac")
    rows = [r for r in d["rows"] if r["nhom"] == nhom]
    if q.strip():
        f = fold(q)
        rows = [r for r in rows if f in fold(f"{r['ten']} {r['ma']} {r['ten_app']} {r['sku_cu']}")]
    rows.sort(key=lambda r: (-(r["diem"] or 0), r["ten"]))
    query = dict(nhom=nhom, q=q)
    p = paging.build(trang, so, len(rows), query)
    rows = rows[p["offset"]: p["offset"] + p["size"]]
    tiles = [dict(label=label, n=d["dem"][k], on=k == nhom, href="/dang-web/ghep-ma?" + urlencode(dict(nhom=k))) for k, label in GHEP_NHOM]
    dang_ghi = _viec_ghi_ma(g)
    return render(request, "dang_web_ghep.jinja", rows=rows, p=p, query=query, nhom=nhom, q=q, tiles=tiles, dem=d["dem"],
                  so_bo=d["so_bo"], web=d["web"], nhip=hd.doc_nhip(g), da_luu=da_luu, loi=loi[:200], dang_ghi=dang_ghi,
                  loi_thu_muc=hd.kiem_ghi_duoc(g))


def _ghep_mot(d: dict, web_id: str, ma: str) -> str:
    """Kiểm rồi ghép; trả lý do nếu không ghép được."""
    ma = ma.strip().upper()
    if not web_id.isdigit() or not any(int(it["id"]) == int(web_id) for it in d["web"]["ds"]):
        return "Không thấy sản phẩm này trên danh sách web. Bấm Cập nhật từ web rồi thử lại."
    if ma not in d["theo_ma"]:
        return f"Không thấy mã {ma} trong Hàng hóa (hoặc đã ngừng dùng)."
    khac = [w for w, c in d["lien_ket"].items() if c == ma and w != int(web_id)]
    if khac:
        ten = next((it.get("ten", "") for it in d["web"]["ds"] if int(it["id"]) == khac[0]), "")
        return f"Mã {ma} đã ghép với sản phẩm web khác ({ten or khac[0]}). Bỏ ghép cái đó trước."
    hd.ghep_ma(goc(), ma, int(web_id))
    hd.dat_bo_ghep(goc(), int(web_id), False)
    d["lien_ket"][int(web_id)] = ma
    return ""


@router.post("/dang-web/ghep-ma")
def match_codes_save(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    _can_ghi()
    g = goc()
    d = _ghep_du_lieu(session, g)
    hanh_dong = str(form.get("hanh_dong", ""))
    web_id = str(form.get("web_id", ""))
    ve = _ghep_quay_lai(form)
    noi = "&" if "?" in ve else "?"
    if hanh_dong == "dung":
        loi = _ghep_mot(d, web_id, str(form.get("ma", "")))
        if loi:
            return RedirectResponse(ve + noi + urlencode(dict(loi=loi)), status_code=303)
        return RedirectResponse(ve + noi + "da_luu=1", status_code=303)
    if hanh_dong == "dung_nhieu":
        so, loi = 0, ""
        for cap in dict.fromkeys(str(x) for x in form.getlist("chon")):
            wid, _, ma = cap.partition(":")
            l1 = _ghep_mot(d, wid, ma)
            if l1:
                loi = loi or l1
            else:
                so += 1
        if not so and not loi:
            loi = "Tick ít nhất một dòng."
        q = dict(da_luu=str(so)) if so else {}
        if loi:
            q["loi"] = loi
        return RedirectResponse(ve + noi + urlencode(q), status_code=303)
    if not web_id.isdigit():
        raise BusinessError("BAD_FORM", "Thiếu sản phẩm trên web.")
    if hanh_dong == "khong_co":
        hd.bo_ghep_web(g, int(web_id))
        hd.dat_bo_ghep(g, int(web_id), True)
    elif hanh_dong == "bo":
        hd.bo_ghep_web(g, int(web_id))
    elif hanh_dong == "bo_web":
        r = next((x for x in d["rows"] if x["id"] == int(web_id) and x["nhom"] == "da-co"), None)
        if not r or not r["bo_duoc"]:
            raise BusinessError("BAD_STATE", "Sản phẩm này không bỏ ghép được ở đây.", status=409)
        if _viec_ghi_ma(g):
            return RedirectResponse(ve + noi + urlencode(dict(loi="Máy đang ghi mã lên web, chờ xong rồi bỏ ghép.")), status_code=303)
        hd.bo_ghep_web(g, int(web_id))
        viec_id = hd.tao_viec(g, "san_pham", dict(kieu="ghi_sku", ghi=[dict(id=r["id"], ma="", ma_cu=r["ma"], ten=r["ten"])],
                                                  ten=f"Bỏ mã {r['ma']} trên web · {r['ten']}"[:200], van_dang=True))
        hd.sua(g, viec_id, lambda x: x.update(trang_thai=hd.CHO_DANG))
        return RedirectResponse(f"/dang-web/viec/{viec_id}", status_code=303)
    else:
        raise BusinessError("BAD_FORM", "Thao tác không hợp lệ.")
    return RedirectResponse(ve + noi + "da_luu=1", status_code=303)


@router.post("/dang-web/ghi-ma")
def write_codes(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    """Gom mọi sản phẩm đã ghép mà ô SKU trên web chưa đúng mã thành một việc cho máy đăng web."""
    _can_ghi()
    g = goc()
    dang = _viec_ghi_ma(g)
    if dang:
        return RedirectResponse(f"/dang-web/viec/{dang['id']}", status_code=303)
    d = _ghep_du_lieu(session, g)
    ghi = [dict(id=r["id"], ma=r["ma"], ten=r["ten"]) for r in d["rows"] if r["nhom"] == "cho-ghi"]
    if not ghi:
        return RedirectResponse("/dang-web/ghep-ma?" + urlencode(dict(nhom="cho-ghi", loi="Chưa có sản phẩm nào chờ ghi mã.")), status_code=303)
    viec_id = hd.tao_viec(g, "san_pham", dict(kieu="ghi_sku", ghi=ghi, ten=f"Ghi mã TD lên web · {len(ghi)} sản phẩm", van_dang=True))
    hd.sua(g, viec_id, lambda x: x.update(trang_thai=hd.CHO_DANG))
    return RedirectResponse(f"/dang-web/viec/{viec_id}", status_code=303)
