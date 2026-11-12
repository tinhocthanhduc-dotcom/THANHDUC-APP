"""B4/T3: báo thu DB/DV; khóa chứng từ nguồn trước BT, ghi PT/đảo trong cùng transaction."""
from contextlib import contextmanager
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, literal, union_all, case
from sqlalchemy.orm import Session, object_session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from . import audit, sales_orders, cash_docs, cash_posting, finance_mappings
from .doc_numbers import next_number
from .errors import BusinessError
from .models import Customer, CustomerOpeningDebt, SalesOrder, SalesReceipt, SalesRefund, ServiceTicket, User, SALES_RECEIPT_METHODS
from .schemas import CashDocIn, CustomerReceiptReport, SalesReceiptOut, SalesReceiptReport
from .security import has_role

TZ = ZoneInfo('Asia/Ho_Chi_Minh')


def _today():
    return datetime.now(TZ).date()


def check_finance(user):
    if not has_role(user, 'finance'):
        raise BusinessError('FORBIDDEN', 'Chỉ kế toán hoặc giám đốc được xử lý báo thu.', status=403)


@contextmanager
def _transaction(session):
    try:
        yield
        session.commit()
    except StaleDataError:
        session.rollback()
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa báo thu, hãy tải lại.', status=409)
    except Exception:
        session.rollback()
        raise


def snapshot(receipt):
    return SalesReceiptOut.model_validate(receipt).model_dump(mode='json')


def _version(receipt, version):
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise BusinessError('VERSION_REQUIRED', 'Phải gửi phiên bản báo thu, hãy tải lại.')
    if receipt.version != version:
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa báo thu, hãy tải lại.', {'current': receipt.version}, status=409)


def _sums():
    return (func.coalesce(func.sum(SalesReceipt.amount).filter(SalesReceipt.status == 'DA_XAC_NHAN'), 0),
            func.coalesce(func.sum(SalesReceipt.amount).filter(SalesReceipt.status == 'CHO_XAC_NHAN'), 0))



def totals(model=SalesOrder, *, as_of=None):
    """Nguồn duy nhất cho đã thu thuần: BT xác nhận trừ HT đã chi; DV giữ cách cũ."""
    receipt_window = () if as_of is None else (SalesReceipt.decided_at < as_of,
        SalesReceipt.received_at < as_of.astimezone(TZ).date() if as_of.astimezone(TZ).time() == time.min else SalesReceipt.received_at <= as_of.astimezone(TZ).date())
    refund_window = () if as_of is None else (SalesRefund.decided_at < as_of,)
    if model is CustomerOpeningDebt:
        paid,pending=_sums()
        return select(SalesReceipt.opening_id.label('parent_id'),paid.label('paid'),pending.label('pending'),
            literal(0).label('refunded')).where(SalesReceipt.opening_id.is_not(None), *receipt_window).group_by(SalesReceipt.opening_id).subquery()
    if model is not SalesOrder:
        paid,pending=_sums()
        return select(SalesReceipt.ticket_id.label('parent_id'),paid.label('paid'),pending.label('pending'),
            literal(0).label('refunded')).where(SalesReceipt.ticket_id.is_not(None), *receipt_window).group_by(SalesReceipt.ticket_id).subquery()
    events=union_all(
        select(SalesReceipt.order_id.label('parent_id'),
            case((SalesReceipt.status=='DA_XAC_NHAN',SalesReceipt.amount),else_=0).label('paid'),
            case((SalesReceipt.status=='CHO_XAC_NHAN',SalesReceipt.amount),else_=0).label('pending'),literal(0).label('refunded'))
            .where(SalesReceipt.order_id.is_not(None), *receipt_window),
        select(SalesRefund.order_id,-SalesRefund.amount,literal(0),SalesRefund.amount)
            .where(SalesRefund.order_id.is_not(None),SalesRefund.status=='DA_CHI', *refund_window)).subquery()
    return select(events.c.parent_id,*[func.sum(events.c[k]).label(k) for k in ('paid','pending','refunded')]).group_by(events.c.parent_id).subquery()


def paid_sql(model=SalesOrder):
    rows=totals(model)
    return select(func.coalesce(rows.c.paid,0)).where(rows.c.parent_id==model.id).correlate(model).scalar_subquery()


def _balance(receivable, paid, pending):
    return dict(receivable=receivable, paid=int(paid), pending=int(pending), remaining=max(0,(receivable or 0)-int(paid)))


