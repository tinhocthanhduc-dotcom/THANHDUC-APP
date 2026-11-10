"""Hộp xem nhanh phiếu (07/11/2026): bấm số phiếu ở danh sách mở hộp chỉ đọc, không chuyển trang.

GET /xem-nhanh?u=<đường dẫn trang phiếu> trả một mẩu HTML cho static/quick-view.js đặt vào <dialog>.
Quyền: phải mở được trang đầy đủ (nav.allowed) và qua đúng hàm load của nghiệp vụ (kiểm vai, phiếu của thợ...).
Không có nút thao tác tiền, kho; không hiện giá vốn. Lỗi quyền/không tìm thấy trả mã lỗi, JS chuyển sang trang như cũ.
"""
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import cash_docs, production, purchase_orders, quotes, sales_orders, sales_receipts, sales_returns, service_tickets, stock_docs, technician_returns
from ..db import get_session
from ..errors import BusinessError
from ..models import (Customer, Equipment, Product, PurchaseOrder, Quote, SalesOrder, SalesReceipt, ServiceTicket, StockDoc, Supplier, User)
from ..security import has_role
from . import money, nav, quantity, render, web_user

router = APIRouter()

MAX_LINES = 5
# Đường dẫn trang phiếu -> loại. Giữ khớp với PATTERNS trong static/quick-view.js.
PATTERNS = (
    (re.compile(r'^/(?:dich-vu|xem-dich-vu|quay-sua)/(\d+)$'), 'ticket'),
    (re.compile(r'^/don-ban/(\d+)$'), 'order'),
    (re.compile(r'^/bao-gia/(\d+)$'), 'quote'),
    (re.compile(r'^/don-mua/(\d+)$'), 'purchase'),
    (re.compile(r'^/phieu-kho/(\d+)$'), 'stock'),
    (re.compile(r'^/bao-thu/(\d+)$'), 'receipt'),
    (re.compile(r'^/thu-chi/(\d+)$'), 'cash'),
    (re.compile(r'^/tra-hang/(\d+)$'), 'return'),
    (re.compile(r'^/san-xuat/([A-Z]{2,4}-\d{2}-\d+)$'), 'production'),
    (re.compile(r'^/tho-tra-hang/(\d+)$'), 'tech_return'),
)
DONE = {'DA_DONG', 'HOAN_THANH', 'DA_NHAN', 'DA_GHI', 'DA_XAC_NHAN', 'DA_NHAP', 'CHAP_NHAN'}
CANCELLED = {'DA_HUY', 'TU_CHOI', 'KHONG_LAM_DUOC'}
GENERIC = {'NHAP': 'Nháp', 'DA_GHI': 'Đã ghi sổ', 'DA_HUY': 'Đã hủy', 'CHO_DUYET': 'Chờ duyệt', 'DA_NHAN': 'Đã nhận', 'TU_CHOI': 'Từ chối'}


def parse(path):
    path = (path or '').split('#')[0].split('?')[0]
    for pattern, kind in PATTERNS:
        m = pattern.match(path)
        if m:
            return kind, m.group(1), path
    return None, None, path


def _tone(status, done=None):
    if status in CANCELLED:
        return 'huy'
    if status in (done or DONE):
        return 'xong'
    if status in ('NHAP', 'MOI'):
        return 'nhap'
    if str(status).startswith('CHO'):
        return 'cho'
    return 'lam'


def _d(value):
    return value.strftime('%d/%m/%Y') if value else ''


def _dt(value):
    from .services import local_time
    return local_time(value) if value else ''


def _names(session, model, key, label, values):
    values = {v for v in values if v}
    if not values:
        return {}
    return dict(session.execute(select(getattr(model, key), getattr(model, label)).where(getattr(model, key).in_(values))).all())


def _users(session, *ids):
    return _names(session, User, 'id', 'full_name', ids)


def _customer(session, code):
    if not code:
        return ''
    c = session.get(Customer, code)
    if c is None:
        return code
    phone = c.phones[0].phone if c.phones else ''
    return f'{c.name} · {phone}' if phone else c.name


