"""M3: công nợ NCC, trả tiền qua PC; không ghi trực tiếp sổ quỹ/kho."""
from datetime import UTC, date, datetime

from sqlalchemy import BigInteger, Date, DateTime, String, cast, func, literal, select, union_all

from . import audit, cash_docs, cash_posting, day_closings, finance_mappings
from .customers import like_pattern
from .doc_numbers import next_number
from .errors import BusinessError
from .models import CashDoc, PurchaseOrder, PurchaseReceipt, StockDoc, Supplier, SupplierOpeningDebt, SupplierPayment
from .schemas import CashDocIn, SupplierOpeningDebtIn, SupplierOpeningDebtOut, SupplierPaymentIn, SupplierPaymentOut
from .security import has_role
from .text import fold


def check_reader(user):
    if not has_role(user,'finance'):
        raise BusinessError('FORBIDDEN','Chỉ kế toán hoặc giám đốc được xem và trả công nợ NCC.',status=403)


check_writer = check_reader


def _supplier(session,code,lock=False):
    stmt=select(Supplier).where(Supplier.code==code)
    if lock:stmt=stmt.with_for_update().execution_options(populate_existing=True)
    row=session.scalar(stmt)
    if row is None:raise BusinessError('NOT_FOUND','Không tìm thấy nhà cung cấp.',status=404)
    return row


def _version(row,version):
    current=row.version if row is not None else 0
    if not isinstance(version,int) or isinstance(version,bool) or version<0:
        raise BusinessError('VERSION_REQUIRED','Phải gửi phiên bản, hãy tải lại.')
    if version!=current:
        raise BusinessError('VERSION_CONFLICT','Dữ liệu vừa được người khác cập nhật, hãy tải lại.',{'current':current},status=409)


def _rows(session,stmt):
    with session.no_autoflush:
        rows=[dict(r) for r in session.execute(stmt).mappings()]
    for row in rows:
        for key in ('opening','received','paid','remaining','change','balance'):
            if key in row:row[key]=int(row[key])
    return rows


def _balances():
    received=(select(PurchaseOrder.supplier_code,func.sum(PurchaseReceipt.total).label('amount'))
        .join(PurchaseReceipt,PurchaseReceipt.order_id==PurchaseOrder.id).where(PurchaseReceipt.status=='DA_GHI')
        .group_by(PurchaseOrder.supplier_code).subquery())
    paid=(select(SupplierPayment.supplier_code,func.sum(SupplierPayment.amount).label('amount'))
        .where(SupplierPayment.status=='DA_GHI').group_by(SupplierPayment.supplier_code).subquery())
    opening=func.coalesce(SupplierOpeningDebt.amount,0); received_amount=func.coalesce(received.c.amount,0); paid_amount=func.coalesce(paid.c.amount,0)
    return (select(Supplier.code.label('supplier_code'),Supplier.name,Supplier.active,opening.label('opening'),
            received_amount.label('received'),paid_amount.label('paid'),(opening+received_amount-paid_amount).label('remaining'))
        .outerjoin(SupplierOpeningDebt,SupplierOpeningDebt.supplier_code==Supplier.code)
        .outerjoin(received,received.c.supplier_code==Supplier.code).outerjoin(paid,paid.c.supplier_code==Supplier.code))


def balance(session,user,supplier_code):
    check_reader(user)
    rows=_rows(session,_balances().where(Supplier.code==supplier_code))
    if not rows:raise BusinessError('NOT_FOUND','Không tìm thấy nhà cung cấp.',status=404)
    return {k:rows[0][k] for k in ('opening','received','paid','remaining')}


def search(session,user,q=''):
    check_reader(user)
    b=_balances().subquery()
    stmt=select(b).where(b.c.remaining!=0)
    if fold(q):
        stmt=stmt.join(Supplier,Supplier.code==b.c.supplier_code).where(Supplier.search_text.like(like_pattern(fold(q))))
    return _rows(session,stmt.order_by(b.c.remaining.desc(),b.c.supplier_code))


def totals(session,user,q=''):
    check_reader(user)
    b=_balances().subquery()
    stmt=select(*[func.coalesce(func.sum(b.c[k]),0).label(k) for k in ('opening','received','paid','remaining')]).where(b.c.remaining!=0)
    if fold(q):stmt=stmt.join(Supplier,Supplier.code==b.c.supplier_code).where(Supplier.search_text.like(like_pattern(fold(q))))
    return _rows(session,stmt)[0]


