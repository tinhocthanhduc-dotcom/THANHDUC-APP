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
