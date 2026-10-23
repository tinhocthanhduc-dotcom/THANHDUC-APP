"""S4: giao đơn bán qua kho thợ, mọi ghi kho/tiền qua lõi hiện có."""
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select

from . import attachments, audit, sales_orders, sales_receipts, stock_docs, stock_posting, stock_reservations
from .errors import BusinessError
from .models import AuditLog, Customer, SalesDelivery, SalesDeliveryAttempt, SalesOrder, ServiceTicket, StockReservation, User, Warehouse
from .schemas import StockDocIn, StockDocLineIn
from .security import has_role


def check_assignee(user, order):
    if not (has_role(user,'technician') and order.delivery_assignee_id==user.id):
        raise BusinessError('FORBIDDEN','Chỉ thợ được giao đơn mới thực hiện được.',status=403)


def load(session, user, order_id):
    order=sales_orders._load(session,order_id)
    if not has_role(user,'dispatcher','sales','warehouse','finance'):
        check_assignee(user,order)
    return order


def warehouse(session, assignee_id):
    tech=session.scalar(select(User).where(User.id==assignee_id,User.active.is_(True),User.roles.contains(['technician'])).with_for_update(read=True))
    wh=session.scalar(select(Warehouse).where(Warehouse.holder_id==assignee_id,Warehouse.kind=='THO',Warehouse.active.is_(True)).with_for_update(read=True))
    if tech is None or wh is None:
        raise BusinessError('NO_TECH_WAREHOUSE','Thợ phải đang hoạt động và có kho thợ đang dùng.',status=409)
    return wh


def _held(session, order):
    return list(session.scalars(select(StockReservation).where(StockReservation.order_id==order.id,StockReservation.status=='GIU')))


def held_by_tech(session, order):
    return list(session.scalars(select(StockReservation).join(Warehouse).where(
        StockReservation.order_id==order.id,StockReservation.status=='GIU',Warehouse.kind=='THO')))


def assign(session,user,order_id,body):
    if not has_role(user,'dispatcher'):
        raise BusinessError('FORBIDDEN','Chỉ điều phối hoặc giám đốc được giao thợ.',status=403)
    with sales_orders._transaction(session):
        order=sales_orders._load(session,order_id,lock=True)
        sales_orders._version(order,body.version)
        sales_orders._status(order,('DA_CHOT','GIAO_MOT_PHAN'))
        from .pending_liquidation import check_fulfillment
        check_fulfillment(session,order,for_technician=True)
        if order.fulfillment!='THO_GIAO':
            raise BusinessError('BAD_FULFILLMENT','Chỉ phân công thợ cho đơn Thợ giao.')
        warehouse(session,body.assignee_id)
        if order.delivery_assignee_id!=body.assignee_id and held_by_tech(session,order):
            raise BusinessError('HELD_BY_TECH','Phải trả hàng đang giữ ở kho thợ trước khi đổi thợ.',status=409)
        if body.service_ticket_id is not None:
            ticket=session.scalar(select(ServiceTicket).where(ServiceTicket.id==body.service_ticket_id).with_for_update(read=True))
            if ticket is None or ticket.customer_code!=order.customer_code or ticket.status not in ('MOI','DA_GIAO','DA_NHAN','DA_HEN','DANG_LAM','CHO_LINH_KIEN','TAM_DUNG'):
                raise BusinessError('BAD_SERVICE_TICKET','Phiếu dịch vụ cùng chuyến phải đang mở và cùng khách.')
        before=sales_orders.snapshot(order)
        ticket_id=body.service_ticket_id
        if body.new_service_symptom is not None:
            # Kèm dịch vụ: tạo và giao phiếu cho cùng thợ trong cùng transaction với việc giao đơn.
            from . import service_tickets
            from .schemas import ServiceTicketIn
            ticket=service_tickets.create_in_tx(session,user,ServiceTicketIn(customer_code=order.customer_code,kind='SUA_TAI_CHO',symptom=body.new_service_symptom))
            ticket_id=service_tickets.assign_in_tx(session,user,ticket.id,body.assignee_id,ticket.version,'Kèm đơn giao '+order.number).id
        order.delivery_assignee_id,order.delivery_assigned_at=body.assignee_id,datetime.now(UTC)
        order.service_ticket_id=ticket_id
        order.version+=1
        sales_orders._record(session,user,order,'assign_delivery',before)
    return order


