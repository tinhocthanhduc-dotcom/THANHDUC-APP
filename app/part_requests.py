"""KT3: yêu cầu linh kiện và cấp bằng CK, cùng transaction với lịch sử phiếu.

Luôn khóa phiếu dịch vụ trước yêu cầu; kho vẫn ghi sổ bằng stock_posting.
"""
from datetime import UTC, datetime

from sqlalchemy import select

from . import audit, service_sla, service_tickets as svc, stock_docs, stock_posting, technician_stock
from .doc_numbers import next_number
from .errors import BusinessError
from .models import PartRequest, Product, ServiceTicket, User, Warehouse
from .schemas import PartRequestOut, StockDocIn
from .security import has_role
from .stock_docs import _transaction

STATUSES = {'CHO_CAP': 'Chờ cấp', 'DA_CAP': 'Đã cấp', 'HET_HANG': 'Hết hàng', 'DA_HUY': 'Đã hủy'}


def check_supplier(user):
    if not has_role(user, 'warehouse'):
        raise BusinessError('FORBIDDEN', 'Chỉ thủ kho hoặc giám đốc được cấp linh kiện.', status=403)


def check_requester(user, ticket):
    if not has_role(user, 'dispatcher') and not ('technician' in user.roles and ticket.assignee_id == user.id):
        raise BusinessError('FORBIDDEN', 'Chỉ thợ được giao hoặc điều phối được yêu cầu linh kiện.', status=403)


def _warehouse(session, ticket):
    tech = session.get(User, ticket.assignee_id) if ticket.assignee_id else None
    warehouse = technician_stock.own_warehouse(session, tech, lock=True) if tech and tech.active and 'technician' in tech.roles else None
    if warehouse is None:
        raise BusinessError('NO_TECH_WAREHOUSE', 'Thợ được giao chưa có kho riêng đang dùng.', status=409)
    return warehouse


def _ticket(session, ticket_id):
    ticket = session.scalar(select(ServiceTicket).where(ServiceTicket.id == ticket_id)
                            .with_for_update().execution_options(populate_existing=True))
    if ticket is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy phiếu dịch vụ.', status=404)
    return ticket


def snapshot(row):
    return PartRequestOut.model_validate(row).model_dump(mode='json')


def _audit(session, user, row, action, before=None):
    session.flush()
    audit.record(session, user, 'part_request.' + action, 'part_request', str(row.id), before, snapshot(row), row.decision_note)


def create(session, user, ticket_id, body):
    return create_many(session, user, ticket_id, body.version, [body])[0]


def create_many(session, user, ticket_id, version, bodies):
    """UX5b: xin nhiều mã cho một phiếu trong một lần gửi; một mã lỗi thì không tạo yêu cầu nào."""
    if not bodies:
        raise BusinessError('LINES_REQUIRED', 'Hãy chọn ít nhất một linh kiện.')
    if len(bodies) > 20:
        raise BusinessError('TOO_MANY_LINES', 'Mỗi lần xin tối đa 20 mã.')
    codes = [b.product_code for b in bodies]
    duplicate = next((c for c in codes if codes.count(c) > 1), None)
    if duplicate:
        raise BusinessError('DUPLICATE_PRODUCT', f'Mã {duplicate} được chọn hai lần, hãy gộp thành một dòng.')
    with _transaction(session):
        ticket = _ticket(session, ticket_id)
        check_requester(user, ticket)
        svc._check_version(ticket, version)
        if ticket.status not in ('DANG_LAM', 'CHO_LINH_KIEN'):
            raise BusinessError('BAD_STATUS', 'Chỉ yêu cầu linh kiện khi đang làm hoặc chờ linh kiện.', status=409)
        _warehouse(session, ticket)
        reason = bodies[0].reason.strip()
        rows = []
        now = datetime.now(UTC)
        for body in bodies:
            product = session.scalar(select(Product).where(Product.code == body.product_code, Product.active.is_(True)).with_for_update(read=True))
            if product is None:
                raise BusinessError('BAD_PRODUCT', 'Mã hàng không tồn tại hoặc đã ngừng bán.' if len(bodies) == 1 else
                                    f'Mã hàng {body.product_code} không tồn tại hoặc đã ngừng bán.')
            if not reason:
                raise BusinessError('REASON_REQUIRED', 'Phải ghi lý do cần linh kiện.')
            row = PartRequest(number=next_number(session, 'YC', now.astimezone(service_sla.TZ).date()),
                              ticket_id=ticket.id, requester_id=user.id, product_code=product.code, qty=body.qty,
                              reason=reason, status='CHO_CAP')
            session.add(row)
            rows.append(row)
        numbers = ', '.join(r.number for r in rows)
        if ticket.status == 'DANG_LAM':
            before = svc.snapshot(ticket)
            from . import service_visits
            service_visits.close_visit(session, ticket, 'CHO_LINH_KIEN', svc._now(),
                f'Cần linh kiện {numbers}: {reason}')
            ticket.status, ticket.due_at = 'CHO_LINH_KIEN', None
            svc._record(session, user, ticket, 'transition', before, 'DANG_LAM', f'Cần linh kiện {numbers}: {reason}')
        for row in rows:
            _audit(session, user, row, 'create')
    return rows


