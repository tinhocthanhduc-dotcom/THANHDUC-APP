"""Ghi chú phiếu AI (TUẤN duyệt 15/11/2026, mẫu WE5ip3oxBpydWpiB1jfE4C).

Mở phiếu dịch vụ, màn thợ, kiểm tra máy, nhận đơn mới là có sẵn vài dòng lưu ý lấy từ lịch sử máy và khách:
máy mới sửa, còn bảo hành, quay lại nhiều lần, báo giá lần trước, lần mực trước, khách hợp đồng, khách mới,
phiếu khác cùng chỗ, ghi chú khách/máy, nợ quá hạn.

Chạy bằng quy tắc, chỉ SELECT, tính lại mỗi lần mở (không lưu, luôn đúng số mới nhất). Không gọi API, không sửa phiếu,
không in ra giấy đưa khách. Thợ không thấy số tiền nợ (TUẤN chọn: chỉ "có nợ cũ quá hạn"), không bao giờ thấy giá vốn, lãi.
Cài đặt (bật/tắt từng loại dòng) nằm ở Nhân viên AI, mã GHI_CHU.
"""
from datetime import date, timedelta

from sqlalchemy import func, select

from .models import (ContractQuote, Customer, Equipment, Product, RepairQuoteVersion, SalesOrder, ServiceLine,
                     ServiceTicket, User)
from .security import has_role
from .service_sla import TZ

CODE = 'GHI_CHU'
RULES = {
    'bao_hanh': 'Còn bảo hành',
    'moi_sua': 'Máy mới sửa',
    'quay_lai': 'Máy quay lại nhiều lần',
    'bao_gia': 'Báo giá lần trước khách chọn gì',
    'muc': 'Lần mực trước',
    'hop_dong': 'Khách có báo giá hợp đồng',
    'khach_moi': 'Khách mới',
    'cung_cho': 'Phiếu khác cùng chỗ đang mở',
    'ghi_chu': 'Ghi chú khách / máy',
    'no': 'Nợ quá hạn',
}
DEFAULTS = dict(rules=list(RULES), recent_days=90, repeat_count=3, repeat_days=60, zalo=True)
OPEN = ("MOI", "DA_GIAO", "DA_NHAN", "DA_HEN", "DANG_LAM", "CHO_LINH_KIEN", "TAM_DUNG")
DONE = ('HOAN_THANH', 'DA_DONG')
OFFICE = ('sales', 'finance', 'dispatcher', 'director')
LIMIT = 6
KIND_LABELS = {'MUC_DINH_KY': 'Mực định kỳ', 'SUA_TAI_CHO': 'Sửa tại chỗ', 'MANG_VE': 'Mang về xưởng',
               'GIAO_HANG': 'Giao hàng', 'BAO_HANH': 'Bảo hành'}


def _today():
    from .service_tickets import _now
    return _now().astimezone(TZ).date()


def _d(day: date, year=False) -> str:
    return day.strftime('%d/%m/%Y' if year else '%d/%m')


def _m(n: int) -> str:
    return f'{int(n):,}'.replace(',', '.')


def _cut(text: str, n: int) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= n else text[:n - 1].rstrip() + '…'


def settings(session) -> dict | None:
    """Cấu hình đang dùng, None khi Ghi chú phiếu AI (hoặc công tắc tổng) đang tắt."""
    from . import nhan_vien_ao as nva
    row = nva.active(session, CODE)
    return nva.cfg(row) if row is not None else None


def _note(icon, text, *, href='', link='', warn=False, who='', rank=5):
    return dict(icon=icon, text=text, href=href, link=link, warn=warn, who=who, rank=rank)


def _url(user, ticket_id):
    from .web import nav
    return nav.ticket_url(user, ticket_id) if user is not None else f'/dich-vu/{ticket_id}'


def _done_day(session, t):
    from . import service_warranty
    stamp, _ = service_warranty.completion(session, t)
    stamp = stamp or t.created_at
    return stamp.astimezone(TZ).date() if stamp else None


