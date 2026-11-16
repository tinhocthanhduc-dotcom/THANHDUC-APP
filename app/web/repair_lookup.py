"""Bảng giá màn thợ, 3 tab (TUẤN duyệt mẫu M1–M4 và D1–D3, 15/11/2026).

Giá sửa 3 bước: B1 chọn model → B2 chọn gói sửa chữa (chọn cấp 1/2/3/chính hãng) → B3 chọn phụ phí; tổng gồm VAT.
Giá dịch vụ: B1 model → B2 dịch vụ (ô xổ xuống, −/+ số lượng; dịch vụ theo model chỉ hiện khi có giá cho máy đó) → B3 phụ phí.
Giá mực (trước là Giá sản phẩm): chỉ hộp mực; thêm hàng (−/+) + phụ phí; tổng gồm VAT.
Chỉ xem để báo khách, không ghi phiếu, không giá vốn/số tồn.
Giá gói lấy pb.lookup (model ghi đè → giá nhóm), không bao giờ đoán giá: model chưa có thì gợi ý model gần giống.
"""
from collections import Counter

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import price_book as pb
from .. import products as product_svc
from .. import quick_quotes as qq
from ..db import get_session
from ..models import Equipment, PriceService, Product, RepairGroup, RepairPrice, RepairInspection, ServiceTicket, User
from ..service_tickets import CLOSED
from . import render, web_user

router = APIRouter(include_in_schema=False)
MAX_MINE = 6
TAB_COOKIE = 'td_tra_gia'  # nhớ lần chọn trước: sua / dv / sp (Giá mực)
TAB_HOME = {'sp': '/tra-gia/san-pham', 'dv': '/tra-gia/dich-vu'}


def my_models(session: Session, user: User) -> list[dict]:
    """Máy thợ đang sửa (phiếu của mình chưa đóng): tên model từ kiểm tra máy, rồi thiết bị."""
    rows = session.execute(
        select(ServiceTicket.number, RepairInspection.model_name, Equipment.model_name, Product.name)
        .outerjoin(RepairInspection, RepairInspection.ticket_id == ServiceTicket.id)
        .outerjoin(Equipment, Equipment.serial == ServiceTicket.serial)
        .outerjoin(Product, Product.code == Equipment.product_code)
        .where(ServiceTicket.assignee_id == user.id, ServiceTicket.status.not_in(CLOSED))
        .order_by(ServiceTicket.id.desc()).limit(30)).all()
    out, seen = [], set()
    for number, inspected, eq_model, product in rows:
        name = ' '.join(str(inspected or eq_model or product or '').split())
        if not name:
            continue
        m = pb.find_model(session, name)
        label = m.name if m and m.active else name
        if label.lower() in seen:
            continue
        seen.add(label.lower())
        out.append(dict(name=label, ticket=number))
        if len(out) >= MAX_MINE:
            break
    return out


def _matches(session, q: str) -> list:
    q = ' '.join(str(q or '').split())[:60]
    return [m for m in pb.models(session, q) if m.active][:8] if len(q) >= 2 else []


LEVELS = ('1', '2', '3', 'CH')
LEVEL_LABEL = {'1': 'Cấp 1', '2': 'Cấp 2', '3': 'Cấp 3', 'CH': 'C.hãng'}
MAX_QTY = 99


def model_services(session: Session, m) -> list[dict]:
    """Gói sửa có giá cho model (giá riêng model hoặc giá nhóm máy), mỗi gói đủ ô cấp 1/2/3/chính hãng gồm VAT."""
    sids = set(session.scalars(select(RepairPrice.service_id).where(
        or_(RepairPrice.group_id == m.group_id, RepairPrice.model_id == m.id))))
    out = []
    for s in pb.services(session, 'GOI', active_only=True):
        if s.id not in sids:
            continue
        cells = {}
        for k in LEVELS:
            r = pb.lookup(session, s.id, m.name, 2 if k == 'CH' else int(k), genuine=k == 'CH')
            cells[k] = dict(price=r['price_vat'], warranty=r['warranty'] or 0) if r['ok'] else None
        prices = [c['price'] for c in cells.values() if c]
        if not prices:
            continue
        default = '2' if cells['2'] else next(k for k in LEVELS if cells[k])
        out.append(dict(id=s.id, name=s.name, cells=cells, low=min(prices), high=max(prices), default=default,
                        warranty=max(c['warranty'] for c in cells.values() if c)))
    return out


