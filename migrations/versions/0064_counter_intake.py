"""Khách mang máy tới quầy (TUẤN 26/10/2026): phiếu nhận tại quầy không cần thợ được giao."""
from alembic import op
import sqlalchemy as sa
revision='0064'
down_revision='0063'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('service_tickets',sa.Column('counter_intake',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.drop_constraint('ck_service_tickets_assignee','service_tickets',type_='check')
    op.create_check_constraint('ck_service_tickets_assignee','service_tickets',
        "status = 'MOI' or assignee_id is not null or status = 'DA_HUY' or counter_intake")


def downgrade():
    op.drop_constraint('ck_service_tickets_assignee','service_tickets',type_='check')
    op.execute("update service_tickets set status='DA_HUY' where counter_intake and assignee_id is null and status not in ('MOI','DA_HUY')")
    op.create_check_constraint('ck_service_tickets_assignee','service_tickets',"status = 'MOI' or assignee_id is not null or status = 'DA_HUY'")
    op.drop_column('service_tickets','counter_intake')