def _parts(session, t) -> list[str]:
    codes = [x.product_code for x in session.scalars(select(ServiceLine).where(
        ServiceLine.ticket_id == t.id, ServiceLine.kind == 'VAT_TU').order_by(ServiceLine.line_no)) if x.product_code]
    if not codes:
        return []
    names = dict(session.execute(select(Product.code, Product.name).where(Product.code.in_(codes))).all())
    out = []
    for c in codes:
        name = names.get(c, c)
        if name not in out:
            out.append(name)
    return out


def _machine(session, user, t, serial, c, today, rules, out):
    if not serial:
        return
    from . import service_warranty
    me = t.id if t is not None else None
    since = today - timedelta(days=max(int(c['recent_days']), int(c['repeat_days']), 180))
    past = [x for x in session.scalars(select(ServiceTicket).where(
        ServiceTicket.serial == serial, ServiceTicket.status != 'DA_HUY', ServiceTicket.created_at >= since)
        .order_by(ServiceTicket.created_at.desc(), ServiceTicket.id.desc()).limit(30)) if x.id != me]
    done = [x for x in past if x.status in DONE and x.kind != 'MUC_DINH_KY']
    names = dict(session.execute(select(User.id, User.full_name)).all())
    opened = (t.created_at.astimezone(TZ).date() if t is not None and t.created_at else today)
    # Còn bảo hành: phiếu sửa gần nhất còn hạn, không thì hạn bảo hành máy mua mới.
    if 'bao_hanh' in rules:
        if t is not None and t.kind == 'BAO_HANH' and t.warranty_of_id:
            parent = session.get(ServiceTicket, t.warranty_of_id)
            if parent is not None:
                end = service_warranty.warranty_end(session, parent)
                out.append(_note('🛡', f"Phiếu bảo hành của {parent.number}" + (f", hạn đến {_d(end, True)}" if end else '') + '.',
                                 href=_url(user, parent.id), link=parent.number, rank=1))
        else:
            hit = None
            for x in done:
                end = service_warranty.warranty_end(session, x)
                if end and end >= opened:
                    hit = (x, end)
                    break
            if hit:
                x, end = hit
                out.append(_note('🛡', f"Còn bảo hành sửa đến {_d(end, True)}. Cùng lỗi thì báo điều phối chuyển phiếu Bảo hành, không thu tiền.",
                                 href=_url(user, x.id), link=x.number, warn=True, rank=0))
            else:
                eq = session.get(Equipment, serial)
                if eq is not None and eq.warranty_until and eq.warranty_until >= opened:
                    out.append(_note('🛡', f"Máy còn bảo hành mua mới đến {_d(eq.warranty_until, True)}.", warn=True, rank=0))
    if 'moi_sua' in rules:
        recent = [x for x in done if (_done_day(session, x) or today) >= today - timedelta(days=int(c['recent_days']))][:2]
        for x in recent:
            day = _done_day(session, x)
            who = names.get(x.repaired_by or x.assignee_id, '')
            fault = _cut(x.fault_found or x.symptom, 60)
            fix = _cut(x.fix_done, 70)
            parts = _parts(session, x)
            text = f"Sửa {_d(day)}" + (f" ({who.split()[-1] if who else ''})" if who else '') + f": {fault}"
            if fix:
                text += f" → {fix}"
            if parts:
                text += f". Đã thay: {_cut(', '.join(parts), 70)}"
            gap = (opened - day).days if day else None
            if t is not None and gap is not None and gap >= 0 and x is recent[0]:
                text += f". Quay lại sau {gap} ngày"
            out.append(_note('🔁', text + '.', href=_url(user, x.id), link=x.number, rank=2))
    if 'quay_lai' in rules:
        window = today - timedelta(days=int(c['repeat_days']))
        n = sum(1 for x in past if x.kind != 'MUC_DINH_KY' and x.created_at.astimezone(TZ).date() >= window) + (1 if t is not None else 0)
        if n >= int(c['repeat_count']):
            out.append(_note('⚠️', f"Máy này quay lại lần {n} trong {c['repeat_days']} ngày: nên kiểm kỹ, báo phương án triệt để.",
                             warn=True, rank=1))
    if 'bao_gia' in rules:
        ids = [x.id for x in past]
        v = session.scalar(select(RepairQuoteVersion).where(RepairQuoteVersion.ticket_id.in_(ids),
                           RepairQuoteVersion.status.in_(('DONG_Y', 'CHUA_DONG_Y', 'KHONG_SUA')))
                           .order_by(RepairQuoteVersion.id.desc()).limit(1)) if ids else None
        if v is not None:
            snap = v.snapshot or {}
            total = (snap.get('chosen') or {}).get('total') or 0
            day = (v.created_at if getattr(v, 'created_at', None) else None)
            when = f" {_d(day.astimezone(TZ).date())}" if day else ''
            if v.status == 'DONG_Y':
                alt = v.reply_choice and v.reply_choice != snap.get('pick')
                what = 'khách đồng ý' + (' phương án còn lại' if alt else '')
            elif v.status == 'KHONG_SUA':
                what = 'khách không sửa'
            else:
                what = 'khách chưa đồng ý' + (f" ({_cut(v.reply_reason, 40)})" if v.reply_reason else '')
            num = session.get(ServiceTicket, v.ticket_id)
            out.append(_note('📋', f"Báo giá{when}" + (f" {_m(total)}" if total else '') + f": {what}.",
                             href=_url(user, v.ticket_id), link=num.number if num else '', rank=3))


