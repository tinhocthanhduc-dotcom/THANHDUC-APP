"""CXL3: read-only aggregates; quantities and money stay exact in PostgreSQL."""
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Date, case, cast, func, literal, select

from . import finance_reports
from .models import (PendingDisposition as XL, PendingItem as Item, SalesDelivery,
                     SalesDeliveryLine, SalesReturn, SalesReturnLine, Supplier, Warehouse)
from .pending_items import GROUPS

BUCKETS = (('0_30', '0–30 ngày', 0), ('31_60', '31–60 ngày', 31),
           ('61_90', '61–90 ngày', 61), ('over_90', 'Trên 90 ngày', 91))
HOLD_BUCKETS = BUCKETS + (('unknown', 'Chưa có ngày gửi', None),)
MONTH_COLUMNS = (
    ('damage_documents', 'Hủy · số phiếu'), ('damage_cost', 'Hủy · giá vốn'),
    ('return_cost', 'Trả NCC · giá vốn'), ('return_amount', 'Trả NCC · chưa VAT'),
    ('return_vat', 'Trả NCC · VAT'), ('return_total', 'Trả NCC · tổng trừ nợ'),
    ('return_difference', 'Trả NCC · chênh lệch'), ('liquidation_qty', 'Thanh lý · số lượng'),
    ('liquidation_cost', 'Thanh lý · giá vốn'), ('liquidation_revenue', 'Thanh lý · doanh thu chưa VAT sau trả hàng'),
    ('liquidation_profit', 'Thanh lý · lãi/lỗ'), ('restored_qty', 'Sửa xong / NCC trả về · số lượng'),
    ('restored_cost', 'Sửa xong / NCC trả về · giá vốn'))
SUPPLIER_COLUMNS = (
    ('holding_count', 'Đang gửi · hồ sơ'), ('holding_qty', 'Đang gửi · số lượng'),
    ('holding_value', 'Đang gửi · giá vốn'), ('oldest_sent_at', 'Ngày gửi lâu nhất'),
    ('returned_qty', 'NCC trả về · số lượng'), ('returned_cost', 'NCC trả về · giá vốn'),
    ('rejected_qty', 'NCC không nhận · số lượng'), ('rejected_cost', 'NCC không nhận · giá vốn'),
    ('credit_total', 'Trả NCC trừ nợ · gồm VAT'))

check_reader = finance_reports.check_reader


def _rows(session, statement):
    with session.no_autoflush:
        return [dict(row) for row in session.execute(statement).mappings()]


def _cell():
    return dict(count=0, qty=Decimal(0), value=Decimal(0))


def aging(session, user, now=None):
    check_reader(user)
    now = now or datetime.now(UTC)
    stamp = case((Warehouse.supplier_hold.is_(True), Item.sent_at), else_=Item.entered_at)
    days = func.floor(func.extract('epoch', literal(now) - stamp) / 86400)
    bucket = case((stamp.is_(None), 'unknown'), (days <= 30, '0_30'),
                  (days <= 60, '31_60'), (days <= 90, '61_90'), else_='over_90')
    supplier = case((Warehouse.supplier_hold.is_(True), Item.supplier_code), else_=None)
    rows = _rows(session, select(Warehouse.supplier_hold.label('holding'), Item.group_code,
        supplier.label('supplier_code'), Supplier.name.label('supplier_name'), bucket.label('bucket'),
        func.count().label('count'), func.sum(Item.qty_left).label('qty'),
        func.sum(Item.qty_left * Item.unit_cost).label('value'))
        .select_from(Item).join(Warehouse, Warehouse.code == Item.warehouse_code)
        .outerjoin(Supplier, Supplier.code == supplier).where(Item.status == 'DANG_CHO')
        .group_by(Warehouse.supplier_hold, Item.group_code, supplier, Supplier.name, bucket))
    # Only aggregated cells are assembled here, never individual dossiers.
    normal = {code: dict(group_code=code, label=label, cells={k: _cell() for k, _, _ in BUCKETS}, total=_cell())
              for code, label in GROUPS.items()}
    holding = {}
    total = _cell()
    for row in rows:
        if row['holding']:
            key = (row['supplier_code'], row['group_code'])
            target = holding.setdefault(key, dict(group_code=row['group_code'], label=GROUPS[row['group_code']],
                supplier_code=row['supplier_code'], supplier_name=row['supplier_name'] or 'Chưa có NCC',
                cells={k: _cell() for k, _, _ in HOLD_BUCKETS}, total=_cell()))
        else:
            target = normal[row['group_code']]
        for field in ('count', 'qty', 'value'):
            target['cells'][row['bucket']][field] += row[field]
            target['total'][field] += row[field]
            total[field] += row[field]
    return dict(rows=list(normal.values()), holding=sorted(holding.values(), key=lambda r: (r['supplier_name'], r['group_code'])), total=total)


def _day():
    return cast(func.timezone('Asia/Ho_Chi_Minh', XL.decided_at), Date)


def _sum(kind, column):
    return func.coalesce(func.sum(column).filter(XL.kind.in_(kind)), 0)


