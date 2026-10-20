"""Read-only display data for the three entry forms."""
from zoneinfo import ZoneInfo
from sqlalchemy import select, func
from ..models import SalesOrder, SalesOrderLine
from ..security import has_role


def customer_cards(session, user, customers):
    customers = [c for c in customers if c]
    debts = {}
    permitted = has_role(user, 'finance')
    if customers and permitted:
        from ..finance_reports import _debts
        query = _debts()
        debts = dict(session.execute(select(query.c.customer_code, func.sum(query.c.remaining))
            .where(query.c.customer_code.in_([c.code for c in customers])).group_by(query.c.customer_code)).all())
    return {c.code: dict(code=c.code, name=c.name, address=c.address,
        phone=' · '.join(p.phone for p in c.phones),
        contact=c.contacts[0].name if c.contacts else '',
        debt=int(debts.get(c.code, 0)), limit=c.credit_limit, can_debt=permitted) for c in customers}


def last_prices(session, customer_code, products):
    codes = [p.code for p in products]
    if not customer_code or not codes:
        return {}
    # DISTINCT ON returns one row per visible product, ordered by actual confirmation.
    rows = session.execute(select(SalesOrderLine.product_code, SalesOrderLine.unit_price, SalesOrder.confirmed_at)
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.order_id)
        .where(SalesOrder.customer_code == customer_code, SalesOrder.confirmed_at.is_not(None),
               SalesOrder.status.in_(['DA_CHOT', 'GIAO_MOT_PHAN', 'DA_GIAO']), SalesOrderLine.product_code.in_(codes))
        .distinct(SalesOrderLine.product_code)
        .order_by(SalesOrderLine.product_code, SalesOrder.confirmed_at.desc(), SalesOrder.id.desc(), SalesOrderLine.line_no.desc()))
    return {code: dict(price=price, date=stamp.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%d/%m'))
            for code, price, stamp in rows}


def line_errors(lines, error='', code='', stock_values=None):
    """Presentation only; POST validation remains owned by the existing core."""
    import re
    from decimal import Decimal, InvalidOperation
    result = set()
    targeted = re.search(r'[Dd]òng(?: tự do)?\s+(\d+)', error or '')
    for index, line in enumerate(lines):
        goods = line.get('kind') == 'HANG'
        invalid = not (line.get('description', '').strip() or line.get('product_code'))
        invalid |= goods and (not line.get('product_code') or not line.get('warehouse_code'))
        try:
            invalid |= Decimal(str(line.get('qty', '')).replace(',', '.')) <= 0
        except InvalidOperation:
            invalid = True
        price = str(line.get('unit_price') or '').strip()
        invalid |= bool(price and not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)', price))
        invalid |= bool(targeted and int(targeted[1]) == index + 1)
        if code == 'BAD_PRODUCT' and line.get('product_code') and line['product_code'] in (error or ''): invalid = True
        if stock_values and goods:
            stock = stock_values.get((line.get('warehouse_code'), line.get('product_code')))
            if stock:
                try: invalid |= Decimal(str(line['qty']).replace(',', '.')) > stock['available']
                except InvalidOperation: pass
        if invalid: result.add(index)
    return result
