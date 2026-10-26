"""CXL1: hồ sơ đi kèm mỗi lần nhập kho LOI, cùng transaction ghi sổ."""
from datetime import UTC,datetime,timedelta
from contextlib import nullcontext
from decimal import Decimal,InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import func,select
from pydantic import ValidationError

from . import audit,doc_numbers
from .config import get_settings
from .errors import BusinessError
from .models import PendingItem,Product,StockBalance,StockLedger,Supplier,Warehouse
from .security import has_role
from .stock_docs import _transaction
from .text import fold
from . import search as tim

GROUPS={'HANG_LOI':'Hàng lỗi','CHO_BH_NCC':'Chờ BH NCC','THANH_LY':'Thanh lý','THU_HOI':'Thu hồi','CHUA_PHAN_LOAI':'Chưa phân loại'}
SOURCES={'THU_HOI':'Linh kiện thu hồi','THO_BAO_HU':'Thợ báo hư','KHACH_TRA':'Khách trả','CHUYEN_TAY':'Chuyển tay','KIEM_KE':'Kiểm kê','TON_CU':'Tồn cũ'}
OUT_MESSAGE='Hàng trong Kho Chờ Xử lý chỉ được xử lý qua màn Chờ xử lý.'


def reader(user):
    if not has_role(user,'warehouse','finance','dispatcher'):raise BusinessError('FORBIDDEN','Chỉ kho, kế toán, điều phối hoặc giám đốc được xem Chờ xử lý.',status=403)


def writer(user):
    if not has_role(user,'warehouse'):raise BusinessError('FORBIDDEN','Chỉ thủ kho hoặc giám đốc được xử lý hồ sơ.',status=403)


def can_value(user):return has_role(user,'warehouse','finance')


def block_out():raise BusinessError('CXL_DUNG_LUONG_RIENG',OUT_MESSAGE,status=409)


def snapshot(row):
    return {key:(str(value) if isinstance(value,Decimal) else value.isoformat() if isinstance(value,datetime) else value)
        for key in ('number','warehouse_code','product_code','qty_in','qty_left','unit_cost','group_code','source','source_ref','stock_doc_id','supplier_code','reason','entered_at','sent_at','created_by','status','version') for value in [getattr(row,key)]}


def _record(session,user,row,action,before,reason=''):
    session.flush();audit.record(session,user,'pending_item.'+action,'pending_item',str(row.id),before,snapshot(row),reason)


def quantity(value):
    try:qty=Decimal(str(value))
    except (InvalidOperation,ValueError):raise BusinessError('BAD_QTY','Số lượng chưa hợp lệ.')
    if not qty.is_finite() or qty<=0 or qty>=Decimal('100000000000') or qty.as_tuple().exponent < -3:
        raise BusinessError('BAD_QTY','Số lượng phải lớn hơn 0, tối đa 3 số thập phân.')
    return qty


def _group(session,group_code,supplier_code=None):
    if group_code not in GROUPS:raise BusinessError('BAD_GROUP','Nhóm chờ xử lý không tồn tại.')
    if supplier_code and not session.scalar(select(Supplier.code).where(Supplier.code==supplier_code,Supplier.active.is_(True))):
        raise BusinessError('BAD_SUPPLIER','Nhà cung cấp không tồn tại hoặc đã ngừng dùng.')


def open_in_tx(session,user,warehouse_code,product_code,qty,unit_cost,group_code,source,source_ref,stock_doc,reason='',supplier_code=None,*,entered_at=None,sent_at=None):
    qty=quantity(qty);_group(session,group_code,supplier_code)
    wh=session.get(Warehouse,warehouse_code)
    if wh is None or wh.kind!='LOI':raise BusinessError('BAD_WAREHOUSE','Hồ sơ chỉ dành cho Kho Chờ Xử lý.')
    if source not in SOURCES:raise BusinessError('BAD_SOURCE','Nguồn hồ sơ chưa hợp lệ.')
    if stock_doc.status!='DA_GHI' or stock_doc.posted_at is None:raise BusinessError('DOC_NOT_POSTED','Phải ghi sổ trước khi mở hồ sơ.')
    stamp=stock_doc.posted_at
    row=PendingItem(number=doc_numbers.next_number(session,'CXL',stamp.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date()),
        warehouse_code=warehouse_code,product_code=product_code,qty_in=qty,qty_left=qty,unit_cost=unit_cost,group_code=group_code,
        source=source,source_ref=source_ref,stock_doc_id=stock_doc.id,supplier_code=supplier_code,reason=reason,
        entered_at=entered_at or stamp,sent_at=sent_at,created_by=user.id,status='DANG_CHO')
    session.add(row);_record(session,user,row,'create',None,reason)
    return row