def _lines(session, rows):
    """rows: (mã hàng, mô tả, số lượng, thành tiền chữ). Trả (5 dòng đầu, câu 'và n dòng nữa')."""
    names = _names(session, Product, 'code', 'name', [r[0] for r in rows])
    out = [dict(name=(desc or names.get(code) or code or ''), qty=qty if isinstance(qty, str) else quantity(qty or 0), amount=amt)
           for code, desc, qty, amt in rows[:MAX_LINES]]
    more = f'và {len(rows) - MAX_LINES} dòng nữa · Mở đầy đủ để xem hết' if len(rows) > MAX_LINES else ''
    return out, more


def _link(user, path, label):
    return dict(no=label, href=path) if path and nav.allowed(user, path) else None


def _full(user, path):
    return path if nav.allowed(user, path) else None


def _pending(session, user, field, ident, b, total_label, total, base_rows=()):
    """Khung tiền khi đã có Báo thu chờ xác nhận (07/11/2026): tách tiền NV đang giữ khỏi phần khách còn phải trả.

    Chỉ đổi cách hiện; Còn nợ trên sổ vẫn = phải thu − đã xác nhận. Trả (dòng tiền, ghi chú, việc kế toán, nút xác nhận).
    """
    holders = sales_receipts.pending_holders(session, field, [ident]).get(ident, [])
    pending, remaining = int(b['pending']), int(b['remaining'])
    unpaid = max(0, remaining - pending)
    details = []
    for h in holders:
        when = _dt(h['at'])
        if h['method'] == 'TIEN_MAT':
            details.append(f"Tiền mặt · {h['name']} đang giữ" + (f' · báo {when}' if when else ''))
        else:
            details.append(f"Chuyển khoản chờ đối soát · {h['name']} báo" + (f' {when}' if when else ''))
    rows = [(total_label, total, ''), *base_rows, ('Kế toán đã xác nhận', money(int(b.get('paid', 0))), ''),
            ('Đã thu · chờ KT xác nhận', money(pending), 'wait', ' · '.join(details)),
            ('Khách còn phải trả', money(unpaid), 'due' if unpaid > 0 else 'ok')]
    note = f'Sổ công nợ vẫn ghi {money(remaining)} đến khi kế toán xác nhận.' if remaining else ''
    task = ''
    cash = [h for h in holders if h['method'] == 'TIEN_MAT']
    if len(holders) == 1 and cash:
        task = f"Nhận {money(cash[0]['amount'])} đ tiền mặt từ {cash[0]['name']} rồi xác nhận"
    elif holders and not cash:
        task = f'Đối soát chuyển khoản {money(pending)} đ rồi xác nhận'
    confirm = None
    if holders and has_role(user, 'finance', 'director') and nav.allowed(user, '/bao-thu'):
        first = holders[0]
        confirm = '/bao-thu?nguoi=' + (str(first['user_id']) if first['method'] == 'TIEN_MAT' else 'ck')
    return rows, note, task, confirm


