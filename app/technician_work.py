"""UX1e: one customer total, materials at cost, direct technician handover."""
from collections import Counter
from decimal import Decimal

from sqlalchemy import select

from . import service_charges, service_tickets, technician_returns, technician_stock
from .config import DEFAULT_VAT_RATE
from .errors import BusinessError
from .models import Product, StockBalance, User, TechnicianReturn
from .schemas import ServiceCharges, SalesReceiptReport

FEE_DESCRIPTION = 'Phí dịch vụ (gồm công và vật tư)'


def net_from_gross(total, vat=DEFAULT_VAT_RATE):
    center=service_charges._round(Decimal(total)*100/(100+vat))
    candidates=[(net,service_charges._round(Decimal(net)*vat/100)) for net in range(max(0,center-2),center+3)]
    net,tax=min(candidates,key=lambda pair:(abs(sum(pair)-total),abs(pair[0]-center),pair[0]))
    return net,tax


def money(value):
    import re
    raw=str(value or '').strip()
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)',raw) or len(raw.replace('.',''))>18:
        raise BusinessError('BAD_AMOUNT','Số tiền phải là số nguyên đồng, có thể dùng dấu chấm hàng nghìn.')
    return int(raw.replace('.',''))


def charges(session,user,ticket,version,total,mode,reason,materials):
    if mode not in ('','FULL','PARTIAL','FREE'):
        raise BusinessError('BAD_COLLECTION','Chọn Thu đủ, Thu một phần hoặc Miễn phí.')
    free=mode=='FREE'
    amount=0 if free else money(total)
    if not free and amount<=0:
        raise BusinessError('CUSTOMER_AMOUNT_REQUIRED','Chưa nhập tiền thu khách hoặc chọn Miễn phí')
    own=technician_stock.own_warehouse(session,user,lock=True)
    # Issued rows are immutable; foreign unissued rows survive an internal reopen.
    lines=[service_charges.line_input(x) for x in ticket.lines if x.kind=='VAT_TU' and
        (x.stock_doc_id is not None or own is None or x.warehouse_code!=own.code)]
    lines.extend(material_lines(session,user,materials))
    net,tax=net_from_gross(amount)
    if not free:lines.append(dict(kind='PHI',description=FEE_DESCRIPTION,qty=1,unit_price=net,vat_rate=DEFAULT_VAT_RATE))
    return ServiceCharges(version=version,billing='MIEN_PHI' if free else 'THU_TIEN',free_reason=reason if free else '',lines=lines),net+tax


def material_lines(session,user,materials):
    own=technician_stock.own_warehouse(session,user,lock=True)
    lines=[];counts=Counter()
    for material in materials:
        qty=technician_returns.quantity(material.get('qty',0))
        if not qty:continue
        if not own:raise BusinessError('NO_TECH_WAREHOUSE','Thợ chưa có kho riêng.',status=409)
        code=material.get('product_code','');product=session.get(Product,code)
        if not product or not product.active:raise BusinessError('BAD_PRODUCT','Hàng không còn đang dùng.')
        counts[code]+=qty
        # Never trust warehouse, price, tax or amount from the browser.
        lines.append(dict(kind='VAT_TU',description=product.name,product_code=code,warehouse_code=own.code,qty=qty,unit_price=0,vat_rate=0))
    for code in sorted(counts):
        balance=session.scalar(select(StockBalance).where(StockBalance.warehouse_code==own.code,StockBalance.product_code==code))
        if not balance or counts[code]>balance.qty:
            raise BusinessError('NEGATIVE_STOCK','Số vật tư vượt số đang giữ trong kho của bạn.',status=409)
    return lines


def apply_report(session,t,fault,fix):
    """Báo cáo thợ: bắt buộc kết quả/cách xử lý; lỗi gặp và ảnh biên bản không bắt buộc (TUẤN 22/10, chỉ cần ảnh nghiệm thu)."""
    fault,fix=(fault or '').strip(),(fix or '').strip()
    if len(fix)<3:raise BusinessError('FIX_REQUIRED','Chọn kết quả sửa: Đã sửa xong hoặc Xong một phần.')
    if len(fault)>2000 or len(fix)>2000:raise BusinessError('TEXT_TOO_LONG','Lỗi gặp và cách xử lý tối đa 2000 ký tự.')
    t.fault_found,t.fix_done=fault,fix