def balance(order: SalesOrder | ServiceTicket | CustomerOpeningDebt):
    """Đơn/phiếu đã load trong session; đọc tổng mới, không dùng cache công nợ."""
    session = object_session(order)
    if session is None:
        raise ValueError('Đơn cần được load trong session để đọc số dư hiện tại.')
    rows=totals(type(order))
    found=session.execute(select(rows.c.paid,rows.c.pending,rows.c.refunded).where(rows.c.parent_id==order.id)).first()
    paid,pending,refunded=found if found else (0,0,0)
    result = _balance(order.receivable, paid, pending)
    if isinstance(order,SalesOrder):
        result['refunded'] = int(refunded)
        result['advance'] = max(0,int(paid)-(order.receivable or 0))
        result['available'] = max(0,sales_orders.effective_total(order)-int(paid)-int(pending))
    return result


def for_order(session: Session, user, order_id):
    order = sales_orders.load(session, user, order_id)
    rows = list(session.scalars(select(SalesReceipt).where(SalesReceipt.order_id == order_id)
                               .order_by(SalesReceipt.reported_at, SalesReceipt.id)))
    return dict(balance=balance(order), receipts=rows)


def _record(session, user, receipt, action, before, reason=''):
    session.flush()
    audit.record(session, user, 'sales_receipt.'+action, 'sales_receipt', str(receipt.id), before, snapshot(receipt), reason)


def report(session: Session, user, order_id, body: SalesReceiptReport):
    with _transaction(session):
        return report_in_tx(session,user,order_id,body)


def report_in_tx(session, user, order_id, body: SalesReceiptReport):
    order = sales_orders._load(session, order_id, lock=True)
    if not (has_role(user, 'sales') or (has_role(user, 'technician') and order.delivery_assignee_id == user.id)):
        raise BusinessError('FORBIDDEN', 'Chỉ kinh doanh hoặc thợ được giao đơn được báo thu.', status=403)
    sales_orders._version(order, body.version)
    if order.status not in ('DA_CHOT', 'GIAO_MOT_PHAN', 'DA_GIAO'):
        raise BusinessError('BAD_STATUS', 'Chỉ báo thu cho đơn đã chốt hoặc đã giao.', status=409)
    current = balance(order)
    if current['paid'] + current['pending'] + body.amount > sales_orders.effective_total(order):
        raise BusinessError('OVER_PAYMENT', 'Tổng đã thu và chờ xác nhận vượt số phải thu của đơn.',
                            current, status=409)
    received = body.received_at or _today()
    receipt = SalesReceipt(number=next_number(session, 'BT', received), order_id=order.id,
                 method=body.method, amount=body.amount, received_at=received, note=body.note,
                 status='CHO_XAC_NHAN', reason='', reported_by=user.id)
    session.add(receipt)
    _record(session, user, receipt, 'report', None)
    return receipt


def _ticket(session,ticket_id):
    row = session.scalar(select(ServiceTicket).where(ServiceTicket.id==ticket_id).with_for_update()
                         .execution_options(populate_existing=True))
    if row is None:
        raise BusinessError('NOT_FOUND','Không tìm thấy phiếu dịch vụ.',status=404)
    return row


def check_ticket_reporter(user, ticket):
    if not (has_role(user,'dispatcher','sales') or
            (has_role(user,'technician') and ticket.assignee_id == user.id)):
        raise BusinessError('FORBIDDEN','Chỉ điều phối, kinh doanh, giám đốc hoặc thợ được giao phiếu được báo thu dịch vụ.',status=403)


def report_for_ticket(session,user,ticket_id,body: SalesReceiptReport):
    with _transaction(session):
        ticket = _ticket(session,ticket_id)
        check_ticket_reporter(user, ticket)
        _version(ticket,body.version)
        return report_for_ticket_in_tx(session, user, ticket, body)


