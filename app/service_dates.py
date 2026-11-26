"""Sửa ngày phiếu dịch vụ (TUẤN duyệt mẫu R2BcBQtqyfJGfcHGZAxgXd 26/11/2026).

Chỉ giám đốc. Sửa được 3 ngày: ngày nhận phiếu (created_at), ngày hoàn thành (done_at thay giờ bước
Hoàn thành, vì lịch sử bước không sửa được) và ngày chốt phí (priced_at, ngày tính doanh thu dịch vụ).
Chỉ đổi ngày, giữ nguyên giờ. Không đổi tiền, báo thu, công nợ, kho, sổ quỹ nên không cần mở chốt sổ.
Chặn đổi ngày nhận / ngày hoàn thành khi tháng cũ hoặc tháng mới đã chốt hoa hồng hoặc duyệt bảng lương.
"""
from datetime import date, datetime

from sqlalchemy.orm import Session

from . import service_tickets as svc
from .errors import BusinessError
from .models import CommissionMonth, PayrollMonth, ServiceTicket, User
from .security import has_role
from .stock_docs import _transaction

TZ = svc.service_sla.TZ
LABELS = {'received': 'Ngày nhận', 'done': 'Ngày hoàn thành', 'priced': 'Ngày chốt phí'}


def _local(value: datetime) -> date:
    return value.astimezone(TZ).date()


def _move(value: datetime, day: date) -> datetime:
    """Đổi sang ngày mới, giữ nguyên giờ địa phương."""
    local = value.astimezone(TZ)
    return datetime.combine(day, local.time().replace(tzinfo=None), tzinfo=TZ)


def current(session: Session, t: ServiceTicket) -> dict:
    """Ba ngày đang có của phiếu (giờ đầy đủ), None nếu phiếu chưa tới bước đó."""
    from .service_warranty import completion
    done = None
    if t.status in ('HOAN_THANH', 'DA_DONG'):
        stamp, _ = completion(session, t)
        done = stamp
    return {'received': t.created_at, 'done': done, 'priced': t.priced_at}


def can_fix(user: User, t: ServiceTicket) -> bool:
    return has_role(user, 'director') and t.status != 'DA_HUY'


def _check_months(session: Session, days):
    for d in sorted({d.replace(day=1) for d in days}):
        if session.get(PayrollMonth, d, populate_existing=True) or session.get(CommissionMonth, d, populate_existing=True):
            raise BusinessError('MONTH_LOCKED', f'Bảng lương / hoa hồng tháng {d.month}/{d.year} đã chốt, '
                                'không đổi ngày nhận hoặc ngày hoàn thành vào hay ra khỏi tháng đó. Hoa hồng điều chỉnh ở tháng sau.',
                                status=409)


def fix_dates(session: Session, user: User, ticket_id: int, version: int | None, received: date | None,
              done: date | None, priced: date | None, reason: str) -> ServiceTicket:
    if not has_role(user, 'director'):
        raise BusinessError('FORBIDDEN', 'Chỉ giám đốc được sửa ngày phiếu.', status=403)
    reason = (reason or '').strip()
    if len(reason) < 3 or len(reason) > 500:
        raise BusinessError('REASON_REQUIRED', 'Phải ghi lý do sửa ngày (3–500 ký tự).')
    today = _local(svc._now())
    with _transaction(session):
        t = svc._get(session, user, ticket_id, lock=True); svc._check_version(t, version)
        if t.status == 'DA_HUY':
            raise BusinessError('BAD_STATUS', 'Phiếu đã hủy, không sửa ngày.', status=409)
        now = current(session, t)
        wanted = {'received': received, 'done': done, 'priced': priced}
        for key, day in wanted.items():
            if day is None:
                continue
            if now[key] is None:
                raise BusinessError('BAD_FORM', f'Phiếu chưa có {LABELS[key].lower()}, không sửa được.')
            if day > today:
                raise BusinessError('FUTURE_DAY', f'{LABELS[key]} không được sau hôm nay.')
        changes = {k: d for k, d in wanted.items() if d is not None and d != _local(now[k])}
        if not changes:
            raise BusinessError('NO_CHANGE', 'Chưa đổi ngày nào.')
        final = {k: changes.get(k, _local(now[k]) if now[k] else None) for k in now}
        for key in ('done', 'priced'):
            if final[key] is not None and final[key] < final['received']:
                raise BusinessError('BAD_DATE', f'{LABELS[key]} không được trước ngày nhận phiếu.')
        locked = [d for k in ('received', 'done') if k in changes for d in (_local(now[k]), changes[k])]
        if locked:
            _check_months(session, locked)
        before = svc.snapshot(t)
        if 'received' in changes:
            t.created_at = _move(t.created_at, changes['received'])
        if 'done' in changes:
            t.done_at = _move(now['done'], changes['done'])
        if 'priced' in changes:
            t.priced_at = _move(t.priced_at, changes['priced'])
        t.version += 1
        note = ' · '.join(f"{LABELS[k]} {_local(now[k]):%d/%m/%Y} → {d:%d/%m/%Y}" for k, d in changes.items())
        svc._record(session, user, t, 'fix_dates', before, t.status, note + ' · Lý do: ' + reason, reason)
    return t
