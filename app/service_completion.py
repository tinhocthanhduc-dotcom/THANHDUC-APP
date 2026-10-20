"""UX1b: lưu tiền, hoàn thành và báo thu tại chỗ trong một transaction."""
from . import sales_receipts, service_charges, service_tickets
from .errors import BusinessError
from .schemas import SalesReceiptReport, ServiceCharges
from .stock_docs import _transaction


def complete(session, user, ticket_id: int, charges: ServiceCharges, note: str = '',
             receipt: SalesReceiptReport | None = None):
    with _transaction(session):
        ticket = service_tickets._get(session, user, ticket_id, lock=True)
        if 'technician' not in user.roles or ticket.assignee_id != user.id:
            raise BusinessError('FORBIDDEN', 'Chỉ thợ được giao phiếu được hoàn thành tại đây.', status=403)
        # Chỉ kiểm phiên bản người dùng gửi một lần, trước mọi thay đổi.
        service_tickets._check_version(ticket, charges.version)
        if ticket.status != 'DANG_LAM':
            raise BusinessError('BAD_STATUS', 'Chỉ hoàn thành phiếu đang làm.', status=409)
        service_charges.set_charges_in_tx(session, user, ticket, charges)
        service_tickets.transition_in_tx(session, user, ticket, 'HOAN_THANH', note)
        if receipt is not None:
            sales_receipts.report_for_ticket_in_tx(session, user, ticket,
                receipt.model_copy(update={'version': ticket.version}))
    return ticket
