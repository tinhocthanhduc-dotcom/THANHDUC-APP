"""Biên bản nghiệm thu và bàn giao máy (TUẤN duyệt mẫu 23/11/2026): một phiếu, hoặc gộp nhiều máy của một khách.

- Ngày sửa xong lấy ở bước "Sửa xong, chờ trả máy" (máy về xưởng) hoặc bước Hoàn thành; máy đang được giao thợ đi trả
  vẫn là đã sửa xong nên không còn chữ "BẢN XEM TRƯỚC".
- Ngày bàn giao chỉ in sẵn khi phiếu đã hoàn thành; chưa thì để trống cho thợ ghi tay lúc trả máy.
"""
from datetime import timedelta

from sqlalchemy import select

from ..errors import BusinessError
from .. import equipment, service_tickets as svc, service_warranty
from ..models import Customer, CustomerSite, Equipment, Product, ServiceTicket, User

DONE = ('HOAN_THANH', 'DA_DONG')
STOPPED = ('DA_HUY', 'KHONG_LAM_DUOC')
INTAKE_ACTIONS = ('workshop_handover', 'workshop', 'counter_intake')


def _steps(t):
    done = next((s for s in reversed(t.steps) if s.to_status == 'HOAN_THANH'), None)
    fixed = next((s for s in reversed(t.steps) if s.action == 'repair_done'), None)
    intake = next((s for s in t.steps if s.action in INTAKE_ACTIONS), None)
    return done, fixed, intake


def is_ready(t):
    """Đã sửa xong (đã hoàn thành, hoặc đã qua bước sửa xong chờ trả máy) và không bị hủy."""
    if t.status in STOPPED:
        return False
    done, fixed, _ = _steps(t)
    return t.status in DONE or fixed is not None


def warranty_short(t):
    if t.warranty_months == 0 or t.kind == 'MUC_DINH_KY':
        return 'Không'
    return f'{t.warranty_months} tháng' if t.warranty_months is not None else ''


def machine(session, user, t, *, users=None):
    """Một máy trên biên bản: phiếu, tên máy, ngày, việc đã làm, bảo hành, vật tư."""
    eq = session.get(Equipment, t.serial) if t.serial else None
    product = session.get(Product, eq.product_code) if eq and eq.product_code else None
    done, fixed, intake = _steps(t)
    completed = t.status in DONE
    last = t.steps[-1] if t.steps else None
    person = (users or {}).get
    tech_id = t.repaired_by or t.assignee_id
    tech = person(tech_id) if users is not None else (session.get(User, tech_id) if tech_id else None)
    codes = {line.product_code for line in t.lines if line.product_code}
    note_step = done if (done and completed) else fixed or last
    return dict(
        t=t, name=equipment.display_name(eq, product) if eq else '', serial=t.serial or '',
        technician=tech.full_name if tech else '', ready=is_ready(t),
        repaired_at=(fixed.at if fixed else done.at if done else None),
        completed_at=done.at if (done and completed) else None,
        received_at=intake.at if intake else None,
        work=t.fix_done or (note_step.note if note_step else '') or '',
        warranty=service_warranty.terms(session, t), warranty_short=warranty_short(t),
        products={p.code: p for p in session.scalars(select(Product).where(Product.code.in_(codes)))} if codes else {},
    )


def watermark(t, ready):
    return 'ĐÃ HỦY' if t.status == 'DA_HUY' else '' if ready else 'BẢN XEM TRƯỚC'


def single_context(session, user, t):
    from ..config import get_settings
    from ..money_words import money_words
    from .. import sales_receipts
    m = machine(session, user, t)
    customer = session.get(Customer, t.customer_code)
    return dict(t=t, m=m, customer=customer, company=get_settings(), back=f'/dich-vu/{t.id}',
                site=session.get(CustomerSite, t.site_id) if t.site_id else None,
                balance=sales_receipts.balance(t) if m['ready'] and t.billing == 'THU_TIEN' else None,
                words=money_words(t.total or 0), watermark=watermark(t, m['ready']))


def _trip_day(x):
    return x.appointment_at.astimezone(svc.service_sla.TZ).date() if x.appointment_at else None


