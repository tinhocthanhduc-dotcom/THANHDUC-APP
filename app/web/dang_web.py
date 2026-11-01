"""Màn Đăng web (TUẤN duyệt mẫu 01/11/2026): soạn và đăng nháp sản phẩm, bài viết lên thanhduc.vn.

App chỉ ghi việc vào thư mục chung (dang_web/hang_doi.py); phần nặng (tách nền, AI, gọi web) do máy đăng
web chạy nền làm ở container riêng, nên app không chậm. Không đụng dữ liệu nghiệp vụ trong database.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from dang_web import hang_doi as hd

from . import checked_form, paging, render, web_user
from .. import products as product_svc
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import Product, User
from ..text import fold

router = APIRouter(include_in_schema=False)

TABS = (("san-pham", "Sản phẩm", "san_pham"), ("bai-viet", "Bài viết", "bai_viet"), ("da-dang", "Đã đăng", None))
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
    return "tu_dong" if nhip and nhip.get("ai") else "mau"


def _them_nhan(v: dict) -> dict:
    v = dict(v)
    v["nhan"] = hd.NHAN.get(v.get("trang_thai"), v.get("trang_thai"))
    v["tone"] = TONE.get(v.get("trang_thai"), "nhap")
    v["dang_chay"] = v.get("trang_thai") in hd.DANG_CHAY
    return v


# ---------------- danh sách ----------------
def _anh_dau(viec_id) -> str:
    v = hd.doc(goc(), viec_id) if viec_id else None
    return (v.get("anh_web") or [""])[0] if v else ""


def _cu(luc: str, giay: int) -> bool:
    try:
        return (hd.bay_gio() - datetime.fromisoformat(luc)).total_seconds() > giay
    except (TypeError, ValueError):
        return True


def _da_dang(q: str, chip: str) -> tuple[list[dict], dict]:
    tt = hd.doc_trang_thai(goc())
    web = hd.doc_json(hd.file_web(goc()), {})
    rows = []
    for loai in ("san_pham", "bai_viet"):
        for khoa, info in tt[loai].items():
            if not isinstance(info, dict) or not info.get("id"):
                continue
            w = (web.get(loai) or {}).get(str(info["id"]), {})
            st = w.get("trang_thai", "")
            nhan, tone = WEB_TT.get(st, ("Chưa rõ", "nhap"))
            if loai == "san_pham" and st == "publish":
                nhan = "Đang bán"
            rows.append(dict(loai=loai, khoa=khoa, id=info["id"], ten=w.get("ten") or info.get("ten") or khoa,
                             st=st, nhan=nhan, tone=tone, link_sua=info.get("link", ""), xem=w.get("xem") or info.get("xem", ""),
                             luc=info.get("luc", ""), co_san=info.get("co_san", False), anh=_anh_dau(info.get("viec"))))
    rows.sort(key=lambda r: r["luc"], reverse=True)
    dem = {"tat-ca": len(rows), "nhap": sum(r["st"] == "draft" for r in rows),
           "dang-hien": sum(r["st"] == "publish" for r in rows), "bai-viet": sum(r["loai"] == "bai_viet" for r in rows)}
    if chip == "nhap":
        rows = [r for r in rows if r["st"] == "draft"]
    elif chip == "dang-hien":
        rows = [r for r in rows if r["st"] == "publish"]
    elif chip == "bai-viet":
        rows = [r for r in rows if r["loai"] == "bai_viet"]
    if q:
        f = fold(q)
        rows = [r for r in rows if f in fold(f"{r['ten']} {r['khoa']}")]
    return rows, {**dem, "luc_web": web.get("luc", "")}


@router.get("/dang-web")
def listing(request: Request, tab: str = "san-pham", loc: str = "", q: str = "", chip: str = "tat-ca", trang: str = "1", so: str = "",
            da_luu: str = "", user: User = Depends(web_user)):
    tab = tab if tab in [t[0] for t in TABS] else "san-pham"
    g = goc()
    loi_thu_muc = hd.kiem_ghi_duoc(g)
    nhip = hd.doc_nhip(g)
    viec = [_them_nhan(v) for v in hd.ds(g)]
    con = [v for v in viec if v.get("trang_thai") not in hd.XONG]
    tt = hd.doc_trang_thai(g)
    dem_da_dang = sum(1 for k in ("san_pham", "bai_viet") for x in tt[k].values() if isinstance(x, dict) and x.get("id"))
    tabs = [dict(label=label, href=f"/dang-web?tab={key}", on=key == tab,
                 n=dem_da_dang if loai is None else sum(1 for v in con if v["loai"] == loai)) for key, label, loai in TABS]
    ctx = dict(tab=tab, tabs=tabs, nhip=nhip, loi_thu_muc=loi_thu_muc, da_luu=da_luu, q=q, chip=chip, loc=loc,
               cach_viet=hd.CACH_VIET, cach_mac_dinh=_cach_mac_dinh(nhip))
    if tab == "da-dang":
        rows, dem = _da_dang(q.strip(), chip)
        # Mở tab là nhờ máy đăng web lấy lại trạng thái trên web (không quá 10 phút một lần).
        if not loi_thu_muc and _cu(dem["luc_web"], 600):
            hd.yeu_cau_lam_moi(g)
        p = paging.build(trang, so, len(rows), dict(tab=tab, q=q, chip=chip))
        rows = rows[(p["page"] - 1) * p["size"]: p["page"] * p["size"]]
        return render(request, "dang_web.jinja", rows=rows, dem=dem, p=p, web_tt=WEB_TT, **ctx)
    loai = dict((t[0], t[2]) for t in TABS)[tab]
    cua_tab = [v for v in con if v["loai"] == loai]
    tiles = [dict(label=label, n=sum(1 for v in cua_tab if v["trang_thai"] in sts),
                  href=f"/dang-web?{urlencode(dict(tab=tab, loc='' if loc == key else key))}", on=loc == key) for key, label, sts in LOC]
    sts = dict((k, s) for k, _, s in LOC).get(loc)
    rows = [v for v in cua_tab if not sts or v["trang_thai"] in sts]
    gan_day = [v for v in viec if v["loai"] == loai and v.get("trang_thai") == hd.DA_DANG][:5]
    chu_de = []
    if loai == "bai_viet":
        dang_co = {v.get("chu_de") for v in con}
        chu_de = [dict(ten=c, xong=c in tt["bai_viet"], dang=c in dang_co, trung=c in tt["nghi_trung"]) for c in hd.doc_chu_de(g)]
    return render(request, "dang_web.jinja", rows=rows, tiles=tiles, gan_day=gan_day, chu_de=chu_de, lich=hd.doc_lich(g),
                  co_chay=any(v["dang_chay"] for v in viec), **ctx)


# ---------------- soạn mới ----------------
def _form_soan(request, session, d: dict, error="", status=200):
    nhip = hd.doc_nhip(goc())
    sp = session.get(Product, d.get("ma", "")) if d.get("ma") else None
    anh_co_san = bool(sp and hd.co_anh_goc(goc(), sp.code))
    if not d.get("cach_viet"):
        d["cach_viet"] = "tu_dong" if nhip and nhip.get("ai") else ("chatgpt_web" if d["loai"] == "bai_viet" else "mau")
    return render(request, "dang_web_soan.jinja", d=d, sp=sp, anh_co_san=anh_co_san, nhip=nhip, cach_viet=hd.CACH_VIET,
                  error=error, toi_da_anh=TOI_DA_ANH, status=status)


@router.get("/dang-web/soan")
def new_form(request: Request, loai: str = "san-pham", ma: str = "", chu_de: str = "", wp_id: str = "",
             user: User = Depends(web_user), session: Session = Depends(get_session)):
    d = dict(loai="bai_viet" if loai == "bai-viet" else "san_pham", ma=ma.strip().upper(), chu_de=chu_de.strip(),
             wp_id=wp_id if wp_id.isdigit() else "")
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
             nen_trang=form.get("nen_trang") == "1", wp_id=str(form.get("wp_id", "")).strip())
    try:
        _can_ghi()
        g = goc()
        if d["cach_viet"] not in hd.CACH_VIET:
            raise BusinessError("BAD_FORM", "Chọn cách viết.")
        if loai == "bai_viet" and d["cach_viet"] == "mau":
            raise BusinessError("BAD_FORM", "Bài viết cần AI hoặc ChatGPT web; mẫu có sẵn chỉ dùng cho sản phẩm.")
        wp_id = int(d["wp_id"]) if d["wp_id"].isdigit() else None
        anh = await _doc_anh(form)
        them = dict(cach_viet=d["cach_viet"], ghi_chu=d["ghi_chu"], wp_id=wp_id, van_dang=bool(wp_id), viet_lai=bool(wp_id))
        if loai == "san_pham":
            sp = session.get(Product, d["ma"]) if d["ma"] else None
            if not sp or not sp.active:
                raise BusinessError("BAD_PRODUCT", "Chọn mã hàng đang dùng trong Hàng hóa.")
            if not anh and not wp_id and not hd.co_anh_goc(g, sp.code):
                raise BusinessError("NO_PHOTO", "Chụp hoặc chọn ít nhất 1 ảnh sản phẩm.")
            them.update(ma=sp.code, ten=sp.name, tach_nen=not d["nen_trang"])
        else:
            if len(d["chu_de"]) < 8:
                raise BusinessError("BAD_FORM", "Gõ chủ đề bài viết (ít nhất 8 chữ).")
            them.update(chu_de=d["chu_de"][:200], ten=d["chu_de"][:200])
        viec_id = hd.tao_viec(g, loai, them, anh)
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


# ---------------- một việc ----------------
def _page_viec(request, v, error="", status=200):
    g = goc()
    return render(request, "dang_web_viec.jinja", v=_them_nhan(v), nhip=hd.doc_nhip(g), error=error, status=status,
                  danh_muc=hd.doc_json(hd.file_danh_muc(g), []), cach_viet=hd.CACH_VIET)


@router.get("/dang-web/viec/{viec_id}")
def view(request: Request, viec_id: str, user: User = Depends(web_user)):
    return _page_viec(request, _viec(viec_id))


def _nap_sua(v: dict, form) -> None:
    d = dict(v.get("noi_dung") or {})
    for k in (SUA_SP if v["loai"] == "san_pham" else SUA_BAI):
        if k in form:
            d[k] = str(form.get(k, "")).strip()
    tieu_de = d.get("ten_web") if v["loai"] == "san_pham" else d.get("tieu_de")
    if not tieu_de:
        raise BusinessError("BAD_FORM", "Tên trên web không được để trống.")
    v["noi_dung"] = d
    if v["loai"] == "san_pham":
        v["gia"] = re.sub(r"\D", "", str(form.get("gia", "")))
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
            sau = hd.CHO_DANG if v.get("buoc_loi") == "dang" and v.get("noi_dung") else hd.CHO_SOAN
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
    _can_ghi()
    hd.yeu_cau_lam_moi(goc())
    return RedirectResponse("/dang-web?tab=da-dang&da_luu=lam-moi", status_code=303)


@router.post("/dang-web/chu-de")
def topics(request: Request, form=Depends(checked_form)):
    _can_ghi()
    g = goc()
    chu_de = " ".join(str(form.get("chu_de", "")).split())[:200]
    hanh_dong = str(form.get("hanh_dong", ""))
    if hanh_dong == "xoa":
        hd.xoa_chu_de(g, chu_de)
        return RedirectResponse("/dang-web?tab=bai-viet&da_luu=xoa-chu-de", status_code=303)
    if len(chu_de) < 8:
        raise BusinessError("BAD_FORM", "Gõ chủ đề bài viết (ít nhất 8 chữ).")
    hd.them_chu_de(g, chu_de)
    return RedirectResponse("/dang-web?tab=bai-viet&da_luu=them-chu-de", status_code=303)


@router.post("/dang-web/lich")
def schedule(request: Request, form=Depends(checked_form)):
    _can_ghi()
    gio = str(form.get("gio", "07:00"))
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", gio):
        raise BusinessError("BAD_FORM", "Giờ chạy chưa hợp lệ.")

    def so(k, toi_da):
        s = str(form.get(k, "0"))
        return min(toi_da, int(s)) if s.isdigit() else 0

    hd.ghi_lich(goc(), {"bat": form.get("bat") == "1", "gio": gio, "so_sp": so("so_sp", 5), "so_bai": so("so_bai", 3)})
    return RedirectResponse(f"/dang-web?tab={'bai-viet' if form.get('tab') == 'bai-viet' else 'san-pham'}&da_luu=lich", status_code=303)