def _own_warehouse(session, requester_id):
    tech = session.get(User, requester_id)
    warehouse = technician_stock.own_warehouse(session, tech, lock=True) if tech and tech.active and 'technician' in tech.roles else None
    if warehouse is None:
        raise BusinessError('NO_TECH_WAREHOUSE', 'Thợ yêu cầu chưa có kho riêng đang dùng.', status=409)
    return warehouse


def _stock_row(session, user, body, reason):
    product = session.scalar(select(Product).where(Product.code == body.product_code, Product.active.is_(True)).with_for_update(read=True))
    if product is None:
        raise BusinessError('BAD_PRODUCT', f'Mã hàng {body.product_code} không tồn tại hoặc đã ngừng bán.')
    row = PartRequest(number=next_number(session, 'YC', datetime.now(UTC).astimezone(service_sla.TZ).date()),
                      ticket_id=None, requester_id=user.id, product_code=product.code, qty=body.qty,
                      reason=reason, status='CHO_CAP')
    session.add(row)
    _audit(session, user, row, 'create')
    return row


def create_stock(session, user, body):
    """Thợ xin hàng mang theo: không gắn phiếu, kho cấp vào kho riêng của chính thợ."""
    return create_stock_many(session, user, [body])[0]


def create_stock_many(session, user, bodies):
    """Thẻ A: xin nhiều mã trong một lần gửi; một mã lỗi thì không tạo yêu cầu nào."""
    if 'technician' not in user.roles:
        raise BusinessError('FORBIDDEN', 'Chỉ thợ được xin hàng mang theo.', status=403)
    if not bodies:
        raise BusinessError('LINES_REQUIRED', 'Hãy chọn ít nhất một linh kiện.')
    if len(bodies) > 20:
        raise BusinessError('TOO_MANY_LINES', 'Mỗi lần xin tối đa 20 mã.')
    codes = [b.product_code for b in bodies]
    duplicate = next((c for c in codes if codes.count(c) > 1), None)
    if duplicate:
        raise BusinessError('DUPLICATE_PRODUCT', f'Mã {duplicate} được chọn hai lần, hãy gộp thành một dòng.')
    reason = bodies[0].reason.strip()
    with _transaction(session):
        _own_warehouse(session, user.id)
        if not reason:
            raise BusinessError('REASON_REQUIRED', 'Phải ghi lý do cần linh kiện.')
        return [_stock_row(session, user, body, reason) for body in bodies]


def for_requester(session, user, limit=20):
    """Yêu cầu mang theo (không gắn phiếu) của chính thợ, mới nhất trước."""
    return list(session.scalars(select(PartRequest).where(PartRequest.requester_id == user.id, PartRequest.ticket_id.is_(None))
                                .order_by(PartRequest.id.desc()).limit(limit)))


