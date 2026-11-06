"""Báo cáo sản xuất theo kỳ (TUẤN duyệt mẫu 06/11/2026). Chỉ đọc.

Lấy phiếu sản xuất hoàn thành trong kỳ (Hoàn thành hoặc Đã nhập kho, theo ngày Hoàn thành giờ Việt Nam).
Vốn 1 cái = giá vốn thành phẩm của phiếu (bình quân theo số tốt), giá bán = giá bán hiện tại trong danh mục (chưa VAT).
Thời gian = giờ làm việc từ Bắt đầu đến Hoàn thành (bỏ nghỉ trưa, Chủ nhật, ngày lễ). Không tính lương thợ vào vốn.
"""
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import production_bom, production_stale
from .models import Product, ProductionOrder, User
from .service_sla import TZ

HIGH_SCRAP = 0.10


def week_of(day: date) -> tuple[date, date]:
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def month_of(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt - timedelta(days=1)


def _pct(part, whole):
    return part / whole if whole else 0.0


def build(session, start: date, end: date, now: datetime | None = None) -> dict:
    lo = datetime.combine(start, datetime.min.time(), TZ)
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), TZ)
    rows = list(session.scalars(select(ProductionOrder).options(selectinload(ProductionOrder.outputs), selectinload(ProductionOrder.lines))
                                .where(ProductionOrder.status.in_(('HOAN_THANH', 'DA_NHAP')), ProductionOrder.finished_at >= lo,
                                       ProductionOrder.finished_at < hi).order_by(ProductionOrder.id)))
    holidays = production_stale._holidays(session, start - timedelta(days=production_stale.MAX_DAYS), end)
    products, techs = {}, {}
    for r in rows:
        minutes = production_stale.working_minutes(r.started_at, r.finished_at, holidays) if r.started_at and r.finished_at else None
        t = techs.setdefault(r.technician_id, dict(orders=0, good=0, scrap=0, minutes=[]))
        t['orders'] += 1
        t['good'] += r.qty_good
        t['scrap'] += r.qty_scrap
        if minutes is not None:
            t['minutes'].append(minutes)
        for o in r.outputs:
            p = products.setdefault(o.product_code, dict(code=o.product_code, good=0, scrap=0, cost_total=0, cost_qty=0))
            p['good'] += o.qty_good
            p['scrap'] += o.qty_scrap
            if o.unit_cost and o.qty_good:
                p['cost_total'] += o.unit_cost * o.qty_good
                p['cost_qty'] += o.qty_good
    catalog = {p.code: p for p in session.scalars(select(Product).where(Product.code.in_(list(products))))} if products else {}
    names = dict(session.execute(select(User.id, User.full_name).where(User.id.in_([k for k in techs if k]))).all()) if techs else {}
    product_rows = []
    for code, p in products.items():
        made = p['good'] + p['scrap']
        cost = round(p['cost_total'] / p['cost_qty']) if p['cost_qty'] else None
        price = catalog[code].sale_price if code in catalog else 0
        margin = price - cost if cost is not None and price else None
        product_rows.append(dict(code=code, name=catalog[code].name if code in catalog else code, good=p['good'], scrap=p['scrap'],
                                 rate=_pct(p['scrap'], made), high=_pct(p['scrap'], made) > HIGH_SCRAP, cost=cost, price=price or None,
                                 margin=margin, margin_pct=_pct(margin, price) if margin is not None else None))
    product_rows.sort(key=lambda x: (-x['good'], x['name']))
    tech_rows = []
    for ident, t in techs.items():
        made = t['good'] + t['scrap']
        hours = sum(t['minutes']) / len(t['minutes']) / 60 if t['minutes'] else None
        tech_rows.append(dict(name=names.get(ident, 'Chưa giao'), orders=t['orders'], good=t['good'], scrap=t['scrap'],
                              rate=_pct(t['scrap'], made), high=_pct(t['scrap'], made) > HIGH_SCRAP, hours=hours))
    tech_rows.sort(key=lambda x: (-x['good'], x['name']))
    good, scrap = sum(r.qty_good for r in rows), sum(r.qty_scrap for r in rows)
    all_minutes = [m for t in techs.values() for m in t['minutes']]
    stale = 0
    if now is not None:
        stale = len(production_stale.stale_map(session, production_stale.open_rows(session), now))
    deviate = sum(1 for r in rows if production_bom.deviations(r))
    return dict(start=start, end=end, orders=len(rows), good=good, scrap=scrap, rate=_pct(scrap, good + scrap), deviate=deviate,
                hours=sum(all_minutes) / len(all_minutes) / 60 if all_minutes else None, stale=stale,
                products=product_rows, techs=tech_rows)


def _num(x, digits=1):
    return f'{x:.{digits}f}'.replace('.', ',')


def _money(n):
    return f'{n:,}'.replace(',', '.')


def render_text(d: dict, base_url: str) -> str:
    head = f"🏭 SẢN XUẤT TUẦN {d['start'].strftime('%d/%m')} – {d['end'].strftime('%d/%m')}"
    if not d['orders']:
        return '\n'.join([head, 'Tuần này không có phiếu sản xuất hoàn thành.', f'Xem: {base_url}/san-xuat/bao-cao'])
    lines = [head, f"Tốt {d['good']} · hỏng {d['scrap']} ({_num(d['rate'] * 100)}%) · {d['orders']} phiếu"]
    worst = max((p for p in d['products'] if p['scrap']), key=lambda p: (p['rate'], p['scrap']), default=None)
    if worst:
        lines.append(f"Hỏng cao nhất: {worst['name']} {_num(worst['rate'] * 100, 0)}% ({worst['scrap']}/{worst['good'] + worst['scrap']})")
    costs = [f"{p['name']} {_money(p['cost'])}" for p in d['products'] if p['cost']][:2]
    if costs:
        lines.append('Vốn 1 cái: ' + ' · '.join(costs))
    if d.get('deviate'):
        lines.append(f"Vật tư lệch định mức quá 10%: {d['deviate']} phiếu")
    if d['stale']:
        lines.append(f"Đang đọng: {d['stale']} phiếu")
    lines.append(f'Xem: {base_url}/san-xuat/bao-cao')
    return '\n'.join(lines)