def report_for_ticket_in_tx(session, user, ticket: ServiceTicket, body: SalesReceiptReport):
    """Caller đã khóa phiếu, kiểm phiên bản và quản lý transaction."""
    check_ticket_reporter(user, ticket)
    if ticket.status not in ('HOAN_THANH','DA_DONG') or (ticket.receivable or 0)<=0 or ticket.billing=='MIEN_PHI':
        raise BusinessError('BAD_STATUS','Chỉ báo thu cho phiếu dịch vụ đã hoàn thành hoặc đã đóng còn số phải thu.',status=409)
    current = balance(ticket)
    if current['paid']+current['pending']+body.amount>ticket.receivable:
        raise BusinessError('OVER_PAYMENT','Tổng đã thu và chờ xác nhận vượt số phải thu của phiếu dịch vụ.',
                            {**current,'available':current['remaining']-current['pending']},status=409)
    received = body.received_at or _today()
    receipt = SalesReceipt(number=next_number(session,'BT',received),ticket_id=ticket.id,
                method=body.method,amount=body.amount,received_at=received,note=body.note,
                status='CHO_XAC_NHAN',reason='',reported_by=user.id)
    session.add(receipt)
    _record(session,user,receipt,'report',None)
    return receipt


def check_opening_reporter(user):
    if not has_role(user, 'finance'):
        raise BusinessError('FORBIDDEN', 'Chỉ kế toán hoặc giám đốc được báo thu nợ đầu kỳ.', status=403)


def _opening(session, opening_id):
    row = session.scalar(select(CustomerOpeningDebt).where(CustomerOpeningDebt.id==opening_id).with_for_update()
                         .execution_options(populate_existing=True))
    if row is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy nợ đầu kỳ của khách.', status=404)
    return row


def report_for_opening(session, user, opening_id, body: SalesReceiptReport):
    """Báo thu trừ nợ đầu kỳ khách: cùng luật chờ xác nhận, phiếu thu khi xác nhận như đơn bán."""
    check_opening_reporter(user)
    with _transaction(session):
        opening = _opening(session, opening_id)
        _version(opening, body.version)
        return report_for_opening_in_tx(session, user, opening, body)


def report_for_opening_in_tx(session, user, opening: CustomerOpeningDebt, body: SalesReceiptReport):
    """Caller đã khóa nợ đầu kỳ, kiểm phiên bản và quản lý transaction."""
    check_opening_reporter(user)
    current = balance(opening)
    if current['paid'] + current['pending'] + body.amount > opening.amount:
        raise BusinessError('OVER_PAYMENT', 'Tổng đã thu và chờ xác nhận vượt số nợ đầu kỳ của khách.',
                            {**current, 'available': current['remaining'] - current['pending']}, status=409)
    received = body.received_at or _today()
    receipt = SalesReceipt(number=next_number(session, 'BT', received), opening_id=opening.id,
                method=body.method, amount=body.amount, received_at=received, note=body.note,
                status='CHO_XAC_NHAN', reason='', reported_by=user.id)
    session.add(receipt)
    _record(session, user, receipt, 'report', None)
    return receipt


# ---- Báo thu theo khách (TUẤN 06/11/2026) ----
# Một số tiền tổng chia vào các chứng từ khách còn chưa trả, cũ nhất trước (cùng thứ tự với Công nợ khách hàng).
# Không có luật tiền mới: mỗi phần là một báo thu chờ xác nhận bình thường, qua đúng hàm báo thu từng chứng từ
# (quyền, trạng thái, chặn vượt số phải thu giữ nguyên). Tổng vượt số khách còn chưa trả thì chặn, không ghi ứng trước.

def customer_unpaid_docs(session, customer_code, lock=False):
    """Chứng từ khách còn chưa trả (còn nợ − chờ xác nhận > 0), cũ trước. lock=True khóa từng chứng từ rồi đọc lại số dư."""
    from .finance_reports import _debts
    d = _debts()
    rows = list(session.execute(select(d.c.kind, d.c.id, d.c.number, d.c.day).where(d.c.customer_code == customer_code)
                                .order_by(d.c.day, d.c.kind, d.c.id)))
    loaded = {}
    if lock:  # thứ tự khóa cố định (loại, id) để hai người báo thu cùng lúc không khóa chéo nhau
        for kind, doc_id in sorted({(r.kind, r.id) for r in rows}):
            loaded[(kind, doc_id)] = (sales_orders._load(session, doc_id, lock=True) if kind == 'DB'
                                      else _ticket(session, doc_id) if kind == 'DV' else _opening(session, doc_id))
    else:
        for r in rows:
            model = {'DB': SalesOrder, 'DV': ServiceTicket, 'DK': CustomerOpeningDebt}[r.kind]
            loaded[(r.kind, r.id)] = session.get(model, r.id)
    out = []
    for r in rows:
        doc = loaded[(r.kind, r.id)]
        current = balance(doc)
        unpaid = max(0, current['remaining'] - current['pending'])
        if unpaid > 0:
            out.append(dict(kind=r.kind, id=r.id, number=r.number, day=r.day, unpaid=unpaid, doc=doc))
    return out


