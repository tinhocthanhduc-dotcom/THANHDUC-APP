"""Khách lâu chưa gọi mực (TUẤN duyệt mẫu 06/11/2026). Tính nhịp chỉ SELECT; ghi kết quả gọi là ghi chú, không tiền, không kho.

Mực định kỳ không theo lịch cố định, khách tự gọi. App lấy các lần khách lấy mực (phiếu Mực định kỳ chưa hủy và đơn bán
đã chốt có hộp mực/mực nạp), cần ít nhất MIN_EVENTS lần khác ngày. Nhịp = số ở giữa (trung vị) các khoảng cách của
tối đa LAST_EVENTS lần gần nhất. Quá THRESHOLD × nhịp kể từ lần cuối thì lên danh sách cho kinh doanh gọi hỏi thăm.
Khách đang có phiếu Mực định kỳ mở thì không hiện. Kết quả gọi (sau lần lấy mực cuối) tạm ẩn khách:
khách đặt / hẹn lại / không nghe: 7 ngày; còn mực: thêm một nhịp; đã chuyển nơi khác: ẩn hẳn (tab riêng).
"""
import math
from datetime import date, datetime, timedelta
from statistics import median

from sqlalchemy import func, select

from . import audit
from .errors import BusinessError
from .models import (CUSTOMER_INK_CALL_RESULTS, Customer, CustomerInkCall, CustomerPhone, PartRequest, Product,
                     SalesOrder, SalesOrderLine, ServiceTicket, User)
from .security import has_role
from .service_sla import TZ

INK_GROUPS = ('Hộp mực', 'Mực nạp / Mực nước')
MIN_EVENTS = 3
LAST_EVENTS = 7
THRESHOLD = 1.5
SOON_DAYS = 7
MIN_RHYTHM = 7
WRITERS = ('sales', 'finance')
RESULTS = {'DAT_MUC': 'Khách đặt mực', 'HEN_LAI': 'Hẹn gọi lại', 'KHONG_NGHE': 'Không nghe máy',
           'CON_MUC': 'Còn mực, chưa cần', 'CHUYEN_NOI_KHAC': 'Đã chuyển nơi khác'}
assert tuple(RESULTS) == CUSTOMER_INK_CALL_RESULTS
OPEN = ('MOI', 'DA_GIAO', 'DA_NHAN', 'DA_HEN', 'DANG_LAM', 'CHO_LINH_KIEN', 'TAM_DUNG')


def _local(dt) -> date:
    return dt.astimezone(TZ).date()


def _events(session):
    """{customer: [(ngày, 'DV'|'DB', id, số phiếu)]} các lần lấy mực, mới nhất sau cùng."""
    out = {}
    for code, at, ident, number in session.execute(select(ServiceTicket.customer_code, ServiceTicket.created_at, ServiceTicket.id, ServiceTicket.number)
                                                   .where(ServiceTicket.kind == 'MUC_DINH_KY', ServiceTicket.status != 'DA_HUY')):
        out.setdefault(code, []).append((_local(at), 'DV', ident, number))
    ink = select(SalesOrderLine.order_id).join(Product, Product.code == SalesOrderLine.product_code).where(Product.group_name.in_(INK_GROUPS))
    when = func.coalesce(SalesOrder.delivered_at, SalesOrder.confirmed_at, SalesOrder.created_at)
    for code, at, ident, number in session.execute(select(SalesOrder.customer_code, when, SalesOrder.id, SalesOrder.number)
                                                   .where(SalesOrder.status.not_in(('NHAP', 'DA_HUY')), SalesOrder.id.in_(ink))):
        out.setdefault(code, []).append((_local(at), 'DB', ident, number))
    for rows in out.values():
        rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return out


def rhythm(days: list[date]) -> int | None:
    """Nhịp (ngày) từ các ngày lấy mực khác nhau; None nếu chưa đủ MIN_EVENTS lần."""
    unique = sorted(set(days))[-LAST_EVENTS:]
    if len(unique) < MIN_EVENTS:
        return None
    gaps = [(b - a).days for a, b in zip(unique, unique[1:])]
    return max(MIN_RHYTHM, int(round(median(gaps))))


def hide_until(call, cycle: int):
    if call.result == 'CHUYEN_NOI_KHAC':
        return None  # ẩn hẳn
    days = cycle if call.result == 'CON_MUC' else 7
    return _local(call.called_at) + timedelta(days=days)