def ticket(session, user, ident, path):
    from .customer_overview import _service_next
    from .services import KINDS, STATUSES
    t = service_tickets.load(session, user, int(ident))
    full = nav.ticket_url(user, t.id)
    if full is None:
        raise BusinessError('FORBIDDEN', 'Bạn không mở được phiếu dịch vụ này.', status=403)
    users = _users(session, t.assignee_id, t.created_by)
    machine = session.get(Equipment, t.serial) if t.serial else None
    model = session.get(Product, machine.product_code) if machine and machine.product_code else None
    kv = [('Khách', _customer(session, t.customer_code))]
    if machine or t.serial:
        kv.append(('Máy', ' · '.join(x for x in ((model.name if model else ''), (f'SN {t.serial}' if t.serial else '')) if x)))
    if t.symptom:
        kv.append(('Lỗi báo', t.symptom))
    who_when = ' · '.join(x for x in (users.get(t.assignee_id, ''), _dt(t.appointment_at)) if x)
    kv.append(('Thợ · hẹn', who_when or 'Chưa giao thợ'))
    if t.fix_done:
        kv.append(('Đã làm', t.fix_done))
    lines, more = _lines(session, [(l.product_code, l.description, l.qty, money((l.amount or 0) + (l.vat_amount or 0))) for l in t.lines])
    money_rows, note, task, confirm = [], '', '', None
    if t.receivable is not None and (has_role(user, 'finance', 'director', 'sales', 'dispatcher', 'reception')):
        b = sales_receipts.balance(t)
        if b.get('pending') and b['remaining'] > 0:
            money_rows, note, task, confirm = _pending(session, user, 'ticket_id', t.id, b, 'Tổng', money(t.total or 0))
        else:
            money_rows = [('Tổng', money(t.total or 0), ''), ('Đã thu', money(int(b.get('paid', 0))), '')]
            if b.get('pending'):
                money_rows.append(('Chờ xác nhận', money(int(b['pending'])), ''))
            money_rows.append(('Còn thu', money(int(b['remaining'])), 'due' if b['remaining'] > 0 else ''))
    who, title = _service_next(t, users, session)
    if task and who == 'kế toán':
        title = task
    links = []
    for o in session.execute(select(SalesOrder.id, SalesOrder.number).where(SalesOrder.service_ticket_id == t.id).order_by(SalesOrder.id)).all():
        links.append(_link(user, f'/don-ban/{o.id}', o.number))
    return dict(no=t.number, chip=STATUSES.get(t.status, t.status), tone=_tone(t.status, {'DA_DONG', 'HOAN_THANH'}),
                sub=f'Phiếu dịch vụ · {KINDS.get(t.kind, "Chưa chọn loại")} · tạo {_dt(t.created_at)}' + (f' bởi {users[t.created_by]}' if users.get(t.created_by) else ''),
                kv=kv, lines=lines, more=more, line_head='Vật tư · công', amt_head='Thành tiền', money=money_rows, money_note=note,
                who=who, next=title, links=[x for x in links if x], full_url=full, print_url=None, confirm_url=confirm)


def order(session, user, ident, path):
    from . import doc_frame
    from .orders import STATUSES
    o = sales_orders.load(session, user, int(ident))
    users = _users(session, o.delivery_assignee_id, o.created_by)
    kv = [('Khách', _customer(session, o.customer_code))]
    ways = {'TAI_QUAY': 'Tại quầy', 'KHO_GIAO': 'Kho giao', 'VAN_CHUYEN': 'Vận chuyển', 'THO_GIAO': 'Thợ giao'}
    how = ways.get(o.fulfillment, o.fulfillment or '')
    if o.fulfillment == 'THO_GIAO':
        how += ' · ' + (users.get(o.delivery_assignee_id) or 'chưa có thợ')
    kv.append(('Giao', how))
    if o.delivery_address:
        kv.append(('Địa chỉ', o.delivery_address))
    if o.promised_date:
        kv.append(('Hẹn giao', _d(o.promised_date)))
    lines, more = _lines(session, [(l.product_code, l.description, l.qty, money((l.amount or 0) + (l.vat_amount or 0))) for l in o.lines])
    money_rows, nxt, note, confirm = [('Tổng (gồm VAT)', money(o.total or 0), '')], None, '', None
    if o.status != 'NHAP':
        b = sales_receipts.balance(o)
        available = b.get('available', b['remaining'] - b['pending'])
        nxt = doc_frame.order_next(o, dict(balance=b), available)
        if b.get('pending') and b['remaining'] > 0:
            money_rows, note, task, confirm = _pending(session, user, 'order_id', o.id, b, 'Tổng (gồm VAT)', money(o.total or 0))
            if task and nxt['who'] == 'kế toán':
                nxt = dict(nxt, title=task)
        else:
            money_rows += [('Đã thu', money(int(b.get('paid', 0))), '')]
            if b.get('pending'):
                money_rows.append(('Chờ xác nhận', money(int(b['pending'])), ''))
            money_rows.append(('Còn nợ', money(int(b['remaining'])), 'due' if b['remaining'] > 0 else ''))
    else:
        nxt = dict(who='kinh doanh', title='Kiểm tra rồi chốt đơn')
    links = []
    if o.service_ticket_id:
        t = session.get(ServiceTicket, o.service_ticket_id)
        url = nav.ticket_url(user, o.service_ticket_id)
        if t and url:
            links.append(dict(no=t.number, href=url))
    if o.quote_id:
        q = session.get(Quote, o.quote_id)
        if q:
            links.append(_link(user, f'/bao-gia/{q.id}', q.number))
    return dict(no=o.number, chip=STATUSES.get(o.status, o.status), tone=_tone(o.status, {'DA_GIAO'}),
                sub=f'Đơn bán · {ways.get(o.fulfillment, "")} · tạo {_dt(o.created_at)}' + (f' bởi {users[o.created_by]}' if users.get(o.created_by) else ''),
                kv=kv, lines=lines, more=more, line_head='Hàng', amt_head='Thành tiền', money=money_rows, money_note=note,
                who=nxt['who'], next=nxt['title'], links=[x for x in links if x], full_url=_full(user, path), confirm_url=confirm,
                # Phiếu xuất kho bán hàng (10/11/2026): đơn đã chốt trở đi.
                print_url=_full(user, path + '/xuat-kho/in') if o.status not in ('NHAP', 'DA_HUY') else None, print_label='In phiếu xuất kho')


