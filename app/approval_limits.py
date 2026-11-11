"""Hạn mức duyệt (TUẤN duyệt mẫu 11/11/2026): kế toán duyệt thay giám đốc trong hạn mức giám đốc đặt.

Giao được: bổ sung giờ vào/ra (mọi phiếu hoặc không giao), xin nghỉ tới N ngày, tạm ứng tới X đồng, hủy hàng chờ xử lý
tới Y đồng theo giá vốn. Giá trị 0 = không giao. Không ai duyệt phiếu của chính mình; trên mức thì vẫn về giám đốc.
Giám đốc luôn duyệt/từ chối được mọi phiếu. Vẫn chỉ giám đốc: điều chỉnh sổ quỹ, ghi sổ kiểm kê, hủy báo thu/phiếu trả,
thanh lý dưới giá vốn (không đổi ở đây).
"""
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select

from . import audit
from .errors import BusinessError
from .models import (ApprovalLimit, AttendanceRequest, PendingDisposition, PendingItem, Product, SalaryAdvance,
                     StockBalance, User)
from .security import has_role

KEYS = ('BO_SUNG', 'NGHI', 'TAM_UNG', 'HUY_HANG')
DEFAULTS = {'BO_SUNG': 1, 'NGHI': 2, 'TAM_UNG': 2_000_000, 'HUY_HANG': 500_000}
LABELS = {'BO_SUNG': 'Bổ sung giờ vào / ra', 'NGHI': 'Xin nghỉ', 'TAM_UNG': 'Tạm ứng lương', 'HUY_HANG': 'Hủy hàng chờ xử lý'}
MAX = {'BO_SUNG': 1, 'NGHI': 30, 'TAM_UNG': 1_000_000_000, 'HUY_HANG': 1_000_000_000}
LEAVE_PICKS = (0, 1, 2, 3)


def limits(session) -> dict:
    out = dict(DEFAULTS)
    out.update({r.key: int(r.value) for r in session.scalars(select(ApprovalLimit))})
    return out


def director(user):
    if not user.active or not has_role(user, 'director'):
        raise BusinessError('FORBIDDEN', 'Chỉ giám đốc được đặt hạn mức duyệt.', status=403)


def save(session, user, values: dict):
    director(user)
    clean = {}
    for key in KEYS:
        raw = str(values.get(key, '')).replace('.', '').replace(',', '').strip()
        if not raw.isdigit():
            raise BusinessError('BAD_LIMIT', f'{LABELS[key]}: nhập số nguyên từ 0 (0 = không giao).')
        value = int(raw)
        if value > MAX[key]:
            raise BusinessError('BAD_LIMIT', f'{LABELS[key]}: tối đa {MAX[key]:,}.'.replace(',', '.'))
        clean[key] = value
    before = limits(session)
    now = datetime.now(UTC)
    for key, value in clean.items():
        row = session.get(ApprovalLimit, key)
        if row is None:
            session.add(ApprovalLimit(key=key, value=value, updated_by=user.id, updated_at=now))
        elif row.value != value:
            row.value, row.updated_by, row.updated_at = value, user.id, now
    audit.record(session, user, 'approval_limits.save', 'approval_limits', 'all', before, clean)
    session.commit()
    return clean


def leave_days(row) -> Decimal:
    if row.part != 'CA_NGAY':
        return Decimal('0.5')
    return Decimal((row.to_date - row.from_date).days + 1)


def disposal_cost(session, disposition) -> int:
    item = session.get(PendingItem, disposition.item_id)
    avg = session.scalar(select(StockBalance.avg_cost).where(StockBalance.warehouse_code == item.warehouse_code,
                                                             StockBalance.product_code == item.product_code))
    unit = avg if avg is not None else item.unit_cost
    return int((Decimal(disposition.qty) * Decimal(unit or 0)).to_integral_value())


def _within(key, size, table) -> bool:
    limit = table[key]
    if limit <= 0:
        return False
    return key == 'BO_SUNG' or size <= limit


def attendance_key(row):
    return ('BO_SUNG', 0) if row.punch_at else ('NGHI', leave_days(row))


def delegated(session, user, key, size, owner_id, table=None) -> bool:
    """Kế toán (không phải giám đốc) được duyệt phiếu này theo hạn mức."""
    if not user.active or not has_role(user, 'finance') or has_role(user, 'director') or owner_id == user.id:
        return False
    return _within(key, size, table or limits(session))


def can_decide(session, user, key, size, owner_id, table=None) -> bool:
    if user.active and has_role(user, 'director'):
        return True
    return delegated(session, user, key, size, owner_id, table)


def require(session, user, key, size, owner_id):
    if can_decide(session, user, key, size, owner_id):
        return
    if user.active and has_role(user, 'finance') and owner_id == user.id:
        raise BusinessError('FORBIDDEN', 'Không duyệt phiếu của chính mình; phiếu này chờ giám đốc.', status=403)
    if user.active and has_role(user, 'finance') and limits(session)[key] > 0:
        raise BusinessError('TREN_HAN_MUC', 'Phiếu trên hạn mức kế toán được duyệt; chờ giám đốc.', status=403)
    raise BusinessError('FORBIDDEN', 'Chỉ giám đốc hoặc kế toán trong hạn mức được duyệt.', status=403)


