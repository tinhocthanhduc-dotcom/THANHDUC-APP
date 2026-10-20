"""CXL2a: một phiếu xử lý, một phiếu kho, mọi thay đổi trong cùng giao dịch."""
from contextlib import nullcontext
from datetime import UTC,datetime
from decimal import Decimal

from zoneinfo import ZoneInfo

from sqlalchemy import func,select

from . import audit,day_closings,quotes,doc_numbers,pending_items as items,stock_docs,stock_posting
from .errors import BusinessError
from .models import PendingDisposition,PendingItem,PendingItemMove,StockBalance,StockDoc,StockLedger,Supplier,Warehouse,SalesDelivery,SalesOrder
from .schemas import StockDocIn
from .security import has_role

KINDS={'SUA_XONG':'Sửa xong','GUI_NCC':'Gửi NCC','NCC_TRA_VE':'NCC trả về','NCC_KHONG_NHAN':'NCC không nhận','HUY':'Hủy','TRA_NCC':'Trả NCC trừ nợ','THANH_LY':'Thanh lý'}
STATUSES={'CHO_DUYET':'Chờ duyệt','DA_GHI':'Đã ghi','TU_CHOI':'Từ chối','DA_HUY':'Đã hủy'}
TZ=ZoneInfo('Asia/Ho_Chi_Minh')


def director(user):
    if not has_role(user,'director'):raise BusinessError('FORBIDDEN','Chỉ giám đốc được duyệt hoặc hủy phiếu xử lý.',status=403)


def snapshot(row):
    return {key:(str(value) if key=='qty' else value.isoformat() if isinstance(value,datetime) else value)
        for key in ('number','item_id','kind','qty','to_warehouse_code','supplier_code','stock_doc_id','cost_value','status','reason','requested_by','requested_at','decided_by','decided_at','decision_note','void_reason','version','amount','vat_rate','vat_amount','total','delivery_id','order_line_no','voided_at') for value in [getattr(row,key)]}


def record(session,user,row,action,before=None,reason=''):
    session.flush();audit.record(session,user,'pending_disposition.'+action,'pending_disposition',str(row.id),before,snapshot(row),reason)


def reserved(session,item_id,exclude=None):
    stmt=select(func.coalesce(func.sum(PendingDisposition.qty),0)).where(PendingDisposition.item_id==item_id,PendingDisposition.status=='CHO_DUYET',PendingDisposition.kind.in_(('HUY','TRA_NCC')))
    if exclude is not None:stmt=stmt.where(PendingDisposition.id!=exclude)
    return session.scalar(stmt)


def ensure_available(session,item,qty,exclude=None):
    free=item.qty_left-reserved(session,item.id,exclude)
    from .pending_liquidation import check_available
    current=session.get(PendingDisposition,exclude) if exclude is not None else None
    delivery=session.get(SalesDelivery,current.delivery_id) if current and current.delivery_id else None
    check_available(session,item,qty,exclude_disposition=exclude,exclude_order=delivery.order_id if delivery else None)
    if qty>free:raise BusinessError('CXL_QTY_RESERVED',f'Số lượng vượt số còn xử lý được ({free}); đã trừ phiếu chờ duyệt.',status=409)


def _row(session,ident,version=None):
    row=session.scalar(select(PendingDisposition).where(PendingDisposition.id==ident).with_for_update().execution_options(populate_existing=True))
    if row is None:raise BusinessError('NOT_FOUND','Không tìm thấy phiếu xử lý.',status=404)
    if version is not None and version!=row.version:raise BusinessError('VERSION_CONFLICT','Phiếu xử lý vừa thay đổi, hãy tải lại.',status=409)
    return row