def quote(session, user, ident, path):
    from . import doc_frame
    from .quotes import STATUSES
    q = quotes.load(session, user, int(ident))
    users = _users(session, q.created_by)
    linked = session.execute(select(SalesOrder.id, SalesOrder.number).where(SalesOrder.quote_id == q.id, SalesOrder.status != 'DA_HUY').order_by(SalesOrder.id.desc()).limit(1)).first()
    kv = [('Khách', _customer(session, q.customer_code))]
    if q.contact_name:
        kv.append(('Người nhận', q.contact_name))
    if q.valid_until:
        kv.append(('Hiệu lực đến', _d(q.valid_until)))
    lines, more = _lines(session, [(l.product_code, l.description, l.qty, money((l.amount or 0) + (l.vat_amount or 0))) for l in q.lines])
    nxt = doc_frame.quote_next(q, linked)
    links = [_link(user, f'/don-ban/{linked.id}', linked.number)] if linked else []
    return dict(no=q.number, chip=STATUSES.get(q.status, q.status), tone=_tone(q.status), sub=f'Báo giá · tạo {_dt(q.created_at)}' + (f' bởi {users[q.created_by]}' if users.get(q.created_by) else ''),
                kv=kv, lines=lines, more=more, line_head='Hàng', amt_head='Thành tiền', money=[('Tổng (gồm VAT)', money(q.total or 0), '')],
                who=nxt['who'], next=nxt['title'], links=[x for x in links if x], full_url=_full(user, path), print_url=_full(user, path + '/in'))


def purchase(session, user, ident, path):
    from .purchases import STATUSES
    from .. import supplier_debts
    o = purchase_orders.load(session, user, int(ident))
    users = _users(session, o.created_by)
    sup = session.get(Supplier, o.supplier_code)
    kv = [('NCC', sup.name if sup else o.supplier_code)]
    kho = sorted({l.warehouse_code for l in o.lines if l.warehouse_code})
    if kho:
        kv.append(('Kho nhận', ', '.join(kho)))
    if o.expected_date:
        kv.append(('Hẹn giao', _d(o.expected_date)))
    lines, more = _lines(session, [(l.product_code, '', f'{quantity(l.received_qty or 0)}/{quantity(l.qty)}', money((l.amount or 0) + (l.vat_amount or 0))) for l in o.lines])
    left = sum(max(0, (l.qty or 0) - (l.received_qty or 0)) for l in o.lines)
    if o.status in ('DA_DAT', 'NHAN_MOT_PHAN'):
        who, title = 'thủ kho', f'Nhận nốt {quantity(left)} đơn vị hàng' if o.status == 'NHAN_MOT_PHAN' else 'Nhận hàng khi NCC giao'
    elif o.status == 'NHAP':
        who, title = 'thủ kho', 'Kiểm tra rồi đặt hàng'
    else:
        who, title = 'không ai', STATUSES.get(o.status, '')
    return dict(no=o.number, chip=STATUSES.get(o.status, o.status), tone=_tone(o.status), sub=f'Đơn mua · tạo {_dt(o.created_at)}' + (f' bởi {users[o.created_by]}' if users.get(o.created_by) else ''),
                kv=kv, lines=lines, more=more, line_head='Hàng · đã nhận/đặt', amt_head='Thành tiền', money=[('Tổng (gồm VAT)', money(o.total or 0), '')],
                who=who, next=title, links=[], full_url=_full(user, path), print_url=_full(user, path + '/in'))


