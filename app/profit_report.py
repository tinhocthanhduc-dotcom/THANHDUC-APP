"""Lãi ròng tháng (đợt 4, TUẤN duyệt 08/11/2026) — chỉ giám đốc.

Doanh thu, giá vốn, lãi gộp dùng nguyên nguồn Báo cáo lãi gộp (purchase_reports._margin_sources).
Trừ tiếp: lương từ bảng lương tháng đã chốt (chưa chốt thì ước bằng tháng chốt gần nhất), phiếu chi của danh mục
có cờ CHI_PHI, hàng hủy (chờ xử lý), chi phí ngoài sổ giám đốc nhập → lãi ròng trước lương giám đốc → trừ lương
giám đốc mục tiêu → còn lại. Không ghi sổ; chỉ bảng chi phí ngoài sổ và lương giám đốc mục tiêu là dữ liệu riêng.
"""
from calendar import monthrange
from datetime import date, datetime, timedelta

from sqlalchemy import Date, Numeric, cast, func, select

from . import finance_reports, purchase_reports
from .errors import BusinessError
from .finance_catalogs import check_version, transaction
from .models import (CashCategory, CashDoc, Customer, FundLedger, PayrollLine, PayrollMonth, PendingDisposition, PlAdjustment,
                     PlSetting, Product, SalesDelivery, SalesOrder, SalesReceipt, SalesReturn, ServiceTicket, StockLedger)
from .sales_reports import _window
from .security import has_role
from . import audit

TZ = finance_reports.TZ
_today = finance_reports._today
PL_KINDS = {'CHI_PHI': 'Chi phí', 'KHONG_TINH': 'Không tính', 'CHU_SO_HUU': 'Chủ sở hữu'}
DIRECTOR_SALARY_KEY = 'LUONG_GIAM_DOC'
DEFAULT_DIRECTOR_SALARY = 45_000_000  # mục tiêu thu nhập TUẤN 45 triệu/tháng; sửa được trên tab Chi phí ngoài sổ
SERVICE_FEE_GROUP = 'Dịch vụ · tiền công'


def check_reader(user):
    if not has_role(user, 'director'):
        raise BusinessError('FORBIDDEN', 'Chỉ giám đốc xem lãi ròng tháng.', status=403)


def _now():
    return datetime.now(TZ)


def month_of(value=None) -> date:
    """Tháng báo cáo (ngày 1). Nhận `2026-10`, `2026-10-15`, date hoặc None = tháng hiện tại."""
    if value is None or value == '':
        return _today().replace(day=1)
    if isinstance(value, date):
        return value.replace(day=1)
    text = str(value).strip()
    try:
        if len(text) == 7:
            return date.fromisoformat(text + '-01')
        return date.fromisoformat(text).replace(day=1)
    except ValueError:
        raise BusinessError('BAD_MONTH', 'Tháng chưa hợp lệ, chọn theo dạng 2026-10.')


def month_range(month: date):
    return month, month.replace(day=monthrange(month.year, month.month)[1])


def prev_month(month: date) -> date:
    return (month - timedelta(days=1)).replace(day=1)


def next_month(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1)


def _int(value):
    return int(value or 0)


def _scalar(session, statement):
    with session.no_autoflush:
        return _int(session.scalar(statement))


# ---------- Lãi gộp (nguồn báo cáo lãi gộp) ----------

def _gross(session, start, end):
    """Doanh thu thuần theo loại chứng từ và giá vốn, đúng nguồn gross_margin nhưng không dựng bảng từng phiếu."""
    documents, costs = purchase_reports._margin_sources(start, end)
    with session.no_autoflush:
        revenue = {kind: _int(value) for kind, value in session.execute(
            select(documents.c.kind, func.sum(documents.c.revenue)).group_by(documents.c.kind))}
        cost = _int(session.scalar(select(func.coalesce(func.sum(costs.c.cost), 0))))
    total_revenue = sum(revenue.values())
    return dict(deliveries=revenue.get('GH', 0), services=revenue.get('DV', 0), returns=revenue.get('TH', 0),
                revenue=total_revenue, cost=cost, gross=total_revenue - cost,
                rate=round((total_revenue - cost) * 100 / total_revenue, 1) if total_revenue else None)


# ---------- Lương ----------

