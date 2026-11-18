"""Lặp lại phiếu Mực định kỳ lần trước (TUẤN duyệt 06/11/2026). Chỉ SELECT, không ghi gì.

Khách gọi xin mực (không theo lịch cố định): chọn khách ở Nhận đơn dịch vụ, app đưa lần Mực định kỳ gần nhất
của mỗi địa điểm (tối đa 3) kèm mã mực, số lượng, giá hợp đồng. Bấm Dùng lại chỉ điền sẵn form, người nhận đơn
vẫn sửa được rồi mới bấm Nhận đơn, nên phiếu tạo ra đi đúng đường cũ (yêu cầu Chờ cấp, phí hợp đồng).
Số lượng: phiếu đã hoàn thành/đóng lấy số thợ thực giao (dòng vật tư); chưa xong lấy số đã chọn lúc nhận đơn.
Mã đã ngưng bán bị bỏ ra và ghi tên vào `dropped`.
"""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .models import CustomerSite, Equipment, PartRequest, Product, ServiceLine, ServiceTicket, User

TZ = ZoneInfo('Asia/Ho_Chi_Minh')
LIMIT = 3
DONE = ('HOAN_THANH', 'DA_DONG')


def _qty(q):
    q = Decimal(q).normalize()
    return format(q, 'f')


def _money(n):
    return f'{n:,}'.replace(',', '.') if n else ''


def _lines(session, t):
    if t.status in DONE:
        used = [(x.product_code, x.qty) for x in t.lines if x.kind == 'VAT_TU' and x.product_code]
        if used:
            return used
    return [(r.product_code, r.qty) for r in session.scalars(
        select(PartRequest).where(PartRequest.ticket_id == t.id, PartRequest.status != 'DA_HUY').order_by(PartRequest.id))]


def _price(t):
    from .service_tickets import INK_FEE_DESCRIPTION
    for x in t.lines:
        if x.kind == 'PHI' and x.description == INK_FEE_DESCRIPTION:
            gross = int(x.amount or 0) + int(x.vat_amount or 0)
            # Giá gồm VAT gõ lúc nhận đơn (vd 520.000) tách ra net + thuế làm tròn có thể lệch 1–2 đ; trả lại số tròn đã gõ.
            near = round(gross / 1000) * 1000
            return near if abs(near - gross) <= 2 else gross
    return 0


def last_ink(session, customer_code, now=None):
    """Lần Mực định kỳ gần nhất của mỗi địa điểm của khách (mới nhất trước), tối đa LIMIT địa điểm."""
    if not customer_code:
        return []
    rows = session.scalars(select(ServiceTicket).where(ServiceTicket.customer_code == customer_code,
                                                       ServiceTicket.kind == 'MUC_DINH_KY', ServiceTicket.status != 'DA_HUY')
                           .order_by(ServiceTicket.created_at.desc(), ServiceTicket.id.desc()).limit(60)).all()
    picked, seen = [], set()
    for t in rows:
        if t.site_id in seen:
            continue
        seen.add(t.site_id)
        picked.append(t)
        if len(picked) >= LIMIT:
            break
    if not picked:
        return []
    today = (now or datetime.now(TZ)).astimezone(TZ).date()
    out = []
    for t in picked:
        raw = []
        for code, qty in _lines(session, t):
            for item in raw:
                if item[0] == code:
                    item[1] += qty
                    break
            else:
                raw.append([code, Decimal(qty)])
        codes = [c for c, _ in raw]
        products = {p.code: p for p in session.scalars(select(Product).where(Product.code.in_(codes)))} if codes else {}
        lines, dropped = [], []
        for code, qty in raw:
            p = products.get(code)
            if p is None or not p.active:
                dropped.append(code)
                continue
            lines.append(dict(code=code, name=p.name, qty=_qty(qty)))
        site = session.get(CustomerSite, t.site_id) if t.site_id else None
        machine = ''
        if t.serial:
            eq = session.get(Equipment, t.serial)
            name = ''
            if eq is not None:
                prod = session.get(Product, eq.product_code) if eq.product_code else None
                name = prod.name if prod else (getattr(eq, 'model_name', '') or '')
            machine = f'{name} · {t.serial}' if name else t.serial
        tech = session.get(User, t.repaired_by or t.assignee_id) if (t.repaired_by or t.assignee_id) else None
        created = t.created_at.astimezone(TZ).date() if t.created_at else today
        price = _price(t)
        out.append(dict(
            ticket_id=t.id, number=t.number, date=created.strftime('%d/%m/%Y'), days=(today - created).days,
            site_id=str(t.site_id or ''), site=site.name if site else 'Địa chỉ chính', address=site.address if site else '',
            serial=t.serial or '', machine=machine, technician=tech.full_name if tech else '',
            lines=lines, dropped=dropped, price=_money(price), note=t.symptom or ''))
    return out