def complete(session,user,ticket_id,version,total,mode,reason,materials,partial='',method='TIEN_MAT',note='',fault='',fix='',return_machine=None,warranty_months=None,arrive=None):
    from .stock_docs import _transaction
    from .models import ServiceDraft
    with _transaction(session):
        t=service_tickets._get(session,user,ticket_id,lock=True)
        if 'technician' not in user.roles or t.assignee_id!=user.id:
            raise BusinessError('FORBIDDEN','Chỉ thợ được giao được hoàn thành phiếu.',status=403)
        service_tickets._check_version(t,version)
        if arrive is not None and t.status=='DA_HEN':
            # Trả máy là một màn (TUẤN 24/10): Đã đến khách + vị trí + trả + thu cùng một transaction, lỗi thì không đổi gì.
            service_tickets.transition_in_tx(session,user,t,'DANG_LAM',arrive.get('note') or 'Đến khách trả máy')
            if arrive.get('lat'):
                from .service_visits import record_arrival_in_tx
                record_arrival_in_tx(session,user,t.id,arrive.get('lat'),arrive.get('lng'),arrive.get('accuracy'))
        if t.status!='DANG_LAM':raise BusinessError('BAD_STATUS','Chỉ hoàn thành phiếu đang làm.',status=409)
        service_tickets.require_kind(t)
        if t.serial:
            from .equipment_custody import _machine
            _machine(session,t.serial,lock=True)  # ticket -> machine -> warehouse, also for two dossiers sharing a serial
        locked=service_charges.return_locked(t)
        if locked:
            actual=t.receivable or 0
            if materials or money(str(total))!=actual or mode not in ('','FULL','PARTIAL') or (reason and reason!=t.free_reason):
                raise BusinessError('CHARGES_LOCKED','Số phải thu đã chốt khi sửa xong; chỉ chọn cách thu, không sửa phí hoặc vật tư.',status=409)
        else:
            apply_report(session,t,fault,fix)
            body,actual=charges(session,user,t,version,total,mode,reason,materials)
            # TUẤN 24/10: sửa tại chỗ thợ chọn bảo hành khi Hoàn thành (mặc định 1 tháng); sửa ở xưởng đã chốt lúc Sửa xong.
            if t.kind=='MUC_DINH_KY':warranty_months=0  # Mực định kỳ không bảo hành sau sửa, kể cả số cũ từ nháp
            if warranty_months is not None:
                from .service_workshop import WARRANTIES
                if type(warranty_months) is not int or warranty_months not in WARRANTIES:
                    raise BusinessError('WARRANTY_REQUIRED','Chọn số tháng bảo hành: 0, 1, 2, 3, 6 hoặc 12.')
                t.warranty_months=warranty_months
        received=0
        if mode=='FULL':received=actual
        elif mode=='PARTIAL':
            received=money(partial)
            if not 0<received<actual:raise BusinessError('BAD_PARTIAL_AMOUNT','Thu một phần phải lớn hơn 0 và nhỏ hơn tổng phải thu.')
        if locked:
            if return_machine is not None:
                from .equipment_custody import return_customer_in_tx
                return_customer_in_tx(session,user,t.id,return_machine)
        else:service_charges.set_charges_in_tx(session,user,t,body)
        service_tickets.transition_in_tx(session,user,t,'HOAN_THANH',note,auto_close=False)
        if received:
            from . import sales_receipts
            sales_receipts.report_for_ticket_in_tx(session,user,t,SalesReceiptReport(version=t.version,amount=received,method=method,note=note))
        service_tickets.auto_close_in_tx(session,user,t)
        draft=session.get(ServiceDraft,(t.id,user.id))
        if draft is not None:session.delete(draft)
    return t


DRAFT_LIMIT=16000