def move(session,user,order_id,mode,body):
    roles=('warehouse','dispatcher') if mode=='use' else ('warehouse',)
    if not has_role(user,*roles):
        raise BusinessError('FORBIDDEN','Không có quyền bàn giao hàng cho thợ.',status=403)
    if mode not in ('transfer','use','return'):
        raise BusinessError('BAD_HANDOVER','Chọn bàn giao, dùng hàng sẵn hoặc trả kho.')
    with sales_orders._transaction(session):
        order=sales_orders._load(session,order_id,lock=True)
        sales_orders._version(order,body.version)
        sales_orders._status(order,('DA_CHOT','GIAO_MOT_PHAN'))
        if order.fulfillment!='THO_GIAO' or order.delivery_assignee_id is None:
            raise BusinessError('NO_TECH_WAREHOUSE','Đơn chưa có thợ giao.',status=409)
        wh=warehouse(session,order.delivery_assignee_id)
        originals={l.line_no:l for l in order.lines if l.kind=='HANG'}
        remaining=sales_orders.remaining_lines(session,order)
        selected={}
        for part in body.lines:
            if part.order_line_no in selected or part.order_line_no not in originals or part.qty<=0:
                raise BusinessError('OVER_HANDOVER','Dòng bàn giao không hợp lệ.',status=409)
            if part.qty>remaining[part.order_line_no]['remaining']:
                raise BusinessError('OVER_HANDOVER','Số bàn giao vượt phần còn lại.',status=409)
            selected[part.order_line_no]=part.qty
        before=sales_orders.snapshot(order)
        # Cấp số CK trước khóa tồn, cùng thứ tự các luồng kho.
        docs=[]
        if mode!='use':
            groups={}
            for n,qty in selected.items():
                line=originals[n]
                source,target=(wh.code,line.warehouse_code) if mode=='return' else (line.warehouse_code,wh.code)
                groups.setdefault((source,target),[]).append((line,qty))
            for (source,target),parts in sorted(groups.items()):
                doc=stock_docs.insert(session,user,StockDocIn(kind='CK',warehouse_code=source,to_warehouse_code=target,
                    doc_date=sales_orders._today(),reason=f'{order.number}: bàn giao cho thợ' if mode=='transfer' else f'{order.number}: thợ trả kho',
                    lines=[StockDocLineIn(product_code=l.product_code,qty=qty) for l,qty in parts]), source_kind='TECHNICIAN_DELIVERY')
                docs.append((doc,parts))
        stock_posting._lock_balances(session,{(code,originals[n].product_code) for n in selected for code in (wh.code,originals[n].warehouse_code)})
        if mode=='use':
            for n,qty in selected.items():
                line=originals[n]
                if qty>stock_reservations.available(session,wh.code,line.product_code):
                    raise BusinessError('RESERVED_STOCK','Kho thợ không đủ số khả dụng cho đơn.',status=409)
                stock_reservations.transfer_in_tx(session,user,order,line,qty,line.warehouse_code,wh.code,'Dùng hàng thợ đang có')
        else:
            # Xác nhận số giữ nguồn trước khi ghi CK; tất cả trong một transaction.
            for doc,parts in docs:
                for line,qty in parts:
                    held=sum((r.qty for r in _held(session,order) if r.order_line_id==line.line_no and r.warehouse_code==doc.warehouse_code),Decimal(0))
                    if qty>held:
                        raise BusinessError('OVER_HANDOVER','Số chuyển vượt phần giữ tại kho nguồn.',status=409)
                stock_posting.post_in_tx(session,user,doc.id,None,for_order_id=order.id)
                for line,qty in parts:
                    stock_reservations.transfer_in_tx(session,user,order,line,qty,doc.warehouse_code,doc.to_warehouse_code,
                        f'Chuyển cho thợ {doc.number}' if mode=='transfer' else f'Thợ trả kho {doc.number}')
        order.version+=1
        sales_orders._record(session,user,order,'handover_'+mode,before)
    return order