def approver(user, session=None, keys=()):
    """Vào trang duyệt: giám đốc, hoặc kế toán khi giám đốc có giao ít nhất một việc trong keys (danh sách lọc theo hạn mức)."""
    if not user.active or not has_role(user, 'director', 'finance'):
        raise BusinessError('FORBIDDEN', 'Chỉ giám đốc hoặc kế toán được duyệt.', status=403)
    if session is not None and keys and not has_role(user, 'director'):
        table = limits(session)
        if all(table[k] <= 0 for k in keys):
            raise BusinessError('FORBIDDEN', 'Giám đốc chưa giao kế toán duyệt việc này.', status=403)


# ---- Hàng chờ duyệt (Việc của tôi kế toán, báo cáo sáng giám đốc) ----

def _finance_ids(session) -> list[int]:
    return [u.id for u in session.scalars(select(User).where(User.active.is_(True)))
            if 'finance' in u.roles and 'director' not in u.roles]


def _delegable(key, size, owner_id, table, finance_ids) -> bool:
    return _within(key, size, table) and any(i != owner_id for i in finance_ids)


def queue(session) -> list[dict]:
    """Mọi phiếu chờ duyệt thuộc 3 loại giao được; delegable=True nếu có kế toán duyệt được."""
    table = limits(session)
    finance_ids = _finance_ids(session)
    names = dict(session.execute(select(User.id, User.full_name)).all())
    out = []
    for r in session.scalars(select(AttendanceRequest).where(AttendanceRequest.status == 'CHO_DUYET').order_by(AttendanceRequest.created_at, AttendanceRequest.id)):
        key, size = attendance_key(r)
        label = ('Bổ sung giờ ' + ('vào' if r.kind == 'BO_SUNG_VAO' else 'ra')) if r.punch_at else f'Xin nghỉ {_days(size)} ngày'
        out.append(dict(key=key, id=r.id, number=r.number, label=label, who=names.get(r.user_id, '—'), amount=0, size=size,
                        owner_id=r.user_id, link=f'/cham-cong/duyet#cc-{r.id}', delegable=_delegable(key, size, r.user_id, table, finance_ids)))
    for r in session.scalars(select(SalaryAdvance).where(SalaryAdvance.status == 'CHO_DUYET').order_by(SalaryAdvance.requested_at, SalaryAdvance.id)):
        out.append(dict(key='TAM_UNG', id=r.id, number=r.number, label='Tạm ứng', who=names.get(r.user_id, '—'), amount=int(r.amount),
                        size=int(r.amount), owner_id=r.user_id, link=f'/tam-ung#tu-{r.id}',
                        delegable=_delegable('TAM_UNG', int(r.amount), r.user_id, table, finance_ids)))
    for r, product in session.execute(select(PendingDisposition, Product.name).join(PendingItem, PendingItem.id == PendingDisposition.item_id)
                                      .join(Product, Product.code == PendingItem.product_code)
                                      .where(PendingDisposition.kind == 'HUY', PendingDisposition.status == 'CHO_DUYET')
                                      .order_by(PendingDisposition.requested_at, PendingDisposition.id)):
        cost = disposal_cost(session, r)
        out.append(dict(key='HUY_HANG', id=r.id, number=r.number, label=f'Hủy {_qty(r.qty)} {product}', who=names.get(r.requested_by, '—'),
                        amount=cost, size=cost, owner_id=r.requested_by, link=f'/cho-xu-ly/duyet-huy#xl-{r.id}',
                        delegable=_delegable('HUY_HANG', cost, r.requested_by, table, finance_ids)))
    return out


def for_user(session, user) -> list[dict]:
    table = limits(session)
    return [r for r in queue(session) if delegated(session, user, r['key'], r['size'], r['owner_id'], table)]


def _days(size) -> str:
    return f'{size:g}'.replace('.', ',') if isinstance(size, Decimal) else str(size)


def _qty(value) -> str:
    value = Decimal(value)
    return str(int(value)) if value == value.to_integral_value() else f'{value:g}'.replace('.', ',')


def delegated_between(session, start, end) -> dict:
    """Phiếu kế toán (không phải giám đốc) đã duyệt trong [start, end): đếm theo loại, tiền tạm ứng/hủy hàng."""
    finance_ids = [u.id for u in session.scalars(select(User)) if 'finance' in u.roles and 'director' not in u.roles]
    if not finance_ids:
        return dict(punch=0, leave=0, advance=0, advance_amount=0, disposal=0, disposal_amount=0)
    att = session.execute(select(AttendanceRequest.punch_at.is_not(None), func.count()).where(
        AttendanceRequest.status == 'DA_DUYET', AttendanceRequest.decided_by.in_(finance_ids),
        AttendanceRequest.decided_at >= start, AttendanceRequest.decided_at < end).group_by(AttendanceRequest.punch_at.is_not(None))).all()
    counts = {bool(k): int(n) for k, n in att}
    adv = session.execute(select(func.count(), func.coalesce(func.sum(SalaryAdvance.amount), 0)).where(
        SalaryAdvance.status.in_(('DA_DUYET', 'DA_CHI')), SalaryAdvance.decided_by.in_(finance_ids),
        SalaryAdvance.decided_at >= start, SalaryAdvance.decided_at < end)).one()
    disp = session.execute(select(func.count(), func.coalesce(func.sum(PendingDisposition.cost_value), 0)).where(
        PendingDisposition.kind == 'HUY', PendingDisposition.status == 'DA_GHI', PendingDisposition.decided_by.in_(finance_ids),
        PendingDisposition.decided_at >= start, PendingDisposition.decided_at < end)).one()
    return dict(punch=counts.get(True, 0), leave=counts.get(False, 0), advance=int(adv[0]), advance_amount=int(adv[1]),
                disposal=int(disp[0]), disposal_amount=int(disp[1]))