def payroll_cost(session, month):
    """Tổng lương gộp (base + phụ cấp + hoa hồng + thưởng) của bảng lương tháng đã chốt.

    Tháng chưa chốt: ước bằng tháng chốt gần nhất trước đó (status 'UOC'), chưa có tháng nào thì 0 ('CHUA_CO').
    Phiếu chi lương, tạm ứng không được tính ở chi phí hoạt động để khỏi trùng (danh mục ghép Lương đặt Không tính).
    """
    with session.no_autoflush:
        closed = session.get(PayrollMonth, month)
        source = month if closed else session.scalar(select(func.max(PayrollMonth.month)).where(PayrollMonth.month < month))
        if source is None:
            return dict(amount=0, status='CHUA_CO', source=None, people=0)
        amount, people = session.execute(select(func.coalesce(func.sum(PayrollLine.gross), 0), func.count()).where(PayrollLine.month == source)).one()
    return dict(amount=_int(amount), status='DA_CHOT' if closed else 'UOC', source=source, people=int(people))


# ---------- Phiếu chi theo cờ ----------

def _expense_rows(session, start, end, pl_kind):
    stmt = (select(CashCategory.code, CashCategory.name, CashCategory.pl_group, func.count(func.distinct(FundLedger.doc_id)).label('documents'),
                   func.coalesce(func.sum(-FundLedger.amount), 0).label('amount'))
            .select_from(FundLedger).join(CashDoc, CashDoc.id == FundLedger.doc_id).join(CashCategory, CashCategory.code == CashDoc.category_code)
            .where(FundLedger.entry_date.between(start, end), CashDoc.kind == 'PC', CashDoc.is_opening.is_(False), CashCategory.pl_kind == pl_kind)
            .group_by(CashCategory.code, CashCategory.name, CashCategory.pl_group).order_by(CashCategory.pl_group, CashCategory.code))
    with session.no_autoflush:
        return [dict(code=code, name=name, group=group, documents=int(docs), amount=_int(amount))
                for code, name, group, docs, amount in session.execute(stmt)]


def operating_expenses(session, start, end):
    rows = _expense_rows(session, start, end, 'CHI_PHI')
    return dict(rows=rows, amount=sum(r['amount'] for r in rows))


def owner_draws(session, start, end):
    rows = _expense_rows(session, start, end, 'CHU_SO_HUU')
    return dict(rows=rows, amount=sum(r['amount'] for r in rows))


def damage_cost(session, start, end):
    day = cast(func.timezone('Asia/Ho_Chi_Minh', PendingDisposition.decided_at), Date)
    return _scalar(session, select(func.coalesce(func.sum(PendingDisposition.cost_value), 0)).where(
        PendingDisposition.kind == 'HUY', PendingDisposition.status == 'DA_GHI', day.between(start, end)))


# ---------- Chi phí ngoài sổ, lương giám đốc ----------

def adjustments(session, month):
    with session.no_autoflush:
        rows = list(session.scalars(select(PlAdjustment).where(PlAdjustment.month == month).order_by(PlAdjustment.pl_group, PlAdjustment.id)))
    return dict(rows=rows, amount=sum(r.amount for r in rows))


def director_salary(session):
    with session.no_autoflush:
        row = session.get(PlSetting, DIRECTOR_SALARY_KEY)
    return _int(row.amount) if row else DEFAULT_DIRECTOR_SALARY


def _adjustment_snapshot(row):
    return dict(id=row.id, month=row.month.isoformat(), name=row.name, pl_group=row.pl_group, amount=row.amount, note=row.note)


def save_adjustment(session, user, month, name, amount, pl_group='', note='', ident=None, version=None):
    check_reader(user)
    name = str(name or '').strip()
    if not name:
        raise BusinessError('NAME_REQUIRED', 'Hãy ghi tên khoản chi phí.')
    if not isinstance(amount, int) or amount <= 0:
        raise BusinessError('BAD_AMOUNT', 'Số tiền phải lớn hơn 0.')
    with transaction(session):
        if ident is None:
            row = PlAdjustment(month=month, name=name[:200], pl_group=str(pl_group or '').strip()[:100], amount=amount, note=str(note or '')[:2000],
                               created_by=user.id, created_at=_now())
            session.add(row)
            session.flush()
            audit.record(session, user, 'pl_adjustment.create', 'pl_adjustment', str(row.id), None, _adjustment_snapshot(row))
        else:
            row = session.get(PlAdjustment, ident, with_for_update=True)
            if row is None:
                raise BusinessError('NOT_FOUND', 'Không tìm thấy khoản chi phí.', status=404)
            check_version(row, version)
            before = _adjustment_snapshot(row)
            row.name, row.pl_group, row.amount, row.note = name[:200], str(pl_group or '').strip()[:100], amount, str(note or '')[:2000]
            row.version += 1
            audit.record(session, user, 'pl_adjustment.update', 'pl_adjustment', str(row.id), before, _adjustment_snapshot(row))
    return row


