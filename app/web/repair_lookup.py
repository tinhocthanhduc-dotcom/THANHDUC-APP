"""Bảng giá màn thợ (TUẤN duyệt mẫu M1–M4, 15/11/2026).

Giá sửa 3 bước: B1 chọn model → B2 chọn gói sửa chữa (chọn cấp 1/2/3/chính hãng) → B3 chọn phụ phí; tổng gồm VAT.
Giá sản phẩm: thêm hàng (−/+ số lượng) + phụ phí; tổng gồm VAT. Chỉ xem để báo khách, không ghi phiếu, không giá vốn/số tồn.
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
TAB_COOKIE = 'td_tra_gia'  # nhớ lần chọn trước: Giá sửa / Giá sản phẩm


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
    return [dict(id=f.id, name=f.name, price=pb.with_vat(f.price), note=' '.join((f.description or '').split())[:160])
            for f in pb.services(session, 'PHI', active_only=True)]


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
    if not qp and request.cookies.get(TAB_COOKIE) == 'sp':
        return RedirectResponse('/tra-gia/san-pham', status_code=303)
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


# ---------- Giá sản phẩm (TUẤN duyệt mẫu SP1–SP2 + M4 phụ phí, 15/11/2026) ----------
# Thợ chỉ xem giá bán gồm VAT và bảo hành; không giá vốn, không tồn kho (kể cả Còn/Hết hàng, TUẤN 15/11), không hàng ngừng bán.
MAX_PRODUCTS = 30


def product_rows(session: Session, q: str, nhom: str = '') -> tuple[list[dict], list[tuple[str, int]]]:
    q = ' '.join(str(q or '').split())[:60]
    if len(q) < 2:
        return [], []
    found = [p for p in product_svc.search(session, q, active_only=True, limit=200) if not p.is_test]
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
    rows = _cards(session, found)
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
