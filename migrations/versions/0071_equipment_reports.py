"""TUẤN 01/11/2026: thợ báo sai thông tin máy; điều phối sửa rồi đánh dấu đã xử lý.

Revision ID: 0071
Revises: 0070
"""
from alembic import op
import sqlalchemy as sa

revision = '0071'
down_revision = '0070'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('equipment_reports',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('serial', sa.String(length=64), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=8), server_default='MO', nullable=False),
        sa.Column('reported_by', sa.Integer(), nullable=False),
        sa.Column('reported_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('resolved_by', sa.Integer(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status in ('MO','DA_XU_LY')", name='ck_equipment_report_status'),
        sa.CheckConstraint('length(trim(note))>0', name='ck_equipment_report_note'),
        sa.ForeignKeyConstraint(['serial'], ['equipment.serial'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reported_by'], ['users.id']),
        sa.ForeignKeyConstraint(['resolved_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_equipment_reports_serial', 'equipment_reports', ['serial'])


def downgrade():
    op.drop_index('ix_equipment_reports_serial', table_name='equipment_reports')
    op.drop_table('equipment_reports')