def delete_adjustment(session, user, ident, version):
    check_reader(user)
    with transaction(session):
        row = session.get(PlAdjustment, ident, with_for_update=True)
        if row is None:
            raise BusinessError('NOT_FOUND', 'Không tìm thấy khoản chi phí.', status=404)
        check_version(row, version)
        audit.record(session, user, 'pl_adjustment.delete', 'pl_adjustment', str(row.id), _adjustment_snapshot(row), None)
        session.delete(row)


def copy_adjustments(session, user, month):
    """Chép các khoản của tháng trước sang tháng này (chỉ khi tháng này chưa có khoản nào)."""
    check_reader(user)
    source = prev_month(month)
    with transaction(session):
        if session.scalar(select(func.count()).select_from(PlAdjustment).where(PlAdjustment.month == month)):
            raise BusinessError('DA_CO', 'Tháng này đã có khoản chi phí ngoài sổ; xóa hết trước khi chép.', status=409)
        rows = list(session.scalars(select(PlAdjustment).where(PlAdjustment.month == source).order_by(PlAdjustment.id)))
        if not rows:
            raise BusinessError('KHONG_CO', f'Tháng {source:%m/%Y} chưa có khoản nào để chép.', status=409)
        copies = []
        for src in rows:
            row = PlAdjustment(month=month, name=src.name, pl_group=src.pl_group, amount=src.amount, note=src.note, created_by=user.id, created_at=_now())
            session.add(row)
            copies.append(row)
        session.flush()
        for row in copies:
            audit.record(session, user, 'pl_adjustment.create', 'pl_adjustment', str(row.id), None, _adjustment_snapshot(row), reason=f'Chép từ {source:%m/%Y}')
    return copies


def set_director_salary(session, user, amount, version):
    check_reader(user)
    if not isinstance(amount, int) or amount < 0:
        raise BusinessError('BAD_AMOUNT', 'Lương giám đốc mục tiêu phải từ 0 trở lên.')
    with transaction(session):
        row = session.get(PlSetting, DIRECTOR_SALARY_KEY, with_for_update=True)
        if row is None:
            row = PlSetting(key=DIRECTOR_SALARY_KEY, amount=amount, updated_by=user.id, updated_at=_now())
            session.add(row)
            before = None
        else:
            check_version(row, version)
            before = dict(amount=row.amount)
            row.amount, row.updated_by, row.updated_at = amount, user.id, _now()
            row.version += 1
        audit.record(session, user, 'pl_setting.update', 'pl_setting', DIRECTOR_SALARY_KEY, before, dict(amount=amount))
    return row


# ---------- Tổng hợp ----------

def _lines(session, month):
    start, end = month_range(month)
    gross = _gross(session, start, end)
    payroll = payroll_cost(session, month)
    opex = operating_expenses(session, start, end)
    damage = damage_cost(session, start, end)
    adjust = adjustments(session, month)
    before_director = gross['gross'] - payroll['amount'] - opex['amount'] - damage - adjust['amount']
    return dict(month=month, start=start, end=end, **gross, payroll=payroll, opex=opex, damage=damage, adjustments=adjust,
                before_director=before_director)


def summary(session, user, month=None):
    check_reader(user)
    month = month_of(month)
    current = _lines(session, month)
    previous = _lines(session, prev_month(month))
    salary = director_salary(session)
    start, end = current['start'], current['end']
    current.update(director_salary=salary, remaining=current['before_director'] - salary, owner=owner_draws(session, start, end),
                   cash=_cash_bridge(session, start, end, current), previous=previous, previous_remaining=previous['before_director'] - salary)
    # Thiếu bao nhiêu lãi gộp để trả nổi lương GĐ, quy ra doanh thu ở biên lãi gộp của tháng.
    gap = max(0, -current['remaining'])
    current['gap'] = gap
    current['gap_revenue'] = int(gap * 100 / current['rate']) if gap and current['rate'] and current['rate'] > 0 else None
    return current


