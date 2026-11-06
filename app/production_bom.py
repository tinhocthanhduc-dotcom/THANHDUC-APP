"""Định mức vật tư sản xuất (TUẤN duyệt mẫu 06/11/2026).

Định mức = vật tư cho 1 thành phẩm tốt. Chỉ dùng để điền sẵn bảng vật tư dự kiến khi lập phiếu sản xuất
(số định làm × định mức, cộng dồn theo mã vật tư); kho vẫn chỉ trừ khi thợ Hoàn thành như cũ, giá vốn không đổi.
Vốn ước tính = Σ số lượng × giá vốn bình quân hiện tại (tổng giá trị tồn / tổng tồn mọi kho; hết tồn thì giá lần cuối).
"Lưu làm định mức" từ một phiếu một thành phẩm đã hoàn thành: số thật dùng / số tốt.
"""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select

from . import audit
from .errors import BusinessError
from .models import Product, ProductionBom, ProductionBomLine, ProductionOrder, StockBalance
from .security import has_role

EDITORS = ('warehouse', 'dispatcher')
COST_VIEWERS = ('warehouse', 'finance')
MAX_LINES = 50
MAX_QTY = Decimal('100000')
DEVIATE = Decimal('0.10')     # thật dùng lệch dự kiến quá 10% thì báo
HIGH_COST = Decimal('0.70')   # vốn ước tính trên 70% giá bán thì báo
LOOKBACK_DAYS = 90
Q3 = Decimal('0.001')


def can_edit(user) -> bool:
    return has_role(user, *EDITORS)


def _q(x) -> Decimal:
    return Decimal(x).quantize(Q3, rounding=ROUND_HALF_UP)


def qty_text(q) -> str:
    q = Decimal(q).normalize()
    return format(q, 'f').replace('.', ',')


def bom_map(session, codes=None) -> dict:
    """{thành phẩm: [(mã vật tư, số cho 1 cái)]} theo thứ tự dòng."""
    stmt = select(ProductionBomLine).order_by(ProductionBomLine.product_code, ProductionBomLine.line_no)
    if codes is not None:
        codes = list(codes)
        if not codes:
            return {}
        stmt = stmt.where(ProductionBomLine.product_code.in_(codes))
    out = {}
    for line in session.scalars(stmt):
        out.setdefault(line.product_code, []).append((line.material_code, line.qty))
    return out


def plan_lines(boms: dict, outputs) -> list[tuple[str, Decimal]]:
    """[(mã vật tư, số dự kiến)] cho các thành phẩm [(mã, số định làm)], cộng dồn, giữ thứ tự xuất hiện."""
    total = {}
    for code, qty in outputs:
        for material, per in boms.get(code, ()):
            total[material] = total.get(material, Decimal(0)) + Decimal(per) * Decimal(qty)
    return [(m, _q(q)) for m, q in total.items()]