def _ink(session, user, t, customer_code, site_id, out):
    from . import service_repeat
    rows = [r for r in service_repeat.last_ink(session, customer_code) if t is None or r['ticket_id'] != t.id]
    if site_id:
        rows = [r for r in rows if r['site_id'] == str(site_id)] or rows[:0]
    if not rows:
        return
    r = rows[0]
    goods = ', '.join(f"{x['qty']} {x['name']}" for x in r['lines'][:3])
    text = f"Lần mực trước {r['date'][:5]}" + (f": {_cut(goods, 80)}" if goods else '') + (f", giá {r['price']}" if r['price'] else '')
    out.append(_note('🖨', text + '.', href=_url(user, r['ticket_id']), link=r['number'], rank=3))


def _debt(session, user, customer_code, today, technician_only):
    from .finance_reports import _debts
    d = _debts()
    unpaid, late = 0, 0
    for r in session.execute(select(d.c.remaining, d.c.pending, d.c.due).where(d.c.customer_code == customer_code)):
        left = max(0, int(r.remaining) - int(r.pending))
        if left and r.due and r.due < today:
            unpaid += left
            late = max(late, (today - r.due).days)
    if not unpaid:
        return None
    if technician_only:
        return _note('💰', 'Khách có nợ cũ quá hạn. Khách muốn trả thì dùng Thu hộ công nợ.', rank=4)
    return _note('💰', f"Khách còn nợ quá hạn {_m(unpaid)}, trễ {late} ngày.", href='/cong-no-khach-hang?q=' + customer_code,
                 link='Sổ nợ', who='Chỉ văn phòng thấy số tiền', rank=4)


