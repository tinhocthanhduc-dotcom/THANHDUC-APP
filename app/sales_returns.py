"""S5: trả hàng theo GH gốc; nhận đúng giá vốn PX qua lõi kho."""
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from . import audit, quotes, sales_orders, sales_receipts, stock_docs, stock_posting
from .doc_numbers import next_number
from .errors import BusinessError
from .models import SalesReturn, SalesReturnLine, SalesDeliveryLine, SalesRefund, StockLedger, Warehouse
from .schemas import StockDocIn, StockDocLineIn
from .security import has_role

TZ=ZoneInfo('Asia/Ho_Chi_Minh')


def _now():
    return datetime.now(UTC)


def _writer(user):
    if not has_role(user,'sales','warehouse'):
        raise BusinessError('FORBIDDEN','Chỉ kinh doanh, kho hoặc giám đốc được lập phiếu trả hàng.',status=403)


def snapshot(row):
    values={c.name:getattr(row,c.name) for c in row.__table__.columns}
    values={k:v.isoformat() if isinstance(v,datetime) else v for k,v in values.items()}
    values['lines']=[{c.name:str(getattr(line,c.name)) if isinstance(getattr(line,c.name),Decimal) else getattr(line,c.name)
        for c in line.__table__.columns} for line in row.lines]
    return values


def for_order(session,user,order_id):
    sales_orders.load(session,user,order_id)
    return list(session.scalars(select(SalesReturn).options(selectinload(SalesReturn.lines)).where(SalesReturn.order_id==order_id).order_by(SalesReturn.id)))


def load(session,user,return_id):
    sales_orders.check_reader(user)
    row=session.scalar(select(SalesReturn).options(selectinload(SalesReturn.lines)).where(SalesReturn.id==return_id))
    if row is None:raise BusinessError('NOT_FOUND','Không tìm thấy phiếu trả hàng.',status=404)
    return row


def returned(session,delivery_id,line_no):
    values=session.execute(select(*[func.coalesce(func.sum(getattr(SalesReturnLine,k)),0) for k in ('qty','amount','vat_amount','cost_amount')])
        .join(SalesReturn).where(SalesReturn.status=='DA_NHAN',SalesReturnLine.delivery_id==delivery_id,SalesReturnLine.order_line_no==line_no)).one()
    return (values[0],*[int(v) for v in values[1:]])


def original_cost(session,piece):
    # S2 ghi PX theo thứ tự dòng GH trong từng kho. Một mã có thể xuất nhiều dòng,
    # nên phải lấy đúng dòng sổ, không lấy bình quân hiện tại hoặc chỉ lọc mã hàng.
    siblings=list(session.scalars(select(SalesDeliveryLine).where(SalesDeliveryLine.delivery_id==piece.delivery_id,
        SalesDeliveryLine.stock_doc_id==piece.stock_doc_id).order_by(SalesDeliveryLine.order_line_no)))
    position=next(i for i,line in enumerate(siblings,1) if line.order_line_no==piece.order_line_no)
    ledger=session.scalar(select(StockLedger).where(StockLedger.doc_id==piece.stock_doc_id,StockLedger.line_no==position,
        StockLedger.qty_out>0,StockLedger.is_reversal.is_(False)))
    if ledger is None:raise BusinessError('SOURCE_COST_MISSING','Không tìm thấy giá vốn PX gốc.',status=409)
    return ledger.unit_cost,-ledger.amount