def _ink_of(session, kind, ident):
    if kind == 'DV':
        rows = session.execute(select(Product.name, func.sum(PartRequest.qty)).join(Product, Product.code == PartRequest.product_code)
                               .where(PartRequest.ticket_id == ident, PartRequest.status != 'DA_HUY').group_by(Product.name).order_by(Product.name))
    else:
        rows = session.execute(select(Product.name, func.sum(SalesOrderLine.qty)).join(Product, Product.code == SalesOrderLine.product_code)
                               .where(SalesOrderLine.order_id == ident, Product.group_name.in_(INK_GROUPS)).group_by(Product.name).order_by(Product.name))
    return [f'{name} ×{format(qty.normalize(), "f")}' for name, qty in rows][:3]


def collect(session, today: date) -> dict:
    """dict(goi, sap, hen, mat: [item], xong: số cuộc gọi tuần này)."""
    events = _events(session)
    codes = list(events)
    if not codes:
        return dict(goi=[], sap=[], hen=[], mat=[], xong=0)
    customers = {c.code: c for c in session.scalars(select(Customer).where(Customer.code.in_(codes), Customer.status == 'DANG_DUNG'))}
    busy = set(session.scalars(select(ServiceTicket.customer_code).where(ServiceTicket.kind == 'MUC_DINH_KY', ServiceTicket.status.in_(OPEN))))
    last_call = {}
    for call in session.scalars(select(CustomerInkCall).where(CustomerInkCall.customer_code.in_(codes)).order_by(CustomerInkCall.called_at, CustomerInkCall.id)):
        last_call[call.customer_code] = call
    phones = {}
    for code, phone in session.execute(select(CustomerPhone.customer_code, CustomerPhone.phone).where(CustomerPhone.customer_code.in_(codes))
                                       .order_by(CustomerPhone.customer_code, CustomerPhone.is_contact.desc(), CustomerPhone.phone)):
        phones.setdefault(code, phone)
    users = dict(session.execute(select(User.id, User.full_name)).all())
    out = dict(goi=[], sap=[], hen=[], mat=[])
    for code, rows in events.items():
        c = customers.get(code)
        if c is None or code in busy:
            continue
        cycle = rhythm([r[0] for r in rows])
        if cycle is None:
            continue
        last = rows[-1]
        since = (today - last[0]).days
        limit = math.ceil(THRESHOLD * cycle)
        call = last_call.get(code)
        if call is not None and _local(call.called_at) < last[0]:
            call = None  # khách đã lấy mực sau cuộc gọi
        if since < limit - SOON_DAYS and not (call and call.result == 'CHUYEN_NOI_KHAC'):
            continue
        item = dict(code=code, name=c.name, phone=phones.get(code, ''), last=last[0], last_number=last[3],
                    last_link=(f'/dich-vu/{last[2]}' if last[1] == 'DV' else f'/don-ban/{last[2]}'), rhythm=cycle, since=since,
                    over=since - cycle, due_in=max(0, limit - since), times=len({r[0] for r in rows}), ink=[], call=None)
        if call is not None:
            until = hide_until(call, cycle)
            item['call'] = dict(result=call.result, label=RESULTS[call.result], note=call.note, at=_local(call.called_at),
                                by=users.get(call.called_by, ''), until=until)
            if until is None:
                out['mat'].append(item)
                continue
            if today < until:
                out['hen'].append(item)
                continue
        out['goi' if since >= limit else 'sap'].append(item)
    for key in out:
        out[key].sort(key=lambda i: (-i['over'], i['name']))
    for item in out['goi'] + out['sap'] + out['hen']:
        rows = events[item['code']]
        item['ink'] = _ink_of(session, rows[-1][1], rows[-1][2])
    monday = today - timedelta(days=today.weekday())
    start = datetime.combine(monday, datetime.min.time(), TZ)
    out['xong'] = session.scalar(select(func.count()).select_from(CustomerInkCall).where(CustomerInkCall.called_at >= start)) or 0
    return out


def record(session, user, customer_code: str, result: str, note: str = '') -> CustomerInkCall:
    if not has_role(user, *WRITERS):
        raise BusinessError('FORBIDDEN', 'Chỉ kinh doanh hoặc kế toán ghi kết quả gọi khách.', status=403)
    if result not in RESULTS:
        raise BusinessError('BAD_RESULT', 'Chọn kết quả cuộc gọi.')
    customer = session.get(Customer, (customer_code or '')[:16])
    if customer is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy khách hàng.', status=404)
    note = (note or '').strip()[:1000]
    row = CustomerInkCall(customer_code=customer.code, result=result, note=note, called_by=user.id)
    session.add(row)
    session.flush()
    audit.record(session, user, 'customer.ink_call', 'customer', customer.code, None, dict(result=result, note=note))
    session.commit()
    return row
