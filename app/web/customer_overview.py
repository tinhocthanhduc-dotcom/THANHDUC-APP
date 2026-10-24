"""Đợt 4: trang tổng quan một khách: nút tạo nhanh, ô đếm, việc đang mở, công nợ, máy, liên hệ. Chỉ đọc."""
from datetime import UTC, datetime

from sqlalchemy import func, select

from .. import customer_sites as site_svc, customers as customer_svc, finance_reports, service_tickets as svc
from ..equipment_custody import LOCATIONS
from ..routers.customers import WRITERS as CUSTOMER_WRITERS
from ..models import Equipment, Product, Quote, SalesOrder, ServiceTicket, User
from ..security import has_role
from . import money, nav, paging, render
from .doc_frame import QUOTE_NEXT, order_next
from .lists import OLD_DEBT_DAYS, _today
from .orders import STATUSES as ORDER_STATUSES
from .quotes import STATUSES as QUOTE_STATUSES
from .services import STATUSES as SERVICE_STATUSES

TABS = {'mo': 'Đang mở', 'bao-gia': 'Báo giá', 'don-ban': 'Đơn bán', 'dich-vu': 'Dịch vụ', 'may': 'Máy', 'xong': 'Đã xong'}
QUOTE_OPEN, ORDER_OPEN = ('NHAP', 'DA_GUI', 'CHAP_NHAN'), ('NHAP', 'DA_CHOT', 'GIAO_MOT_PHAN')
QUOTE_TONE = {'NHAP': 'nhap', 'DA_GUI': 'cho', 'CHAP_NHAN': 'xong', 'TU_CHOI': 'huy', 'DA_HUY': 'huy'}
ORDER_TONE = {'NHAP': 'nhap', 'DA_CHOT': 'lam', 'GIAO_MOT_PHAN': 'lam', 'DA_GIAO': 'xong', 'DA_HUY': 'huy'}
SERVICE_TONE = {'MOI': 'cho', 'DA_GIAO': 'lam', 'DA_NHAN': 'lam', 'DA_HEN': 'lam', 'DANG_LAM': 'lam', 'CHO_LINH_KIEN': 'cho',
                'TAM_DUNG': 'cho', 'HOAN_THANH': 'xong', 'DA_DONG': 'xong', 'KHONG_LAM_DUOC': 'huy', 'DA_HUY': 'huy'}


def _service_next(t, names):
    """Bước tiếp của phiếu dịch vụ: (ai, việc). Chỉ để nhắc, không thay luật chuyển trạng thái."""
    tho = names.get(t.assignee_id) or 'thợ'
    if t.status == 'MOI':
        return 'điều phối', 'Giao thợ'
    if t.status == 'DA_GIAO':
        return tho, 'Nhận đơn'
    if t.status == 'DA_NHAN':
        return tho, 'Hẹn khách'
    if t.status == 'DA_HEN':
        when = t.appointment_at.astimezone(svc.service_sla.TZ).strftime(' %d/%m %H:%M') if t.appointment_at else ''
        return tho, 'Đến khách' + when
    if t.status == 'DANG_LAM':
        return tho, 'Làm xong, thu tiền, Hoàn thành'
    if t.status == 'CHO_LINH_KIEN':
        return 'kho', 'Cấp linh kiện cho thợ'
    if t.status == 'TAM_DUNG' and t.hold_reason == 'CHO_SUA_XUONG':
        if t.declined_at or t.quote_approved_at:
            return 'điều phối', 'Sửa xong rồi giao thợ trả máy' if t.quote_approved_at else 'Giao thợ trả máy'
        return 'điều phối', 'Báo giá, hỏi khách đồng ý'
    if t.status == 'TAM_DUNG' and t.hold_reason == 'SUA_XONG_CHO_TRA':
        return 'điều phối', 'Giao thợ trả máy, thu tiền'
    if t.status == 'TAM_DUNG':
        return 'điều phối', svc.HOLD_REASONS.get(t.hold_reason, 'Tạm dừng')
    return 'không ai', 'Đã đóng'


