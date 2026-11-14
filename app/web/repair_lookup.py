"""Tra giá sửa cho thợ (TUẤN duyệt mẫu T1–T3, 15/11/2026): chọn model, chọn lỗi, bấm Tra giá.

Thay bảng giá kiểu máy tính trên màn thợ. Kết quả chỉ các gói sửa gắn với lỗi đã chọn (cùng luật Hỏi giá sửa:
quick_quotes.compute), giá đã gồm VAT, ô sáng là cấp hay gặp với lỗi. Model chưa có thì không đoán giá, gợi ý model gần giống.
"""
from collections import Counter

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import price_book as pb
from .. import products as product_svc
from .. import quick_quotes as qq
from ..db import get_session
from ..models import Equipment, Product, RepairGroup, RepairInspection, ServiceTicket, StockBalance, User, Warehouse
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


@router.get('/tra-gia')
def lookup(request: Request, model: str = '', q: str = '', xem: str = '', user: User = Depends(web_user),
           session: Session = Depends(get_session)):
    pb.require_view(user)
    if not request.query_params and request.cookies.get(TAB_COOKIE) == 'sp':
        return RedirectResponse('/tra-gia/san-pham', status_code=303)
    ht = [str(v)[:200] for v in request.query_params.getlist('ht')][:qq.MAX_SYMPTOMS]
    model = ' '.join(str(model or '').split())[:160]
    m = pb.find_model(session, model) if model else None
    m = m if m and m.active else None
    ctx = dict(group=None, model=model, m=m, ht=ht, q=q[:60], mine=my_models(session, user), matches=_matches(session, q or (model if not m else '')),
               chips=[], result=None, error='', money=qq.money, level_keys=('1', '2', '3'), level_hint=qq.LEVEL_HINT)
    if m:
        ctx['group'] = session.get(RepairGroup, m.group_id)
        ctx['chips'] = qq.symptom_names(session, m)
        if xem:
            if not ht:
                ctx['error'] = 'Chọn ít nhất một lỗi của máy.'
            else:
                ctx['result'] = qq.compute(session, m.name, ht, 'CUA_HANG')
    ctx['group_names'] = {g.id: g.name for g in pb.groups(session, active_only=False)}
    resp = render(request, 'tra_gia_tho.jinja', **ctx)
    resp.set_cookie(TAB_COOKIE, 'sua', max_age=180 * 86400, httponly=True, samesite='lax')
    return resp


@router.get('/tra-gia/tim')
def find(request: Request, q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    pb.require_view(user)
    return render(request, 'tra_gia_tho_tim.jinja', matches=_matches(session, q), q=q[:60],
                  group_names={g.id: g.name for g in pb.groups(session, active_only=False)})


# ---------- Giá sản phẩm (TUẤN duyệt mẫu SP1–SP2, 15/11/2026) ----------
# Thợ chỉ xem giá bán gồm VAT, bảo hành, Còn hàng/Hết hàng (kho thường); không giá vốn, không số tồn, không hàng ngừng bán.
MAX_PRODUCTS = 30


def product_rows(session: Session, q: str, nhom: str = '') -> tuple[list[dict], list[tuple[str, int]]]:
    q = ' '.join(str(q or '').split())[:60]
    if len(q) < 2:
        return [], []
    found = [p for p in product_svc.search(session, q, active_only=True, limit=200) if not p.is_test]
    groups = Counter(p.group_name for p in found if p.group_name)
    if nhom:
        found = [p for p in found if p.group_name == nhom]
    found = found[:MAX_PRODUCTS]
    codes = [p.code for p in found]
    stock = dict(session.execute(select(StockBalance.product_code, func.sum(StockBalance.qty))
                                 .join(Warehouse, Warehouse.code == StockBalance.warehouse_code)
                                 .where(Warehouse.kind == 'KHO', StockBalance.product_code.in_(codes))
                                 .group_by(StockBalance.product_code)).all()) if codes else {}
    rows = [dict(code=p.code, name=p.name, group=p.group_name, price=pb.with_vat(p.sale_price), warranty=p.warranty_months or 0,
                 in_stock=(stock.get(p.code) or 0) > 0) for p in found]
    return rows, sorted(groups.items(), key=lambda g: (-g[1], g[0]))


def _sp_ctx(session, q, nhom):
    q, nhom = ' '.join(str(q or '').split())[:60], str(nhom or '')[:100]
    rows, groups = product_rows(session, q, nhom)
    return dict(q=q, nhom=nhom, rows=rows, groups=groups, money=qq.money)


@router.get('/tra-gia/san-pham')
def products(request: Request, q: str = '', nhom: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    pb.require_view(user)
    resp = render(request, 'tra_gia_sp.jinja', **_sp_ctx(session, q, nhom))
    resp.set_cookie(TAB_COOKIE, 'sp', max_age=180 * 86400, httponly=True, samesite='lax')
    return resp


@router.get('/tra-gia/san-pham/tim')
def products_find(request: Request, q: str = '', nhom: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    pb.require_view(user)
    return render(request, 'tra_gia_sp_ds.jinja', **_sp_ctx(session, q, nhom))
