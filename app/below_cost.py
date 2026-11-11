"""Bán dưới giá vốn (TUẤN duyệt 11/11/2026).

- Báo giá, đơn bán: dòng hàng ở kho thường (kind KHO) có tiền hàng chưa VAT thấp hơn giá vốn bình quân × số lượng
  thì gọi là "dưới giá vốn". Kho chưa có giá vốn (0) thì bỏ qua. Hàng thanh lý (kho LOI) giữ luật cũ ở pending_liquidation.
- Nhân viên không thấy con số giá vốn; chỉ giám đốc thấy lãi gộp từng dòng.
- Chốt đơn có dòng dưới giá vốn: người không phải giám đốc phải ghi lý do (không chờ duyệt). Lần chốt ghi một dòng nhật ký
  'sales_order.below_cost' (số đơn, dòng, lỗ, lý do) để báo cáo sáng và Tổng quan đọc lại. Không đổi số liệu tiền, không migration.
"""
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from . import audit
from .errors import BusinessError
from .models import AuditLog, Product, StockBalance, User, Warehouse
from .security import has_role

ACTION = 'sales_order.below_cost'
REASON_REQUIRED = 'DUOI_GIA_VON_CAN_LY_DO'
TZ = ZoneInfo('Asia/Ho_Chi_Minh')


def _net(row, line):
    """Tiền hàng chưa VAT của dòng sau giảm giá; như check_prices của hàng thanh lý."""
    from .quotes import adjusted_pricing
    return Decimal(line.amount) if adjusted_pricing(row) else Decimal(line.unit_price) * Decimal(line.qty)


def cost_of(session, warehouse_code, product_code):
    """Giá vốn bình quân của hàng ở kho thường; None khi không áp dụng (kho lỗi, chưa có giá vốn)."""
    if not warehouse_code or not product_code:
        return None
    wh = session.get(Warehouse, warehouse_code)
    if wh is None or wh.kind != 'KHO':
        return None
    balance = session.get(StockBalance, (warehouse_code, product_code))
    cost = int(balance.avg_cost) if balance else 0
    return cost or None


def quote_cost(session, product_code):
    """Báo giá chưa chọn kho: giá vốn kho bán mặc định, chưa có thì bình quân các kho thường còn hàng."""
    if not product_code:
        return None
    from sqlalchemy import func
    from .config import get_settings
    cost = cost_of(session, get_settings().sales_default_warehouse, product_code)
    if cost:
        return cost
    qty, value = session.execute(select(func.sum(StockBalance.qty), func.sum(StockBalance.value))
                                 .join(Warehouse, Warehouse.code == StockBalance.warehouse_code)
                                 .where(StockBalance.product_code == product_code, Warehouse.kind == 'KHO', StockBalance.qty > 0)).one()
    return int(round(value / qty)) if qty and value else None


def line_margins(session, row):
    """Mỗi dòng hàng có giá vốn: dict(line_no, product_code, description, qty, net, cost, margin, below)."""
    out = []
    for line in row.lines:
        if getattr(line, 'kind', 'HANG') != 'HANG':
            continue
        cost = (cost_of(session, line.warehouse_code, line.product_code) if hasattr(line, 'warehouse_code')
                else quote_cost(session, line.product_code))
        if cost is None:
            continue
        net = _net(row, line)
        total_cost = Decimal(cost) * Decimal(line.qty)
        margin = int((net - total_cost).to_integral_value())
        out.append(dict(line_no=line.line_no, product_code=line.product_code, description=line.description,
                        qty=line.qty, net=int(net.to_integral_value()), cost=int(total_cost.to_integral_value()),
                        margin=margin, below=net < total_cost))
    return out


def summary(session, row):
    """Cho màn xem phiếu: dòng dưới giá vốn (mọi người) và lãi gộp (giám đốc mới hiển thị)."""
    lines = line_margins(session, row)
    net = sum(l['net'] for l in lines)
    margin = sum(l['margin'] for l in lines)
    return dict(lines={l['line_no']: l for l in lines}, below=[l for l in lines if l['below']],
                net=net, margin=margin, rate=round(margin * 100 / net, 1) if net else None)


def check_confirm(session, user, order, reason=''):
    """Gọi trong transaction chốt đơn. Thiếu lý do thì chặn (trừ giám đốc); có dòng dưới vốn thì ghi nhật ký."""
    below = [l for l in line_margins(session, order) if l['below']]
    if not below:
        return ''
    reason = (reason or '').strip()[:500]
    if not reason and not has_role(user, 'director'):
        first = below[0]
        raise BusinessError(REASON_REQUIRED, f"Dòng {first['line_no']} ({first['product_code']}) bán dưới giá vốn. Ghi lý do để chốt đơn.", status=409)
    loss = -sum(l['margin'] for l in below)
    audit.record(session, user, ACTION, 'sales_order', str(order.id), None,
                 dict(number=order.number, customer_code=order.customer_code, loss=loss,
                      lines=[dict(line_no=l['line_no'], product_code=l['product_code'], qty=str(l['qty']), loss=-l['margin'])
                             for l in below]), reason)
    return reason


def recent(session, start: date, end: date):
    """Đơn chốt dưới giá vốn từ ngày start đến end (giờ VN), mới nhất trước."""
    lo = datetime.combine(start, time.min, TZ).astimezone(UTC)
    hi = datetime.combine(end + timedelta(days=1), time.min, TZ).astimezone(UTC)
    rows = session.execute(select(AuditLog, User.full_name).outerjoin(User, User.id == AuditLog.user_id)
                           .where(AuditLog.action == ACTION, AuditLog.at >= lo, AuditLog.at < hi)
                           .order_by(AuditLog.at.desc(), AuditLog.id.desc())).all()
    return [dict(order_id=int(a.entity_id), number=(a.after or {}).get('number', ''), loss=int((a.after or {}).get('loss', 0)),
                 reason=a.reason or '', by=name or '', at=a.at) for a, name in rows]