def allocate(docs, amount):
    """Chia số tiền vào chứng từ cũ trước; trả [(doc, phần)] chỉ gồm phần > 0."""
    plan, left = [], amount
    for doc in docs:
        if left <= 0:
            break
        part = min(left, doc['unpaid'])
        plan.append((doc, part))
        left -= part
    return plan


def check_customer_reporter(user, docs):
    """Quyền báo thu từng loại chứng từ giữ như cũ; báo trước một câu thay vì lỗi giữa chừng."""
    kinds = {d['kind'] for d in docs}
    if 'DB' in kinds and not has_role(user, 'sales'):
        raise BusinessError('FORBIDDEN', 'Khách có đơn bán còn nợ: chỉ kinh doanh hoặc giám đốc được báo thu đơn bán.', status=403)
    if 'DV' in kinds and not has_role(user, 'dispatcher', 'sales'):
        raise BusinessError('FORBIDDEN', 'Khách có phiếu dịch vụ còn nợ: chỉ điều phối, kinh doanh hoặc giám đốc được báo thu.', status=403)
    if 'DK' in kinds:
        check_opening_reporter(user)


def report_for_customer(session, user, customer_code, body: CustomerReceiptReport):
    with _transaction(session):
        customer = session.scalar(select(Customer).where(Customer.code == customer_code).with_for_update())
        if customer is None:
            raise BusinessError('NOT_FOUND', 'Không tìm thấy khách hàng.', status=404)
        docs = customer_unpaid_docs(session, customer_code, lock=True)
        total = sum(d['unpaid'] for d in docs)
        if total <= 0:
            raise BusinessError('NOTHING_DUE', 'Khách không còn khoản nào chưa trả.', status=409)
        if body.expected_total is not None and body.expected_total != total:
            raise BusinessError('DEBT_CHANGED', 'Công nợ của khách vừa thay đổi, hãy xem lại bảng chia rồi báo thu lại.',
                                {'total': total}, status=409)
        if body.amount > total:
            raise BusinessError('OVER_PAYMENT', f'Khách đưa dư {body.amount - total:,} đ so với tổng nợ {total:,} đ. Sửa lại số tiền.'.replace(',', '.'),
                                {'total': total, 'amount': body.amount}, status=409)
        check_customer_reporter(user, docs)
        plan = allocate(docs, body.amount)
        tag = f'Báo thu theo khách: tổng {body.amount:,} đ chia {len(plan)} chứng từ'.replace(',', '.')
        note = (body.note.strip() + ' · ' + tag) if body.note.strip() else tag
        receipts = []
        for doc, part in plan:
            row = doc['doc']
            sub = SalesReceiptReport(version=row.version, method=body.method, amount=part, received_at=body.received_at, note=note[:4000])
            if doc['kind'] == 'DB':
                receipts.append(report_in_tx(session, user, row.id, sub))
            elif doc['kind'] == 'DV':
                receipts.append(report_for_ticket_in_tx(session, user, row, sub))
            else:
                receipts.append(report_for_opening_in_tx(session, user, row, sub))
        return receipts


def for_opening(session, user, opening):
    rows = list(session.scalars(select(SalesReceipt).where(SalesReceipt.opening_id == opening.id)
                               .order_by(SalesReceipt.reported_at, SalesReceipt.id)))
    return dict(balance=balance(opening), receipts=rows)


def pending_holders(session, field, ids):
    """Báo thu chờ xác nhận của nhiều đơn/phiếu: tiền mặt đang ở ai, khoản CK chờ đối soát.

    Chỉ để hiển thị (07/11/2026); công nợ vẫn chỉ giảm khi kế toán xác nhận.
    field: 'order_id' hoặc 'ticket_id'. Trả {id: [dict(method, user_id, name, amount, at)]}.
    """
    ids = {i for i in ids if i}
    if not ids:
        return {}
    col = getattr(SalesReceipt, field)
    first = func.min(SalesReceipt.reported_at)
    rows = session.execute(select(col, SalesReceipt.method, SalesReceipt.reported_by, User.full_name,
                                  func.sum(SalesReceipt.amount), first)
                           .join(User, User.id == SalesReceipt.reported_by)
                           .where(col.in_(ids), SalesReceipt.status == 'CHO_XAC_NHAN')
                           .group_by(col, SalesReceipt.method, SalesReceipt.reported_by, User.full_name)
                           .order_by(col, first)).all()
    out = {}
    for parent, method, uid, name, amount, at in rows:
        out.setdefault(parent, []).append(dict(method=method, user_id=uid, name=name, amount=int(amount), at=at))
    return out