def _locked(session, ident, version):
    found = session.execute(select(PartRequest.id, PartRequest.ticket_id).where(PartRequest.id == ident)).first()
    if found is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy yêu cầu linh kiện.', status=404)
    ticket = _ticket(session, found.ticket_id) if found.ticket_id is not None else None
    row = session.scalar(select(PartRequest).where(PartRequest.id == ident)
                         .with_for_update().execution_options(populate_existing=True))
    if version != row.version:
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa cập nhật yêu cầu, hãy tải lại.', {'current': row.version}, status=409)
    if row.status != 'CHO_CAP':
        raise BusinessError('BAD_STATUS', 'Yêu cầu không còn chờ cấp.', status=409)
    return ticket, row


def supply(session, user, ident, warehouse_code, version):
    check_supplier(user)
    with _transaction(session):
        ticket, row = _locked(session, ident, version)
        if ticket is not None and ticket.status not in svc.OPEN:
            raise BusinessError('BAD_STATUS', 'Phiếu dịch vụ đã đóng, không cấp linh kiện.', status=409)
        dest = _warehouse(session, ticket) if ticket is not None else _own_warehouse(session, row.requester_id)
        source = session.scalar(select(Warehouse).where(Warehouse.code == warehouse_code, Warehouse.kind == 'KHO', Warehouse.active.is_(True)).with_for_update(read=True))
        if source is None:
            raise BusinessError('BAD_WAREHOUSE', 'Phải chọn kho công ty đang dùng để cấp linh kiện.')
        before = snapshot(row)
        doc = stock_docs.insert(session, user, StockDocIn(kind='CK', warehouse_code=source.code,
            to_warehouse_code=dest.code, doc_date=datetime.now(service_sla.TZ).date(),
            reason=f'Cấp {row.number}/{ticket.number}' if ticket is not None else f'Cấp {row.number} cho thợ mang theo', lines=[dict(product_code=row.product_code, qty=row.qty)]))
        stock_posting.post_in_tx(session, user, doc.id, doc.version)
        row.status, row.from_warehouse_code, row.to_warehouse_code, row.stock_doc_id = 'DA_CAP', source.code, dest.code, doc.id
        row.decided_by, row.decided_at = user.id, datetime.now(UTC)
        _audit(session, user, row, 'supply', before)
    return row


def _decide(session, user, ident, note, version, status):
    if status == 'HET_HANG':
        check_supplier(user)
    with _transaction(session):
        _, row = _locked(session, ident, version)
        if status == 'DA_HUY' and row.requester_id != user.id and not has_role(user, 'dispatcher'):
            raise BusinessError('FORBIDDEN', 'Chỉ người tạo hoặc điều phối được hủy yêu cầu linh kiện.', status=403)
        if not note.strip():
            raise BusinessError('REASON_REQUIRED', 'Phải ghi chú hết hàng.' if status == 'HET_HANG' else 'Phải ghi lý do hủy yêu cầu.')
        before = snapshot(row)
        row.status, row.decision_note = status, note.strip()
        row.decided_by, row.decided_at = user.id, datetime.now(UTC)
        _audit(session, user, row, 'out_of_stock' if status == 'HET_HANG' else 'cancel', before)
    return row


def out_of_stock(session, user, ident, note, version):
    return _decide(session, user, ident, note, version, 'HET_HANG')


def cancel(session, user, ident, reason, version):
    return _decide(session, user, ident, reason, version, 'DA_HUY')


def for_ticket(session, user, ticket_id):
    svc.load(session, user, ticket_id)
    return list(session.scalars(select(PartRequest).where(PartRequest.ticket_id == ticket_id).order_by(PartRequest.id)))


def search(session, user, status='CHO_CAP'):
    check_supplier(user)
    stmt = select(PartRequest)
    if status:
        stmt = stmt.where(PartRequest.status == status)
    return list(session.scalars(stmt.order_by(PartRequest.created_at, PartRequest.id)))
