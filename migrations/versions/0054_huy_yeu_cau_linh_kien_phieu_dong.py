"""Hủy các yêu cầu linh kiện còn chờ cấp của phiếu dịch vụ đã đóng (kẹt trước khi lõi tự hủy).

Revision ID: 0054
Revises: 0053
"""
from alembic import op

revision = '0054'
down_revision = '0053'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        update part_requests r
           set status = 'DA_HUY', decided_at = now(), version = r.version + 1,
               decision_note = 'Tự hủy: phiếu ' || t.number || ' đã đóng trước khi cấp'
          from service_tickets t
         where t.id = r.ticket_id and r.status = 'CHO_CAP'
           and t.status in ('HOAN_THANH', 'DA_DONG', 'KHONG_LAM_DUOC', 'DA_HUY')
    """)


def downgrade():
    pass
