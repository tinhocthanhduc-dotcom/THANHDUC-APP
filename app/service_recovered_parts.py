"""KT6: hàng hỏng tháo từ máy khách; chỉ nhập kho LOI qua lõi ghi sổ, không ghi quỹ/công nợ."""
from sqlalchemy import or_, select

from . import audit, service_tickets as svc, stock_docs, stock_posting
from .errors import BusinessError
from .models import Product, ServiceRecoveredPart, ServiceTicket, Warehouse
from .schemas import RecoveredPartOut, StockDocIn
from .security import has_role
from .stock_docs import _transaction

STATUSES = {'CHO_NOP': 'Chờ nộp', 'DA_NHAN': 'Đã nhận', 'DA_HUY': 'Đã hủy'}


def snapshot(row):
    return RecoveredPartOut.model_validate(row).model_dump(mode='json')


def visible(user):
    if has_role(user, 'warehouse', 'finance', 'dispatcher'): return True
    if 'technician' in user.roles:
        return or_(ServiceRecoveredPart.holder_id == user.id, ServiceTicket.assignee_id == user.id)
    raise BusinessError('FORBIDDEN', 'Bạn không có quyền xem linh kiện thu hồi.', status=403)


def search(session, user, holder_id=None, ticket_id=None, pending=False):
    stmt = select(ServiceRecoveredPart).join(ServiceTicket).where(visible(user))
    if holder_id is not None: stmt = stmt.where(ServiceRecoveredPart.holder_id == holder_id)
    if ticket_id is not None: stmt = stmt.where(ServiceRecoveredPart.ticket_id == ticket_id)
    if pending: stmt = stmt.where(ServiceRecoveredPart.status == 'CHO_NOP')
    return list(session.scalars(stmt.order_by(ServiceRecoveredPart.created_at, ServiceRecoveredPart.id)))


def _locked(session, user, ident, version):
    tid = session.scalar(select(ServiceRecoveredPart.ticket_id).where(ServiceRecoveredPart.id == ident))
    if tid is None: raise BusinessError('NOT_FOUND', 'Không tìm thấy dòng thu hồi.', status=404)
    ticket = session.scalar(select(ServiceTicket).where(ServiceTicket.id == tid).with_for_update().execution_options(populate_existing=True))
    row = session.scalar(select(ServiceRecoveredPart).where(ServiceRecoveredPart.id == ident).with_for_update().execution_options(populate_existing=True))
    if not search_visible(user, ticket, row): raise BusinessError('NOT_FOUND', 'Không tìm thấy dòng thu hồi.', status=404)
    svc._check_version(row, version)
    if row.status != 'CHO_NOP': raise BusinessError('BAD_STATUS', 'Dòng thu hồi không còn chờ nộp.', status=409)
    return ticket, row


def search_visible(user, ticket, row):
    return has_role(user, 'warehouse', 'finance', 'dispatcher') or (
        'technician' in user.roles and (ticket.assignee_id == user.id or row.holder_id == user.id))


def create(session, user, ticket_id, body):
    with _transaction(session):
        ticket = svc._get(session, user, ticket_id, lock=True)
        svc._check_version(ticket, body.version)
        if not has_role(user, 'dispatcher') and not ('technician' in user.roles and ticket.assignee_id == user.id):
            raise BusinessError('FORBIDDEN', 'Chỉ thợ được giao hoặc điều phối được thu hồi linh kiện.', status=403)
        if ticket.status not in svc.OPEN: raise BusinessError('BAD_STATUS', 'Chỉ thu hồi trên phiếu đang mở.', status=409)
        if ticket.assignee_id is None: raise BusinessError('BAD_ASSIGNEE', 'Phải giao thợ để ghi người đang giữ linh kiện.')
        if session.scalar(select(Product.code).where(Product.code == body.product_code, Product.active.is_(True)).with_for_update(read=True)) is None:
            raise BusinessError('BAD_PRODUCT', 'Mã hàng không tồn tại hoặc đã ngừng dùng.')
        if body.qty <= 0 or not body.note.strip(): raise BusinessError('BAD_RECOVERED_PART', 'Phải nhập số lượng dương và tình trạng linh kiện.')
        row = ServiceRecoveredPart(ticket_id=ticket.id, product_code=body.product_code, qty=body.qty,
            note=body.note.strip(), holder_id=ticket.assignee_id, created_by=user.id, status='CHO_NOP')
        session.add(row); session.flush()
        audit.record(session, user, 'recovered_part.create', 'service_recovered_part', str(row.id), None, snapshot(row))
    return row