def build(request, session, user, c, tab='mo', trang='', so='', saved=False, warnings=()):
    code, today, now = c.code, _today(), datetime.now(UTC)
    see_quotes, see_orders = nav.allowed(user, '/bao-gia'), nav.allowed(user, '/don-ban')
    see_services, see_machines = nav.allowed(user, '/dich-vu'), True
    can_debt = has_role(user, *customer_svc.CREDIT_ROLES)
    tab = tab if tab in TABS else 'mo'
    names = dict(session.execute(select(User.id, User.full_name)).all())

    # Còn phải thu theo từng chứng từ (đơn bán, phiếu dịch vụ): cùng nguồn với Báo cáo tài chính.
    d = finance_reports._debts()
    owing = {(r.kind, r.id): r for r in session.execute(select(d).where(d.c.customer_code == code))}

    docs = []  # mỗi chứng từ: dict(kind, no, href, what, at, status, tone, who, next, late, amount, open)
    if see_quotes:
        quotes = list(session.scalars(select(Quote).where(Quote.customer_code == code)))
        linked = {q_id for q_id, in session.execute(select(SalesOrder.quote_id).where(
            SalesOrder.customer_code == code, SalesOrder.quote_id.is_not(None), SalesOrder.status != 'DA_HUY'))}
        for q in quotes:
            is_open = q.status in QUOTE_OPEN and q.id not in linked and not q.replaced_by_id
            who, title = QUOTE_NEXT.get(q.status, ('kinh doanh', 'Kiểm tra giá rồi gửi khách'))
            late = q.status == 'DA_GUI' and q.valid_until and q.valid_until < today
            if q.status == 'DA_GUI' and q.valid_until:
                title += (', đã hết hạn ' if late else ', hết hạn ') + q.valid_until.strftime('%d/%m')
            if q.id in linked:
                who, title = 'không ai', 'Đã có đơn bán'
            docs.append(dict(kind='BG', label='Báo giá', no=q.number, href=f'/bao-gia/{q.id}', at=q.created_at, what=q.contact_name,
                             status=QUOTE_STATUSES.get(q.status, q.status), tone=QUOTE_TONE.get(q.status, 'nhap'),
                             who=who, next=title, late=bool(late), amount=q.total, open=is_open))
    if see_orders:
        for o in session.scalars(select(SalesOrder).where(SalesOrder.customer_code == code)):
            debt = owing.get(('DB', o.id))
            due = int(debt.remaining - debt.pending) if debt is not None else 0
            is_open = o.status in ORDER_OPEN or (o.status == 'DA_GIAO' and debt is not None)
            if o.status == 'NHAP':
                who, title = 'kinh doanh', 'Kiểm tra rồi chốt đơn'
            else:
                step = order_next(o, {'balance': {'pending': int(debt.pending), 'paid': int(debt.paid)}} if debt is not None else None, max(due, 0))
                who, title = step['who'], step['title']
            late = o.status in ('DA_CHOT', 'GIAO_MOT_PHAN') and o.promised_date and o.promised_date < today
            docs.append(dict(kind='ĐB', label='Đơn bán', no=o.number, href=f'/don-ban/{o.id}', at=o.created_at,
                             what=o.delivery_address[:80] if o.fulfillment != 'TAI_QUAY' else 'Tại quầy',
                             status=ORDER_STATUSES.get(o.status, o.status), tone=ORDER_TONE.get(o.status, 'nhap'),
                             who=who, next=title, late=bool(late), amount=o.total, open=is_open))
    if see_services:
        for t in session.scalars(select(ServiceTicket).where(ServiceTicket.customer_code == code)):
            who, title = _service_next(t, names)
            symptom = (t.symptom or '').strip()
            docs.append(dict(kind='DV', label='Dịch vụ', no=t.number, href=f'/dich-vu/{t.id}', at=t.created_at,
                             status=SERVICE_STATUSES.get(t.status, t.status), tone=SERVICE_TONE.get(t.status, 'nhap'),
                             who=who, next=title, what=symptom[:80], late=t.status in svc.OPEN and t.due_at is not None and t.due_at < now,
                             amount=t.total, open=t.status in svc.OPEN))
    docs.sort(key=lambda x: x['at'], reverse=True)

    machines = []
    if see_machines:
        rows = session.execute(select(Equipment, Product.name).join(Product, Product.code == Equipment.product_code)
                               .where(Equipment.customer_code == code).order_by(Equipment.active.desc(), Equipment.serial))
        for eq, product in rows:
            if eq.custody_location != 'TAI_KHACH':
                state, tone = LOCATIONS.get(eq.custody_location, eq.custody_location), 'lam'
            elif eq.warranty_until and eq.warranty_until >= today:
                state, tone = 'BH đến ' + eq.warranty_until.strftime('%d/%m/%Y'), 'xong'
            else:
                state, tone = ('Hết BH' if eq.warranty_until else 'Không BH'), 'nhap'
            machines.append(dict(serial=eq.serial, name=product, state=state, tone=tone, active=eq.active,
                                 href=f'/thiet-bi/{eq.serial}' if nav.allowed(user, '/thiet-bi') else ''))

    debt = None
    if can_debt:
        rows = [r for r in owing.values() if r.remaining > 0]
        if rows:
            since = min(r.day or today for r in rows)
            total = int(sum(r.remaining for r in rows))
            debt = dict(total=total, since=since, days=(today - since).days, pending=int(sum(r.pending for r in rows)),
                        old=(today - since).days > OLD_DEBT_DAYS, over=total > (c.credit_limit if c.allow_debt else 0),
                        docs=sorted(rows, key=lambda r: r.day or today))

    open_docs = [x for x in docs if x['open']]
    count = lambda kind: sum(1 for x in docs if x['kind'] == kind)
    link = paging.linker(f'/khach-hang/{code}', {'tab': '' if tab == 'mo' else tab})
    tab_rows = {'mo': open_docs, 'bao-gia': [x for x in docs if x['kind'] == 'BG'], 'don-ban': [x for x in docs if x['kind'] == 'ĐB'],
                'dich-vu': [x for x in docs if x['kind'] == 'DV'], 'xong': [x for x in docs if not x['open']]}
    shown_tabs = [k for k in TABS if (k != 'bao-gia' or see_quotes) and (k != 'don-ban' or see_orders)
                  and (k != 'dich-vu' or see_services) and (k != 'may' or see_machines)]
    tab = tab if tab in shown_tabs else 'mo'
    counts = {k: (len(machines) if k == 'may' else len(tab_rows[k])) for k in shown_tabs}
    tabs = [dict(label=TABS[k], n=counts[k], href=link(tab='' if k == 'mo' else k, trang=''), on=k == tab) for k in shown_tabs]
    page, rows = paging.slice_rows(machines if tab == 'may' else tab_rows[tab], trang, so, {'tab': '' if tab == 'mo' else tab})

    tiles = [dict(label='Việc đang mở', n=len(open_docs), href=link(tab='', trang=''), on=tab == 'mo')]
    if see_quotes:
        tiles.append(dict(label='Báo giá chờ khách', n=sum(1 for x in open_docs if x['kind'] == 'BG'), href=link(tab='bao-gia', trang=''), on=tab == 'bao-gia'))
    if see_orders:
        n = sum(1 for x in open_docs if x['kind'] == 'ĐB')
        tiles.append(dict(label='Đơn chưa xong', n=n, href=link(tab='don-ban', trang=''), on=tab == 'don-ban', tone='cho' if n else ''))
    if see_services:
        tiles.append(dict(label='Phiếu dịch vụ đang mở', n=sum(1 for x in open_docs if x['kind'] == 'DV'), href=link(tab='dich-vu', trang=''), on=tab == 'dich-vu'))
    tiles.append(dict(label='Máy của khách', n=len(machines), href=link(tab='may', trang=''), on=tab == 'may'))
    if can_debt:
        tiles.append(dict(label='Còn nợ', n=money(debt['total']) if debt else '0', sub=('Nợ từ ' + debt['since'].strftime('%d/%m/%Y')) if debt else 'Không nợ',
                          href='#cong-no', on=False, tone='huy' if debt and (debt['old'] or debt['over']) else ''))

    quick = [(label, path) for label, path in (('+ Báo giá', '/bao-gia/moi'), ('+ Đơn bán', '/don-ban/moi'), ('+ <span class="kx-long">Nhận đơn dịch vụ</span><span class="kx-short">Dịch vụ</span>', '/dich-vu/moi'))
             if nav.allowed(user, path)] if c.status == 'DANG_DUNG' else []
    phones = [p.phone for p in c.phones if not p.is_contact]
    return render(request, 'khach_xem.jinja', c=c, phones=phones, contacts=c.contacts, sites=site_svc.search(session, code),
                  quick=quick, tiles=tiles, tabs=tabs, tab=tab, rows=rows, page=page, debt=debt, can_debt=can_debt,
                  can_write=has_role(user, *CUSTOMER_WRITERS), saved=saved, warnings=warnings,
                  open_count=len(open_docs))