def notes(session, user, *, ticket=None, customer_code='', serial='', site_id=None, today=None, cfg=None, part='all') -> list[dict]:
    """Các dòng lưu ý cho phiếu (hoặc cho khách / từng máy ở Nhận đơn: part='customer' / 'machine'). [] khi tắt hoặc không có gì."""
    c = cfg if cfg is not None else settings(session)
    if c is None:
        return []
    c = {**DEFAULTS, **c}
    rules = set(c.get('rules') or [])
    if ticket is not None:
        customer_code, serial, site_id = ticket.customer_code, ticket.serial or '', ticket.site_id
    if not customer_code:
        return []
    today = today or _today()
    customer = session.get(Customer, customer_code)
    if customer is None:
        return []
    out: list[dict] = []
    with session.no_autoflush:
        if part == 'machine':
            _machine(session, user, ticket, serial, c, today, rules, out)
            eq = session.get(Equipment, serial) if serial and 'ghi_chu' in rules else None
            if eq is not None and eq.note.strip():
                out.append(_note('💬', f'Ghi chú máy: "{_cut(eq.note, 160)}"', rank=4))
            out.sort(key=lambda n: n['rank'])
            return out[:3]
        _machine(session, user, ticket, serial, c, today, rules, out)
        if 'muc' in rules and (ticket is None or ticket.kind == 'MUC_DINH_KY'):
            if ticket is not None:
                _ink(session, user, ticket, customer_code, site_id, out)
        if 'hop_dong' in rules:
            from . import contract_quotes
            for q in session.scalars(select(ContractQuote).where(ContractQuote.customer_code == customer_code,
                                     ContractQuote.status == 'DA_KY').order_by(ContractQuote.id.desc()).limit(3)):
                if contract_quotes.state(q, today) == 'DA_KY':
                    out.append(_note('🧾', 'Khách có báo giá hợp đồng: tính theo giá hợp đồng.',
                                     href=f'/bao-gia-hop-dong/{q.id}' if user is not None and has_role(user, *OFFICE) else '',
                                     link=q.number, rank=3))
                    break
        me = ticket.id if ticket is not None else 0
        if 'khach_moi' in rules:
            older = session.scalar(select(func.count()).select_from(ServiceTicket).where(
                ServiceTicket.customer_code == customer_code, ServiceTicket.status != 'DA_HUY', ServiceTicket.id != me)) or 0
            orders = session.scalar(select(func.count()).select_from(SalesOrder).where(
                SalesOrder.customer_code == customer_code, SalesOrder.status.not_in(('NHAP', 'DA_HUY')))) or 0
            if not older and not orders:
                out.append(_note('🆕', 'Khách mới: lần đầu làm việc với Thành Đức.', rank=2))
        if 'cung_cho' in rules and part == 'all':
            stmt = select(ServiceTicket).where(ServiceTicket.customer_code == customer_code, ServiceTicket.status.in_(OPEN),
                                               ServiceTicket.id != me)
            if site_id:
                stmt = stmt.where(ServiceTicket.site_id == site_id)
            names = None
            for x in session.scalars(stmt.order_by(ServiceTicket.id.desc()).limit(2)):
                names = names or dict(session.execute(select(User.id, User.full_name)).all())
                who = names.get(x.assignee_id, '') if x.assignee_id else 'chưa giao thợ'
                out.append(_note('📍', f"Cùng chỗ còn phiếu đang mở: {KIND_LABELS.get(x.kind, 'Dịch vụ')} ({who}). Đi chung chuyến.",
                                 href=_url(user, x.id), link=x.number, rank=4))
        if 'ghi_chu' in rules:
            if customer.note.strip():
                out.append(_note('💬', f'Ghi chú khách: "{_cut(customer.note, 160)}"', rank=4))
            eq = session.get(Equipment, serial) if serial else None
            if eq is not None and eq.note.strip():
                out.append(_note('💬', f'Ghi chú máy: "{_cut(eq.note, 160)}"', rank=4))
        if 'no' in rules and user is not None:
            row = _debt(session, user, customer_code, today, not has_role(user, *OFFICE))
            if row:
                out.append(row)
    out.sort(key=lambda n: n['rank'])
    return out[:LIMIT]


def zalo_lines(session, user, ticket, n=2) -> str:
    """2 dòng lưu ý quan trọng nhất để gắn vào tin Zalo nhắc thợ. '' khi tắt hoặc không có gì."""
    c = settings(session)
    if c is None or not c.get('zalo', True):
        return ''
    rows = notes(session, user, ticket=ticket, cfg=c)[:n]
    if not rows:
        return ''
    return '\nLưu ý:\n' + '\n'.join(f"{r['icon']} {r['text']}" for r in rows)