def save_draft(session,user,ticket_id,data):
    """Thẻ B: lưu nháp báo cáo của thợ (chỉ chữ, tối đa ~16KB); ghi đè nháp cũ."""
    import json
    from datetime import UTC, datetime
    from .models import ServiceDraft
    from .stock_docs import _transaction
    if not isinstance(data,dict) or not all(isinstance(k,str) and (isinstance(v,str) or (isinstance(v,list) and all(isinstance(x,str) for x in v))) for k,v in data.items()):
        raise BusinessError('BAD_DRAFT','Nháp không hợp lệ.')
    if len(json.dumps(data,ensure_ascii=False))>DRAFT_LIMIT:raise BusinessError('DRAFT_TOO_LARGE','Nháp quá dài.')
    with _transaction(session):
        t=service_tickets._get(session,user,ticket_id)
        if 'technician' not in user.roles or t.assignee_id!=user.id:
            raise BusinessError('FORBIDDEN','Chỉ thợ được giao được lưu nháp phiếu.',status=403)
        if t.status not in service_tickets.OPEN:raise BusinessError('BAD_STATUS','Phiếu đã đóng, không lưu nháp.',status=409)
        from sqlalchemy.dialects.postgresql import insert
        # Hai lần tự lưu chạy cùng lúc: ghi đè theo khóa chính, không lỗi trùng.
        stmt=insert(ServiceDraft).values(ticket_id=t.id,user_id=user.id,data=data,updated_at=datetime.now(UTC))
        session.execute(stmt.on_conflict_do_update(index_elements=['ticket_id','user_id'],set_={'data':stmt.excluded.data,'updated_at':stmt.excluded.updated_at}))
    return True


def transfer(session,user,ticket_id,assignee_id,version,reason,failed=False,entries=None):
    with technician_returns.photo_transaction(session):
        t=service_tickets._get(session,user,ticket_id,lock=True)
        if 'technician' not in user.roles or t.assignee_id!=user.id:
            raise BusinessError('FORBIDDEN','Chỉ chuyển được phiếu của chính mình.',status=403)
        service_tickets._check_version(t,version)
        allowed=('DANG_LAM','CHO_LINH_KIEN','TAM_DUNG') if failed else ('DA_GIAO','DA_NHAN','DA_HEN')
        if t.status not in allowed:raise BusinessError('BAD_STATUS','Không chuyển thợ ở bước này.',status=409)
        if service_charges.return_locked(t):raise BusinessError('CHARGES_LOCKED','Điều phối cần phân công trả máy, không đổi phí hoặc chuyển việc sửa.',status=409)
        reason=(reason or '').strip()
        if len(reason)<3:raise BusinessError('REASON_REQUIRED','Phải ghi lý do chuyển thợ ít nhất 3 ký tự.')
        if assignee_id==user.id:raise BusinessError('SAME_ASSIGNEE','Chọn thợ khác với chính mình.')
        service_tickets._technician(session,assignee_id)
        proposal=technician_returns.proposal(session,t,lock=True)
        # Auto-return allocated stock; the form may split it by condition but may not omit it.
        required=Counter()
        for row in proposal['rows']:required[row['product_code']]+=row['qty']
        submitted=proposal['rows'] if entries is None else entries
        totals=Counter()
        for row in submitted:totals[row['product_code']]+=technician_returns.quantity(row['qty'])
        if any(totals[code]<qty for code,qty in required.items()):
            raise BusinessError('RETURN_REQUIRED','Phải tạo yêu cầu trả đủ vật tư đang giữ của phiếu trước khi chuyển thợ.')
        # Existing pending returns already cover their quantities; don't request the same stock twice.
        pending=list(session.scalars(select(TechnicianReturn).where(TechnicianReturn.ticket_id==t.id,TechnicianReturn.technician_id==user.id,TechnicianReturn.status=='CHO_NHAN').with_for_update()))
        covered=Counter()
        for request in pending:
            for line in request.lines:covered[line.product_code]+=line.qty
        adjusted=[]
        for row in submitted:
            qty=technician_returns.quantity(row['qty']);skip=min(qty,covered[row['product_code']]);covered[row['product_code']]-=skip
            adjusted.append({**row,'qty':qty-skip})
        submitted=adjusted
        grouped=technician_returns.prepare(session,proposal['warehouse'],submitted)
        technician_returns.create_requests(session,user,user.id,proposal['warehouse'],t.id,grouped)
        old_name=user.full_name;new_name=session.get(User,assignee_id).full_name
        history=f'Thợ {old_name} chuyển cho thợ {new_name}: {reason}'
        # The visit outcome differs from a dispatcher reassignment.
        if failed and t.status=='DANG_LAM':
            from . import service_visits
            service_visits.close_visit(session,t,'KHONG_LAM_DUOC',service_tickets._now(),history)
        t.lines[:]=[line for line in t.lines if line.stock_doc_id is not None];session.flush()
        keep_free=bool(t.warranty_basis and t.billing=='MIEN_PHI')
        t.billing='MIEN_PHI' if keep_free else None
        t.free_reason=t.free_reason if keep_free else ''
        service_tickets.assign_in_tx(session,user,t.id,assignee_id,None,history,_technician_transfer=True)
    return t