def holder_text(items):
    """'Tiền mặt ở Đoàn Cảnh Phong · Chuyển khoản chờ đối soát' (gộp CK thành một cụm)."""
    parts = []
    for h in items or ():
        text = f"Tiền mặt ở {h['name']}" if h['method'] == 'TIEN_MAT' else 'Chuyển khoản chờ đối soát'
        if text not in parts:
            parts.append(text)
    return ' · '.join(parts)


def held_by(session, user):
    """Tiền mặt đang chờ của chính người báo, không phụ thuộc người hiện giữ phiếu."""
    with session.no_autoflush:
        rows = [dict(row) for row in session.execute(select(
            SalesReceipt.id, SalesReceipt.number, SalesReceipt.ticket_id, SalesReceipt.order_id,
            ServiceTicket.number.label('ticket_number'), func.coalesce(SalesOrder.number, CustomerOpeningDebt.number).label('order_number'),
            Customer.name.label('customer'), SalesReceipt.amount, SalesReceipt.received_at,
            func.sum(SalesReceipt.amount).over().label('held_total')
        ).outerjoin(ServiceTicket, ServiceTicket.id == SalesReceipt.ticket_id).outerjoin(
            SalesOrder, SalesOrder.id == SalesReceipt.order_id).outerjoin(
            CustomerOpeningDebt, CustomerOpeningDebt.id == SalesReceipt.opening_id).join(
            Customer, Customer.code == func.coalesce(ServiceTicket.customer_code, SalesOrder.customer_code, CustomerOpeningDebt.customer_code))
        .where(SalesReceipt.reported_by == user.id, SalesReceipt.method == 'TIEN_MAT',
               SalesReceipt.status == 'CHO_XAC_NHAN')
        .order_by(SalesReceipt.received_at, SalesReceipt.reported_at, SalesReceipt.id)).mappings()]
        total = int(rows[0]['held_total']) if rows else 0
        for row in rows:
            row.pop('held_total')
            row['amount'] = int(row['amount'])
        return dict(rows=rows, total=total)


def for_ticket(session,user,ticket_id):
    from .service_tickets import load
    ticket = load(session,user,ticket_id)
    rows = list(session.scalars(select(SalesReceipt).where(SalesReceipt.ticket_id==ticket_id)
                               .order_by(SalesReceipt.reported_at,SalesReceipt.id)))
    return dict(balance=balance(ticket),receipts=rows)


def _locked(session, receipt_id):
    source = session.execute(select(SalesReceipt.order_id,SalesReceipt.ticket_id,SalesReceipt.opening_id).where(SalesReceipt.id==receipt_id)).first()
    if source is None:
        raise BusinessError('NOT_FOUND','Không tìm thấy báo thu.',status=404)
    # Luôn khóa chứng từ nguồn trước BT, cùng thứ tự report/cancel/transition.
    if source.order_id is not None:
        parent = sales_orders._load(session,source.order_id,lock=True)
    elif source.opening_id is not None:
        parent = _opening(session,source.opening_id)
    else:
        parent = _ticket(session,source.ticket_id)
    receipt = session.scalar(select(SalesReceipt).where(SalesReceipt.id==receipt_id).with_for_update()
                             .execution_options(populate_existing=True))
    return parent,receipt


