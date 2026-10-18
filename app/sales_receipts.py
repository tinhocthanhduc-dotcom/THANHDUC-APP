"""B4/T3: báo thu DB/DV; khóa chứng từ nguồn trước BT, ghi PT/đảo trong cùng transaction."""
from contextlib import contextmanager
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from . import audit, sales_orders, cash_docs, cash_posting, finance_mappings
from .doc_numbers import next_number
from .errors import BusinessError
from .models import SalesOrder, SalesReceipt, ServiceTicket, SALES_RECEIPT_METHODS
from .schemas import CashDocIn, SalesReceiptOut, SalesReceiptReport
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


def _balance(receivable, paid, pending):
    return dict(receivable=receivable, paid=int(paid), pending=int(pending), remaining=(receivable or 0)-int(paid))


def balance(order: SalesOrder | ServiceTicket):
    """Đơn/phiếu đã load trong session; đọc tổng mới, không dùng cache công nợ."""
    session = object_session(order)
    if session is None:
        raise ValueError('Đơn cần được load trong session để đọc số dư hiện tại.')
    column = SalesReceipt.ticket_id if isinstance(order,ServiceTicket) else SalesReceipt.order_id
    paid,pending = session.execute(select(*_sums()).where(column == order.id)).one()
    return _balance(order.receivable, paid, pending)


def for_order(session: Session, user, order_id):
    order = sales_orders.load(session, user, order_id)
    rows = list(session.scalars(select(SalesReceipt).where(SalesReceipt.order_id == order_id)
                               .order_by(SalesReceipt.reported_at, SalesReceipt.id)))
    return dict(balance=balance(order), receipts=rows)


def _record(session, user, receipt, action, before, reason=''):
    session.flush()
    audit.record(session, user, 'sales_receipt.'+action, 'sales_receipt', str(receipt.id), before, snapshot(receipt), reason)


def report(session: Session, user, order_id, body: SalesReceiptReport):
    sales_orders.check_writer(user)
    with _transaction(session):
        order = sales_orders._load(session, order_id, lock=True)
        sales_orders._version(order, body.version)
        if order.status not in ('DA_CHOT', 'DA_GIAO'):
            raise BusinessError('BAD_STATUS', 'Chỉ báo thu cho đơn đã chốt hoặc đã giao.', status=409)
        current = balance(order)
        if current['paid'] + current['pending'] + body.amount > (order.receivable or 0):
            raise BusinessError('OVER_PAYMENT', 'Tổng đã thu và chờ xác nhận vượt số phải thu của đơn.',
                                {**current, 'available': current['remaining']-current['pending']}, status=409)
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


def check_ticket_reporter(user):
    if not has_role(user,'dispatcher','sales'):
        raise BusinessError('FORBIDDEN','Chỉ điều phối, kinh doanh hoặc giám đốc được báo thu dịch vụ.',status=403)


def report_for_ticket(session,user,ticket_id,body: SalesReceiptReport):
    check_ticket_reporter(user)
    with _transaction(session):
        ticket = _ticket(session,ticket_id)
        _version(ticket,body.version)
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


def for_ticket(session,user,ticket_id):
    from .service_tickets import load
    ticket = load(session,user,ticket_id)
    rows = list(session.scalars(select(SalesReceipt).where(SalesReceipt.ticket_id==ticket_id)
                               .order_by(SalesReceipt.reported_at,SalesReceipt.id)))
    return dict(balance=balance(ticket),receipts=rows)


def _locked(session, receipt_id):
    source = session.execute(select(SalesReceipt.order_id,SalesReceipt.ticket_id).where(SalesReceipt.id==receipt_id)).first()
    if source is None:
        raise BusinessError('NOT_FOUND','Không tìm thấy báo thu.',status=404)
    # Luôn khóa chứng từ nguồn trước BT, cùng thứ tự report/cancel/transition.
    parent = sales_orders._load(session,source.order_id,lock=True) if source.order_id is not None else _ticket(session,source.ticket_id)
    receipt = session.scalar(select(SalesReceipt).where(SalesReceipt.id==receipt_id).with_for_update()
                             .execution_options(populate_existing=True))
    return parent,receipt


def _post_cash(session,user,parent,receipt):
    fund_key = 'THU_TIEN_MAT' if receipt.method=='TIEN_MAT' else 'THU_CHUYEN_KHOAN'
    category_key = 'DANH_MUC_BAN_HANG' if receipt.order_id is not None else 'DANH_MUC_DICH_VU'
    fund = finance_mappings.require(session,fund_key)
    category = finance_mappings.require(session,category_key)
    doc = cash_docs.insert(session,user,CashDocIn(kind='PT',doc_date=receipt.received_at,fund_code=fund,
        category_code=category,amount=receipt.amount,customer_code=parent.customer_code,
        description=f'Thu {receipt.number} cho {parent.number}',source_type='SALES_RECEIPT',source_id=str(receipt.id)))
    cash_posting.post_in_tx(session,user,doc.id,doc.version)
    receipt.cash_doc_id = doc.id


def _decide(session, user, receipt_id, version, target, reason=''):
    if target == 'DA_HUY':
        if not has_role(user, 'director'):
            raise BusinessError('FORBIDDEN', 'Chỉ giám đốc được hủy báo thu đã xác nhận.', status=403)
    else:
        check_finance(user)
    with _transaction(session):
        parent,receipt = _locked(session, receipt_id)
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
    paid,pending = _sums()
    totals = select(SalesReceipt.order_id,paid.label('paid'),pending.label('pending')).group_by(SalesReceipt.order_id).subquery()
    stmt = (select(SalesOrder,func.coalesce(totals.c.paid,0),func.coalesce(totals.c.pending,0)).options(selectinload(SalesOrder.lines))
            .outerjoin(totals, totals.c.order_id == SalesOrder.id)
            .where(SalesOrder.status.in_(('DA_CHOT','DA_GIAO')),SalesOrder.receivable > func.coalesce(totals.c.paid,0))
            .order_by(SalesOrder.confirmed_at,SalesOrder.id))
    return [dict(order=o,balance=_balance(o.receivable,pay,wait),
                 days=max(0,(_today()-o.confirmed_at.astimezone(TZ).date()).days))
            for o,pay,wait in session.execute(stmt)]
