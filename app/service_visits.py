"""Lượt đến khách. Các hàm ghi chỉ được gọi sau khi khóa phiếu trong transaction chuyển bước."""
from sqlalchemy import select

from .errors import BusinessError
from .models import ServiceVisit

OUTCOMES = {'XONG': 'Xong việc', 'HEN_LAI': 'Hẹn lại', 'KHONG_GAP_KHACH': 'Không gặp khách',
            'CHO_LINH_KIEN': 'Chờ linh kiện', 'TAM_DUNG': 'Tạm dừng',
            'KHONG_LAM_DUOC': 'Không làm được', 'CHUYEN_THO': 'Chuyển thợ', 'HUY': 'Hủy'}


def list_for(session, ticket_id):
    return list(session.scalars(select(ServiceVisit).where(ServiceVisit.ticket_id == ticket_id)
                               .order_by(ServiceVisit.started_at, ServiceVisit.id)))


def open_visit(session, ticket, stamp):
    row = ServiceVisit(ticket_id=ticket.id, technician_id=ticket.assignee_id, started_at=stamp)
    session.add(row)
    session.flush()  # Chỉ mục duy nhất bảo vệ cả những lệnh INSERT ngoài luồng chuyển bước.
    return row


def close_visit(session, ticket, outcome, stamp, note='', next_appointment_at=None):
    row = session.scalar(select(ServiceVisit).where(ServiceVisit.ticket_id == ticket.id,
                                                   ServiceVisit.ended_at.is_(None)).with_for_update())
    if row is None:
        raise BusinessError('VISIT_REQUIRED', 'Không tìm thấy lượt đang mở; hãy tải lại phiếu.', status=409)
    row.ended_at, row.outcome, row.note = stamp, outcome, note
    row.next_appointment_at = next_appointment_at
    session.flush()
    return row
