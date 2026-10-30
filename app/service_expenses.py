"""Chi phí phát sinh theo phiếu dịch vụ (TUẤN 30/10/2026, mẫu DuHangChiPhi).

Thợ tự bỏ tiền mua ngoài (linh kiện không nhập kho, gửi xe, xe chở máy...) ghi theo phiếu sau khi đã đến khách.
Kế toán duyệt từng khoản; khoản đã duyệt tính vào giá vốn phiếu (lãi gộp dịch vụ). Hoàn tiền gộp theo thợ
bằng một phiếu chi đã ghi sổ (danh mục ghép DANH_MUC_CHI_PHI_THO). Không đụng kho, không đụng công nợ khách.
"""
from datetime import UTC, date, datetime

from sqlalchemy import func, select

from . import audit, cash_docs, cash_posting, doc_numbers, finance_mappings, service_tickets as svc
from .errors import BusinessError
from .models import CashDoc, Customer, ServiceExpense, ServiceTicket, User
from .schemas import CashDocIn
from .security import has_role
from .stock_docs import _transaction

KINDS = {'LINH_KIEN': 'Mua linh kiện ngoài', 'GUI_XE': 'Gửi xe', 'CHO_MAY': 'Xe chở máy', 'KHAC': 'Khác'}
STATUSES = {'CHO_DUYET': 'Chờ duyệt', 'DA_DUYET': 'Đã duyệt · chờ hoàn', 'DA_HOAN': 'Đã hoàn tiền', 'TU_CHOI': 'Từ chối', 'DA_HUY': 'Đã xóa'}
COUNTED = ('DA_DUYET', 'DA_HOAN')  # tính vào giá vốn phiếu
OPEN_WORK = ('DANG_LAM', 'CHO_LINH_KIEN', 'TAM_DUNG')  # sau khi đã đến khách
MAX_AMOUNT = 100_000_000


def _now():
    return datetime.now(UTC)


def snapshot(row):
    return {k: getattr(row, k) for k in ('id', 'ticket_id', 'technician_id', 'kind', 'description', 'place', 'amount', 'status',
                                         'decision_note', 'cash_doc_id', 'version')}


def check_finance(user):
    if not has_role(user, 'finance'):
        raise BusinessError('FORBIDDEN', 'Chỉ kế toán hoặc giám đốc được duyệt và hoàn chi phí thợ.', status=403)


def _amount(value):
    text = str(value or '').strip().replace('.', '').replace(' ', '')
    if not text.isascii() or not text.isdigit() or len(text) > 12:
        raise BusinessError('BAD_AMOUNT', 'Nhập số tiền đã trả, số nguyên đồng (có thể dùng dấu chấm hàng nghìn).')
    amount = int(text)
    if amount <= 0 or amount > MAX_AMOUNT:
        raise BusinessError('BAD_AMOUNT', 'Số tiền phải lớn hơn 0 và không quá 100.000.000 đồng.')
    return amount


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


def counted_total(session, ticket_id):
    return int(session.scalar(select(func.coalesce(func.sum(ServiceExpense.amount), 0)).where(
        ServiceExpense.ticket_id == ticket_id, ServiceExpense.status.in_(COUNTED))))


