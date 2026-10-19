"""M2: nhận/hủy hàng nguyên tử qua lõi phiếu kho, chưa thanh toán NCC."""
from datetime import UTC, datetime
from decimal import Decimal, localcontext

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from . import audit, purchase_orders as orders, quotes, stock_docs, stock_posting
from .errors import BusinessError
from .models import Warehouse, PurchaseReceipt, PurchaseReceiptLine, PurchaseReturn
from .schemas import PurchaseReceiveLineIn, PurchaseReceiptOut, StockDocIn, StockDocLineIn

check_writer = orders.check_writer
check_reader = orders.check_reader


def snapshot(row):
    return PurchaseReceiptOut.model_validate(row).model_dump(mode='json')


def _record(session, user, row, action, before, reason=''):
    session.flush()
    audit.record(session, user, 'purchase_receipt.' + action, 'purchase_receipt', str(row.id), before, snapshot(row), reason)


def _reason(reason):
    reason = (reason or '').strip()
    if not reason or len(reason) > 2000:
        raise BusinessError('REASON_REQUIRED', 'Phải nhập lý do (tối đa 2000 ký tự).')
    return reason


def list_by_order(session, user, order_id):
    check_reader(user)
    return list(session.scalars(select(PurchaseReceipt).where(PurchaseReceipt.order_id == order_id)
        .options(selectinload(PurchaseReceipt.lines)).order_by(PurchaseReceipt.id.desc())))


def payable_by_order(session, user, order_id):
    check_reader(user)
    with session.no_autoflush:
        return int(session.scalar(select(func.coalesce(func.sum(PurchaseReceipt.total), 0)).where(
            PurchaseReceipt.order_id == order_id, PurchaseReceipt.status == 'DA_GHI')))


def _amounts(session, order_id):
    return {r.order_line_id: (int(r.amount), int(r.vat_amount)) for r in session.execute(
        select(PurchaseReceiptLine.order_line_id, func.sum(PurchaseReceiptLine.amount).label('amount'),
               func.sum(PurchaseReceiptLine.vat_amount).label('vat_amount'))
        .join(PurchaseReceipt).where(PurchaseReceipt.order_id == order_id, PurchaseReceipt.status == 'DA_GHI')
        .group_by(PurchaseReceiptLine.order_line_id))}


def _state(order):
    if all(line.received_qty == line.qty for line in order.lines):
        return 'DA_NHAN'
    return 'NHAN_MOT_PHAN' if any(line.received_qty > 0 for line in order.lines) else 'DA_DAT'


def _requests(lines):
    if not lines:
        raise BusinessError('EMPTY_RECEIPT', 'Chọn ít nhất một dòng có số lượng nhận lớn hơn 0.')
    result=[]; seen=set()
    for n, data in enumerate(lines, 1):
        try:
            part=PurchaseReceiveLineIn.model_validate(data)
        except ValidationError:
            raise BusinessError('BAD_QTY', f'Dòng nhận {n} chưa hợp lệ; số lượng phải lớn hơn 0, tối đa 3 chữ số lẻ.')
        if part.order_line_id in seen:
            raise BusinessError('DUPLICATE_LINE', 'Một dòng đơn mua chỉ được chọn một lần trong lần nhận.')
        seen.add(part.order_line_id); result.append(part)
    return result