def _destination(session,item,kind,to_code,supplier):
    source=session.get(Warehouse,item.warehouse_code)
    if kind not in KINDS:raise BusinessError('BAD_KIND','Cách xử lý chưa hợp lệ.')
    if source.supplier_hold and kind not in ('NCC_TRA_VE','NCC_KHONG_NHAN','HUY','TRA_NCC') or not source.supplier_hold and kind in ('NCC_TRA_VE','NCC_KHONG_NHAN'):
        raise BusinessError('BAD_KIND','Cách xử lý không phù hợp với kho hiện tại.')
    if source.recovered_only and not source.supplier_hold and kind not in ('GUI_NCC','HUY'):
        raise BusinessError('BAD_KIND','Kho Thu hồi chỉ gửi NCC hoặc hủy; không chuyển sang kho hàng thường.')
    if kind in ('GUI_NCC','TRA_NCC') and not supplier:raise BusinessError('SUPPLIER_REQUIRED','Chọn nhà cung cấp.')
    if kind in ('GUI_NCC','TRA_NCC') and supplier and not session.scalar(select(Supplier.code).where(Supplier.code==supplier,Supplier.active.is_(True))):
        raise BusinessError('BAD_SUPPLIER','Nhà cung cấp không tồn tại hoặc đã ngừng dùng.')
    if kind=='THANH_LY' and (source.supplier_hold or source.recovered_only or item.group_code!='THANH_LY'):
        raise BusinessError('BAD_KIND','Chỉ thanh lý hồ sơ nhóm Thanh lý tại LOI thường.')
    if kind=='TRA_NCC' and source.recovered_only:raise BusinessError('BAD_KIND','Kho Thu hồi chỉ gửi NCC hoặc hủy.')
    if kind in ('HUY','TRA_NCC','THANH_LY'):return None
    target=session.get(Warehouse,to_code) if to_code else None
    expected='KHO' if kind in ('SUA_XONG','NCC_TRA_VE') else 'LOI'
    if target is None or not target.active or target.code==source.code or target.kind!=expected or (expected=='LOI' and target.supplier_hold!=(kind=='GUI_NCC')):
        raise BusinessError('BAD_WAREHOUSE','Chọn đúng kho đích đang dùng cho cách xử lý này.')
    if (target.recovered_only and kind!='GUI_NCC') or (source.recovered_only and target.kind=='LOI' and not target.recovered_only) or (kind=='GUI_NCC' and target.recovered_only!=source.recovered_only):
        raise BusinessError('BAD_WAREHOUSE','Không chuyển giữa Kho Thu hồi và LOI thường. Hàng thu hồi gửi vào kho NCC riêng có cờ Kho Thu hồi.')
    return target


def create(session,user,item_id,kind,qty,version,*,to_warehouse_code=None,supplier_code=None,reason='',amount=0,vat_rate=0,_in_tx=False):
    items.writer(user)
    if kind=='THANH_LY':raise BusinessError('BAD_KIND','Thanh lý phải giao qua đơn bán.')
    with (nullcontext() if _in_tx else stock_docs._transaction(session)):
        item=items.load(session,user,item_id);qty=items.quantity(qty);reason=(reason or '').strip()
        if kind in ('HUY','NCC_KHONG_NHAN','TRA_NCC') and not reason:raise BusinessError('REASON_REQUIRED','Phải ghi lý do xử lý.')
        if len(reason)>1900:raise BusinessError('BAD_REASON','Lý do tối đa 1.900 ký tự.')
        supplier=item.supplier_code if supplier_code is None else supplier_code
        target=_destination(session,item,kind,to_warehouse_code,supplier)
        stamp=datetime.now(UTC)
        value=vat=total=rate=0
        if kind=='TRA_NCC':
            if isinstance(amount,bool) or not (str(amount).isascii() and str(amount).isdigit()) or int(amount)<=0 or str(vat_rate) not in ('0','5','8','10'):
                raise BusinessError('BAD_AMOUNT','Nhập tiền NCC chấp nhận trừ nguyên đồng, lớn hơn 0 và VAT 0/5/8/10%.')
            value=quotes._money_limit(int(amount));rate=int(vat_rate);vat=quotes._money_limit(quotes._round(Decimal(value)*rate/100));total=quotes._money_limit(value+vat)
        row=PendingDisposition(number=doc_numbers.next_number(session,'XL',stamp.astimezone(TZ).date()),item_id=item.id,kind=kind,qty=qty,
            to_warehouse_code=target.code if target else None,supplier_code=supplier,reason=reason,requested_by=user.id,requested_at=stamp,
            status='CHO_DUYET',cost_value=0,decision_note='',void_reason='',amount=value,vat_rate=rate,vat_amount=vat,total=total)
        # Request holds are checked under the same balance/item locks used by stock posting.
        if kind=='TRA_NCC' or (kind=='HUY' and not has_role(user,'director')):
            item=items._locked(session,user,item_id,version);ensure_available(session,item,qty)
            session.add(row);record(session,user,row,'request',reason=reason)
        else:
            session.add(row);record(session,user,row,'request',reason=reason)
            _post(session,user,row,version)
    return row


