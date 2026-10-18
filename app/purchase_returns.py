"""M4: trả hàng và giảm nợ theo giá mua, xuất kho qua lõi giá vốn bình quân."""
from datetime import UTC, datetime
from decimal import Decimal, localcontext

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from . import audit, purchase_orders as orders, purchase_receipts as receipts, quotes, stock_docs, stock_posting
from .doc_numbers import next_number
from .errors import BusinessError
from .models import PurchaseReceipt, PurchaseReturn, PurchaseReturnLine
from .schemas import PurchaseReturnIn, PurchaseReturnOut, StockDocIn, StockDocLineIn

check_writer = orders.check_writer
check_reader = orders.check_reader


def snapshot(row):
    return PurchaseReturnOut.model_validate(row).model_dump(mode='json')


def _record(session, user, row, action, before, reason=''):
    session.flush()
    audit.record(session, user, 'purchase_return.'+action, 'purchase_return', str(row.id), before, snapshot(row), reason)


def load(session, user, return_id):
    check_reader(user)
    row=session.scalar(select(PurchaseReturn).where(PurchaseReturn.id==return_id).options(selectinload(PurchaseReturn.lines)))
    if row is None:raise BusinessError('NOT_FOUND','Không tìm thấy phiếu trả hàng.',status=404)
    return row


def list_by_order(session, user, order_id):
    check_reader(user)
    return list(session.scalars(select(PurchaseReturn).where(PurchaseReturn.order_id==order_id)
        .options(selectinload(PurchaseReturn.lines)).order_by(PurchaseReturn.id.desc())))


def returned_by_line(session, user, receipt_id):
    check_reader(user)
    return {r.receipt_line_id: r.qty for r in session.execute(select(PurchaseReturnLine.receipt_line_id,
        func.sum(PurchaseReturnLine.qty).label('qty')).join(PurchaseReturn)
        .where(PurchaseReturn.receipt_id==receipt_id,PurchaseReturn.status=='DA_GHI').group_by(PurchaseReturnLine.receipt_line_id))}


def _receipt(session, receipt_id, lock=False):
    stmt=select(PurchaseReceipt).where(PurchaseReceipt.id==receipt_id).options(selectinload(PurchaseReceipt.lines))
    if lock:stmt=stmt.with_for_update().execution_options(populate_existing=True)
    row=session.scalar(stmt)
    if row is None:raise BusinessError('NOT_FOUND','Không tìm thấy lần nhận.',status=404)
    return row