def unit_costs(session, codes) -> dict:
    """Giá vốn bình quân hiện tại mỗi mã (mọi kho)."""
    codes = list(set(codes))
    if not codes:
        return {}
    out = {}
    for code, qty, value, last in session.execute(
            select(StockBalance.product_code, func.sum(StockBalance.qty), func.sum(StockBalance.value), func.max(StockBalance.avg_cost))
            .where(StockBalance.product_code.in_(codes)).group_by(StockBalance.product_code)):
        out[code] = int((Decimal(value) / qty).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if qty else int(last or 0)
    return out


def estimate(lines, costs) -> int:
    return int(sum((Decimal(q) * costs.get(m, 0) for m, q in lines), Decimal(0)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def save(session, user, product_code: str, lines, note: str = '') -> ProductionBom | None:
    """Thay toàn bộ định mức của một thành phẩm. lines = [(mã vật tư, số cho 1 cái)]; rỗng = xóa định mức."""
    if not can_edit(user):
        raise BusinessError('FORBIDDEN', 'Chỉ thủ kho, điều phối hoặc giám đốc sửa định mức.', status=403)
    product = session.get(Product, (product_code or '')[:16])
    if product is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy thành phẩm.', status=404)
    clean, seen = [], set()
    for code, qty in lines:
        code = (code or '').strip()[:16]
        if not code:
            continue
        try:
            qty = Decimal(str(qty).strip().replace(',', '.') or '0')
        except Exception:
            raise BusinessError('BAD_QTY', f'Số lượng vật tư {code} chưa hợp lệ.')
        if not qty.is_finite() or qty < 0 or qty > MAX_QTY or qty != qty.quantize(Q3):
            raise BusinessError('BAD_QTY', f'Số lượng vật tư {code} phải từ 0 đến 100.000, tối đa 3 số lẻ.')
        if qty == 0:
            continue
        if code == product.code:
            raise BusinessError('BAD_MATERIAL', 'Vật tư không được trùng thành phẩm.')
        if code in seen:
            raise BusinessError('BAD_MATERIAL', f'Vật tư {code} bị trùng dòng.')
        if session.get(Product, code) is None:
            raise BusinessError('BAD_MATERIAL', f'Không tìm thấy vật tư {code}.')
        seen.add(code)
        clean.append((code, qty))
    if len(clean) > MAX_LINES:
        raise BusinessError('BAD_MATERIAL', f'Tối đa {MAX_LINES} dòng vật tư.')
    note = (note or '').strip()[:2000]
    row = session.get(ProductionBom, product.code)
    before = dict(lines=[[l.material_code, format(l.qty.normalize(), 'f')] for l in row.lines], note=row.note) if row else None
    if not clean:
        if row is not None:
            session.delete(row)
            session.flush()
            audit.record(session, user, 'production.bom', 'product', product.code, before, None)
        session.commit()
        return None
    if row is None:
        row = ProductionBom(product_code=product.code, updated_by=user.id)
        session.add(row)
    row.lines.clear()
    session.flush()
    row.lines.extend(ProductionBomLine(material_code=c, qty=q, line_no=i) for i, (c, q) in enumerate(clean, 1))
    row.note, row.updated_by, row.updated_at = note, user.id, func.now()
    session.flush()
    audit.record(session, user, 'production.bom', 'product', product.code, before,
                 dict(lines=[[c, format(q.normalize(), 'f')] for c, q in clean], note=note))
    session.commit()
    return row


def from_order(order) -> list[tuple[str, Decimal]] | None:
    """Định mức 1 cái từ phiếu một thành phẩm đã hoàn thành có xuất vật tư: thật dùng / số tốt."""
    if order.status not in ('HOAN_THANH', 'DA_NHAP') or len(order.outputs) != 1 or not order.qty_good or order.px_doc_id is None:
        return None
    lines = [(l.product_code, _q(Decimal(l.qty_used) / order.qty_good)) for l in order.lines if l.qty_used > 0]
    return [(c, q) for c, q in lines if q > 0] or None


def deviations(order) -> dict:
    """{line_no: tỷ lệ lệch} cho dòng có dự kiến > 0 mà thật dùng lệch quá DEVIATE (phiếu đã hoàn thành)."""
    if order.status not in ('HOAN_THANH', 'DA_NHAP') or order.px_doc_id is None:
        return {}
    out = {}
    for l in order.lines:
        if l.qty_plan and l.qty_plan > 0:
            rate = (Decimal(l.qty_used) - l.qty_plan) / l.qty_plan
            if abs(rate) > DEVIATE:
                out[l.line_no] = rate
    return out


def usage(session, product_code, now) -> tuple[int, dict]:
    """(số cái tốt, {vật tư: thật dùng trung bình 1 cái}) từ phiếu một thành phẩm hoàn thành trong LOOKBACK_DAYS ngày."""
    rows = session.scalars(select(ProductionOrder).where(ProductionOrder.product_code == product_code,
                                                         ProductionOrder.status.in_(('HOAN_THANH', 'DA_NHAP')),
                                                         ProductionOrder.px_doc_id.is_not(None),
                                                         ProductionOrder.finished_at >= now - timedelta(days=LOOKBACK_DAYS)))
    good, used = 0, {}
    for r in rows:
        if len(r.outputs) != 1 or not r.qty_good:
            continue
        good += r.qty_good
        for l in r.lines:
            if l.qty_used > 0:
                used[l.product_code] = used.get(l.product_code, Decimal(0)) + Decimal(l.qty_used)
    return good, {k: _q(v / good) for k, v in used.items()} if good else {}
