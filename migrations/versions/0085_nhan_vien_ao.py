"""Nhân viên ảo đợt 1 (TUẤN duyệt 07/11/2026): cấu hình từng nhân viên ảo + nhật ký việc đã làm.

Revision ID: 0085
Revises: 0084
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0085'
down_revision = '0084'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'virtual_agents',
        sa.Column('code', sa.String(12), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('mode', sa.String(10), nullable=False, server_default='TU_LAM'),
        sa.Column('config', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode in ('TU_LAM','CHAY_THU')", name='ck_virtual_agents_mode'),
    )
    # TUẤN 07/11/2026: cho chạy thật ngay (Q4), không giới hạn số phiếu (Q3).
    op.execute("INSERT INTO virtual_agents (code) VALUES ('TAT_CA'), ('DIEU_PHOI'), ('NHAC_HEN'), ('THU_KY')")
    op.create_table(
        'virtual_agent_log',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('agent', sa.String(12), nullable=False),
        sa.Column('action', sa.String(12), nullable=False),
        sa.Column('ticket_id', sa.BigInteger(), sa.ForeignKey('service_tickets.id'), nullable=True),
        sa.Column('target_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('text', sa.Text(), nullable=False, server_default=''),
        sa.Column('reason', sa.Text(), nullable=False, server_default=''),
        sa.Column('at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('undone_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('undone_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_virtual_agent_log_at', 'virtual_agent_log', ['at', 'id'])
    op.create_index('ix_virtual_agent_log_ticket', 'virtual_agent_log', ['ticket_id', 'agent', 'action'])


def downgrade():
    op.drop_index('ix_virtual_agent_log_ticket', table_name='virtual_agent_log')
    op.drop_index('ix_virtual_agent_log_at', table_name='virtual_agent_log')
    op.drop_table('virtual_agent_log')
    op.drop_table('virtual_agents')