def _post_cash(session,user,parent,receipt,doc_date=None):
    fund_key = 'THU_TIEN_MAT' if receipt.method=='TIEN_MAT' else 'THU_CHUYEN_KHOAN'
    category_key = 'DANH_MUC_DICH_VU' if receipt.ticket_id is not None else 'DANH_MUC_BAN_HANG'  # nợ đầu kỳ thu như bán hàng
    fund = finance_mappings.require(session,fund_key)
    category = finance_mappings.require(session,category_key)
    day = doc_date or receipt.received_at
    note = '' if day == receipt.received_at else f' (ngày thu {receipt.received_at:%d/%m/%Y}, ngày đó đã chốt sổ)'
    doc = cash_docs.insert(session,user,CashDocIn(kind='PT',doc_date=day,fund_code=fund,
        category_code=category,amount=receipt.amount,customer_code=parent.customer_code,
        description=f'Thu {receipt.number} cho {parent.number}{note}',source_type='SALES_RECEIPT',source_id=str(receipt.id)))
    cash_posting.post_in_tx(session,user,doc.id,doc.version)
    receipt.cash_doc_id = doc.id


def _decide(session, user, receipt_id, version, target, reason=''):
    if target == 'DA_HUY':
        if not has_role(user, 'director'):
            raise BusinessError('FORBIDDEN', 'Chỉ giám đốc được hủy báo thu đã xác nhận.', status=403)
    with _transaction(session):
        return decide_in_tx(session,user,receipt_id,version,target,reason)


def decide_in_tx(session,user,receipt_id,version,target,reason=''):
    if target=='DA_HUY' and not has_role(user,'director'):
        raise BusinessError('FORBIDDEN','Chỉ giám đốc được hủy báo thu đã xác nhận.',status=403)
    parent,receipt = _locked(session, receipt_id)
    if target != 'DA_HUY':
        if (target=='DA_XAC_NHAN' and receipt.order_id is not None and receipt.reported_by==user.id
                and has_role(user,'technician') and not has_role(user,'director')):
            raise BusinessError('SELF_CONFIRM','Không được tự xác nhận báo thu do mình lập.',status=403)
        check_finance(user)
    _version(receipt, version)
    expected = 'DA_XAC_NHAN' if target == 'DA_HUY' else 'CHO_XAC_NHAN'
    if receipt.status != expected:
        raise BusinessError('BAD_STATUS', 'Không thực hiện được ở trạng thái báo thu hiện tại.',
                            {'status': receipt.status}, status=409)
    if target == 'DA_XAC_NHAN' and receipt.reported_by == user.id and not has_role(user, 'director'):
        raise BusinessError('SELF_CONFIRM', 'Không được tự xác nhận báo thu do mình lập.', status=403)
    reason = (reason or '').strip()
    if target in ('TU_CHOI', 'DA_HUY') and not reason:
        raise BusinessError('REASON_REQUIRED', 'Phải nhập lý do từ chối hoặc hủy báo thu.')
    before = snapshot(receipt)
    if target=='DA_HUY' and receipt.order_id is not None and balance(parent)['paid']<receipt.amount:
        raise BusinessError('REFUND_PAID','Đã hoàn tiền, không thể hủy báo thu làm số thu thuần âm.',status=409)
    if target=='DA_XAC_NHAN':
        _post_cash(session,user,parent,receipt)
    elif target=='DA_HUY' and receipt.cash_doc_id is not None:
        doc = cash_docs._load(session,receipt.cash_doc_id)
        cash_posting.void_in_tx(session,user,doc.id,reason,doc.version)
    receipt.status, receipt.reason = target, reason
    receipt.decided_by, receipt.decided_at = user.id, datetime.now(UTC)
    _record(session, user, receipt, {'DA_XAC_NHAN':'confirm', 'TU_CHOI':'reject', 'DA_HUY':'void'}[target], before, reason)
    return receipt


def confirm(session, user, receipt_id, version):
    return _decide(session, user, receipt_id, version, 'DA_XAC_NHAN')


def reject(session, user, receipt_id, reason, version):
    return _decide(session, user, receipt_id, version, 'TU_CHOI', reason)


def void(session, user, receipt_id, reason, version):
    return _decide(session, user, receipt_id, version, 'DA_HUY', reason)


# ---- Đổi hình thức báo thu (TUẤN duyệt 07/11/2026) ----
# Chọn nhầm Tiền mặt/Chuyển khoản: một bước thay cho "từ chối/hủy rồi báo lại". Không có luật tiền mới:
# BT cũ đi đúng đường từ chối (chờ) hoặc hủy (đã xác nhận, đảo PT cũ), BT mới cùng số tiền, ngày thu, người báo,
# nguồn; đã xác nhận thì BT mới ghi PT vào quỹ của hình thức mới. Công nợ và số đã thu thuần không đổi.