def stock(session, user, ident, path):
    from .stock import KINDS
    if not nav.allowed(user, path):
        raise BusinessError('FORBIDDEN', 'Bạn không mở được phiếu kho.', status=403)
    doc = stock_docs.load(session, int(ident))
    users = _users(session, doc.created_by)
    where = doc.warehouse_code + (f' → {doc.to_warehouse_code}' if doc.to_warehouse_code else '')
    kv = [('Kho', where), ('Ngày', _d(doc.doc_date))]
    if doc.supplier_code:
        sup = session.get(Supplier, doc.supplier_code)
        kv.append(('NCC', sup.name if sup else doc.supplier_code))
    if doc.customer_code:
        kv.append(('Khách', _customer(session, doc.customer_code)))
    if doc.reason or doc.note:
        kv.append(('Ghi chú', doc.reason or doc.note))
    lines, more = _lines(session, [(l.product_code, '', l.qty, l.serial or '') for l in doc.lines])
    total = sum((l.qty or 0) for l in doc.lines)
    status = GENERIC.get(doc.status, doc.status)
    who, title = ('thủ kho', 'Kiểm tra rồi ghi sổ') if doc.status == 'NHAP' else ('giám đốc', 'Duyệt kiểm kê') if doc.status == 'CHO_DUYET' else ('không ai', 'Phiếu đã xong' if doc.status == 'DA_GHI' else 'Phiếu đã hủy')
    return dict(no=doc.number, chip=status, tone=_tone(doc.status), sub=f'Phiếu kho · {KINDS.get(doc.kind, doc.kind)} · tạo {_dt(doc.created_at)}' + (f' bởi {users[doc.created_by]}' if users.get(doc.created_by) else ''),
                kv=kv, lines=lines, more=more, line_head='Hàng', amt_head='Serial', money=[('Tổng số lượng', quantity(total), ''), ('Số dòng', str(len(doc.lines)), '')],
                who=who, next=title, links=[], full_url=path, print_url=_full(user, path + '/in'))