def _post(session,user,row,item_version=None):
    item=session.get(PendingItem,row.item_id)
    _destination(session,item,row.kind,row.to_warehouse_code,row.supplier_code)
    day=datetime.now(UTC).astimezone(TZ).date()
    if row.kind=='TRA_NCC':day_closings.check_open(session,day)
    delivery=session.get(SalesDelivery,row.delivery_id) if row.delivery_id else None
    doc=stock_docs.insert(session,user,StockDocIn(kind='PX' if row.kind in ('HUY','TRA_NCC','THANH_LY') else 'CK',warehouse_code=item.warehouse_code,
        to_warehouse_code=row.to_warehouse_code,doc_date=day,
        supplier_code=row.supplier_code if row.kind=='TRA_NCC' else None,customer_code=(session.get(SalesOrder,delivery.order_id).customer_code if delivery else None),
        reason=f'Hủy hàng · {row.number}' if row.kind=='HUY' else f'{KINDS[row.kind]} · {row.number}',note=row.reason,
        lines=[dict(product_code=item.product_code,qty=row.qty)]),_pending_disposition=True)
    keys={(item.warehouse_code,item.product_code)}
    if row.to_warehouse_code:keys.add((row.to_warehouse_code,item.product_code))
    stock_posting._lock_balances(session,keys)
    # Approval checks fresh quantity; creation also checks the version shown on the form.
    fresh=session.scalar(select(PendingItem).where(PendingItem.id==item.id).with_for_update().execution_options(populate_existing=True))
    if item_version is not None and fresh.version!=item_version:raise BusinessError('VERSION_CONFLICT','Hồ sơ vừa thay đổi, hãy tải lại.',status=409)
    if fresh.status!='DANG_CHO':raise BusinessError('BAD_STATUS','Hồ sơ không còn chờ xử lý.',status=409)
    ensure_available(session,fresh,row.qty,row.id)
    row.stock_doc_id=doc.id;session.flush()
    stock_posting.post_in_tx(session,user,doc.id,doc.version,_pending_disposition=True,for_order_id=delivery.order_id if delivery else None)
    before=snapshot(row);row.status='DA_GHI';row.decided_by=user.id;row.decided_at=doc.posted_at
    record(session,user,row,'post',before)


def apply_posted_in_tx(session,user,doc):
    row=session.scalar(select(PendingDisposition).where(PendingDisposition.stock_doc_id==doc.id,PendingDisposition.status=='CHO_DUYET').with_for_update())
    if row is None:raise BusinessError('BAD_DISPOSITION','Phiếu kho không thuộc phiếu xử lý đang chờ.')
    item=session.get(PendingItem,row.item_id)
    if len(doc.lines)!=1 or doc.warehouse_code!=item.warehouse_code or doc.lines[0].product_code!=item.product_code or doc.lines[0].qty!=row.qty:
        raise BusinessError('BAD_DISPOSITION','Dòng hàng không khớp phiếu xử lý.')
    ensure_available(session,item,row.qty,row.id)
    before=items.snapshot(item);item.qty_left-=row.qty
    if item.qty_left==0:item.status='XONG'
    items._record(session,user,item,'disposition',before,row.number)
    session.add(PendingItemMove(item_id=item.id,disposition_id=row.id,stock_doc_id=doc.id,qty=row.qty))
    outgoing=session.scalar(select(StockLedger).where(StockLedger.doc_id==doc.id,StockLedger.qty_out>0,StockLedger.is_reversal.is_(False)))
    row.cost_value=-outgoing.amount
    if row.kind in ('GUI_NCC','NCC_KHONG_NHAN'):
        incoming=session.scalar(select(StockLedger).where(StockLedger.doc_id==doc.id,StockLedger.qty_in>0,StockLedger.is_reversal.is_(False)))
        items.open_in_tx(session,user,row.to_warehouse_code,item.product_code,row.qty,incoming.unit_cost,
            'CHO_BH_NCC' if row.kind=='GUI_NCC' else 'HANG_LOI',item.source,item.source_ref,doc,row.reason,row.supplier_code,
            entered_at=item.entered_at,sent_at=doc.posted_at if row.kind=='GUI_NCC' else None)
    session.flush()


