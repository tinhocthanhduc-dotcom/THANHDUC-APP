"""Trả máy cả chuyến (TUẤN duyệt mẫu 23/11/2026).

Một chuyến = các phiếu máy đã sửa xong của cùng khách, cùng thợ, cùng ngày hẹn trả. Không có bảng mới:
- Giao một lần: gọi đúng bước "Giao thợ trả máy" hiện có cho từng phiếu trong một transaction.
- Thợ trả một lần: mỗi máy được tick đi đúng các bước hiện có (đến khách, trả máy, hoàn thành), tiền khách đưa
  chia theo thứ tự phiếu, mỗi phiếu một báo thu như "Thu chung". Lỗi ở một máy thì không lưu máy nào.
Tiền, bảo hành, hoa hồng vẫn theo từng phiếu.
"""
from sqlalchemy import select

from . import service_charges, service_tickets as tickets
from .errors import BusinessError
from .models import Customer, EquipmentCustody, ServiceTicket
from .stock_docs import _transaction

READY = 'SUA_XONG_CHO_TRA'
RETURN_TAG = 'Trả máy cả chuyến'
# Đã giao thợ trả (đổi thợ thì DA_GIAO, thợ nhận DA_NHAN), đã hẹn, hoặc đã đến khách.
ON_TRIP = ('DA_GIAO', 'DA_NHAN', 'DA_HEN', 'DANG_LAM')


def ready_for_return(t):
    """Máy sửa xong (hoặc khách không sửa) đã chốt tiền, đang chờ giao thợ trả."""
    return t.status == 'TAM_DUNG' and t.hold_reason == READY and service_charges.return_locked(t) and not t.counter_intake


def ready_by_customer(session, code=None):
    """{mã khách: [phiếu chờ giao thợ trả]} theo số phiếu, chỉ khách có từ 2 máy (hoặc đúng khách được hỏi)."""
    stmt = select(ServiceTicket).where(ServiceTicket.status == 'TAM_DUNG', ServiceTicket.hold_reason == READY)
    if code:
        stmt = stmt.where(ServiceTicket.customer_code == code)
    out = {}
    for t in session.scalars(stmt.order_by(ServiceTicket.id)):
        if ready_for_return(t):
            out.setdefault(t.customer_code, []).append(t)
    return out if code else {k: v for k, v in out.items() if len(v) > 1}


def schedule_many(session, user, items, appointment, slot, assignee_id, note=''):
    """items = [(ticket_id, version)]; cùng một khách, giao cùng thợ, cùng hẹn. Lỗi một phiếu thì không lưu phiếu nào."""
    from . import service_workshop
    tickets._require(user, tickets.DISPATCHERS, 'Chỉ điều phối hoặc giám đốc được hẹn trả máy.')
    if len(items) < 2:
        raise BusinessError('PICK_TWO', 'Chọn ít nhất 2 máy để giao trả cả chuyến.')
    with _transaction(session):
        rows = []
        for ident, version in items:
            t = session.scalar(select(ServiceTicket).where(ServiceTicket.id == ident).with_for_update()
                               .execution_options(populate_existing=True))
            if t is None:
                raise BusinessError('NOT_FOUND', 'Không tìm thấy phiếu dịch vụ.', status=404)
            rows.append(t)
        if len({t.customer_code for t in rows}) != 1:
            raise BusinessError('OTHER_CUSTOMER', 'Chỉ giao trả cả chuyến các máy của cùng một khách.', status=409)
        for t, (_, version) in zip(rows, items):
            try:
                service_workshop.schedule_return_in_tx(session, user, t, version, appointment, slot, assignee_id, note)
            except BusinessError as exc:
                raise BusinessError(exc.code, f'{t.number}: {exc.message}', exc.details, status=exc.status) from exc
    return rows


def _day(t):
    return t.appointment_at.astimezone(tickets.service_sla.TZ).date() if t.appointment_at else None


def trip_for(session, user, t):
    """Các phiếu cùng chuyến trả với phiếu t của thợ này (gồm t), theo số phiếu; ít hơn 2 thì không phải chuyến."""
    if t.assignee_id != user.id or t.status not in ON_TRIP or not service_charges.return_locked(t):
        return []
    rows = session.scalars(select(ServiceTicket).where(ServiceTicket.customer_code == t.customer_code,
        ServiceTicket.assignee_id == user.id, ServiceTicket.status.in_(ON_TRIP)).order_by(ServiceTicket.id)).all()
    out = [x for x in rows if service_charges.return_locked(x) and _day(x) == _day(t)]
    return out if len(out) > 1 else []


def due(t):
    """Còn phải thu của phiếu, trừ cả báo thu đang chờ kế toán xác nhận."""
    from . import sales_receipts
    b = sales_receipts.balance(t)
    return max(0, (b['receivable'] or 0) - b['paid'] - b['pending'])


