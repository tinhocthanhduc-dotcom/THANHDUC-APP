"""Cổng chỉ đọc cho Growth OS (app Dịch vụ SEO, marketing.quanlythanhduc.com), TUẤN 16/11/2026, Phần A.

- Khóa riêng "growth-os" (phạm vi marketing_read) tạo ở Quản trị › Trợ lý AI, chỉ dùng từ trong máy chủ Z2
  (Growth OS nối vào mạng nội bộ của app, gọi http://api:8000; đi qua Cloudflare bị chặn như mọi khóa AI).
- Chỉ GET, chỉ 3 đường dưới đây. Không đọc khách hàng, số điện thoại, đơn, tiền, giá vốn từng mã.
- Mỗi lần đọc ghi một dòng nhật ký 'ai.marketing_read' (xem ở Trợ lý AI › Nhật ký).

Tồn khả dụng = tồn − đang giữ cho đơn, cộng mọi kho bán hàng đang dùng (loại KHO). Không tính kho thợ (THO),
kho hàng lỗi/thanh lý (LOI). Nhóm biên lãi tính từ giá bán chưa VAT và giá vốn bình quân như cảnh báo dưới giá vốn.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from . import ai_agent, audit
from .below_cost import quote_cost
from .config import DEFAULT_VAT_RATE
from .db import get_session
from .errors import BusinessError
from .models import AiKey, Product, StockBalance, StockMinLevel, Warehouse
from .price_book import with_vat
from .stock_reservations import quantities

router = APIRouter(prefix='/marketing/v1', tags=['Growth OS (chỉ đọc)'])

SCOPE = 'marketing_read'
PAGE_MAX = 1000
CODES_MAX = 200
SALE_KINDS = ('KHO',)
# Nhóm biên lãi gộp trên giá bán chưa VAT. Đổi ngưỡng ở đây, Growth OS chỉ thấy tên nhóm.
BANDS = ((Decimal('0.30'), 'CAO'), (Decimal('0.15'), 'VUA'), (Decimal('0'), 'THAP'))


def marketing_key(request: Request, session: Session = Depends(get_session)) -> AiKey:
    raw = request.headers.get('authorization', '')
    raw = raw[7:].strip() if raw.lower().startswith('bearer ') else ''
    return ai_agent.authenticate(session, raw, request.headers, SCOPE)


def _now():
    return ai_agent._now()


def _iso(value: datetime | None):
    return value.astimezone(ai_agent.TZ).isoformat() if value else None


def _num(value) -> int | float:
    value = Decimal(value or 0)
    return int(value) if value == value.to_integral_value() else float(value)


def _log(session: Session, key: AiKey, path: str, params: dict, rows: int):
    audit.record(session, None, 'ai.marketing_read', 'ai_key', str(key.id), None,
                 dict(title=f'Growth OS đọc {path} ({rows} dòng)', path=path, params=params, rows=rows))
    session.commit()


def _listed():
    # Hàng chạy thử và mã máy cũ CARE không phải hàng bán.
    return and_(Product.is_test.is_(False), Product.code != 'MAY-CU-CARE')


# ---------- products ----------

def _parse_since(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.strip().replace('Z', '+00:00'))
    except ValueError:
        raise BusinessError('BAD_SINCE', 'updated_since phải là thời điểm ISO, vd. 2026-11-16T08:00:00+07:00.', status=422)
    return value if value.tzinfo else value.replace(tzinfo=ai_agent.TZ)


def _cursor(raw: str | None):
    if not raw:
        return None
    at, sep, code = raw.partition('|')
    if not sep or not code:
        raise BusinessError('BAD_CURSOR', 'cursor không đúng; dùng nguyên giá trị next_cursor đã nhận.', status=422)
    return _parse_since(at), code


def product_rows(session: Session, since: datetime | None = None, cursor=None, limit=500):
    stmt = select(Product).where(_listed())
    if since is not None:
        stmt = stmt.where(Product.updated_at >= since)
    if cursor is not None:
        at, code = cursor
        stmt = stmt.where(or_(Product.updated_at > at, and_(Product.updated_at == at, Product.code > code)))
    rows = list(session.scalars(stmt.order_by(Product.updated_at, Product.code).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    items = [dict(code=p.code, name=p.name, group=p.group_name or '', unit=p.unit, sale_price=int(p.sale_price or 0),
                  price_includes_vat=False, vat_rate=DEFAULT_VAT_RATE, sale_price_with_vat=with_vat(p.sale_price),
                  warranty_months=p.warranty_months or 0, active=bool(p.active), updated_at=_iso(p.updated_at),
                  data_version=p.version) for p in rows]
    nxt = f'{rows[-1].updated_at.isoformat()}|{rows[-1].code}' if more and rows else None
    return items, nxt


@router.get('/products')
def products(updated_since: str | None = None, cursor: str | None = None, limit: int = Query(500, ge=1, le=PAGE_MAX),
             key: AiKey = Depends(marketing_key), session: Session = Depends(get_session)):
    items, nxt = product_rows(session, _parse_since(updated_since), _cursor(cursor), limit)
    out = dict(as_of=_iso(_now()), items=items, next_cursor=nxt)
    _log(session, key, '/products', dict(updated_since=updated_since, cursor=cursor, limit=limit), len(items))
    return out


# ---------- stock ----------

def _codes(raw: str | None) -> list[str]:
    codes = list(dict.fromkeys(c.strip().upper() for c in (raw or '').split(',') if c.strip()))
    if not codes:
        raise BusinessError('CODES_REQUIRED', 'Cần codes=TD-0045,TD-0046 (tối đa 200 mã).', status=422)
    if len(codes) > CODES_MAX:
        raise BusinessError('TOO_MANY_CODES', f'Tối đa {CODES_MAX} mã mỗi lần.', status=422)
    return codes


def stock_rows(session: Session, codes: list[str]):
    known = set(session.scalars(select(Product.code).where(Product.code.in_(codes), _listed())))
    wh = {w.code: w.name for w in session.scalars(select(Warehouse).where(Warehouse.kind.in_(SALE_KINDS), Warehouse.active.is_(True)))}
    keys = [(w, c) for w, c in session.execute(select(StockBalance.warehouse_code, StockBalance.product_code)
                                                .where(StockBalance.product_code.in_(sorted(known)),
                                                       StockBalance.warehouse_code.in_(sorted(wh))))]
    q = quantities(session, keys)
    mins = dict(session.execute(select(StockMinLevel.product_code, func.sum(StockMinLevel.min_qty))
                                .where(StockMinLevel.product_code.in_(sorted(known)), StockMinLevel.warehouse_code.in_(sorted(wh)))
                                .group_by(StockMinLevel.product_code)).all())
    as_of = _iso(_now())
    items = []
    for code in codes:
        if code not in known:
            continue
        parts = []
        for (w, c), v in sorted(q.items()):
            if c != code:
                continue
            free = max(Decimal(v['available'] or 0), Decimal(0))
            parts.append(dict(warehouse=w, name=wh[w], available=_num(free)))
        total = sum((Decimal(str(p['available'])) for p in parts), Decimal(0))
        items.append(dict(code=code, available=_num(total), min_level=_num(mins.get(code, 0)), as_of=as_of,
                          by_warehouse=[p for p in parts if p['available']]))
    return items, [c for c in codes if c not in known]


@router.get('/stock')
def stock(codes: str | None = None, key: AiKey = Depends(marketing_key), session: Session = Depends(get_session)):
    wanted = _codes(codes)
    items, missing = stock_rows(session, wanted)
    out = dict(as_of=_iso(_now()), warehouses_counted='Mọi kho bán hàng đang dùng (loại KHO). Không tính kho thợ, kho hàng lỗi/thanh lý.',
               items=items, not_found=missing)
    _log(session, key, '/stock', dict(codes=wanted[:20], count=len(wanted)), len(items))
    return out


# ---------- margin band ----------

def band_for(price: int, cost: int | None) -> str | None:
    if not cost or not price:
        return None
    margin = (Decimal(price) - Decimal(cost)) / Decimal(price)
    for floor, name in BANDS:
        if margin >= floor:
            return name
    return 'AM'


def margin_band(session: Session, code: str) -> dict:
    product = session.scalar(select(Product).where(Product.code == code.strip().upper(), _listed()))
    if product is None:
        raise BusinessError('NOT_FOUND', 'Không có mã hàng này.', status=404)
    cost = quote_cost(session, product.code)
    band = band_for(int(product.sale_price or 0), cost)
    reason = None if band else ('CHUA_CO_GIA_BAN' if not product.sale_price else 'CHUA_CO_GIA_VON')
    return dict(code=product.code, band=band, reason=reason, as_of=_iso(_now()))


@router.get('/products/{code}/margin-band')
def product_margin_band(code: str, key: AiKey = Depends(marketing_key), session: Session = Depends(get_session)):
    out = margin_band(session, code)
    _log(session, key, f'/products/{out["code"]}/margin-band', {}, 1)
    return out


# ---------- orders (Phần C) ----------
# Đơn bán + báo giá theo giờ đổi. Mã khách băm (HMAC, khóa bí mật của app), không tên, không SĐT, không địa chỉ, không ghi chú.
# Giờ đổi = muộn nhất trong: giờ sửa đơn/báo giá, lần giao (và lúc hủy lần giao), lần trả hàng (và lúc hủy), lần hoàn tiền đã chi.

ORDER_KIND, QUOTE_KIND = 'DON', 'BAO_GIA'
CONFIRMED = ('DA_CHOT', 'GIAO_MOT_PHAN', 'DA_GIAO')


def customer_hash(code: str | None) -> str | None:
    import hashlib
    import hmac
    from .config import get_settings

    if not code:
        return None
    key = ('growth-os:' + get_settings().secret_key).encode()
    return hmac.new(key, code.encode(), hashlib.sha256).hexdigest()[:20]


def _changed_columns():
    from sqlalchemy import literal
    from .models import Customer, Quote, SalesDelivery, SalesOrder, SalesRefund, SalesReturn

    def latest(model, *cols):
        return (select(func.max(func.greatest(*[func.coalesce(c, SalesOrder.updated_at) for c in cols])))
                .where(model.order_id == SalesOrder.id).correlate(SalesOrder).scalar_subquery())

    changed = func.greatest(SalesOrder.updated_at,
                            func.coalesce(latest(SalesDelivery, SalesDelivery.delivered_at, SalesDelivery.voided_at), SalesOrder.updated_at),
                            func.coalesce(latest(SalesReturn, SalesReturn.returned_at, SalesReturn.voided_at), SalesOrder.updated_at),
                            func.coalesce(latest(SalesRefund, SalesRefund.decided_at), SalesOrder.updated_at))
    real_order = SalesOrder.customer_code.in_(select(Customer.code).where(Customer.is_test.is_(False)))
    real_quote = Quote.customer_code.in_(select(Customer.code).where(Customer.is_test.is_(False)))
    orders = select(literal(ORDER_KIND).label('kind'), SalesOrder.id.label('id'), changed.label('changed_at')).where(real_order)
    quotes_ = select(literal(QUOTE_KIND).label('kind'), Quote.id.label('id'), Quote.updated_at.label('changed_at')).where(real_quote)
    from sqlalchemy import union_all
    return union_all(orders, quotes_).subquery()


def _order_cursor(raw: str | None):
    if not raw:
        return None
    parts = raw.split('|')
    if len(parts) != 3 or parts[1] not in (ORDER_KIND, QUOTE_KIND) or not parts[2].isdigit():
        raise BusinessError('BAD_CURSOR', 'cursor không đúng; dùng nguyên giá trị next_cursor đã nhận.', status=422)
    return _parse_since(parts[0]), parts[1], int(parts[2])


def _day(value):
    return value.astimezone(ai_agent.TZ).date().isoformat() if value else None


def _lead(row):
    return dict(lead_source=row.lead_source, campaign_code=row.campaign_code, lead_ref=row.lead_ref,
                web_order_ref=getattr(row, 'web_order_ref', None))


def order_rows(session: Session, since: datetime | None = None, cursor=None, limit=200):
    from .models import Customer, Quote, SalesDelivery, SalesOrder, SalesOrderLine, SalesRefund, SalesReturn, SalesReturnLine

    ch = _changed_columns()
    stmt = select(ch.c.kind, ch.c.id, ch.c.changed_at)
    if since is not None:
        stmt = stmt.where(ch.c.changed_at >= since)
    if cursor is not None:
        at, kind, rid = cursor
        stmt = stmt.where(or_(ch.c.changed_at > at, and_(ch.c.changed_at == at, or_(ch.c.kind > kind, and_(ch.c.kind == kind, ch.c.id > rid)))))
    keys = list(session.execute(stmt.order_by(ch.c.changed_at, ch.c.kind, ch.c.id).limit(limit + 1)))
    more = len(keys) > limit
    keys = keys[:limit]
    order_ids = [k.id for k in keys if k.kind == ORDER_KIND]
    quote_ids = [k.id for k in keys if k.kind == QUOTE_KIND]
    orders = {o.id: o for o in session.scalars(select(SalesOrder).where(SalesOrder.id.in_(order_ids)))} if order_ids else {}
    quotes_ = {q.id: q for q in session.scalars(select(Quote).where(Quote.id.in_(quote_ids)))} if quote_ids else {}
    codes = {r.customer_code for r in [*orders.values(), *quotes_.values()]}
    merged = dict(session.execute(select(Customer.code, Customer.merged_into).where(Customer.code.in_(codes))).all()) if codes else {}
    delivered, returns, refunds = {}, {}, {}
    if order_ids:
        for d in session.scalars(select(SalesDelivery).where(SalesDelivery.order_id.in_(order_ids), SalesDelivery.status == 'DA_GIAO')):
            delivered[d.order_id] = delivered.get(d.order_id, 0) + int(d.subtotal or 0)
        line_codes = dict(((l.order_id, l.line_no), l.product_code) for l in session.scalars(
            select(SalesOrderLine).where(SalesOrderLine.order_id.in_(order_ids))))
        for r in session.scalars(select(SalesReturn).where(SalesReturn.order_id.in_(order_ids)).order_by(SalesReturn.id)):
            rl = list(session.scalars(select(SalesReturnLine).where(SalesReturnLine.return_id == r.id).order_by(SalesReturnLine.line_no)))
            returns.setdefault(r.order_id, []).append(dict(
                number=r.number, status=r.status, date=_day(r.returned_at), net_amount=int(r.subtotal or 0),
                voided_at=_iso(r.voided_at),
                lines=[dict(product_code=line_codes.get((l.order_id, l.order_line_no)), qty=_num(l.qty), net_amount=int(l.amount or 0)) for l in rl]))
        for f in session.scalars(select(SalesRefund).where(SalesRefund.order_id.in_(order_ids), SalesRefund.status == 'DA_CHI').order_by(SalesRefund.id)):
            refunds.setdefault(f.order_id, []).append(dict(number=f.number, date=_day(f.decided_at), amount_incl_vat=int(f.amount)))
    items = []
    for k in keys:
        row = orders.get(k.id) if k.kind == ORDER_KIND else quotes_.get(k.id)
        if row is None:
            continue
        cust = merged.get(row.customer_code) or row.customer_code
        base = dict(id=row.id, kind=k.kind, number=row.number, status=row.status, date=_day(row.created_at),
                    customer_hash=customer_hash(cust), net_amount=int(row.subtotal or 0), vat_amount=int(row.vat_total or 0),
                    discount_amount=int(row.discount_amount or 0),
                    lines=[dict(product_code=l.product_code, qty=_num(l.qty), net_amount=int(l.amount or 0)) for l in row.lines],
                    **_lead(row), version=row.version, updated_at=_iso(k.changed_at),
                    cancelled_at=_iso(row.updated_at) if row.status == 'DA_HUY' else None)
        if k.kind == ORDER_KIND:
            got = returns.get(row.id, [])
            back = sum(r['net_amount'] for r in got if r['status'] == 'DA_NHAN')
            base.update(quote_id=row.quote_id, confirmed=row.status in CONFIRMED, confirmed_at=_iso(row.confirmed_at),
                        delivered_net=delivered.get(row.id, 0), returned_net=back,
                        net_revenue=delivered.get(row.id, 0) - back, returns=got, refunds=refunds.get(row.id, []))
        else:
            base.update(quote_id=None, confirmed=False, confirmed_at=None, delivered_net=0, returned_net=0, net_revenue=0,
                        returns=[], refunds=[], valid_until=row.valid_until.isoformat() if row.valid_until else None)
        items.append(base)
    nxt = f'{keys[-1].changed_at.isoformat()}|{keys[-1].kind}|{keys[-1].id}' if more and keys else None
    return items, nxt


@router.get('/orders')
def orders(changed_since: str | None = None, cursor: str | None = None, limit: int = Query(200, ge=1, le=500),
           key: AiKey = Depends(marketing_key), session: Session = Depends(get_session)):
    items, nxt = order_rows(session, _parse_since(changed_since), _order_cursor(cursor), limit)
    out = dict(as_of=_iso(_now()), items=items, next_cursor=nxt)
    _log(session, key, '/orders', dict(changed_since=changed_since, cursor=cursor, limit=limit), len(items))
    return out