def create(session, user, receipt_id, version, return_date, reason, lines):
    check_writer(user)
    with orders._transaction(session):
        order_id=session.scalar(select(PurchaseReceipt.order_id).where(PurchaseReceipt.id==receipt_id))
        if order_id is None:raise BusinessError('NOT_FOUND','Không tìm thấy lần nhận.',status=404)
        # Cùng thứ tự M2: đơn -> lần nhận -> phiếu kho; tuần tự cả trả/hủy/nhận.
        order=orders._load(session,order_id,True);orders._version(order,version)
        receipt=_receipt(session,receipt_id,True)
        if receipt.status!='DA_GHI':raise BusinessError('BAD_STATUS','Chỉ trả hàng từ lần nhận đang ghi sổ.',status=409)
        reason=receipts._reason(reason)
        try:body=PurchaseReturnIn(version=version,return_date=return_date,reason=reason,lines=lines)
        except ValidationError:
            raise BusinessError('BAD_RETURN','Dòng trả chưa hợp lệ; số lượng phải lớn hơn 0, tối đa 3 chữ số lẻ.')
        if not body.lines:raise BusinessError('EMPTY_RETURN','Chọn ít nhất một dòng có số lượng trả lớn hơn 0.')
        previous={r.receipt_line_id:r for r in session.execute(select(PurchaseReturnLine.receipt_line_id,
            func.sum(PurchaseReturnLine.qty).label('qty'),func.sum(PurchaseReturnLine.amount).label('amount'),
            func.sum(PurchaseReturnLine.vat_amount).label('vat')).join(PurchaseReturn)
            .where(PurchaseReturn.receipt_id==receipt_id,PurchaseReturn.status=='DA_GHI').group_by(PurchaseReturnLine.receipt_line_id))}
        by_id={l.id:l for l in receipt.lines};order_lines={l.id:l for l in order.lines};seen=set();parts=[]
        for part in body.lines:
            line=by_id.get(part.receipt_line_id)
            if line is None:raise BusinessError('BAD_RECEIPT_LINE','Dòng trả không thuộc lần nhận này.',{'receipt_line_id':part.receipt_line_id})
            if line.id in seen:raise BusinessError('DUPLICATE_LINE','Một dòng nhận chỉ được chọn một lần trong phiếu trả.')
            seen.add(line.id);old=previous.get(line.id);remaining=line.qty-(old.qty if old else 0)
            if part.qty>remaining:
                raise BusinessError('OVER_RETURN','Số trả vượt số còn trả được của dòng nhận.',
                    {'receipt_line_id':line.id,'remaining':str(remaining),'requested':str(part.qty)},status=409)
            with localcontext() as context:
                context.prec=64
                if part.qty==remaining:amount,vat=line.amount-int(old.amount if old else 0),line.vat_amount-int(old.vat if old else 0)
                else:
                    amount=quotes._round(part.qty*line.unit_cost)
                    vat=quotes._round(Decimal(amount)*line.vat_rate/100)
            if amount<0 or vat<0:raise BusinessError('ROUNDING_CONFLICT','Các lần trả trước đã làm tròn vượt tổng dòng; hãy hủy và gộp lại phiếu trả.')
            parts.append((line,part.qty,amount,vat))
        number=next_number(session,'TH',body.return_date);before=orders.snapshot(order)
        doc=stock_docs.insert(session,user,StockDocIn(kind='PX',warehouse_code=receipt.warehouse_code,
            supplier_code=order.supplier_code,doc_date=body.return_date,reason=f'Trả NCC {number} ({order.number})',note=reason,
            lines=[StockDocLineIn(product_code=order_lines[l.order_line_id].product_code,qty=qty,
                note=f'{number} · {order.number} dòng {order_lines[l.order_line_id].line_no}') for l,qty,_,_ in parts]))
        stock_posting.post_in_tx(session,user,doc.id,doc.version)
        row=PurchaseReturn(number=number,receipt_id=receipt.id,order_id=order.id,supplier_code=order.supplier_code,
            warehouse_code=receipt.warehouse_code,stock_doc_id=doc.id,return_date=body.return_date,reason=reason,created_by=user.id,
            amount=quotes._money_limit(sum(p[2] for p in parts)),vat_amount=quotes._money_limit(sum(p[3] for p in parts)),
            total=quotes._money_limit(sum(p[2]+p[3] for p in parts)),
            lines=[PurchaseReturnLine(receipt_line_id=l.id,qty=qty,unit_cost=l.unit_cost,vat_rate=l.vat_rate,
                amount=amount,vat_amount=vat) for l,qty,amount,vat in parts])
        session.add(row);_record(session,user,row,'create',None,reason)
        order.version+=1;orders._record(session,user,order,'return',before,reason)
    return row


def void(session, user, return_id, reason, version):
    check_writer(user)
    with orders._transaction(session):
        source=session.execute(select(PurchaseReturn.order_id,PurchaseReturn.receipt_id).where(PurchaseReturn.id==return_id)).first()
        if source is None:raise BusinessError('NOT_FOUND','Không tìm thấy phiếu trả hàng.',status=404)
        order=orders._load(session,source.order_id,True);_receipt(session,source.receipt_id,True)
        row=session.scalar(select(PurchaseReturn).where(PurchaseReturn.id==return_id).options(selectinload(PurchaseReturn.lines))
            .with_for_update().execution_options(populate_existing=True))
        orders._version(row,version)
        if row.status!='DA_GHI':raise BusinessError('BAD_STATUS','Phiếu trả hàng này đã hủy.',status=409)
        reason=receipts._reason(reason);before=snapshot(row);order_before=orders.snapshot(order)
        stock_posting.void_in_tx(session,user,row.stock_doc_id,f'Hủy trả {row.number}: {reason}',None)
        row.status,row.void_reason,row.voided_by,row.voided_at='DA_HUY',reason,user.id,datetime.now(UTC)
        _record(session,user,row,'void',before,reason)
        order.version+=1;orders._record(session,user,order,'void_return',order_before,reason)
    return row