def create(session, user, ticket_id, kind, description, place='', amount='', photo=None):
    """Thợ được giao ghi một khoản. photo = (tên tệp, bytes) ảnh hóa đơn, không bắt buộc."""
    description, place = (description or '').strip(), (place or '').strip()
    if kind not in KINDS:
        raise BusinessError('KIND_REQUIRED', 'Chọn loại chi phí.')
    if kind != 'GUI_XE' and len(description) < 2:
        raise BusinessError('DESCRIPTION_REQUIRED', 'Ghi mua gì / chi cho việc gì.')
    if len(description) > 500 or len(place) > 300:
        raise BusinessError('TEXT_TOO_LONG', 'Nội dung tối đa 500 ký tự, nơi mua tối đa 300 ký tự.')
    amount = _amount(amount)
    saved = None
    try:
        with _transaction(session):
            t = svc._get(session, user, ticket_id, lock=True)
            if 'technician' not in user.roles or t.assignee_id != user.id:
                raise BusinessError('FORBIDDEN', 'Chỉ thợ được giao ghi chi phí phát sinh của phiếu.', status=403)
            if t.status not in OPEN_WORK:
                raise BusinessError('BAD_STATUS', 'Ghi chi phí phát sinh sau khi đã đến khách và trước khi hoàn thành phiếu.', status=409)
            row = ServiceExpense(ticket_id=t.id, technician_id=user.id, kind=kind, description=description or KINDS[kind],
                                 place=place, amount=amount, status='CHO_DUYET')
            session.add(row)
            session.flush()
            if photo:
                from . import attachments
                saved = attachments.add(session, user, 'service_expense', str(row.id), 'KHAC', photo[0], photo[1], _in_tx=True)
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
            raise BusinessError('FORBIDDEN', 'Chỉ thợ đã ghi mới xóa được khoản này.', status=403)
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
        before = snapshot(row)
        row.status, row.decided_by, row.decided_at, row.decision_note = ('DA_DUYET' if approve else 'TU_CHOI'), user.id, _now(), note
        _record(session, user, row, 'approve' if approve else 'reject', before)
    return row


def pay(session, user, ids, fund_code, pay_date):
    """Hoàn tiền một lần cho nhiều khoản đã duyệt của cùng một thợ: một phiếu chi đã ghi sổ."""
    check_finance(user)
    try:
        ids = sorted({int(i) for i in ids})
    except (TypeError, ValueError):
        raise BusinessError('BAD_LINES', 'Chọn các khoản cần hoàn.')
    if not ids:
        raise BusinessError('BAD_LINES', 'Chọn các khoản cần hoàn.')
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
            raise BusinessError('BAD_STATUS', 'Chỉ hoàn các khoản đã duyệt, chưa hoàn.', status=409)
        if len({r.technician_id for r in rows}) != 1:
            raise BusinessError('MIXED_TECHNICIANS', 'Mỗi phiếu chi hoàn cho một thợ.')
        category = finance_mappings.require(session, 'DANH_MUC_CHI_PHI_THO')
        name = session.scalar(select(User.full_name).where(User.id == rows[0].technician_id))
        numbers = dict(session.execute(select(ServiceTicket.id, ServiceTicket.number).where(ServiceTicket.id.in_({r.ticket_id for r in rows}))).all())
        detail = '; '.join(f'{numbers[r.ticket_id]} {r.description} {r.amount:,}'.replace(',', '.') for r in rows)
        doc = cash_docs.insert(session, user, CashDocIn(kind='PC', doc_date=pay_date, fund_code=fund_code, category_code=category,
            partner_name=name, description=('Hoàn chi phí phát sinh cho thợ: ' + detail)[:2000], amount=sum(r.amount for r in rows),
            source_type='SERVICE_EXPENSE', source_id=str(rows[0].id)))
        cash_posting.post_in_tx(session, user, doc.id, doc.version)
        stamp = _now()
        for r in rows:
            before = snapshot(r)
            r.status, r.cash_doc_id, r.paid_by, r.paid_at = 'DA_HOAN', doc.id, user.id, stamp
            _record(session, user, r, 'pay', before)
    return doc


def listing(session, user, status):
    """Danh sách cho màn kế toán: (khoản, tên thợ, số phiếu DV, khách, số phiếu chi)."""
    check_finance(user)
    q = (select(ServiceExpense, User.full_name, ServiceTicket.number, Customer.name, CashDoc.number)
         .join(User, User.id == ServiceExpense.technician_id).join(ServiceTicket, ServiceTicket.id == ServiceExpense.ticket_id)
         .join(Customer, Customer.code == ServiceTicket.customer_code).outerjoin(CashDoc, CashDoc.id == ServiceExpense.cash_doc_id)
         .where(ServiceExpense.status == status))
    order = (User.full_name, ServiceExpense.id) if status in ('CHO_DUYET', 'DA_DUYET') else (ServiceExpense.id.desc(),)
    return [dict(row=r, technician=tech, ticket=number, customer=customer, cash=cash)
            for r, tech, number, customer, cash in session.execute(q.order_by(*order))]


def counts(session):
    return dict(session.execute(select(ServiceExpense.status, func.count()).group_by(ServiceExpense.status)).all())