def trips(session, user):
    """Chuyến trả máy của thợ trên màn Hôm nay: [dict(anchor, tickets, customer, due)]."""
    rows = session.scalars(select(ServiceTicket).where(ServiceTicket.assignee_id == user.id,
        ServiceTicket.status.in_(ON_TRIP)).order_by(ServiceTicket.id)).all()
    groups = {}
    for t in rows:
        if service_charges.return_locked(t):
            groups.setdefault((t.customer_code, _day(t)), []).append(t)
    out = []
    for (code, _), ts in groups.items():
        if len(ts) < 2:
            continue
        c = session.get(Customer, code)
        out.append(dict(anchor=ts[0], tickets=ts, customer=c.name if c else code, due=sum(due(x) for x in ts),
                        appointment=ts[0].appointment_at, slot=ts[0].appointment_slot))
    return out


def intake_accessories(session, t):
    """Phụ kiện lúc nhận máy của chu kỳ hiện tại: mặc định thợ trả đủ như lúc nhận."""
    from sqlalchemy import func
    last = session.scalar(select(func.max(EquipmentCustody.id)).where(EquipmentCustody.serial == t.serial,
        EquipmentCustody.location == 'TAI_KHACH', EquipmentCustody.status == 'DA_NHAN')) or 0
    start = session.scalar(select(EquipmentCustody).where(EquipmentCustody.serial == t.serial, EquipmentCustody.ticket_id == t.id,
        EquipmentCustody.status == 'DA_NHAN', EquipmentCustody.id > last).order_by(EquipmentCustody.id).limit(1))
    return list(start.accessories) if start else []


def complete(session, user, anchor_id, picks, amount, method, note='', arrive=None):
    """Thợ trả các máy được tick (picks = [(ticket_id, phiếu version, máy version)]) và thu chung `amount`.

    Mỗi máy: đến khách → trả máy (phụ kiện như lúc nhận) → Hoàn thành; tiền chia theo thứ tự phiếu, không vượt tổng còn phải thu."""
    from . import sales_receipts
    from .equipment_custody import return_customer_in_tx
    from .models import ServiceDraft
    from .schemas import CustodyIntake, SalesReceiptReport
    if 'technician' not in user.roles:
        raise BusinessError('FORBIDDEN', 'Chỉ thợ được giao được trả máy.', status=403)
    if not picks:
        raise BusinessError('PICK_ONE', 'Tick ít nhất một máy khách đã nhận.')
    if type(amount) is not int or amount < 0:
        raise BusinessError('BAD_AMOUNT', 'Số tiền phải là số nguyên đồng, không âm.')
    with _transaction(session):
        anchor = tickets._get(session, user, anchor_id, lock=True)
        allowed = {x.id for x in trip_for(session, user, anchor)} or {anchor.id}
        done = []
        for ident, version, machine_version in picks:
            if ident not in allowed:
                raise BusinessError('NOT_IN_TRIP', 'Máy không thuộc chuyến trả này.', status=409)
            t = tickets._get(session, user, ident, lock=True)
            tickets._check_version(t, version)
            if t.status == 'DA_GIAO':
                tickets.transition_in_tx(session, user, t, 'DA_NHAN', RETURN_TAG)
            if t.status in ('DA_NHAN', 'DA_HEN'):
                tickets.transition_in_tx(session, user, t, 'DANG_LAM', (arrive or {}).get('note') or 'Đến khách trả máy')
                if arrive and arrive.get('lat'):
                    from .service_visits import record_arrival_in_tx
                    record_arrival_in_tx(session, user, t.id, arrive.get('lat'), arrive.get('lng'), arrive.get('accuracy'))
            if t.status != 'DANG_LAM':
                raise BusinessError('BAD_STATUS', f'{t.number}: chỉ trả máy phiếu đang làm.', status=409)
            tickets.require_kind(t)
            if t.serial:
                return_customer_in_tx(session, user, t.id, CustodyIntake(version=machine_version, accessories=intake_accessories(session, t)))
            tickets.transition_in_tx(session, user, t, 'HOAN_THANH', note or RETURN_TAG, auto_close=False)
            done.append(t)
        session.flush()
        rows = [(t, due(t)) for t in done]
        total = sum(owed for _, owed in rows)
        if amount > total:
            raise BusinessError('OVER_PAYMENT', f'Khách đưa dư {amount - total:,} đ so với tổng còn phải thu {total:,} đ. Sửa lại số tiền.'.replace(',', '.'),
                                {'total': total}, status=409)
        text = f'{RETURN_TAG}: ' + ', '.join(t.number for t in done) + (f'. {note.strip()}' if note and note.strip() else '')
        left = amount
        for t, owed in rows:
            part = min(left, owed)
            if part > 0:
                sales_receipts.report_for_ticket_in_tx(session, user, t, SalesReceiptReport(version=t.version, amount=part, method=method, note=text[:4000]))
                left -= part
        for t in done:
            tickets.auto_close_in_tx(session, user, t)
            draft = session.get(ServiceDraft, (t.id, user.id))
            if draft is not None:
                session.delete(draft)
    return done