def receipt(session, user, ident, path):
    from .receipts import METHODS, STATUSES
    if not nav.allowed(user, path):
        raise BusinessError('FORBIDDEN', 'Bạn không mở được báo thu.', status=403)
    r = session.get(SalesReceipt, int(ident))
    if r is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy báo thu.', status=404)
    users = _users(session, r.reported_by, r.decided_by)
    parent, links, customer_code = None, [], ''
    if r.order_id:
        parent = session.get(SalesOrder, r.order_id)
        if parent:
            links.append(_link(user, f'/don-ban/{parent.id}', parent.number))
            customer_code = parent.customer_code
    elif r.ticket_id:
        parent = session.get(ServiceTicket, r.ticket_id)
        url = nav.ticket_url(user, r.ticket_id) if parent else None
        if parent:
            customer_code = parent.customer_code
            if url:
                links.append(dict(no=parent.number, href=url))
    kv = [('Khách', _customer(session, customer_code) if customer_code else 'Nợ đầu kỳ'), ('Cho', parent.number if parent else 'Nợ đầu kỳ'),
          ('Hình thức', METHODS.get(r.method, r.method)), ('Người báo', users.get(r.reported_by, '')), ('Ngày thu', _d(r.received_at))]
    if r.note:
        kv.append(('Ghi chú', r.note))
    money_rows = [('Số tiền', money(r.amount), '')]
    if parent is not None and getattr(parent, 'receivable', None) is not None:
        b = sales_receipts.balance(parent)
        money_rows.append(('Chứng từ còn nợ', money(int(b['remaining'])), 'due' if b['remaining'] > 0 else ''))
    if r.status == 'CHO_XAC_NHAN':
        who, title = 'kế toán', 'Kiểm tra tiền đã về rồi xác nhận'
    elif r.status == 'DA_XAC_NHAN':
        who, title = 'không ai', 'Đã xác nhận' + (f' bởi {users[r.decided_by]}' if users.get(r.decided_by) else '')
    else:
        who, title = 'không ai', STATUSES.get(r.status, r.status) + (f' · {r.reason}' if r.reason else '')
    return dict(no=r.number, chip=STATUSES.get(r.status, r.status), tone=_tone(r.status), sub=f'Báo thu · {METHODS.get(r.method, "")} · {_dt(r.reported_at)}',
                kv=kv, lines=[], more='', line_head='', amt_head='', money=money_rows, who=who, next=title, links=[x for x in links if x], full_url=path, print_url=None)


def cash(session, user, ident, path):
    doc = cash_docs.load(session, user, int(ident))
    users = _users(session, doc.created_by)
    kv = [('Ngày', _d(doc.doc_date)), ('Quỹ', doc.fund_code + (f' → {doc.to_fund_code}' if doc.to_fund_code else ''))]
    if doc.customer_code:
        kv.append(('Khách', _customer(session, doc.customer_code)))
    if doc.supplier_code:
        sup = session.get(Supplier, doc.supplier_code)
        kv.append(('NCC', sup.name if sup else doc.supplier_code))
    if doc.partner_name:
        kv.append(('Người nộp/nhận', doc.partner_name))
    if doc.description:
        kv.append(('Nội dung', doc.description))
    status = GENERIC.get(doc.status, doc.status)
    who, title = ('kế toán', 'Kiểm tra rồi ghi sổ') if doc.status == 'NHAP' else ('không ai', 'Đã ghi sổ quỹ' if doc.status == 'DA_GHI' else 'Phiếu đã hủy')
    return dict(no=doc.number, chip=status, tone=_tone(doc.status), sub=f'{cash_docs.KINDS.get(doc.kind, doc.kind)} · tạo bởi {users.get(doc.created_by, "")}',
                kv=kv, lines=[], more='', line_head='', amt_head='', money=[('Số tiền', money(doc.amount), '')], who=who, next=title, links=[],
                full_url=_full(user, path), print_url=_full(user, path + '/in'))


def sales_return(session, user, ident, path):
    if not nav.allowed(user, path):
        raise BusinessError('FORBIDDEN', 'Bạn không mở được phiếu trả hàng.', status=403)
    r = sales_returns.load(session, user, int(ident))
    o = session.get(SalesOrder, r.order_id)
    users = _users(session, r.created_by)
    codes = {}
    if o:
        codes = {l.line_no: l.product_code for l in o.lines}
    kv = [('Khách', _customer(session, o.customer_code) if o else ''), ('Ngày trả', _dt(r.returned_at)), ('Lý do', r.reason or '')]
    lines, more = _lines(session, [(codes.get(l.order_line_no), '', l.qty, money((l.amount or 0) + (l.vat_amount or 0))) for l in r.lines])
    links = [_link(user, f'/don-ban/{o.id}', o.number)] if o else []
    return dict(no=r.number, chip=GENERIC.get(r.status, r.status), tone=_tone(r.status), sub='Trả hàng' + (f' · tạo bởi {users[r.created_by]}' if users.get(r.created_by) else ''),
                kv=kv, lines=lines, more=more, line_head='Hàng', amt_head='Thành tiền', money=[('Tổng trả (gồm VAT)', money(r.total or 0), '')],
                who='không ai' if r.status != 'DA_HUY' else 'không ai', next='Hàng đã về kho' if r.status == 'DA_NHAN' else 'Phiếu đã hủy', links=[x for x in links if x],
                full_url=path, print_url=_full(user, path + '/in'))


