"""S5: đề nghị hoàn, duyệt PC qua lõi quỹ, khóa đơn trước đề nghị."""
from datetime import UTC, datetime
from sqlalchemy import func, select

from . import audit, cash_docs, cash_posting, finance_mappings, sales_orders, sales_receipts
from .doc_numbers import next_number
from .errors import BusinessError
from .models import SalesOrder, SalesReceipt, SalesRefund
from .schemas import CashDocIn
from .security import has_role


def snapshot(row):
    return {c.name:(getattr(row,c.name).isoformat() if isinstance(getattr(row,c.name),datetime) else getattr(row,c.name)) for c in row.__table__.columns}


def _pending(session,order_id):
    return int(session.scalar(select(func.coalesce(func.sum(SalesRefund.amount),0)).where(SalesRefund.order_id==order_id,SalesRefund.status=='CHO_DUYET')))


def _new(session,user,order,amount,method,reason):
    row=SalesRefund(number=next_number(session,'HT',cash_docs.today()),order_id=order.id,amount=amount,method=method,
        reason=reason,status='CHO_DUYET',requested_by=user.id)
    session.add(row);session.flush()
    audit.record(session,user,'sales_refund.request','sales_refund',str(row.id),None,snapshot(row),reason)
    return row


def request(session,user,order_id,body):
    sales_orders.check_writer(user)
    with sales_orders._transaction(session):
        order=sales_orders._load(session,order_id,lock=True)
        sales_orders._version(order,body.version)
        reason=body.reason.strip()
        if not reason:raise BusinessError('REASON_REQUIRED','Phải ghi lý do hoàn tiền.')
        available=sales_receipts.balance(order)['advance']-_pending(session,order.id)
        if body.amount>available:raise BusinessError('OVER_REFUND','Số hoàn vượt khách ứng trước trừ đề nghị đang chờ.',{'available':available},status=409)
        before=sales_orders.snapshot(order)
        row=_new(session,user,order,body.amount,body.method,reason)
        order.version+=1
        sales_orders._record(session,user,order,'request_refund',before,reason)
    return row


def for_cancel_in_tx(session,user,order,reason):
    """Hoàn cọc khi hủy: không tạo trùng phần đã có đề nghị; giữ cách thu gốc.

    Nếu có nhiều cách thu, tách HT theo từng cách; mọi HT vẫn cần kế toán duyệt.
    """
    available=sales_receipts.balance(order)['advance']-_pending(session,order.id)
    if available<=0:return
    received=list(session.execute(select(SalesReceipt.method,func.sum(SalesReceipt.amount)).where(
        SalesReceipt.order_id==order.id,SalesReceipt.status=='DA_XAC_NHAN').group_by(SalesReceipt.method).order_by(SalesReceipt.method)))
    for method,amount in received:
        assigned=session.scalar(select(func.coalesce(func.sum(SalesRefund.amount),0)).where(SalesRefund.order_id==order.id,
            SalesRefund.method==method,SalesRefund.status.in_(('CHO_DUYET','DA_CHI'))))
        part=min(available,max(0,int(amount)-int(assigned)))
        if part:
            _new(session,user,order,part,method,f'Hủy {order.number}: {reason}')
            available-=part
        if not available:break
    if available:
        # Có hoàn bằng cách khác cách thu gốc; chọn lại cách thu đã có cho phần còn lại.
        _new(session,user,order,available,received[0][0],f'Hủy {order.number}: {reason}')


def for_order(session,user,order_id):
    sales_orders.load(session,user,order_id)
    return list(session.scalars(select(SalesRefund).where(SalesRefund.order_id==order_id).order_by(SalesRefund.id)))


def pending(session,user):
    sales_receipts.check_finance(user)
    return list(session.scalars(select(SalesRefund).where(SalesRefund.status=='CHO_DUYET').order_by(SalesRefund.id)))


def decide(session,user,refund_id,version,approve,reason=''):
    if not has_role(user,'sales','finance'):
        raise BusinessError('FORBIDDEN','Chỉ kế toán hoặc giám đốc được xử lý hoàn tiền.',status=403)
    with sales_orders._transaction(session):
        oid=session.scalar(select(SalesRefund.order_id).where(SalesRefund.id==refund_id))
        if oid is None:raise BusinessError('NOT_FOUND','Không tìm thấy đề nghị hoàn tiền đơn bán.',status=404)
        order=sales_orders._load(session,oid,lock=True)
        row=session.scalar(select(SalesRefund).where(SalesRefund.id==refund_id).with_for_update().execution_options(populate_existing=True))
        sales_orders._version(row,version);sales_orders._status(row,('CHO_DUYET',))
        if approve and row.requested_by==user.id and not has_role(user,'director'):
            raise BusinessError('SELF_CONFIRM','Không được tự duyệt đề nghị hoàn do mình lập.',status=403)
        sales_receipts.check_finance(user)
        reason=(reason or '').strip()
        if not approve and not reason:raise BusinessError('REASON_REQUIRED','Từ chối hoàn tiền phải ghi lý do.')
        before,old=sales_orders.snapshot(order),snapshot(row)
        if approve:
            if row.amount>sales_receipts.balance(order)['advance']:
                raise BusinessError('OVER_REFUND','Khách không còn đủ số ứng trước để hoàn.',status=409)
            fund=finance_mappings.require(session,'THU_TIEN_MAT' if row.method=='TIEN_MAT' else 'THU_CHUYEN_KHOAN')
            category=finance_mappings.require(session,'DANH_MUC_HOAN_TIEN_BAN')
            doc=cash_docs.insert(session,user,CashDocIn(kind='PC',doc_date=cash_docs.today(),fund_code=fund,category_code=category,
                customer_code=order.customer_code,amount=row.amount,description=f'Hoàn {row.number} cho {order.number}: {row.reason}'[:2000],
                source_type='SALES_REFUND',source_id=str(row.id)))
            cash_posting.post_in_tx(session,user,doc.id,doc.version)
            row.cash_doc_id=doc.id
        else:
            row.reason+='\nTừ chối: '+reason
        row.status='DA_CHI' if approve else 'TU_CHOI'
        row.decided_by,row.decided_at=user.id,datetime.now(UTC)
        order.version+=1
        session.flush()
        audit.record(session,user,'sales_refund.'+('approve' if approve else 'reject'),'sales_refund',str(row.id),old,snapshot(row),reason)
        sales_orders._record(session,user,order,'refund_'+('approve' if approve else 'reject'),before,reason)
    return row