def apply_posted_in_tx(session,user,doc,context=None):
    """Called once by stock_posting with balances still locked; covers manual PN/CK/KK too."""
    rows=list(session.scalars(select(StockLedger).join(Warehouse,Warehouse.code==StockLedger.warehouse_code)
        .where(StockLedger.doc_id==doc.id,StockLedger.is_reversal.is_(False),Warehouse.kind=='LOI').order_by(StockLedger.id)))
    context=context or {}
    for ledger in rows:
        if ledger.qty_out:
            _consume(session,user,ledger.warehouse_code,ledger.product_code,ledger.qty_out,'Kiểm kê thiếu')
        elif ledger.qty_in:
            meta=context.get(ledger.line_no,{})
            source='KIEM_KE' if doc.kind=='KK' else 'CHUYEN_TAY'
            open_in_tx(session,user,ledger.warehouse_code,ledger.product_code,ledger.qty_in,ledger.unit_cost,
                meta.get('group_code','CHUA_PHAN_LOAI'),meta.get('source',source),meta.get('source_ref',doc.number),doc,
                meta.get('reason',doc.reason),meta.get('supplier_code'))


def _consume(session,user,warehouse,product,qty,reason):
    rows=list(session.scalars(select(PendingItem).where(PendingItem.warehouse_code==warehouse,PendingItem.product_code==product,
        PendingItem.status=='DANG_CHO').order_by(PendingItem.entered_at,PendingItem.id).with_for_update().execution_options(populate_existing=True)))
    remaining=qty
    for row in rows:
        if not remaining:break
        before=snapshot(row);used=min(remaining,row.qty_left);row.qty_left-=used;remaining-=used
        if row.qty_left==0:row.status='XONG'
        _record(session,user,row,'inventory_shortage',before,reason)
    if remaining:raise BusinessError('CXL_INVARIANT','Hồ sơ chờ xử lý không khớp tồn kho. Hãy kiểm tra lại.',status=409)


def transfer_in(session,user,from_warehouse_code,lines,group_code,reason,supplier_code=None,*,warehouse_code=None,_in_tx=False):
    from . import stock_docs,stock_posting
    from .schemas import StockDocIn
    writer(user)
    with (nullcontext() if _in_tx else _transaction(session)):
        _group(session,group_code,supplier_code)
        if group_code not in ('HANG_LOI','CHO_BH_NCC','THANH_LY'):raise BusinessError('BAD_GROUP','Chuyển tay chỉ chọn Hàng lỗi, Chờ BH NCC hoặc Thanh lý.')
        reason=(reason or '').strip()
        if not reason:raise BusinessError('REASON_REQUIRED','Phải ghi lý do chuyển vào Chờ xử lý.')
        source=session.get(Warehouse,from_warehouse_code)
        if source is None or source.kind!='KHO' or not source.active:raise BusinessError('BAD_WAREHOUSE','Kho nguồn phải là kho công ty đang dùng.')
        stmt=select(Warehouse).where(Warehouse.kind=='LOI',Warehouse.supplier_hold.is_(False),Warehouse.recovered_only.is_(False),Warehouse.active.is_(True)).order_by(Warehouse.code)
        if warehouse_code:stmt=stmt.where(Warehouse.code==warehouse_code)
        choices=list(session.scalars(stmt))
        if not choices:raise BusinessError('NO_DEFECT_WAREHOUSE','Chưa có Kho Chờ Xử lý đang dùng.',status=409)
        if not warehouse_code and len(choices)>1:raise BusinessError('WAREHOUSE_REQUIRED','Hãy chọn Kho Chờ Xử lý nhận hàng.')
        stamp=datetime.now(UTC);today=stamp.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date()
        try:body=StockDocIn(kind='CK',warehouse_code=source.code,to_warehouse_code=choices[0].code,doc_date=today,reason=reason,lines=lines)
        except ValidationError as exc:
            raise BusinessError('BAD_FORM','Hãy chọn hàng từ kết quả tìm kiếm, nhập số lượng hợp lệ và lý do tối đa 2.000 ký tự.') from exc
        doc=stock_docs.insert(session,user,body, source_kind='PENDING_ITEM')
        context={line.line_no:dict(group_code=group_code,source='CHUYEN_TAY',source_ref=doc.number,reason=reason,supplier_code=supplier_code) for line in doc.lines}
        stock_posting.post_in_tx(session,user,doc.id,doc.version,pending_context=context)
        return list(session.scalars(select(PendingItem).where(PendingItem.stock_doc_id==doc.id).order_by(PendingItem.id)))