def same_trip(x, t):
    """Cùng chuyến trả máy (TUẤN 23/11/2026): cùng thợ được giao và cùng ngày hẹn."""
    return (x.id == t.id or (x.assignee_id is not None and x.assignee_id == t.assignee_id
            and _trip_day(t) is not None and _trip_day(x) == _trip_day(t)))


def candidates(session, user, t):
    """Máy có thể gộp vào một biên bản: các phiếu cùng chỗ (tách từ một phiếu gốc) + phiếu khác của cùng khách
    đã sửa xong trong 30 ngày gần đây. Trả [(phiếu, đã sửa xong, chọn sẵn)] theo số phiếu: chọn sẵn máy cùng chỗ
    và máy cùng chuyến trả (cùng thợ, cùng ngày hẹn)."""
    family = {x.id for x in svc.family(session, t)}
    since = svc._now() - timedelta(days=30)
    others = session.scalars(select(ServiceTicket.id).where(ServiceTicket.customer_code == t.customer_code,
        ServiceTicket.status.not_in(STOPPED), ServiceTicket.created_at >= since)).all()
    rows = []
    for ident in sorted(family | set(others)):
        try:
            x = svc.load(session, user, ident)
        except BusinessError:  # phiếu người này không được xem thì bỏ qua
            continue
        if x.customer_code != t.customer_code:
            continue
        ready = is_ready(x)
        if ident in family or ready:
            rows.append((x, ready, ident in family or (ready and same_trip(x, t))))
    return rows


def group_context(session, user, t, ids):
    """Biên bản gộp: mọi phiếu phải cùng khách và đã sửa xong; tiền giữ theo từng phiếu, cộng lại ở ô tổng."""
    from ..config import get_settings
    from ..money_words import money_words
    from .. import sales_receipts
    tickets = []
    for ident in sorted(set(ids)):
        x = svc.load(session, user, ident)
        if x.customer_code != t.customer_code:
            raise BusinessError('OTHER_CUSTOMER', 'Chỉ gộp máy của cùng một khách.', status=409)
        if not is_ready(x):
            raise BusinessError('NOT_READY', f'Phiếu {x.number} chưa sửa xong, chưa đưa vào biên bản được.', status=409)
        tickets.append(x)
    if len(tickets) < 2:
        raise BusinessError('PICK_TWO', 'Chọn ít nhất 2 máy để in biên bản gộp.', status=400)
    ids_u = {x.repaired_by or x.assignee_id for x in tickets} - {None}
    users = {u.id: u for u in session.scalars(select(User).where(User.id.in_(ids_u)))} if ids_u else {}
    machines = [machine(session, user, x, users=users) for x in tickets]
    paid = [x for x in tickets if x.billing == 'THU_TIEN' and x.total]
    subtotal = sum(x.subtotal or 0 for x in paid)
    vat = sum(x.vat_total or 0 for x in paid)
    total = sum(x.total or 0 for x in paid)
    balances = [sales_receipts.balance(x) for x in paid]
    balance = dict(paid=sum(b['paid'] for b in balances), remaining=sum(b['remaining'] for b in balances)) if balances else None
    symptoms = []
    for x in tickets:
        if x.symptom and x.symptom not in symptoms:
            symptoms.append(x.symptom)
    received = [m['received_at'] for m in machines if m['received_at']]
    repaired = [m['repaired_at'] for m in machines if m['repaired_at']]
    completed = [m['completed_at'] for m in machines]
    technicians = []
    for m in machines:
        if m['technician'] and m['technician'] not in technicians:
            technicians.append(m['technician'])
    customer = session.get(Customer, t.customer_code)
    return dict(t=tickets[0], tickets=tickets, machines=machines, customer=customer, company=get_settings(),
                back=f'/dich-vu/{t.id}', site=session.get(CustomerSite, tickets[0].site_id) if tickets[0].site_id else None,
                subtotal=subtotal, vat=vat, total=total, balance=balance, words=money_words(total),
                symptoms=symptoms, received_at=min(received) if received else None,
                repaired_at=max(repaired) if repaired else None,
                completed_at=max(completed) if all(completed) else None, technicians=technicians,
                watermark='')
