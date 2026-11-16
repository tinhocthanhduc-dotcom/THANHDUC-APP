"""Chi phí phát sinh nội bộ theo đơn (TUẤN 30/10/2026 mẫu DuHangChiPhi; mở rộng 16/11/2026 mẫu 3jpHcVYJarVVHsxfm461sD).

Ghi trên phiếu dịch vụ (mọi loại) hoặc đơn bán (gắn lần giao): linh kiện mua ngoài không nhập kho, trả đối tác sửa,
phí gửi hàng, xe chở máy, gửi xe... Giá báo khách và công nợ khách không đổi. Khoản đã duyệt cộng vào giá vốn đơn
(cột cost, chưa VAT khi có hóa đơn), nên lãi gộp, hoa hồng và Lãi ròng ra đúng. Kế toán duyệt trong hạn mức CHI_PHI,
trên mức giám đốc duyệt; không ai duyệt khoản mình ghi. Kế toán chi bằng một phiếu chi đã ghi sổ cho người ứng hoặc
đối tác (danh mục ghép DANH_MUC_CHI_PHI_THO); phiếu chi này không tính lại vào chi phí hoạt động của Lãi ròng.
"""
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, or_, select

from . import approval_limits, audit, cash_docs, cash_posting, doc_numbers, finance_mappings, service_tickets as svc
from .errors import BusinessError
from .models import CashDoc, Customer, SalesDelivery, SalesOrder, ServiceExpense, ServiceStep, ServiceTicket, User
from .schemas import CashDocIn
from .security import has_role
from .stock_docs import _transaction

KINDS = {'LINH_KIEN': 'Mua linh kiện ngoài', 'DOI_TAC': 'Trả đối tác sửa', 'GUI_HANG': 'Phí gửi hàng', 'CHO_MAY': 'Xe chở máy',
         'GUI_XE': 'Gửi xe', 'KHAC': 'Khác'}
PAYERS = {'NV_UNG': 'Tôi ứng tiền', 'DOI_TAC': 'Công ty trả đối tác sau'}
VAT_RATES = (0, 8, 10)
STATUSES = {'CHO_DUYET': 'Chờ duyệt', 'DA_DUYET': 'Đã duyệt · chờ chi', 'DA_HOAN': 'Đã chi', 'TU_CHOI': 'Từ chối', 'DA_HUY': 'Đã xóa'}
COUNTED = ('DA_DUYET', 'DA_HOAN')  # tính vào giá vốn đơn
TECH_OPEN = ('DA_NHAN', 'DA_HEN', 'DANG_LAM', 'CHO_LINH_KIEN', 'TAM_DUNG')  # thợ: từ lúc nhận việc
OPEN_WORK = ('DANG_LAM', 'CHO_LINH_KIEN', 'TAM_DUNG')  # màn thợ hiện form sau khi đã đến khách (màn hẹn/trả máy giữ gọn)
TICKET_DONE = ('HOAN_THANH', 'DA_DONG', 'KHONG_LAM_DUOC')
LATE_DAYS = 7  # sau khi hoàn thành: người ghi khác kế toán/GĐ chỉ ghi được trong 7 ngày
OFFICE_TICKET = ('dispatcher', 'warehouse', 'finance')
OFFICE_ORDER = ('dispatcher', 'warehouse', 'finance')
MAX_AMOUNT = 100_000_000


def _now():
    return datetime.now(UTC)


def snapshot(row):
    return {k: getattr(row, k) for k in ('id', 'ticket_id', 'order_id', 'delivery_id', 'technician_id', 'kind', 'description', 'place',
                                         'payer', 'amount', 'vat_rate', 'cost', 'note', 'status', 'decision_note', 'cash_doc_id', 'version')}


def check_finance(user):
    if not has_role(user, 'finance'):
        raise BusinessError('FORBIDDEN', 'Chỉ kế toán hoặc giám đốc được duyệt và chi chi phí phát sinh.', status=403)