def failed(session,user,order_id,body):
    with sales_orders._transaction(session):
        order=sales_orders._load(session,order_id,lock=True)
        check_assignee(user,order)
        sales_orders._version(order,body.version)
        sales_orders._status(order,('DA_CHOT','GIAO_MOT_PHAN'))
        if body.reason=='HEN_LAI' and body.promised_date is None:
            raise BusinessError('PROMISED_DATE_REQUIRED','Hẹn lại phải có ngày hẹn mới.')
        note,address=body.note.strip(),body.new_address.strip()
        if body.reason=='SAI_DIA_CHI' and not address:
            raise BusinessError('ADDRESS_REQUIRED','Nhập địa chỉ giao mới.')
        if body.reason=='DE_NGHI_HUY':
            # Thợ chỉ đề nghị; bán hàng hủy đơn ở màn đơn bán (tiền ứng, hàng thợ giữ xử lý ở đó).
            if order.status!='DA_CHOT':
                raise BusinessError('CANCEL_PARTIAL','Đơn đã giao một phần, gọi bán hàng để xử lý phần còn lại.',status=409)
            if len(note)<3:
                raise BusinessError('REASON_REQUIRED','Ghi lý do đề nghị hủy đơn.')
        before=sales_orders.snapshot(order)
        if address:
            note=('Địa chỉ mới: '+address+('; '+note if note else ''))[:4000]
        attempt=SalesDeliveryAttempt(order_id=order.id,assignee_id=user.id,reason=body.reason,note=note)
        session.add(attempt)
        if body.reason=='HEN_LAI':order.promised_date=body.promised_date
        if body.reason=='SAI_DIA_CHI':
            order.delivery_address=address
            if body.promised_date:order.promised_date=body.promised_date
        order.version+=1
        session.flush()
        audit.record(session,user,'sales_delivery_attempt.create','sales_delivery_attempt',str(attempt.id),None,
            dict(order_id=order.id,assignee_id=user.id,result=attempt.result,reason=attempt.reason,note=attempt.note))
        sales_orders._record(session,user,order,'failed_delivery',before,note)
    return order


def deliver(session,user,order_id,body,photos):
    files=[]
    try:
        with sales_orders._transaction(session):
            order=sales_orders._load(session,order_id,lock=True)
            check_assignee(user,order)
            sales_orders._version(order,body.version)
            if not photos:
                raise BusinessError('PHOTO_REQUIRED','Phải có ít nhất một ảnh giao hàng.')
            sales_orders.deliver_in_tx(session,user,order.id,body.version,body.lines,body.receiver_name,body.note,_technician=True)
            gh=session.scalar(select(SalesDelivery).where(SalesDelivery.order_id==order.id).order_by(SalesDelivery.id.desc()).limit(1))
            for name,data in photos:
                attachment=attachments.add(session,user,'sales_delivery',str(gh.id),'GIAO_HANG',name,data,_in_tx=True)
                files.append(attachments._root()/attachment.path)
            if body.receipt:
                sales_receipts.report_in_tx(session,user,order.id,body.receipt.model_copy(update={'version':order.version}))
        return order
    except Exception:
        for path in files:path.unlink(missing_ok=True)
        raise


def state(session,user,order):
    load(session,user,order.id)
    held=held_by_tech(session,order)
    attempts=list(session.scalars(select(SalesDeliveryAttempt).where(SalesDeliveryAttempt.order_id==order.id).order_by(SalesDeliveryAttempt.id.desc())))
    last_action=session.scalar(select(AuditLog.action).where(AuditLog.entity=='sales_order',AuditLog.entity_id==str(order.id),
        AuditLog.action.in_(('sales_order.failed_delivery','sales_order.deliver'))).order_by(AuditLog.id.desc()).limit(1))
    unsuccessful=last_action=='sales_order.failed_delivery'
    # Thợ đã đề nghị hủy và chưa giao lại: chờ bán hàng hủy, thẻ của thợ đổi sang "Chờ hủy".
    cancel_requested=unsuccessful and order.status=='DA_CHOT' and bool(attempts) and attempts[0].reason=='DE_NGHI_HUY'
    label='Chờ bán hàng hủy đơn' if cancel_requested else 'Giao không thành' if unsuccessful else 'Chưa có thợ' if not order.delivery_assignee_id else 'Đang ở thợ' if held else 'Có thợ chưa nhận hàng'
    return dict(held=held,attempts=attempts,label=label,cancel_requested=cancel_requested,assignee=session.get(User,order.delivery_assignee_id) if order.delivery_assignee_id else None)