# Đợt 4b (TUẤN duyệt mẫu 18/11/2026): Nhận đơn hiện khung "Lần trước" cho mọi loại trừ Mực định kỳ (đã có khung riêng).
# Chỉ đọc; nút Dùng lại điền form ở trình duyệt. Máy KHÔNG tự tick (TUẤN 05/11), chỉ tick khi bấm nút.
SAME_INTAKE_SECONDS = 300
KIND_LABELS = {'SUA_TAI_CHO': 'Sửa tại chỗ', 'MANG_VE': 'Mang máy về xưởng', 'BAO_HANH': 'Bảo hành'}


def last_service(session, customer_code, now=None):
    """Lần nhận đơn gần nhất của khách (không tính Mực định kỳ, đã hủy); gom các phiếu nhiều máy tạo cùng lúc."""
    if not customer_code:
        return None
    rows = session.scalars(select(ServiceTicket).where(ServiceTicket.customer_code == customer_code,
                                                       ServiceTicket.status != 'DA_HUY',
                                                       (ServiceTicket.kind.is_(None)) | (ServiceTicket.kind != 'MUC_DINH_KY'))
                           .order_by(ServiceTicket.created_at.desc(), ServiceTicket.id.desc()).limit(12)).all()
    if not rows:
        return None
    t = rows[0]
    group = [x for x in rows if x.site_id == t.site_id and x.created_at is not None and t.created_at is not None
             and abs((t.created_at - x.created_at).total_seconds()) <= SAME_INTAKE_SECONDS] or [t]
    group.sort(key=lambda x: x.id)
    serials = []
    for x in group:
        if x.serial and x.serial not in serials:
            serials.append(x.serial)
    names = []
    for serial in serials[:3]:
        eq = session.get(Equipment, serial)
        prod = session.get(Product, eq.product_code) if eq is not None and eq.product_code else None
        name = prod.name if prod else ((getattr(eq, 'model_name', '') or '') if eq is not None else '')
        names.append(f'{name} · {serial}' if name else serial)
    if len(serials) > 3:
        names.append(f'+{len(serials) - 3} máy')
    site = session.get(CustomerSite, t.site_id) if t.site_id else None
    tech_id = t.repaired_by or t.assignee_id
    tech = session.get(User, tech_id) if tech_id else None
    today = (now or datetime.now(TZ)).astimezone(TZ).date()
    created = t.created_at.astimezone(TZ).date() if t.created_at else today
    first = group[0]
    return dict(ticket_id=first.id, number=first.number, date=created.strftime('%d/%m/%Y'), days=(today - created).days,
                kind=t.kind or '', kind_label=KIND_LABELS.get(t.kind or '', 'Chưa rõ'),
                # Địa điểm chỉ dùng một lần (đã ẩn) thì không điền lại, chỉ ghi "Địa điểm khác".
                site_id=None if site is not None and not site.active else str(t.site_id or ''),
                site=('Địa điểm khác (dùng một lần)' if not site.active else site.name) if site is not None else 'Địa chỉ chính',
                serials=serials, machine=', '.join(names), technician_id=str(tech.id) if tech is not None and tech.active else '',
                technician=tech.full_name if tech is not None else '',
                fault=(t.symptom or '').strip()[:160], fix=(t.fix_done or '').strip()[:160])