def monthly(session, user, start, end):
    check_reader(user)
    start, end = finance_reports.date_range(start, end)
    month = cast(func.date_trunc('month', func.timezone('Asia/Ho_Chi_Minh', XL.decided_at)), Date)
    active = (XL.status == 'DA_GHI', _day().between(start, end))
    rows = _rows(session, select(month.label('month'),
        func.count().filter(XL.kind == 'HUY').label('damage_documents'),
        _sum(('HUY',), XL.cost_value).label('damage_cost'),
        *[_sum(('TRA_NCC',), col).label(name) for col, name in (
            (XL.cost_value, 'return_cost'), (XL.amount, 'return_amount'), (XL.vat_amount, 'return_vat'),
            (XL.total, 'return_total'), (XL.amount - XL.cost_value, 'return_difference'))],
        _sum(('THANH_LY',), XL.qty).label('liquidation_qty'),
        _sum(('THANH_LY',), XL.cost_value).label('liquidation_cost'),
        _sum(('SUA_XONG', 'NCC_TRA_VE'), XL.qty).label('restored_qty'),
        _sum(('SUA_XONG', 'NCC_TRA_VE'), XL.cost_value).label('restored_cost'))
        .where(*active).where((XL.kind != 'THANH_LY') | XL.delivery_id.in_(
            select(SalesDelivery.id).where(SalesDelivery.status == 'DA_GIAO'))).group_by(month))
    # A GH line can have several FIFO dispositions. Reduce to its key BEFORE joining money.
    keys = (select(XL.delivery_id, XL.order_line_no, func.min(month).label('month'))
        .where(*active, XL.kind == 'THANH_LY').group_by(XL.delivery_id, XL.order_line_no).subquery())
    returns = (select(SalesReturnLine.delivery_id, SalesReturnLine.order_line_no,
        func.sum(SalesReturnLine.amount).label('amount')).join(SalesReturn, SalesReturn.id == SalesReturnLine.return_id)
        .where(SalesReturn.status == 'DA_NHAN').group_by(SalesReturnLine.delivery_id, SalesReturnLine.order_line_no).subquery())
    revenue = _rows(session, select(keys.c.month,
        func.sum(SalesDeliveryLine.amount - func.coalesce(returns.c.amount, 0)).label('liquidation_revenue'))
        .select_from(keys).join(SalesDelivery, SalesDelivery.id == keys.c.delivery_id)
        .join(SalesDeliveryLine, (SalesDeliveryLine.delivery_id == keys.c.delivery_id) & (SalesDeliveryLine.order_line_no == keys.c.order_line_no))
        .outerjoin(returns, (returns.c.delivery_id == keys.c.delivery_id) & (returns.c.order_line_no == keys.c.order_line_no))
        .where(SalesDelivery.status == 'DA_GIAO').group_by(keys.c.month))
    by_month = {r['month']: r for r in rows}
    revenues = {r['month']: r['liquidation_revenue'] for r in revenue}
    result = []
    cursor = start.replace(day=1)
    while cursor <= end:
        row = dict(month=cursor, **{key: 0 for key, _ in MONTH_COLUMNS})
        row.update(by_month.get(cursor, {}))
        row['liquidation_revenue'] = revenues.get(cursor, 0)
        row['liquidation_profit'] = row['liquidation_revenue'] - row['liquidation_cost']
        result.append(row)
        cursor = cursor.replace(year=cursor.year + 1, month=1) if cursor.month == 12 else cursor.replace(month=cursor.month + 1)
    total = {key: sum(r[key] for r in result) for key, _ in MONTH_COLUMNS}
    # Reuse the existing financial definitions for the period totals; no posting changes.
    total['damage_cost'] = finance_reports.damage_cost(session, user, start, end)
    total['return_difference'] = finance_reports.pending_return_difference(session, user, start, end)
    return dict(rows=result, total=total)


def supplier_summary(session, user, start, end):
    check_reader(user)
    start, end = finance_reports.date_range(start, end)
    holding = (select(Item.supplier_code.label('code'), func.count().label('holding_count'),
        func.sum(Item.qty_left).label('holding_qty'), func.sum(Item.qty_left * Item.unit_cost).label('holding_value'),
        func.min(Item.sent_at).label('oldest_sent_at')).join(Warehouse, Warehouse.code == Item.warehouse_code)
        .where(Item.status == 'DANG_CHO', Warehouse.supplier_hold.is_(True)).group_by(Item.supplier_code).subquery())
    code = func.coalesce(XL.supplier_code, Item.supplier_code)
    flow = (select(code.label('code'),
        *[_sum((kind,), col).label(name) for kind, col, name in (
            ('NCC_TRA_VE', XL.qty, 'returned_qty'), ('NCC_TRA_VE', XL.cost_value, 'returned_cost'),
            ('NCC_KHONG_NHAN', XL.qty, 'rejected_qty'), ('NCC_KHONG_NHAN', XL.cost_value, 'rejected_cost'),
            ('TRA_NCC', XL.total, 'credit_total'))])
        .join(Item, Item.id == XL.item_id).where(XL.status == 'DA_GHI', _day().between(start, end),
            XL.kind.in_(('NCC_TRA_VE', 'NCC_KHONG_NHAN', 'TRA_NCC'))).group_by(code).subquery())
    # UNION includes the legacy 'unknown supplier' bucket without losing its held value.
    codes = select(holding.c.code).union(select(flow.c.code)).subquery()
    fields = [func.coalesce((holding.c if key.startswith('holding_') else flow.c)[key], 0).label(key)
              for key, _ in SUPPLIER_COLUMNS if key != 'oldest_sent_at']
    return _rows(session, select(codes.c.code.label('supplier_code'), Supplier.name.label('supplier_name'),
        holding.c.oldest_sent_at, *fields).select_from(codes)
        .outerjoin(holding, holding.c.code.is_not_distinct_from(codes.c.code))
        .outerjoin(flow, flow.c.code.is_not_distinct_from(codes.c.code))
        .outerjoin(Supplier, Supplier.code == codes.c.code)
        .order_by(func.coalesce(holding.c.holding_value, 0).desc(), codes.c.code))
