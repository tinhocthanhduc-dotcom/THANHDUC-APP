"""Tự động ra ca 19:00 khi thợ quên bấm (TUẤN 27/10/2026): lần bấm nguồn TU_DONG."""
from alembic import op
revision='0065'
down_revision='0064'
branch_labels=None
depends_on=None

NEW="source in ('APP','BO_SUNG','TU_DONG') and ((source in ('APP','TU_DONG') and request_id is null) or (source='BO_SUNG' and request_id is not null))"
OLD="source in ('APP','BO_SUNG') and ((source='APP' and request_id is null) or (source='BO_SUNG' and request_id is not null))"


def upgrade():
    op.drop_constraint('ck_attendance_punch_source','attendance_punches',type_='check')
    op.create_check_constraint('ck_attendance_punch_source','attendance_punches',NEW)


def downgrade():
    op.execute("update attendance_punches set source='APP' where source='TU_DONG'")
    op.drop_constraint('ck_attendance_punch_source','attendance_punches',type_='check')
    op.create_check_constraint('ck_attendance_punch_source','attendance_punches',OLD)