def _amount(value):
    text = str(value or '').strip().replace('.', '').replace(' ', '')
    if not text.isascii() or not text.isdigit() or len(text) > 12:
        raise BusinessError('BAD_AMOUNT', 'Nhập số tiền đã trả, số nguyên đồng (có thể dùng dấu chấm hàng nghìn).')
    amount = int(text)
    if amount <= 0 or amount > MAX_AMOUNT:
        raise BusinessError('BAD_AMOUNT', 'Số tiền phải lớn hơn 0 và không quá 100.000.000 đồng.')
    return amount


def net_cost(amount, vat_rate):
    """Giá vốn tính vào đơn: có hóa đơn VAT (khấu trừ) thì bỏ VAT, làm tròn đồng."""
    if not vat_rate:
        return int(amount)
    return int((Decimal(amount) * 100 / (100 + int(vat_rate))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _vat(value):
    try:
        rate = int(str(value or '0').strip().rstrip('%') or 0)
    except ValueError:
        rate = -1
    if rate not in VAT_RATES:
        raise BusinessError('BAD_VAT', 'Hóa đơn VAT chọn Không có, 8% hoặc 10%.')
    return rate


def _load(session, ident, version=None, lock=True):
    q = select(ServiceExpense).where(ServiceExpense.id == ident)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(q)
    if row is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy khoản chi phí.', status=404)
    if version is not None and row.version != version:
        raise BusinessError('VERSION_CONFLICT', 'Khoản chi phí vừa được cập nhật, hãy tải lại.', {'current': row.version}, status=409)
    return row


def _record(session, user, row, action, before):
    session.flush()
    audit.record(session, user, 'service_expense.' + action, 'service_expense', str(row.id), before, snapshot(row))


def for_ticket(session, ticket_id):
    return list(session.scalars(select(ServiceExpense).where(ServiceExpense.ticket_id == ticket_id, ServiceExpense.status != 'DA_HUY')
                                .order_by(ServiceExpense.id)))


def for_order(session, order_id):
    return list(session.scalars(select(ServiceExpense).where(ServiceExpense.order_id == order_id, ServiceExpense.status != 'DA_HUY')
                                .order_by(ServiceExpense.id)))


def counted_total(session, ticket_id):
    return int(session.scalar(select(func.coalesce(func.sum(ServiceExpense.cost), 0)).where(
        ServiceExpense.ticket_id == ticket_id, ServiceExpense.status.in_(COUNTED))))


def ticket_done_at(session, t):
    if t.status not in TICKET_DONE:
        return None
    return session.scalar(select(func.max(ServiceStep.at)).where(ServiceStep.ticket_id == t.id, ServiceStep.to_status.in_(TICKET_DONE)))


def order_done_at(session, order):
    if order.status != 'DA_GIAO':
        return None
    return session.scalar(select(func.max(SalesDelivery.delivered_at)).where(SalesDelivery.order_id == order.id, SalesDelivery.status == 'DA_GIAO'))


def _late(done_at):
    return done_at is not None and _now() - done_at > timedelta(days=LATE_DAYS)


def _check_late(user, done_at, note, what):
    if not _late(done_at):
        return
    if not has_role(user, 'finance'):
        raise BusinessError('LATE', f'{what} đã hoàn thành quá {LATE_DAYS} ngày: nhờ kế toán ghi khoản này.', status=409)
    if len(note) < 3:
        raise BusinessError('NOTE_REQUIRED', f'{what} đã hoàn thành quá {LATE_DAYS} ngày: ghi lý do ghi muộn.')


def ticket_can_add(session, user, t, done_at=None):
    """Hiện nút/biểu mẫu trên phiếu (không thay kiểm tra ở create)."""
    if t.status in ('MOI', 'DA_HUY'):
        return False
    tech = 'technician' in user.roles and t.assignee_id == user.id
    if has_role(user, 'finance'):
        return True
    if not (tech or has_role(user, *OFFICE_TICKET)):
        return False
    if tech and not has_role(user, *OFFICE_TICKET) and t.status not in TECH_OPEN + TICKET_DONE:
        return False
    return not _late(done_at if done_at is not None else ticket_done_at(session, t))


def _order_tech(session, user, order):
    if 'technician' not in user.roles:
        return False
    return order.delivery_assignee_id == user.id or bool(session.scalar(select(SalesDelivery.id).where(
        SalesDelivery.order_id == order.id, SalesDelivery.delivered_by == user.id, SalesDelivery.status == 'DA_GIAO').limit(1)))


def _order_office(user, order):
    return has_role(user, *OFFICE_ORDER) or (has_role(user, 'sales') and order.created_by == user.id)


def order_can_add(session, user, order):
    if order.status in ('NHAP', 'DA_HUY'):
        return False
    if has_role(user, 'finance'):
        return True
    if not (_order_office(user, order) or _order_tech(session, user, order)):
        return False
    return not _late(order_done_at(session, order))


def deliveries(session, order_id):
    return list(session.scalars(select(SalesDelivery).where(SalesDelivery.order_id == order_id, SalesDelivery.status == 'DA_GIAO')
                                .order_by(SalesDelivery.id)))


def _pick_delivery(session, user, order, delivery_id, tech):
    if delivery_id not in (None, ''):
        try:
            delivery_id = int(delivery_id)
        except (TypeError, ValueError):
            raise BusinessError('BAD_DELIVERY', 'Lần giao không hợp lệ.')
        row = session.get(SalesDelivery, delivery_id)
        if row is None or row.order_id != order.id or row.status != 'DA_GIAO':
            raise BusinessError('BAD_DELIVERY', 'Chọn một lần giao đã giao của đơn này.')
        return row.id
    rows = deliveries(session, order.id)
    if tech:
        own = [d for d in rows if d.delivered_by == user.id]
        rows = own or rows
    return rows[-1].id if rows else None  # đơn chưa giao: tính vào lần giao đầu tiên


def create(session, user, ticket_id, kind, description, place='', amount='', photo=None, *, payer='NV_UNG', vat_rate=0, note='',
           order_id=None, delivery_id=None):
    """Ghi một khoản trên phiếu dịch vụ (ticket_id) hoặc đơn bán (order_id). photo = (tên tệp, bytes) ảnh hóa đơn."""
    description, place, note = (description or '').strip(), (place or '').strip(), (note or '').strip()
    if kind not in KINDS:
        raise BusinessError('KIND_REQUIRED', 'Chọn loại chi phí.')
    if payer not in PAYERS:
        raise BusinessError('PAYER_REQUIRED', 'Chọn ai trả: tôi ứng tiền hay công ty trả đối tác sau.')
    if kind != 'GUI_XE' and len(description) < 2:
        raise BusinessError('DESCRIPTION_REQUIRED', 'Ghi mua gì / chi cho việc gì.')
    if payer == 'DOI_TAC' and len(place) < 2:
        raise BusinessError('PARTNER_REQUIRED', 'Công ty trả đối tác sau: ghi tên đối tác ở ô Nơi mua / đối tác.')
    if len(description) > 500 or len(place) > 300 or len(note) > 500:
        raise BusinessError('TEXT_TOO_LONG', 'Nội dung tối đa 500 ký tự, nơi mua tối đa 300 ký tự.')
    amount, vat_rate = _amount(amount), _vat(vat_rate)
    if (ticket_id is None) == (order_id is None):
        raise BusinessError('BAD_TARGET', 'Chi phí phải thuộc một phiếu dịch vụ hoặc một đơn bán.')
    saved = None
    try:
        with _transaction(session):
            t = order = None
            if ticket_id is not None:
                t = svc._get(session, user, ticket_id, lock=True)
                tech = 'technician' in user.roles and t.assignee_id == user.id
                office = has_role(user, *OFFICE_TICKET)
                if not (tech or office):
                    raise BusinessError('FORBIDDEN', 'Chỉ thợ được giao, điều phối, kho hoặc kế toán ghi chi phí phát sinh của phiếu.', status=403)
                if t.status in ('MOI', 'DA_HUY') or (not office and t.status not in TECH_OPEN + TICKET_DONE):
                    raise BusinessError('BAD_STATUS', 'Ghi chi phí phát sinh sau khi đã nhận việc; phiếu mới hoặc đã hủy không ghi được.', status=409)
                _check_late(user, ticket_done_at(session, t), note, 'Phiếu')
                row = ServiceExpense(ticket_id=t.id)
            else:
                from . import sales_orders
                order = sales_orders._load(session, order_id, lock=True)
                tech = _order_tech(session, user, order)
                if not (tech or _order_office(user, order)):
                    raise BusinessError('FORBIDDEN', 'Chỉ người lập đơn, thợ giao, điều phối, kho hoặc kế toán ghi chi phí của đơn.', status=403)
                if order.status in ('NHAP', 'DA_HUY'):
                    raise BusinessError('BAD_STATUS', 'Đơn nháp hoặc đã hủy không ghi chi phí phát sinh.', status=409)
                _check_late(user, order_done_at(session, order), note, 'Đơn')
                row = ServiceExpense(order_id=order.id, delivery_id=_pick_delivery(session, user, order, delivery_id, tech))
            row.technician_id, row.kind, row.description, row.place = user.id, kind, description or KINDS[kind], place
            row.payer, row.amount, row.vat_rate, row.cost, row.note, row.status = payer, amount, vat_rate, net_cost(amount, vat_rate), note, 'CHO_DUYET'
            session.add(row)
            session.flush()
            if photo:
                from . import attachments
                saved = attachments.add(session, user, 'service_expense', str(row.id), 'KHAC', photo[0], photo[1], _in_tx=True)
            if t is not None and tech:  # khoản văn phòng ghi (vd trả đối tác) không hiện trong lịch sử phiếu thợ xem
                before = svc.snapshot(t)
                svc._record(session, user, t, 'expense', before, t.status, f'{KINDS[kind]}: {row.description} · {amount:,} đồng'.replace(',', '.'))
            _record(session, user, row, 'create', None)
    except Exception:
        if saved is not None:  # ảnh đã ghi ra đĩa mà transaction lỗi: xóa, không để tệp mồ côi
            from pathlib import Path
            from .config import get_settings
            (Path(get_settings().files_dir) / saved.path).unlink(missing_ok=True)
        raise
    return row


def cancel(session, user, ident, version):
    with _transaction(session):
        row = _load(session, ident, version)
        if row.technician_id != user.id:
            raise BusinessError('FORBIDDEN', 'Chỉ người đã ghi mới xóa được khoản này.', status=403)
        if row.status != 'CHO_DUYET':
            raise BusinessError('BAD_STATUS', 'Khoản đã được kế toán xử lý, không xóa được.', status=409)
        before = snapshot(row)
        row.status = 'DA_HUY'
        _record(session, user, row, 'cancel', before)
    return row


def decide(session, user, ident, version, approve, note=''):
    check_finance(user)
    note = (note or '').strip()
    if not approve and len(note) < 3:
        raise BusinessError('REASON_REQUIRED', 'Ghi lý do từ chối.')
    with _transaction(session):
        row = _load(session, ident, version)
        if row.status != 'CHO_DUYET':
            raise BusinessError('BAD_STATUS', 'Khoản này không còn chờ duyệt.', status=409)
        # Kế toán duyệt tới hạn mức CHI_PHI (theo giá vốn tính), trên mức hoặc khoản mình ghi thì giám đốc duyệt.
        approval_limits.require(session, user, 'CHI_PHI', row.cost, row.technician_id)
        before = snapshot(row)
        row.status, row.decided_by, row.decided_at, row.decision_note = ('DA_DUYET' if approve else 'TU_CHOI'), user.id, _now(), note
        _record(session, user, row, 'approve' if approve else 'reject', before)
    return row


def _payee(row):
    return ('DOI_TAC', ' '.join(row.place.split()).casefold()) if row.payer == 'DOI_TAC' else ('NV_UNG', row.technician_id)


def _numbers(session, rows):
    tickets = dict(session.execute(select(ServiceTicket.id, ServiceTicket.number).where(
        ServiceTicket.id.in_({r.ticket_id for r in rows if r.ticket_id}))).all())
    orders = dict(session.execute(select(SalesOrder.id, SalesOrder.number).where(
        SalesOrder.id.in_({r.order_id for r in rows if r.order_id}))).all())
    return {r.id: tickets.get(r.ticket_id) or orders.get(r.order_id) or '' for r in rows}


def pay(session, user, ids, fund_code, pay_date):
    """Chi một lần cho nhiều khoản đã duyệt của cùng một người nhận (người ứng hoặc đối tác): một phiếu chi đã ghi sổ."""
    check_finance(user)
    try:
        ids = sorted({int(i) for i in ids})
    except (TypeError, ValueError):
        raise BusinessError('BAD_LINES', 'Chọn các khoản cần chi.')
    if not ids:
        raise BusinessError('BAD_LINES', 'Chọn các khoản cần chi.')
    if not fund_code:
        raise BusinessError('BAD_FUND', 'Chọn quỹ chi.')
    if isinstance(pay_date, str):
        try:
            pay_date = date.fromisoformat(pay_date)
        except ValueError:
            raise BusinessError('BAD_DATE', 'Ngày chi không hợp lệ.')
    with _transaction(session):
        from . import day_closings
        day_closings.lock_days(session, [pay_date])
        doc_numbers.lock_counter(session, 'PC', pay_date)
        rows = list(session.scalars(select(ServiceExpense).where(ServiceExpense.id.in_(ids)).order_by(ServiceExpense.id)
                                    .with_for_update().execution_options(populate_existing=True)))
        if len(rows) != len(ids):
            raise BusinessError('NOT_FOUND', 'Không tìm thấy khoản chi phí.', status=404)
        if any(r.status != 'DA_DUYET' for r in rows):
            raise BusinessError('BAD_STATUS', 'Chỉ chi các khoản đã duyệt, chưa chi.', status=409)
        if len({_payee(r) for r in rows}) != 1:
            raise BusinessError('MIXED_TECHNICIANS', 'Mỗi phiếu chi cho một người nhận (một người ứng hoặc một đối tác).')
        category = finance_mappings.require(session, 'DANH_MUC_CHI_PHI_THO')
        numbers = _numbers(session, rows)
        detail = '; '.join(f'{numbers[r.id]} {r.description} {r.amount:,}'.replace(',', '.') for r in rows)
        if rows[0].payer == 'DOI_TAC':
            name, head = rows[0].place, f'Trả đối tác {rows[0].place} (chi phí phát sinh): '
        else:
            name = session.scalar(select(User.full_name).where(User.id == rows[0].technician_id))
            head = 'Hoàn chi phí phát sinh cho ' + ('thợ: ' if rows[0].ticket_id else f'{name}: ')
        doc = cash_docs.insert(session, user, CashDocIn(kind='PC', doc_date=pay_date, fund_code=fund_code, category_code=category,
            partner_name=name, description=(head + detail)[:2000], amount=sum(r.amount for r in rows),
            source_type='SERVICE_EXPENSE', source_id=str(rows[0].id)))
        cash_posting.post_in_tx(session, user, doc.id, doc.version)
        stamp = _now()
        for r in rows:
            before = snapshot(r)
            r.status, r.cash_doc_id, r.paid_by, r.paid_at = 'DA_HOAN', doc.id, user.id, stamp
            _record(session, user, r, 'pay', before)
    return doc


def listing(session, user, status):
    """Danh sách cho màn kế toán: dict(row, technician, ticket(số đơn), href, customer, cash, payee, over_limit)."""
    check_finance(user)
    q = (select(ServiceExpense, User.full_name, ServiceTicket.number, SalesOrder.number, Customer.name, CashDoc.number)
         .join(User, User.id == ServiceExpense.technician_id)
         .outerjoin(ServiceTicket, ServiceTicket.id == ServiceExpense.ticket_id)
         .outerjoin(SalesOrder, SalesOrder.id == ServiceExpense.order_id)
         .join(Customer, Customer.code == func.coalesce(ServiceTicket.customer_code, SalesOrder.customer_code))
         .outerjoin(CashDoc, CashDoc.id == ServiceExpense.cash_doc_id)
         .where(ServiceExpense.status == status))
    order = (ServiceExpense.payer, User.full_name, ServiceExpense.place, ServiceExpense.id) if status in ('CHO_DUYET', 'DA_DUYET') else (ServiceExpense.id.desc(),)
    table = approval_limits.limits(session)
    director = has_role(user, 'director')
    out = []
    for r, tech, ticket_no, order_no, customer, cash in session.execute(q.order_by(*order)):
        out.append(dict(row=r, technician=tech, ticket=ticket_no or order_no, customer=customer, cash=cash,
                        href=f'/dich-vu/{r.ticket_id}' if r.ticket_id else f'/don-ban/{r.order_id}',
                        payee=r.place if r.payer == 'DOI_TAC' else tech,
                        over_limit=status == 'CHO_DUYET' and not director and not approval_limits.can_decide(session, user, 'CHI_PHI', r.cost, r.technician_id, table)))
    return out


def counts(session):
    return dict(session.execute(select(ServiceExpense.status, func.count()).group_by(ServiceExpense.status)).all())


# ---------- Lãi đơn (chỉ GĐ/KT xem) ----------

def _profit(session, kinds_ids, start, extra_pending):
    from . import purchase_reports
    from .web.lists import _today
    documents, costs = purchase_reports._margin_sources(start, _today())
    conds = [(documents.c.kind == k) & (documents.c.id.in_(ids)) for k, ids in kinds_ids if ids]
    if not conds:
        return None
    docs = select(documents.c.kind, documents.c.id, documents.c.revenue).where(or_(*conds)).subquery()
    revenue = int(session.scalar(select(func.coalesce(func.sum(docs.c.revenue), 0))) or 0)
    cost_rows = session.execute(select(costs.c.product_code.is_(None), func.coalesce(func.sum(costs.c.cost), 0))
                                .join(docs, (docs.c.kind == costs.c.kind) & (docs.c.id == costs.c.id))
                                .group_by(costs.c.product_code.is_(None))).all()
    by = {bool(k): int(v) for k, v in cost_rows}
    # Dòng chi phí phát sinh không gắn mã hàng (product_code null); giá vốn hàng từ sổ kho có mã.
    goods, expenses = by.get(False, 0), by.get(True, 0)
    profit = revenue - goods - expenses
    return dict(revenue=revenue, goods=goods, expenses=expenses, profit=profit,
                pct=round(profit * 100 / revenue, 1) if revenue else None, pending=extra_pending)


def _pending(session, col, ident):
    return int(session.scalar(select(func.coalesce(func.sum(ServiceExpense.cost), 0)).where(col == ident, ServiceExpense.status == 'CHO_DUYET')) or 0)


def order_profit(session, user, order):
    if not has_role(user, 'finance'):
        return None
    from .models import SalesReturn
    gh = [d.id for d in deliveries(session, order.id)]
    th = list(session.scalars(select(SalesReturn.id).where(SalesReturn.order_id == order.id, SalesReturn.status == 'DA_NHAN')))
    stamps = [d.delivered_at for d in deliveries(session, order.id)]
    stamps += list(session.scalars(select(SalesReturn.returned_at).where(SalesReturn.id.in_(th)))) if th else []
    if not stamps:
        return dict(revenue=0, goods=0, expenses=0, profit=0, pct=None, pending=_pending(session, ServiceExpense.order_id, order.id))
    start = min(stamps).astimezone(svc_tz()).date() - timedelta(days=1)
    return _profit(session, [('GH', gh), ('TH', th)], start, _pending(session, ServiceExpense.order_id, order.id))


def ticket_profit(session, user, t):
    if not has_role(user, 'finance') or t.status not in ('HOAN_THANH', 'DA_DONG') or t.priced_at is None:
        return None
    start = t.priced_at.astimezone(svc_tz()).date() - timedelta(days=1)
    return _profit(session, [('DV', [t.id])], start, _pending(session, ServiceExpense.ticket_id, t.id))


def svc_tz():
    from zoneinfo import ZoneInfo
    return ZoneInfo('Asia/Ho_Chi_Minh')