def _decider(session,user,ident):
    row=session.get(PendingDisposition,ident)
    if row and row.kind=='TRA_NCC':
        if not has_role(user,'finance'):raise BusinessError('FORBIDDEN','Chỉ kế toán hoặc giám đốc được xử lý Trả NCC.',status=403)
    else:director(user)


def approve(session,user,ident,version):
    _decider(session,user,ident)
    with stock_docs._transaction(session):
        row=_row(session,ident,version)
        if row.status!='CHO_DUYET' or row.kind not in ('HUY','TRA_NCC'):raise BusinessError('BAD_STATUS','Chỉ duyệt phiếu Hủy hoặc Trả NCC đang chờ.',status=409)
        _post(session,user,row)
    return row


def reject(session,user,ident,version,reason):
    _decider(session,user,ident);reason=(reason or '').strip()
    if not reason:raise BusinessError('REASON_REQUIRED','Phải ghi lý do từ chối.')
    with stock_docs._transaction(session):
        row=_row(session,ident,version)
        if row.status!='CHO_DUYET':raise BusinessError('BAD_STATUS','Phiếu không còn chờ duyệt.',status=409)
        before=snapshot(row);row.status='TU_CHOI';row.decided_by=user.id;row.decided_at=datetime.now(UTC);row.decision_note=reason
        record(session,user,row,'reject',before,reason)
    return row


def restore_in_tx(session,user,doc,reason):
    """Called by void_in_tx after all affected balances are locked."""
    rows=list(session.scalars(select(PendingItemMove).where(PendingItemMove.stock_doc_id==doc.id).order_by(PendingItemMove.item_id)))
    if not rows:items.block_out()
    children=list(session.scalars(select(PendingItem).where(PendingItem.stock_doc_id==doc.id).order_by(PendingItem.id).with_for_update().execution_options(populate_existing=True)))
    for child in children:
        used=session.scalar(select(PendingItemMove.id).where(PendingItemMove.item_id==child.id).limit(1))
        if child.status!='DANG_CHO' or child.qty_left!=child.qty_in or used or reserved(session,child.id):
            raise BusinessError('CXL_DA_XU_LY','Hồ sơ ở kho nhận đã tách hoặc có phiếu xử lý khác, không thể hủy.',status=409)
    for child in children:
        before=items.snapshot(child);child.qty_left=0;child.status='DA_HUY';items._record(session,user,child,'cancel',before,reason)
    for move in rows:
        item=session.scalar(select(PendingItem).where(PendingItem.id==move.item_id).with_for_update().execution_options(populate_existing=True))
        before=items.snapshot(item);item.qty_left+=move.qty;item.status='DANG_CHO';items._record(session,user,item,'restore',before,reason)


def void(session,user,ident,version,reason):
    _decider(session,user,ident);reason=(reason or '').strip()
    if len(reason)<3:raise BusinessError('REASON_REQUIRED','Ghi lý do hủy ít nhất 3 ký tự.')
    with stock_docs._transaction(session):
        row=_row(session,ident,version)
        if row.status!='DA_GHI':raise BusinessError('BAD_STATUS','Chỉ hủy được phiếu đã ghi.',status=409)
        if row.kind=='THANH_LY':raise BusinessError('SOURCE_LOCKED','Hủy thanh lý tại lần giao trên đơn bán.',status=409)
        if row.kind=='TRA_NCC':day_closings.check_days(session,[row.decided_at.astimezone(TZ).date(),datetime.now(UTC).astimezone(TZ).date()])
        stock_posting.void_in_tx(session,user,row.stock_doc_id,reason,None,_pending_disposition=True)
        before=snapshot(row);row.status='DA_HUY';row.void_reason=reason;row.voided_at=datetime.now(UTC)
        record(session,user,row,'void',before,reason)
    return row
