"""Trợ lý Zalo trả lời nhân viên bằng Codex/Claude trên máy chủ Z2 (TUẤN 05/11/2026), khách vẫn qua API.

Revision ID: 0079
Revises: 0078
"""
from alembic import op
import sqlalchemy as sa

revision = '0079'
down_revision = '0078'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('zalo_tro_ly', sa.Column('staff_engine', sa.String(8), nullable=False, server_default='api'))
    op.create_check_constraint('ck_zalo_tro_ly_staff_engine', 'zalo_tro_ly', "staff_engine in ('api','codex','claude')")
    op.add_column('zalo_agent_logs', sa.Column('engine', sa.String(8), nullable=False, server_default=''))
    op.create_table(
        'zalo_ai_jobs',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('log_id', sa.BigInteger(), sa.ForeignKey('zalo_agent_logs.id'), nullable=False),
        sa.Column('engine', sa.String(8), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False, server_default=''),
        sa.Column('status', sa.String(8), nullable=False, server_default='CHO'),
        sa.Column('error', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('taken_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("engine in ('codex','claude')", name='ck_zalo_ai_jobs_engine'),
        sa.CheckConstraint("status in ('CHO','DANG','XONG','LOI')", name='ck_zalo_ai_jobs_status'),
    )
    op.create_index('ix_zalo_ai_jobs_status', 'zalo_ai_jobs', ['status', 'id'])


def downgrade():
    op.drop_index('ix_zalo_ai_jobs_status', table_name='zalo_ai_jobs')
    op.drop_table('zalo_ai_jobs')
    op.drop_column('zalo_agent_logs', 'engine')
    op.drop_constraint('ck_zalo_tro_ly_staff_engine', 'zalo_tro_ly', type_='check')
    op.drop_column('zalo_tro_ly', 'staff_engine')