def order_balance(session,user,order_id):
    check_reader(user)
    received=select(func.coalesce(func.sum(PurchaseReceipt.total),0)).where(PurchaseReceipt.order_id==order_id,PurchaseReceipt.status=='DA_GHI').scalar_subquery()
    paid=select(func.coalesce(func.sum(SupplierPayment.amount),0)).where(SupplierPayment.order_id==order_id,SupplierPayment.status=='DA_GHI').scalar_subquery()
    rows=_rows(session,select(PurchaseOrder.id.label('order_id'),PurchaseOrder.supplier_code,PurchaseOrder.number,
        received.label('received'),paid.label('paid'),(received-paid).label('remaining')).where(PurchaseOrder.id==order_id))
    if not rows:raise BusinessError('BAD_ORDER','Không tìm thấy đơn mua này.')
    return rows[0]


def payments(session,user,supplier_code):
    check_reader(user)
    return list(session.scalars(select(SupplierPayment).where(SupplierPayment.supplier_code==supplier_code).order_by(SupplierPayment.id.desc())))


def opening(session,user,supplier_code):
    check_reader(user)
    return session.get(SupplierOpeningDebt,supplier_code)


def _record(session,user,row,action,before,reason=''):
    session.flush()
    schema=SupplierPaymentOut if isinstance(row,SupplierPayment) else SupplierOpeningDebtOut
    entity='supplier_payment' if isinstance(row,SupplierPayment) else 'supplier_opening_debt'
    audit.record(session,user,entity+'.'+action,entity,str(row.id if isinstance(row,SupplierPayment) else row.supplier_code),
                 before,schema.model_validate(row).model_dump(mode='json'),reason)


def pay(session,user,supplier_code,fund_code,amount,paid_at,order_id=None,note=''):
    check_writer(user)
    body=SupplierPaymentIn(fund_code=fund_code,amount=amount,paid_at=paid_at,order_id=order_id,note=note)
    with cash_docs._transaction(session):
        # Cùng thứ tự ngày -> NCC với phiếu quỹ thủ công; chỉ khóa, không kiểm ngày chốt ở đây.
        day_closings.lock_days(session,[body.paid_at])
        supplier=_supplier(session,supplier_code,True)
        if not supplier.active:raise BusinessError('BAD_SUPPLIER','Nhà cung cấp đã ngừng dùng.')
        order=None
        if body.order_id is not None:
            # Không khóa ngược đơn khi đang giữ khóa NCC: M2 khóa đơn rồi kiểm NCC.
            order=session.get(PurchaseOrder,body.order_id,populate_existing=True)
            if order is None or order.supplier_code!=supplier_code:
                raise BusinessError('BAD_ORDER','Đơn mua không thuộc nhà cung cấp này.')
            current=order_balance(session,user,order.id)
            if body.amount>current['remaining']:
                raise BusinessError('OVER_PAYMENT','Số trả vượt tiền còn nợ của đơn mua.',current,status=409)
        current=balance(session,user,supplier_code)
        if body.amount>current['remaining']:
            raise BusinessError('OVER_PAYMENT','Số trả vượt tiền còn nợ nhà cung cấp.',current,status=409)
        category=finance_mappings.require(session,'DANH_MUC_MUA_HANG')
        # Reserve id từ BIGSERIAL để cash_doc_id luôn NOT NULL, source_id vẫn là id TN.
        ident=session.scalar(select(func.nextval(func.pg_get_serial_sequence('supplier_payments','id'))))
        number=next_number(session,'TN',body.paid_at)
        doc=cash_docs.insert(session,user,CashDocIn(kind='PC',doc_date=body.paid_at,fund_code=body.fund_code,
            category_code=category,amount=body.amount,supplier_code=supplier_code,
            description=f'Trả {number} cho NCC {supplier_code}'+(f' ({order.number})' if order else ''),
            source_type='SUPPLIER_PAYMENT',source_id=str(ident)))
        cash_posting.post_in_tx(session,user,doc.id,doc.version)
        row=SupplierPayment(id=ident,number=number,supplier_code=supplier_code,**body.model_dump(),cash_doc_id=doc.id,created_by=user.id)
        session.add(row);_record(session,user,row,'pay',None)
    return row


def void(session,user,payment_id,reason,version):
    check_writer(user)
    with cash_docs._transaction(session):
        source=session.execute(select(SupplierPayment.supplier_code,SupplierPayment.paid_at).where(SupplierPayment.id==payment_id)).first()
        if source is None:raise BusinessError('NOT_FOUND','Không tìm thấy lần trả NCC.',status=404)
        day_closings.lock_days(session,[source.paid_at,cash_docs.today()])
        _supplier(session,source.supplier_code,True)
        row=session.scalar(select(SupplierPayment).where(SupplierPayment.id==payment_id).with_for_update().execution_options(populate_existing=True))
        _version(row,version)
        if row.status!='DA_GHI':raise BusinessError('BAD_STATUS','Lần trả NCC này đã hủy.',status=409)
        reason=(reason or '').strip()
        if not reason or len(reason)>2000:raise BusinessError('REASON_REQUIRED','Hủy trả NCC phải có lý do (tối đa 2000 ký tự).')
        before=SupplierPaymentOut.model_validate(row).model_dump(mode='json')
        doc=cash_docs._load(session,row.cash_doc_id)
        cash_posting.void_in_tx(session,user,doc.id,reason,doc.version)
        row.status,row.void_reason,row.voided_by,row.voided_at='DA_HUY',reason,user.id,datetime.now(UTC)
        _record(session,user,row,'void',before,reason)
    return row