def fees(session: Session) -> list[dict]:
    """Phụ phí (0099: chỉ dòng Loại = Phụ phí; dịch vụ nằm ở tab Giá dịch vụ)."""
    return [dict(id=f.id, name=f.name, price=pb.with_vat(f.price), note=' '.join((f.description or '').split())[:160])
            for f in pb.fee_services(session)]


def _ints(values) -> list[int]:
    out = []
    for v in values:
        try:
            out.append(int(str(v).strip()))
        except ValueError:
            continue
    return out


@router.get('/tra-gia')
def lookup(request: Request, model: str = '', q: str = '', user: User = Depends(web_user),
           session: Session = Depends(get_session)):
    pb.require_view(user)
    qp = request.query_params
    if not qp and request.cookies.get(TAB_COOKIE) in TAB_HOME:
        return RedirectResponse(TAB_HOME[request.cookies[TAB_COOKIE]], status_code=303)
    model = ' '.join(str(model or '').split())[:160]
    m = pb.find_model(session, model) if model else None
    m = m if m and m.active else None
    ctx = dict(group=None, model=model, m=m, q=q[:60], mine=my_models(session, user), matches=_matches(session, q or (model if not m else '')),
               services=[], fees=[], picked={}, fee_ids=set(), total=0, fee_total=0, money=qq.money, levels=LEVELS, level_label=LEVEL_LABEL)
    if m:
        ctx['group'] = session.get(RepairGroup, m.group_id)
        ctx['services'] = model_services(session, m)
        ctx['fees'] = fees(session)
        # Trạng thái chọn từ đường dẫn (g=<id gói>, cap_<id>=<cấp>, pp=<id phí>) để quay lại đúng chỗ; JS cập nhật tổng ngay.
        by_id = {x['id']: x for x in ctx['services']}
        for sid in _ints(qp.getlist('g')):
            if sid in by_id:
                lv = qp.get(f'cap_{sid}', '')
                ctx['picked'][sid] = lv if lv in LEVELS and by_id[sid]['cells'].get(lv) else by_id[sid]['default']
        fee_by = {f['id']: f for f in ctx['fees']}
        ctx['fee_ids'] = {i for i in _ints(qp.getlist('pp')) if i in fee_by}
        ctx['fee_total'] = sum(fee_by[i]['price'] for i in ctx['fee_ids'])
        ctx['total'] = sum(by_id[sid]['cells'][lv]['price'] for sid, lv in ctx['picked'].items()) + ctx['fee_total']
    ctx['group_names'] = {g.id: g.name for g in pb.groups(session, active_only=False)}
    resp = render(request, 'tra_gia_tho.jinja', **ctx)
    resp.set_cookie(TAB_COOKIE, 'sua', max_age=180 * 86400, httponly=True, samesite='lax')
    return resp


