"""TUẤN 01/11/2026: thợ không sửa máy đã có; thấy sai thì Báo sai thông tin, điều phối sửa rồi bấm Đã xử lý."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, equipment
from .customer_sites import check_writer
from .errors import BusinessError
from .models import EquipmentReport, User


def _snap(row):
    return dict(id=row.id, serial=row.serial, note=row.note, status=row.status, reported_by=row.reported_by, resolved_by=row.resolved_by)


def open_rows(session: Session, serial: str):
    """Báo sai đang mở của một máy, kèm tên người báo."""
    return list(session.execute(select(EquipmentReport, User.full_name).join(User, User.id == EquipmentReport.reported_by)
                                .where(EquipmentReport.serial == serial, EquipmentReport.status == 'MO').order_by(EquipmentReport.id)))


def report(session: Session, user, serial: str, note: str):
    from .stock_docs import _transaction
    check_writer(user)
    note = str(note or '').strip()[:1000]
    if not note:
        raise BusinessError('REPORT_NOTE', 'Ghi ngắn chỗ nào sai, ví dụ: "serial đúng là ABC123" hoặc "máy đã bán cho khách khác".')
    serial = equipment.load(session, serial).serial
    with _transaction(session):
        row = EquipmentReport(serial=serial, note=note, reported_by=user.id)
        session.add(row)
        session.flush()
        audit.record(session, user, 'equipment_report.create', 'equipment', row.serial, None, _snap(row))
    return row


def resolve(session: Session, user, report_id: int):
    from .stock_docs import _transaction
    equipment.check_editor(user)
    if session.get(EquipmentReport, report_id) is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy báo sai này.', status=404)
    with _transaction(session):
        row = session.get(EquipmentReport, report_id, with_for_update=True, populate_existing=True)
        if row.status == 'MO':
            before = _snap(row)
            row.status, row.resolved_by, row.resolved_at = 'DA_XU_LY', user.id, datetime.now(timezone.utc)
            audit.record(session, user, 'equipment_report.resolve', 'equipment', row.serial, before, _snap(row))
    return row