def receive(session, user, ident, body):
    if not has_role(user, 'warehouse'): raise BusinessError('FORBIDDEN', 'Chỉ kho hoặc giám đốc được nhận hàng lỗi.', status=403)
    with _transaction(session):
        ticket, row = _locked(session, user, ident, body.version)
        stmt = select(Warehouse).where(Warehouse.kind == 'LOI', Warehouse.supplier_hold.is_(False), Warehouse.recovered_only.is_(True), Warehouse.active.is_(True))
        if body.warehouse_code: stmt = stmt.where(Warehouse.code == body.warehouse_code)
        warehouse = session.scalar(stmt.order_by(Warehouse.code).limit(1).with_for_update(read=True))
        if warehouse is None: raise BusinessError('NO_DEFECT_WAREHOUSE', 'Chưa có Kho Thu hồi.', status=409)
        before = snapshot(row); stamp = svc._now()
        doc = stock_docs.insert(session, user, StockDocIn(kind='PN', warehouse_code=warehouse.code,
            customer_code=ticket.customer_code, doc_date=stamp.astimezone(svc.service_sla.TZ).date(),
            reason=f'Linh kiện thu hồi {ticket.number} · dòng {row.id}',
            lines=[dict(product_code=row.product_code, qty=row.qty, unit_cost=0)]), _recovered_part=True, source_kind='RECOVERED_PART')
        stock_posting.post_in_tx(session, user, doc.id, doc.version, pn_amounts={1: 0}, pending_context={1:dict(source='THU_HOI',group_code='THU_HOI',source_ref=ticket.number,reason=row.note)})
        row.status, row.warehouse_code, row.stock_doc_id = 'DA_NHAN', warehouse.code, doc.id
        row.received_by, row.received_at = user.id, stamp
        session.flush()
        audit.record(session, user, 'recovered_part.receive', 'service_recovered_part', str(row.id), before, snapshot(row))
        svc.auto_close_in_tx(session,user,ticket)
    return row


def cancel(session, user, ident, version, reason):
    if not has_role(user, 'dispatcher') and 'technician' not in user.roles:
        raise BusinessError('FORBIDDEN', 'Chỉ thợ giữ hoặc điều phối được hủy dòng thu hồi.', status=403)
    with _transaction(session):
        ticket, row = _locked(session, user, ident, version)
        if not has_role(user, 'dispatcher') and not ('technician' in user.roles and row.holder_id == user.id):
            raise BusinessError('FORBIDDEN', 'Chỉ thợ giữ hoặc điều phối được hủy dòng thu hồi.', status=403)
        reason = (reason or '').strip()
        if not reason: raise BusinessError('REASON_REQUIRED', 'Phải ghi lý do hủy dòng thu hồi.')
        before = snapshot(row)
        row.status, row.void_reason = 'DA_HUY', reason
        session.flush()
        audit.record(session, user, 'recovered_part.cancel', 'service_recovered_part', str(row.id), before, snapshot(row), reason)
        svc.auto_close_in_tx(session,user,ticket)
    return row


def check_received(session, ticket):
    if session.scalar(select(ServiceRecoveredPart.id).where(ServiceRecoveredPart.ticket_id == ticket.id,
            ServiceRecoveredPart.status == 'CHO_NOP').limit(1)) is not None:
        raise BusinessError('RECOVERED_PENDING', 'Còn linh kiện hỏng chưa nộp; nhận hoặc hủy các dòng trước khi đóng hồ sơ.', status=409)


def lock_for_stock_void(session, doc_id):
    tid = session.scalar(select(ServiceRecoveredPart.ticket_id).where(ServiceRecoveredPart.stock_doc_id == doc_id))
    if tid is not None:
        session.scalar(select(ServiceTicket).where(ServiceTicket.id == tid).with_for_update())
        return session.scalar(select(ServiceRecoveredPart).where(ServiceRecoveredPart.stock_doc_id == doc_id)
            .with_for_update().execution_options(populate_existing=True))


def reopen_after_stock_void(session, user, row, reason):
    if row is None: return
    before = snapshot(row)
    row.status = 'CHO_NOP'
    row.warehouse_code = row.stock_doc_id = row.received_by = row.received_at = None
    session.flush()
    audit.record(session, user, 'recovered_part.receipt_void', 'service_recovered_part', str(row.id), before, snapshot(row), reason)