def _cash_bridge(session, start, end, lines):
    """Lãi là một chuyện, tiền là chuyện khác: dòng tiền ròng tháng và các khoản giải thích chênh lệch."""
    totals = finance_reports._rows(session, select(
        func.coalesce(func.sum(finance_reports._income()), 0).label('income'), func.coalesce(func.sum(finance_reports._expense()), 0).label('expense'))
        .select_from(FundLedger).join(CashDoc, CashDoc.id == FundLedger.doc_id)
        .where(FundLedger.entry_date.between(start, end), CashDoc.kind.in_(('PT', 'PC')), CashDoc.is_opening.is_(False)))[0]
    net_cash = totals['income'] - totals['expense']
    # Nợ khách tăng = phải thu mới phát sinh (có VAT) − báo thu được xác nhận trong tháng.
    arising = (_scalar(session, select(func.coalesce(func.sum(SalesDelivery.total), 0)).where(SalesDelivery.status == 'DA_GIAO', *_window(SalesDelivery.delivered_at, start, end)))
               + _scalar(session, select(func.coalesce(func.sum(ServiceTicket.receivable), 0)).where(
                   ServiceTicket.status.in_(('HOAN_THANH', 'DA_DONG')), ServiceTicket.billing == 'THU_TIEN', *_window(ServiceTicket.priced_at, start, end)))
               - _scalar(session, select(func.coalesce(func.sum(SalesReturn.total), 0)).where(SalesReturn.status == 'DA_NHAN', *_window(SalesReturn.returned_at, start, end))))
    collected = _scalar(session, select(func.coalesce(func.sum(SalesReceipt.amount), 0)).where(
        SalesReceipt.status == 'DA_XAC_NHAN', SalesReceipt.received_at.between(start, end)))
    stock_change = _scalar(session, select(func.coalesce(func.sum(StockLedger.amount), 0)).where(*_window(StockLedger.at, start, end)))
    return dict(income=totals['income'], expense=totals['expense'], net=net_cash, receivable_change=arising - collected,
                stock_change=stock_change, adjustments=lines['adjustments']['amount'], owner=owner_draws(session, start, end)['amount'])


# ---------- Theo loại, theo khách, 12 tháng ----------

def by_group(session, user, month=None):
    """Lãi gộp theo nhóm hàng + dòng 'Dịch vụ · tiền công'. Cộng lại bằng đúng Tổng hợp."""
    check_reader(user)
    month = month_of(month)
    start, end = month_range(month)
    data = purchase_reports.gross_margin(session, user, start, end)
    groups = {}
    names = dict(session.execute(select(Product.code, Product.group_name).where(Product.code.in_([r['product_code'] for r in data['products']]))).all()) if data['products'] else {}
    for r in data['products']:
        key = names.get(r['product_code']) or 'Chưa xếp nhóm'
        g = groups.setdefault(key, dict(group=key, revenue=0, cost=0, products=0))
        g['revenue'] += r['revenue']
        g['cost'] += r['cost']
        g['products'] += 1
    totals, ptotals = data['totals'], data['product_totals']
    fee_revenue, fee_cost = totals['revenue'] - ptotals['revenue'], totals['cost'] - ptotals['cost']
    rows = sorted(groups.values(), key=lambda g: -(g['revenue'] - g['cost']))
    if fee_revenue or fee_cost:
        rows.insert(0, dict(group=SERVICE_FEE_GROUP, revenue=fee_revenue, cost=fee_cost, products=0))
    for g in rows:
        g['profit'] = g['revenue'] - g['cost']
        g['rate'] = round(g['profit'] * 100 / g['revenue'], 1) if g['revenue'] else None
    return dict(rows=rows, totals=dict(revenue=totals['revenue'], cost=totals['cost'], profit=totals['profit'], rate=totals['rate']))


def by_customer(session, user, month=None, limit=10):
    """Khách lãi nhất và khách lỗ (trả hàng, bảo hành miễn phí); tổng bằng đúng Tổng hợp."""
    check_reader(user)
    month = month_of(month)
    start, end = month_range(month)
    data = purchase_reports.gross_margin(session, user, start, end)
    customers = {}
    for r in data['documents']:
        c = customers.setdefault(r['customer_code'], dict(customer_code=r['customer_code'], name=r['customer_name'], revenue=0, cost=0, documents=0))
        c['revenue'] += r['revenue']
        c['cost'] += r['cost']
        c['documents'] += 1
    rows = list(customers.values())
    for c in rows:
        c['profit'] = c['revenue'] - c['cost']
        c['rate'] = round(c['profit'] * 100 / c['revenue'], 1) if c['revenue'] else None
    rows.sort(key=lambda c: -c['profit'])
    return dict(top=[c for c in rows if c['profit'] >= 0][:limit], losses=[c for c in rows if c['profit'] < 0], count=len(rows),
                totals=dict(revenue=data['totals']['revenue'], cost=data['totals']['cost'], profit=data['totals']['profit']))


def twelve_months(session, user, month=None):
    check_reader(user)
    month = month_of(month)
    salary = director_salary(session)
    months = []
    m = month
    for _ in range(12):
        months.append(m)
        m = prev_month(m)
    rows = []
    for m in reversed(months):
        line = _lines(session, m)
        rows.append(dict(month=m, revenue=line['revenue'], gross=line['gross'], rate=line['rate'], payroll=line['payroll']['amount'],
                         payroll_status=line['payroll']['status'], opex=line['opex']['amount'], damage=line['damage'],
                         adjustments=line['adjustments']['amount'], before_director=line['before_director'], remaining=line['before_director'] - salary))
    return dict(rows=rows, director_salary=salary)
