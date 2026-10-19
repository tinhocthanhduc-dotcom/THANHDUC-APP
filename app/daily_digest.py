"""R3: lưu ảnh chụp Tổng quan, không thay đổi sổ hoặc số liệu nguồn."""
from copy import deepcopy
from datetime import UTC, date, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm.exc import StaleDataError

from . import alerts, audit, dashboard
from .errors import BusinessError
from .models import DailyDigest
from .security import has_role

# Khóa riêng bản tóm tắt theo ngày; không dùng khóa ngày của sổ quỹ.
LOCK_NAMESPACE = 0x54444447
SENSITIVE = ('gross_margin_month', 'inventory', 'inventory_total')


def check_writer(user):
    if not user.active or not has_role(user, 'director'):
        raise BusinessError('FORBIDDEN', 'Chỉ giám đốc đang hoạt động được lưu tóm tắt.', status=403)


def build(session, user, day: date) -> dict:
    check_writer(user)
    summary = dashboard.summary(session, user, day)
    notices = alerts.collect(session, user, today=day)
    keys = ('revenue_today', 'revenue_month', 'revenue_last_month', 'gross_margin_month',
            'fund_total', 'customer_debt', 'supplier_debt', 'inventory_total')
    data = {key: int(summary[key]) for key in keys}
    data.update(day=day.isoformat(), funds=deepcopy(summary['funds']),
                inventory=deepcopy(summary['inventory']), services=deepcopy(summary['services']),
                pending_receipts=deepcopy(summary['pending_receipts']),
                alert_counts=dict(notices.counts), red_alert_count=notices.red_count,
                red_alerts=[dict(item) for item in notices if item['level'] == 'DO'][:5])
    return data


def visible(data: dict, user) -> dict:
    """Lọc trước khi render cả bảng lẫn văn bản; không gửi JSON đầy đủ ra trình duyệt."""
    dashboard.check_reader(user)
    result = deepcopy(data)
    if not has_role(user, 'director'):
        for key in SENSITIVE:
            result.pop(key, None)
        result['alert_counts'].pop('CHAM_LUAN_CHUYEN', None)
        result['red_alerts'] = [r for r in result['red_alerts'] if r['kind'] != 'CHAM_LUAN_CHUYEN']
    return result


def render_text(data: dict) -> str:
    def money(value):
        return f'{int(value):,}'.replace(',', '.') + ' đồng'

    lines = [f"TÓM TẮT NGÀY {date.fromisoformat(data['day']).strftime('%d/%m/%Y')}",
             f"Doanh thu ngày (chưa VAT): {money(data['revenue_today'])}",
             f"Doanh thu tháng đến ngày này: {money(data['revenue_month'])}"]
    if 'gross_margin_month' in data:
        lines.append(f"Lãi gộp tháng: {money(data['gross_margin_month'])}")
    lines.append(f"Tổng quỹ: {money(data['fund_total'])}")
    lines.extend(f"  {f['code']} · {f['name']}: {money(f['balance'])}" for f in data['funds'])
    lines.extend([f"Khách còn nợ: {money(data['customer_debt'])}",
                  f"Nợ nhà cung cấp: {money(data['supplier_debt'])}"])
    if 'inventory_total' in data:
        lines.append(f"Giá trị tồn kho: {money(data['inventory_total'])}")
        lines.extend(f"  {w['warehouse_code']} · {w['name']}: {money(w['value'])}" for w in data['inventory'])
    lines.extend([f"Dịch vụ: {data['services']['open']} đang mở, {data['services']['overdue']} quá hạn",
                  f"Báo thu chờ: {data['pending_receipts']['count']} phiếu, {money(data['pending_receipts']['amount'])}",
                  f"Cảnh báo đỏ: {data['red_alert_count']}"])
    counts = [(alerts.LABELS.get(k, k), n) for k, n in data['alert_counts'].items() if n]
    lines.append('Cần xử lý: ' + ('; '.join(f'{label}: {n}' for label, n in counts) or 'Không có việc cần xử lý'))
    lines.extend(f"- {r['title']}: {r['detail']}" for r in data['red_alerts'])
    return '\n'.join(lines)


def _snapshot(row):
    return dict(day=row.day.isoformat(), data=deepcopy(row.data), text=row.text,
                created_at=row.created_at.isoformat(), created_by=row.created_by, version=row.version)


def save(session, user, day: date, *, scheduled=False):
    check_writer(user)
    try:
        # Serialize cả lần đầu lẫn ghi đè, tránh hai tiến trình cùng tạo một ngày.
        session.execute(text('select pg_advisory_xact_lock(:namespace,:day)'),
                        {'namespace': LOCK_NAMESPACE, 'day': day.toordinal()})
        row = session.scalar(select(DailyDigest).where(DailyDigest.day == day)
                             .with_for_update().execution_options(populate_existing=True))
        before = _snapshot(row) if row else None
        data = build(session, user, day)
        if row is None:
            row = DailyDigest(day=day)
            session.add(row)
        else:
            row.version += 1
        row.data, row.text = data, render_text(data)
        # Thời điểm và nguồn tạo của bản chụp hiện hành, kể cả khi ghi đè.
        row.created_at = datetime.now(UTC)
        row.created_by = None if scheduled else user.id
        session.flush()
        audit.record(session, user, 'digest.update' if before else 'digest.create',
                     'daily_digest', day.isoformat(), before, _snapshot(row))
        session.commit()
        return row
    except StaleDataError as exc:
        session.rollback()
        raise BusinessError('VERSION_CONFLICT', 'Bản tóm tắt vừa thay đổi, hãy tải lại.', status=409) from exc
    except Exception:
        session.rollback()
        raise


def listing(session, user, page=1):
    dashboard.check_reader(user)
    total = session.scalar(select(func.count()).select_from(DailyDigest))
    rows = session.execute(select(DailyDigest.day,
        DailyDigest.data['revenue_today'].as_integer().label('revenue_today'),
        DailyDigest.data['red_alert_count'].as_integer().label('red_alert_count'))
        .order_by(DailyDigest.day.desc()).offset((page-1)*30).limit(30)).mappings().all()
    return rows, total


def load(session, user, day):
    dashboard.check_reader(user)
    row = session.get(DailyDigest, day)
    if row is None:
        raise BusinessError('NOT_FOUND', 'Chưa có bản tóm tắt ngày này.', status=404)
    data = visible(row.data, user)
    rendered = row.text if has_role(user, 'director') else render_text(data)
    return dict(day=row.day, data=data, text=rendered,
                created_at=row.created_at.astimezone(dashboard.TZ), version=row.version)