def receive(session, user, order_id, version, receipt_date, lines):
    check_writer(user)
    with orders._transaction(session):
        order = orders._load(session, order_id, True); orders._version(order, version)
        if order.status not in ('DA_DAT', 'NHAN_MOT_PHAN'):
            raise BusinessError('BAD_STATUS', 'Chỉ nhận hàng cho đơn Đã đặt hoặc Nhận một phần.', status=409)
        parts=_requests(lines); by_id={line.id:line for line in order.lines}
        groups={}; previous=_amounts(session, order_id)
        orders._supplier(session, order.supplier_code)
        for part in parts:
            line=by_id.get(part.order_line_id)
            if line is None:
                raise BusinessError('BAD_ORDER_LINE', 'Dòng nhận không thuộc đơn mua này.', {'order_line_id':part.order_line_id})
            if line.received_qty + part.qty > line.qty:
                raise BusinessError('OVER_RECEIVE', f'Dòng {line.line_no} ({line.product_code}) nhận vượt số đặt.',
                    {'order_line_id':line.id,'remaining':str(line.qty-line.received_qty),'requested':str(part.qty)},status=409)
            with localcontext() as context:
                context.prec=64
                if line.received_qty + part.qty == line.qty:
                    paid,vat_paid=previous.get(line.id,(0,0))
                    amount,vat=line.amount-paid,line.vat_amount-vat_paid
                else:
                    amount=quotes._round(part.qty*line.unit_cost)
                    vat=quotes._round(Decimal(amount)*line.vat_rate/100)
            if amount < 0 or vat < 0:
                raise BusinessError('ROUNDING_CONFLICT','Các lần nhận trước đã làm tròn vượt tổng dòng; hãy hủy và gộp lại lần nhận.')
            groups.setdefault(line.warehouse_code,[]).append((line,part.qty,amount,vat))
        before=orders.snapshot(order); result=[]
        for warehouse in sorted(groups):
            if not session.scalar(select(Warehouse.code).where(Warehouse.code == warehouse, Warehouse.kind == 'KHO', Warehouse.active.is_(True)).with_for_update(read=True)):
                raise BusinessError('BAD_WAREHOUSE','Đơn mua chỉ nhận vào kho công ty đang dùng.')
            rows=sorted(groups[warehouse],key=lambda r:r[0].line_no)
            doc=stock_docs.insert(session,user,StockDocIn(kind='PN',warehouse_code=warehouse,doc_date=receipt_date,
                supplier_code=order.supplier_code,reason=f'Nhận hàng {order.number}',
                lines=[StockDocLineIn(product_code=line.product_code,qty=qty,unit_cost=line.unit_cost,
                        note=f'{order.number} dòng {line.line_no}') for line,qty,amount,vat in rows]))
            stock_posting.post_in_tx(session,user,doc.id,doc.version,
                                    pn_amounts={n:amount for n,(_,_,amount,_) in enumerate(rows,1)})
            receipt=PurchaseReceipt(order_id=order.id,stock_doc_id=doc.id,warehouse_code=warehouse,
                receipt_date=receipt_date,created_by=user.id,
                amount=quotes._money_limit(sum(r[2] for r in rows)),vat_amount=quotes._money_limit(sum(r[3] for r in rows)),
                total=quotes._money_limit(sum(r[2]+r[3] for r in rows)),
                lines=[PurchaseReceiptLine(order_line_id=line.id,qty=qty,unit_cost=line.unit_cost,vat_rate=line.vat_rate,
                                           amount=amount,vat_amount=vat) for line,qty,amount,vat in rows])
            session.add(receipt)
            for line,qty,_,_ in rows:line.received_qty+=qty
            _record(session,user,receipt,'receive',None)
            result.append(receipt)
        order.status=_state(order); order.version+=1
        orders._record(session,user,order,'receive',before)
    return result


def void(session, user, receipt_id, reason, version):
    check_writer(user)
    with orders._transaction(session):
        # Mọi nhận/hủy/đóng đều khóa đơn trước, rồi mới khóa lần nhận và PN.
        order_id=session.scalar(select(PurchaseReceipt.order_id).where(PurchaseReceipt.id==receipt_id))
        if order_id is None:
            raise BusinessError('NOT_FOUND','Không tìm thấy lần nhận.',status=404)
        order=orders._load(session,order_id,True)
        row=session.scalar(select(PurchaseReceipt).where(PurchaseReceipt.id==receipt_id)
            .options(selectinload(PurchaseReceipt.lines)).with_for_update().execution_options(populate_existing=True))
        orders._version(row,version)
        if row.status!='DA_GHI':
            raise BusinessError('BAD_STATUS','Lần nhận này đã hủy.',status=409)
        if session.scalar(select(PurchaseReturn.id).where(PurchaseReturn.receipt_id==receipt_id, PurchaseReturn.status=='DA_GHI')) is not None:
            raise BusinessError('HAS_RETURNS','Lần nhận còn phiếu trả hàng; hãy hủy phiếu trả trước.',status=409)
        reason=_reason(reason); before=snapshot(row); order_before=orders.snapshot(order)
        stock_posting.void_in_tx(session,user,row.stock_doc_id,f'Hủy nhận {order.number}: {reason}',None)
        by_id={line.id:line for line in order.lines}
        for part in row.lines:by_id[part.order_line_id].received_qty-=part.qty
        was_closed=bool(order.close_reason)
        order.close_reason='';order.status=_state(order);order.version+=1
        row.status,row.void_reason,row.voided_by,row.voided_at='DA_HUY',reason,user.id,datetime.now(UTC)
        _record(session,user,row,'void',before,reason)
        orders._record(session,user,order,'reopen_after_void' if was_closed else 'void_receipt',order_before,reason)
    return row


def close(session, user, order_id, reason, version):
    check_writer(user)
    with orders._transaction(session):
        order=orders._load(session,order_id,True);orders._version(order,version)
        reason=_reason(reason)
        if order.status!='NHAN_MOT_PHAN':
            raise BusinessError('BAD_STATUS','Chỉ đóng đơn đang Nhận một phần.',status=409)
        before=orders.snapshot(order)
        order.status,order.close_reason='DA_NHAN',reason
        orders._record(session,user,order,'close',before,reason)
    return order