def set_opening(session,user,supplier_code,amount,as_of,note,version):
    if not has_role(user,'director'):raise BusinessError('FORBIDDEN','Chỉ giám đốc được đặt nợ đầu kỳ NCC.',status=403)
    body=SupplierOpeningDebtIn(amount=amount,as_of=as_of,note=note,version=version)
    with cash_docs._transaction(session):
        _supplier(session,supplier_code,True)
        row=session.scalar(select(SupplierOpeningDebt).where(SupplierOpeningDebt.supplier_code==supplier_code)
            .with_for_update().execution_options(populate_existing=True))
        _version(row,body.version)
        before=SupplierOpeningDebtOut.model_validate(row).model_dump(mode='json') if row else None
        if row is None:
            row=SupplierOpeningDebt(supplier_code=supplier_code,**body.model_dump(exclude={'version'}),created_by=user.id);session.add(row)
        else:
            row.amount,row.as_of,row.note=body.amount,body.as_of,body.note;row.version+=1
        _record(session,user,row,'set',before)
    return row


def timeline(session,user,supplier_code):
    check_reader(user)
    def event(day,at,kind,ident,number,change,note,status,order_id=None,order_number=None,stock_id=None,stock_number=None,cash_id=None,cash_number=None):
        def col(v,type_):return literal(v,type_=type_) if v is None or isinstance(v,(str,int)) else v
        return select(day.label('day'),at.label('at'),literal(kind).label('kind'),ident.label('id'),number.label('number'),
            change.label('change'),note.label('note'),status.label('status'),
            col(order_id,BigInteger).label('order_id'),col(order_number,String).label('order_number'),
            col(stock_id,BigInteger).label('stock_doc_id'),col(stock_number,String).label('stock_number'),
            col(cash_id,BigInteger).label('cash_doc_id'),col(cash_number,String).label('cash_number'))
    o=SupplierOpeningDebt;r=PurchaseReceipt;p=SupplierPayment
    initial=event(o.as_of,func.timezone('Asia/Ho_Chi_Minh',cast(o.as_of,DateTime)), 'opening',literal(0),literal('Nợ đầu kỳ'),o.amount,o.note,literal('DA_GHI')).where(o.supplier_code==supplier_code)
    def received(reversal):
        day=cast(func.timezone('Asia/Ho_Chi_Minh',r.voided_at),Date) if reversal else r.receipt_date
        stmt=event(day,r.voided_at if reversal else r.created_at,'void_receive' if reversal else 'receive',r.id,StockDoc.number,
            -r.total if reversal else r.total,r.void_reason if reversal else StockDoc.reason,r.status,r.order_id,PurchaseOrder.number,r.stock_doc_id,StockDoc.number)
        stmt=stmt.select_from(r).join(PurchaseOrder,PurchaseOrder.id==r.order_id).join(StockDoc,StockDoc.id==r.stock_doc_id).where(PurchaseOrder.supplier_code==supplier_code)
        return stmt.where(r.status=='DA_HUY') if reversal else stmt
    def payment(reversal):
        day=cast(func.timezone('Asia/Ho_Chi_Minh',p.voided_at),Date) if reversal else p.paid_at
        stmt=event(day,p.voided_at if reversal else p.created_at,'void_payment' if reversal else 'payment',p.id,p.number,
            p.amount if reversal else -p.amount,p.void_reason if reversal else p.note,p.status,p.order_id,PurchaseOrder.number,cash_id=p.cash_doc_id,cash_number=CashDoc.number)
        stmt=stmt.select_from(p).outerjoin(PurchaseOrder,PurchaseOrder.id==p.order_id).join(CashDoc,CashDoc.id==p.cash_doc_id).where(p.supplier_code==supplier_code)
        return stmt.where(p.status=='DA_HUY') if reversal else stmt
    events=union_all(initial,received(False),received(True),payment(False),payment(True)).subquery()
    ordering=(events.c.day,events.c.at,events.c.kind,events.c.id)
    return _rows(session,select(events,func.sum(events.c.change).over(order_by=ordering,rows=(None,0)).label('balance')).order_by(*ordering))