def check_switch(user, receipt):
    if receipt.status == 'DA_XAC_NHAN':
        if not has_role(user, 'director'):
            raise BusinessError('FORBIDDEN', 'Báo thu đã xác nhận: chỉ giám đốc được đổi hình thức.', status=403)
    elif receipt.status == 'CHO_XAC_NHAN':
        if receipt.reported_by != user.id and not has_role(user, 'finance'):
            raise BusinessError('FORBIDDEN', 'Chỉ người lập báo thu, kế toán hoặc giám đốc được đổi hình thức.', status=403)
    else:
        raise BusinessError('BAD_STATUS', 'Chỉ đổi hình thức báo thu đang chờ hoặc đã xác nhận.', {'status': receipt.status}, status=409)


def can_switch(user, receipt):
    try:
        check_switch(user, receipt)
    except BusinessError:
        return False
    return receipt.replaced_by_id is None


def can_confirm_now(user, receipt):
    """Kế toán đổi BT đang chờ có thể xác nhận luôn; giữ luật không tự xác nhận BT do mình lập."""
    return (receipt.status == 'CHO_XAC_NHAN' and has_role(user, 'finance')
            and (receipt.reported_by != user.id or has_role(user, 'director')))


def switch_method(session, user, receipt_id, version, method, reason, confirm_now=False):
    with _transaction(session):
        return switch_method_in_tx(session, user, receipt_id, version, method, reason, confirm_now)


def switch_method_in_tx(session, user, receipt_id, version, method, reason, confirm_now=False):
    if method not in SALES_RECEIPT_METHODS:
        raise BusinessError('BAD_METHOD', 'Hình thức thu chưa hợp lệ.')
    reason = (reason or '').strip()
    if not reason:
        raise BusinessError('REASON_REQUIRED', 'Phải chọn hoặc nhập lý do đổi hình thức.')
    parent, old = _locked(session, receipt_id)
    check_switch(user, old)
    _version(old, version)
    if old.method == method:
        raise BusinessError('SAME_METHOD', 'Báo thu đã là hình thức này.', status=409)
    if confirm_now and not can_confirm_now(user, old):
        raise BusinessError('SELF_CONFIRM', 'Không được tự xác nhận báo thu do mình lập.', status=403)
    labels = {'TIEN_MAT': 'Tiền mặt', 'CHUYEN_KHOAN': 'Chuyển khoản'}
    was_confirmed = old.status == 'DA_XAC_NHAN'
    new = SalesReceipt(number=next_number(session, 'BT', old.received_at), order_id=old.order_id, ticket_id=old.ticket_id,
                       opening_id=old.opening_id, method=method, amount=old.amount, received_at=old.received_at,
                       note=old.note, status='CHO_XAC_NHAN', reason='', reported_by=old.reported_by)
    session.add(new)
    session.flush()
    text = f'Đổi hình thức {labels[old.method]} → {labels[method]} ({new.number}): {reason}'[:2000]
    before = snapshot(old)
    if was_confirmed and old.cash_doc_id is not None:
        doc = cash_docs._load(session, old.cash_doc_id)
        cash_posting.void_in_tx(session, user, doc.id, text, doc.version)
    old.status, old.reason, old.replaced_by_id = ('DA_HUY' if was_confirmed else 'TU_CHOI'), text, new.id
    old.decided_by, old.decided_at = user.id, datetime.now(UTC)
    _record(session, user, old, 'switch', before, text)
    _record(session, user, new, 'report', None, text)
    if was_confirmed or confirm_now:
        new_before = snapshot(new)
        _post_cash(session, user, parent, new)
        new.status, new.decided_by, new.decided_at = 'DA_XAC_NHAN', user.id, datetime.now(UTC)
        _record(session, user, new, 'confirm', new_before, text)
    return new


def list_pending(session: Session, user, method=None, from_date: date | None=None, to_date: date | None=None):
    check_finance(user)
    if method and method not in SALES_RECEIPT_METHODS:
        raise BusinessError('BAD_METHOD', 'Cách thu không hợp lệ.')
    if from_date is not None and to_date is not None and from_date > to_date:
        raise BusinessError('BAD_DATE_RANGE', 'Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')
    stmt = select(SalesReceipt).where(SalesReceipt.status == 'CHO_XAC_NHAN')
    if method:
        stmt = stmt.where(SalesReceipt.method == method)
    if from_date is not None:
        stmt = stmt.where(SalesReceipt.received_at >= from_date)
    if to_date is not None:
        stmt = stmt.where(SalesReceipt.received_at <= to_date)
    return list(session.scalars(stmt.order_by(SalesReceipt.reported_at, SalesReceipt.id)))