def production_order(session, user, ident, path):
    if not nav.allowed(user, path):
        raise BusinessError('FORBIDDEN', 'Bạn không mở được phiếu sản xuất.', status=403)
    row = production.load(session, user, ident)
    users = _users(session, row.technician_id, row.created_by)
    p = session.get(Product, row.product_code)
    kv = [('Loại', production.KINDS.get(row.kind, row.kind)), ('Sản phẩm', p.name if p else row.product_code), ('Thợ', users.get(row.technician_id, 'Chưa giao')),
          ('Kho', ' → '.join(x for x in (row.from_warehouse_code, row.to_warehouse_code) if x))]
    if row.note:
        kv.append(('Ghi chú', row.note))
    nexts = {'MOI': ('thợ', 'Bắt đầu làm'), 'DANG_LAM': ('thợ', 'Làm xong, báo số đạt/hỏng'), 'HOAN_THANH': ('thủ kho', 'Nhập kho thành phẩm')}
    who, title = nexts.get(row.status, ('không ai', production.STATUSES.get(row.status, '')))
    return dict(no=row.number, chip=production.STATUSES.get(row.status, row.status), tone=_tone(row.status), sub=f'Phiếu sản xuất · tạo {_dt(row.created_at)}',
                kv=kv, lines=[], more='', line_head='', amt_head='',
                money=[('Kế hoạch', quantity(row.qty_plan or 0), ''), ('Đạt', quantity(row.qty_good or 0), ''), ('Hỏng', quantity(row.qty_scrap or 0), '')],
                who=who, next=title, links=[], full_url=path, print_url=None)


def tech_return(session, user, ident, path):
    if not nav.allowed(user, path):
        raise BusinessError('FORBIDDEN', 'Bạn không mở được yêu cầu trả kho.', status=403)
    row = technician_returns.load(session, user, int(ident))
    users = _users(session, row.technician_id, row.received_by)
    kv = [('Thợ', users.get(row.technician_id, '')), ('Kho', ' → '.join(x for x in (row.warehouse_code, row.to_warehouse_code) if x)), ('Tạo lúc', _dt(row.created_at))]
    if row.note:
        kv.append(('Ghi chú', row.note))
    lines, more = _lines(session, [(l.product_code, '', l.qty, 'Hư' if l.condition and l.condition != 'TOT' else 'Tốt') for l in row.lines])
    links = []
    if row.ticket_id:
        t = session.get(ServiceTicket, row.ticket_id)
        url = nav.ticket_url(user, row.ticket_id)
        if t and url:
            links.append(dict(no=t.number, href=url))
    who, title = ('thủ kho', 'Nhận hàng thợ trả') if row.status == 'CHO_NHAN' else ('không ai', technician_returns.STATUSES.get(row.status, ''))
    return dict(no=row.number, chip=technician_returns.STATUSES.get(row.status, row.status), tone=_tone(row.status), sub='Thợ trả hàng',
                kv=kv, lines=lines, more=more, line_head='Hàng', amt_head='Tình trạng', money=[('Tổng số lượng', quantity(sum((l.qty or 0) for l in row.lines)), '')],
                who=who, next=title, links=links, full_url=path, print_url=None)


BUILDERS = dict(ticket=ticket, order=order, quote=quote, purchase=purchase, stock=stock, receipt=receipt, cash=cash,
                **{'return': sales_return}, production=production_order, tech_return=tech_return)


@router.get('/xem-nhanh')
def view(request: Request, u: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    kind, ident, path = parse(u)
    if kind is None:
        raise BusinessError('NOT_FOUND', 'Không có xem nhanh cho trang này.', status=404)
    d = BUILDERS[kind](session, user, ident, path)
    return render(request, 'xem_nhanh.jinja', d=d)