def load(session,user,ident):
    reader(user);row=session.get(PendingItem,ident)
    if row is None:raise BusinessError('NOT_FOUND','Không tìm thấy hồ sơ chờ xử lý.',status=404)
    return row


def _locked(session,user,ident,version):
    writer(user)
    # Same order as posting: balance then pending row. Splitting never waits for a stock writer holding a row.
    key=session.execute(select(PendingItem.warehouse_code,PendingItem.product_code).where(PendingItem.id==ident)).first()
    if key is None:raise BusinessError('NOT_FOUND','Không tìm thấy hồ sơ chờ xử lý.',status=404)
    session.scalar(select(StockBalance).where(StockBalance.warehouse_code==key.warehouse_code,StockBalance.product_code==key.product_code).with_for_update())
    row=session.scalar(select(PendingItem).where(PendingItem.id==ident).with_for_update().execution_options(populate_existing=True))
    if version!=row.version:raise BusinessError('VERSION_CONFLICT','Người khác vừa sửa hồ sơ, hãy tải lại.',status=409)
    if row.status!='DANG_CHO':raise BusinessError('BAD_STATUS','Hồ sơ đã xong, không thể sửa.',status=409)
    return row


def set_group(session,user,item_id,group_code,version,reason='',supplier_code=None):
    with _transaction(session):
        row=_locked(session,user,item_id,version);_group(session,group_code,supplier_code);before=snapshot(row)
        if row.group_code=='THANH_LY' and group_code!='THANH_LY':
            from .pending_liquidation import check_available
            check_available(session,row,row.qty_left)
        row.group_code=group_code;row.supplier_code=supplier_code;row.reason=reason.strip() or row.reason
        row.version+=1;_record(session,user,row,'group',before,reason)
    return row


def split(session,user,item_id,qty,group_code,version):
    with _transaction(session):
        row=_locked(session,user,item_id,version);qty=quantity(qty);_group(session,group_code)
        if qty>=row.qty_left:raise BusinessError('BAD_QTY','Số tách phải nhỏ hơn số còn chờ xử lý.')
        from .pending_dispositions import ensure_available
        ensure_available(session,row,qty)
        if group_code==row.group_code:raise BusinessError('BAD_GROUP','Hãy chọn nhóm khác để tách.')
        before=snapshot(row);row.qty_left-=qty
        stamp=datetime.now(UTC).astimezone(ZoneInfo('Asia/Ho_Chi_Minh'))
        data={key:getattr(row,key) for key in ('warehouse_code','product_code','unit_cost','source','source_ref','stock_doc_id','supplier_code','reason','entered_at','sent_at')}
        child=PendingItem(**data,number=doc_numbers.next_number(session,'CXL',stamp.date()),qty_in=qty,qty_left=qty,
            group_code=group_code,status='DANG_CHO',created_by=user.id)
        session.add(child);_record(session,user,child,'create',None,f'Tách từ {row.number}')
        _record(session,user,row,'split',before,f'Tách {qty} sang {child.number}')
    return child