def receivables(session: Session, user):
    if not has_role(user, 'sales', 'finance'):
        raise BusinessError('FORBIDDEN', 'Chỉ kinh doanh, kế toán hoặc giám đốc được xem đơn cần thu.', status=403)
    sums = totals()
    stmt = (select(SalesOrder,func.coalesce(sums.c.paid,0),func.coalesce(sums.c.pending,0),sales_orders.first_delivery(),sales_orders.due_date_sql()).options(selectinload(SalesOrder.lines))
            .outerjoin(sums, sums.c.parent_id == SalesOrder.id)
            .where(SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN','DA_GIAO')),SalesOrder.receivable > func.coalesce(sums.c.paid,0))
            .order_by(sales_orders.first_delivery(),SalesOrder.id))
    return [dict(order=o,balance=_balance(o.receivable,pay,wait),
                 days=max(0,(_today()-first.astimezone(TZ).date()).days),due_date=due,overdue_days=max(0,(_today()-due).days))
            for o,pay,wait,first,due in session.execute(stmt)]


def confirm_in_tx(session,user,receipt_id,version):
    return decide_in_tx(session,user,receipt_id,version,'DA_XAC_NHAN')


def confirm_cash_group(session,user,items,counted,reporter_id):
    """Confirm only the posted snapshot; lock sources before receipts, then post oldest first."""
    check_finance(user)
    with _transaction(session):
        if not items or len({ident for ident,version in items})!=len(items):
            raise BusinessError('BAD_GROUP','Nhóm phải có báo thu và không được lặp phiếu.')
        if isinstance(counted,bool) or not isinstance(counted,int) or not 0<=counted<=9_223_372_036_854_775_807:
            raise BusinessError('BAD_AMOUNT','Số tiền đã đếm phải là số nguyên đồng hợp lệ.')
        versions=dict(items)
        rows=list(session.scalars(select(SalesReceipt).where(SalesReceipt.id.in_(versions)).order_by(SalesReceipt.reported_at,SalesReceipt.id)))
        if len(rows)!=len(items):raise BusinessError('NOT_FOUND','Có báo thu trong nhóm không còn tồn tại.',status=404)
        # Consistent source lock order also prevents two overlapping groups locking opposite parents.
        for ident in sorted({row.order_id for row in rows if row.order_id is not None}):
            sales_orders._load(session,ident,lock=True)
        for ident in sorted({row.ticket_id for row in rows if row.ticket_id is not None}):
            _ticket(session,ident)
        for ident in sorted({row.opening_id for row in rows if row.opening_id is not None}):
            _opening(session,ident)
        locked=[]
        for source in rows:
            try:
                parent,row=_locked(session,source.id)
                _version(row,versions[row.id])
                if row.status!='CHO_XAC_NHAN':raise BusinessError('BAD_STATUS','Báo thu không còn chờ xác nhận.',status=409)
                if row.method!='TIEN_MAT' or row.reported_by!=reporter_id:
                    raise BusinessError('BAD_GROUP','Chỉ xác nhận chung tiền mặt của cùng một người nộp.')
                if row.reported_by==user.id:
                    raise BusinessError('SELF_CONFIRM','Không được tự xác nhận báo thu trong nhóm.',status=403)
                locked.append(row)
            except BusinessError as exc:
                raise BusinessError(exc.code,source.number+': '+exc.message,{**exc.details,'receipt_id':source.id,'number':source.number},status=exc.status) from exc
        total=sum(row.amount for row in locked)
        if counted!=total:
            difference=counted-total
            amount=format(abs(difference),',').replace(',','.')
            raise BusinessError('CASH_COUNT_MISMATCH','Số tiền đã đếm '+('thừa ' if difference>0 else 'thiếu ')+amount+' đồng so với tổng nhóm.',
                dict(expected=total,counted=counted,difference=difference))
        for row in locked:
            try:confirm_in_tx(session,user,row.id,versions[row.id])
            except BusinessError as exc:
                raise BusinessError(exc.code,row.number+': '+exc.message,{**exc.details,'receipt_id':row.id,'number':row.number},status=exc.status) from exc
        return locked