def create(session,user,order_id,body):
    _writer(user)
    with sales_orders._transaction(session):
        order=sales_orders._load(session,order_id,lock=True)
        sales_orders._version(order,body.version)
        sales_orders._status(order,('GIAO_MOT_PHAN','DA_GIAO'))
        reason=body.reason.strip()
        if not reason:raise BusinessError('REASON_REQUIRED','Phải ghi lý do trả hàng.')
        before=sales_orders.snapshot(order)
        stamp=_now()
        row=SalesReturn(number=next_number(session,'TH',stamp.astimezone(TZ).date()),order_id=order.id,returned_at=stamp,
            created_by=user.id,reason=reason,status='DA_NHAN',void_reason='',subtotal=0,vat_total=0,total=0,lines=[])
        deliveries={g.id:g for g in sales_orders.deliveries(session,order.id)}
        originals={l.line_no:l for l in order.lines}
        running={}
        for n,part in enumerate(body.lines,1):
            gh=deliveries.get(part.delivery_id)
            line=originals.get(part.order_line_no)
            piece=next((p for p in gh.lines if p.order_line_no==part.order_line_no),None) if gh else None
            if gh is None or gh.status!='DA_GIAO' or line is None or piece is None:
                raise BusinessError('OVER_RETURN','Chỉ trả dòng đã giao của GH còn hiệu lực trên đơn này.',status=409)
            key=(gh.id,line.line_no)
            previous=running.setdefault(key,list(returned(session,*key)))
            remaining=piece.qty-previous[0]
            if part.qty>remaining:
                raise BusinessError('OVER_RETURN','Số trả vượt số đã giao trừ số đã trả theo GH.',status=409)
            final=part.qty==remaining
            amount=piece.amount-previous[1] if final else min(piece.amount-previous[1],quotes._round(line.unit_price*part.qty))
            vat=piece.vat_amount-previous[2] if final else min(piece.vat_amount-previous[2],quotes._round(Decimal(amount)*line.vat_rate/100))
            code=None;unit_cost=cost=0
            if line.kind=='HANG':
                if part.condition=='LOI':
                    stmt=select(Warehouse).where(Warehouse.kind=='LOI',Warehouse.active.is_(True))
                    if part.warehouse_code:stmt=stmt.where(Warehouse.code==part.warehouse_code)
                    wh=session.scalar(stmt.order_by(Warehouse.code).limit(1).with_for_update(read=True))
                    if wh is None:raise BusinessError('NO_DEFECT_WAREHOUSE','Chưa có kho hàng lỗi đang dùng.',status=409)
                else:
                    wh=session.scalar(select(Warehouse).where(Warehouse.code==(part.warehouse_code or line.warehouse_code),Warehouse.kind=='KHO',Warehouse.active.is_(True)).with_for_update(read=True))
                    if wh is None:raise BusinessError('BAD_WAREHOUSE','Hàng tốt phải về kho công ty đang dùng.')
                code=wh.code
                unit_cost,original_amount=original_cost(session,piece)
                cost=original_amount-previous[3] if final else min(original_amount-previous[3],quotes._round(part.qty*unit_cost))
            result=SalesReturnLine(line_no=n,order_id=order.id,order_line_no=line.line_no,delivery_id=gh.id,qty=part.qty,
                condition=part.condition,warehouse_code=code,amount=amount,vat_amount=vat,unit_cost=unit_cost,cost_amount=cost)
            row.lines.append(result)
            row.subtotal+=amount;row.vat_total+=vat;row.total=row.subtotal+row.vat_total
            for i,value in enumerate((part.qty,amount,vat,cost)):previous[i]+=value
        row.total=row.subtotal+row.vat_total
        session.add(row)
        session.flush()
        docs=[]
        for code in sorted({l.warehouse_code for l in row.lines if l.warehouse_code}):
            parts=[l for l in row.lines if l.warehouse_code==code]
            doc=stock_docs.insert(session,user,StockDocIn(kind='PN',warehouse_code=code,doc_date=stamp.astimezone(TZ).date(),
                customer_code=order.customer_code,reason=f'Trả hàng {row.number} của {order.number}',
                lines=[StockDocLineIn(product_code=originals[l.order_line_no].product_code,qty=l.qty,unit_cost=l.unit_cost) for l in parts]),_sales_return=True)
            docs.append((doc,parts))
        stock_posting._lock_balances(session,{(l.warehouse_code,originals[l.order_line_no].product_code) for l in row.lines if l.warehouse_code})
        for doc,parts in docs:
            stock_posting.post_in_tx(session,user,doc.id,doc.version,pn_amounts={i:l.cost_amount for i,l in enumerate(parts,1)})
            for l in parts:l.stock_doc_id=doc.id
        order.receivable-=row.total
        order.version+=1
        session.flush()
        audit.record(session,user,'sales_return.create','sales_return',str(row.id),None,snapshot(row),reason)
        sales_orders._record(session,user,order,'return',before,reason)
    return row


def void(session,user,return_id,version,reason):
    if not has_role(user,'director'):raise BusinessError('FORBIDDEN','Chỉ giám đốc được hủy phiếu trả.',status=403)
    with sales_orders._transaction(session):
        oid=session.scalar(select(SalesReturn.order_id).where(SalesReturn.id==return_id))
        if oid is None:raise BusinessError('NOT_FOUND','Không tìm thấy phiếu trả.',status=404)
        order=sales_orders._load(session,oid,lock=True)
        row=session.scalar(select(SalesReturn).options(selectinload(SalesReturn.lines)).where(SalesReturn.id==return_id).with_for_update().execution_options(populate_existing=True))
        sales_orders._version(row,version);sales_orders._status(row,('DA_NHAN',))
        reason=(reason or '').strip()
        if not reason:raise BusinessError('REASON_REQUIRED','Phải ghi lý do hủy trả hàng.')
        if row.returned_at.astimezone(TZ).date()!=_now().astimezone(TZ).date():
            raise BusinessError('RETURN_DAY_ONLY','Chỉ hủy phiếu trả trong ngày.',status=409)
        paid=sales_receipts.balance(order)['paid']
        if session.scalar(select(SalesRefund.id).where(SalesRefund.order_id==oid,SalesRefund.status=='DA_CHI').limit(1)) and paid<order.receivable+row.total:
            raise BusinessError('REFUND_PAID','Đã hoàn tiền; hủy trả hàng sẽ làm khách ứng trước âm.',status=409)
        before,old=sales_orders.snapshot(order),snapshot(row)
        for doc_id in sorted({l.stock_doc_id for l in row.lines if l.stock_doc_id}):
            doc=stock_docs.load(session,doc_id)
            stock_posting.void_in_tx(session,user,doc_id,reason,doc.version)
        row.status,row.void_reason,row.voided_by,row.voided_at='DA_HUY',reason,user.id,_now()
        order.receivable+=row.total;order.version+=1
        session.flush()
        audit.record(session,user,'sales_return.void','sales_return',str(row.id),old,snapshot(row),reason)
        sales_orders._record(session,user,order,'void_return',before,reason)
    return row