def search(session,user,group=None,warehouse=None,min_days=None,q='',status='DANG_CHO',view=''):
    reader(user)
    if group and group not in GROUPS:raise BusinessError('BAD_GROUP','Nhóm chưa hợp lệ.')
    if status not in ('DANG_CHO','XONG','DA_HUY',''):raise BusinessError('BAD_STATUS','Trạng thái chưa hợp lệ.')
    if min_days is not None and (min_days<0 or min_days>36500):raise BusinessError('BAD_DAYS','Số ngày lọc chưa hợp lệ.')
    stmt=select(PendingItem,Product.name,Warehouse.supplier_hold).join(Product,Product.code==PendingItem.product_code).join(Warehouse,Warehouse.code==PendingItem.warehouse_code).order_by(PendingItem.entered_at,PendingItem.id)
    if view=='ncc':stmt=stmt.where(PendingItem.warehouse_code.in_(select(Warehouse.code).where(Warehouse.supplier_hold.is_(True))))
    elif view=='huy':
        from .models import PendingDisposition
        stmt=stmt.where(PendingItem.id.in_(select(PendingDisposition.item_id).where(PendingDisposition.kind=='HUY',PendingDisposition.status=='CHO_DUYET')))
    if group:stmt=stmt.where(PendingItem.group_code==group)
    if warehouse:stmt=stmt.where(PendingItem.warehouse_code==warehouse)
    if status:stmt=stmt.where(PendingItem.status==status)
    now=datetime.now(UTC)
    if min_days is not None:stmt=stmt.where(PendingItem.entered_at<now-timedelta(days=min_days))
    needle=fold(q)
    return [dict(item=row,name=name,sent_days=max(0,(now-row.sent_at).days) if row.sent_at else None,days=max(0,(now-row.entered_at).days),overdue=(bool(row.sent_at and now-row.sent_at>timedelta(days=get_settings().supplier_hold_alert_days)) if holding else now-row.entered_at>timedelta(days=get_settings().pending_alert_days)))
        for row,name,holding in session.execute(stmt) if not needle or tim.text_matches(q,' '.join((row.number,row.product_code,name,row.source_ref)))]


def totals(session,user,now=None):
    reader(user);now=now or datetime.now(UTC)
    rows=session.execute(select(PendingItem.group_code,func.count(),func.coalesce(func.sum(PendingItem.qty_left*PendingItem.unit_cost),0),
        func.count().filter(PendingItem.entered_at<now-timedelta(days=get_settings().pending_alert_days)))
        .where(PendingItem.status=='DANG_CHO').group_by(PendingItem.group_code)).all()
    return {code:dict(count=count,value=value,overdue=overdue) for code,count,value,overdue in rows}


def cancel_receipt_in_tx(session,user,doc,ledgers,reason):
    """Receipt reversal is allowed only while every associated lot is untouched."""
    defect_codes=set(session.scalars(select(Warehouse.code).where(Warehouse.kind=='LOI')))
    affected=[row for row in ledgers if row.warehouse_code in defect_codes]
    if not affected:return
    if doc.kind not in ('PN','CK') or any(row.qty_out for row in affected):block_out()
    rows=list(session.scalars(select(PendingItem).where(PendingItem.stock_doc_id==doc.id).order_by(PendingItem.id).with_for_update().execution_options(populate_existing=True)))
    from .pending_dispositions import reserved
    from .models import PendingItemMove
    processed=any(reserved(session,row.id) or session.scalar(select(PendingItemMove.id).where(PendingItemMove.item_id==row.id).limit(1)) for row in rows)
    if not rows or processed or any(row.status!='DANG_CHO' or row.qty_left!=row.qty_in for row in rows):
        raise BusinessError('CXL_DA_XU_LY','Hồ sơ đã tách hoặc đã xử lý một phần, không thể hủy phiếu nhập.',status=409)
    for row in rows:
        before=snapshot(row);row.qty_left=Decimal(0);row.status='DA_HUY'
        _record(session,user,row,'cancel',before,reason)