@router.get('/tra-gia/tim')
def find(request: Request, q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    pb.require_view(user)
    return render(request, 'tra_gia_tho_tim.jinja', matches=_matches(session, q), q=q[:60],
                  group_names={g.id: g.name for g in pb.groups(session, active_only=False)})


# ---------- Giá dịch vụ (TUẤN duyệt mẫu D1 15/11/2026) ----------
DV_BASE = '/tra-gia/dich-vu'


def dv_services(session: Session, m) -> list[dict]:
    """Dịch vụ (PHI không phải phụ phí) đang dùng: theo model chỉ khi có giá cho model/nhóm máy; một giá luôn có.
    m=None (thợ bấm "Chỉ xem dịch vụ chung") thì chỉ dịch vụ một giá. Theo máy xếp trước, rồi dịch vụ chung."""
    out = []
    for s in pb.services(session, 'PHI', active_only=True):
        if s.is_fee:
            continue
        r = pb.service_price(session, s, m)
        if r is None:
            continue
        out.append(dict(id=s.id, name=s.name, unit=s.unit or 'lần', price=r['price_vat'], warranty=r['warranty'] or 0,
                        by_model=s.by_model, note=' '.join((s.description or '').split())[:120]))
    return sorted(out, key=lambda x: not x['by_model'])


def dv_basket(values, by_id: dict) -> dict[int, int]:
    """Dịch vụ đã chọn từ đường dẫn dv=<id>:<số lượng>: chỉ dịch vụ đang có giá, số lượng 1–99, gộp trùng."""
    qty = {}
    for v in list(values)[:50]:
        sid, _, n = str(v).partition(':')
        try:
            sid, n = int(sid), int(n or 1)
        except ValueError:
            continue
        if sid in by_id and n > 0:
            qty[sid] = min(MAX_QTY, qty.get(sid, 0) + n)
    return qty


@router.get('/tra-gia/dich-vu')
def services_lookup(request: Request, model: str = '', q: str = '', chung: str = '', user: User = Depends(web_user),
                    session: Session = Depends(get_session)):
    pb.require_view(user)
    qp = request.query_params
    model = ' '.join(str(model or '').split())[:160]
    m = pb.find_model(session, model) if model else None
    m = m if m and m.active else None
    general = chung == '1' and not m
    ctx = dict(base=DV_BASE, model=model, m=m, general=general, group=None, q=q[:60], mine=my_models(session, user),
               matches=_matches(session, q or (model if not m else '')), services=[], fees=[], qty={}, fee_ids=set(),
               dv_total=0, fee_total=0, money=qq.money, fee_scope='dv')
    if m or general:
        ctx['group'] = session.get(RepairGroup, m.group_id) if m else None
        ctx['services'] = dv_services(session, m)
        by_id = {x['id']: x for x in ctx['services']}
        ctx['qty'] = dv_basket(qp.getlist('dv'), by_id)
        ctx['picked'] = [by_id[i] for i in by_id if i in ctx['qty']]
        ctx['fees'] = fees(session)
        fee_by = {f['id']: f for f in ctx['fees']}
        ctx['fee_ids'] = {i for i in _ints(qp.getlist('pp')) if i in fee_by}
        ctx['dv_total'] = sum(by_id[i]['price'] * n for i, n in ctx['qty'].items())
        ctx['fee_total'] = sum(fee_by[i]['price'] for i in ctx['fee_ids'])
    ctx['group_names'] = {g.id: g.name for g in pb.groups(session, active_only=False)}
    resp = render(request, 'tra_gia_dv.jinja', **ctx)
    resp.set_cookie(TAB_COOKIE, 'dv', max_age=180 * 86400, httponly=True, samesite='lax')
    return resp


@router.get('/tra-gia/dich-vu/tim')
def services_find(request: Request, q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    pb.require_view(user)
    return render(request, 'tra_gia_tho_tim.jinja', matches=_matches(session, q), q=q[:60], base=DV_BASE,
                  group_names={g.id: g.name for g in pb.groups(session, active_only=False)})


# ---------- Giá mực (trước là Giá sản phẩm; mẫu SP1–SP2 + M4, đổi tên D2 15/11/2026) ----------
# Thợ chỉ xem giá bán gồm VAT và bảo hành; không giá vốn, không tồn kho (kể cả Còn/Hết hàng, TUẤN 15/11), không hàng ngừng bán.
# TUẤN 15/11: chỉ hộp mực / mực (nhóm hàng tab "Mực & hộp mực" của Bảng giá chung); máy in, linh kiện, VPP không hiện.
MAX_PRODUCTS = 30


def _for_tech(p: Product) -> bool:
    """Hàng hiện ở tab Giá mực: nhóm mực (tab "Mực & hộp mực"), bỏ nhóm hàng renew (TUẤN 16/11: chỉ mực in, bỏ renew).
    Lọc theo nhóm hàng (group_name), không theo tên mã hàng."""
    return not p.is_test and pb.product_tab(p) == 'muc' and 'renew' not in pb.fold(p.group_name)


def product_rows(session: Session, q: str, nhom: str = '') -> tuple[list[dict], list[tuple[str, int]]]:
    q = ' '.join(str(q or '').split())[:60]
    if len(q) < 2:
        return [], []
    found = [p for p in product_svc.search(session, q, active_only=True, limit=200) if _for_tech(p)]
    groups = Counter(p.group_name for p in found if p.group_name)
    if nhom:
        found = [p for p in found if p.group_name == nhom]
    return _cards(session, found[:MAX_PRODUCTS]), sorted(groups.items(), key=lambda g: (-g[1], g[0]))


def _cards(session: Session, found) -> list[dict]:
    # TUẤN 15/11: không hiện Còn hàng / Hết hàng trên bảng giá thợ (chỉ giá bán gồm VAT và bảo hành).
    return [dict(code=p.code, name=p.name, group=p.group_name, price=pb.with_vat(p.sale_price), warranty=p.warranty_months or 0)
            for p in found]


def basket(session: Session, values) -> list[dict]:
    """Hàng đã chọn từ đường dẫn sp=<mã>:<số lượng> (mẫu M4): chỉ hàng đang bán, số lượng 1–99, gộp mã trùng."""
    qty = {}
    for v in list(values)[:50]:
        code, _, n = str(v).partition(':')
        code = code.strip().upper()[:16]
        try:
            n = int(n or 1)
        except ValueError:
            continue
        if code and n > 0:
            qty[code] = min(MAX_QTY, qty.get(code, 0) + n)
    if not qty:
        return []
    found = [p for p in session.scalars(select(Product).where(Product.code.in_(list(qty)), Product.active.is_(True),
                                                              Product.is_test.is_(False)).order_by(Product.code))]
    rows = _cards(session, [p for p in found if _for_tech(p)])
    for r in rows:
        r['qty'] = qty[r['code']]
    return rows


def _sp_ctx(session, q, nhom):
    q, nhom = ' '.join(str(q or '').split())[:60], str(nhom or '')[:100]
    rows, groups = product_rows(session, q, nhom)
    return dict(q=q, nhom=nhom, rows=rows, groups=groups, money=qq.money, qty={})


@router.get('/tra-gia/san-pham')
def products(request: Request, q: str = '', nhom: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    pb.require_view(user)
    ctx = _sp_ctx(session, q, nhom)
    qp = request.query_params
    ctx['picked'] = basket(session, qp.getlist('sp'))
    ctx['qty'] = {r['code']: r['qty'] for r in ctx['picked']}
    ctx['fees'] = fees(session)
    fee_by = {f['id']: f for f in ctx['fees']}
    ctx['fee_ids'] = {i for i in _ints(qp.getlist('pp')) if i in fee_by}
    ctx['goods'] = sum(r['price'] * r['qty'] for r in ctx['picked'])
    ctx['fee_total'] = sum(fee_by[i]['price'] for i in ctx['fee_ids'])
    resp = render(request, 'tra_gia_sp.jinja', **ctx)
    resp.set_cookie(TAB_COOKIE, 'sp', max_age=180 * 86400, httponly=True, samesite='lax')
    return resp


@router.get('/tra-gia/san-pham/tim')
def products_find(request: Request, q: str = '', nhom: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    pb.require_view(user)
    return render(request, 'tra_gia_sp_ds.jinja', **_sp_ctx(session, q, nhom))
