"""MV1: workshop repair attribution and ready-for-return hold reason.

Revision ID: 0054
Revises: 0053
"""
from alembic import op
import sqlalchemy as sa

revision = '0054'
down_revision = '0053'
branch_labels = None
depends_on = None

OLD = "hold_reason in ('','CHO_KHACH_DUYET','CHO_KY_THUAT_KHAC','CHO_DOI_TAC','HEN_NGAY_KHAC','KHAC')"


def upgrade():
    op.add_column('service_tickets', sa.Column('repaired_by', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_service_tickets_repaired_by','service_tickets','users',['repaired_by'],['id'])
    op.drop_constraint('ck_service_tickets_hold_reason','service_tickets',type_='check')
    op.create_check_constraint('ck_service_tickets_hold_reason','service_tickets',OLD[:-1]+",'SUA_XONG_CHO_TRA')")


def downgrade():
    op.execute("update service_tickets set hold_reason='KHAC' where hold_reason='SUA_XONG_CHO_TRA'")
    op.drop_constraint('ck_service_tickets_hold_reason','service_tickets',type_='check')
    op.create_check_constraint('ck_service_tickets_hold_reason','service_tickets',OLD)
    op.drop_constraint('fk_service_tickets_repaired_by','service_tickets',type_='foreignkey')
    op.drop_column('service_tickets','repaired_by')
