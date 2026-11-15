"""Đội AI đợt 1 (TUẤN duyệt 15/11/2026): Trưởng nhóm AI gắn người phụ trách + hạn cho từng việc trong Việc của tôi.

Revision ID: 0101
Revises: 0100
"""
from alembic import op
import sqlalchemy as sa

revision = '0101'
down_revision = '0100'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'team_tasks',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('item_key', sa.String(40), nullable=False),
        sa.Column('ref', sa.String(80), nullable=False),
        sa.Column('number', sa.String(80), nullable=False, server_default=''),
        sa.Column('title', sa.Text(), nullable=False, server_default=''),
        sa.Column('link', sa.Text(), nullable=False, server_default=''),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('urgent', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reminded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('dispatch_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('director_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ux_team_tasks_open', 'team_tasks', ['item_key', 'ref'], unique=True,
                    postgresql_where=sa.text('done_at IS NULL'))
    op.create_index('ix_team_tasks_owner', 'team_tasks', ['owner_id', 'done_at'])
    op.create_index('ix_team_tasks_seen', 'team_tasks', ['first_seen_at'])
    op.execute("INSERT INTO virtual_agents (code) VALUES ('TRUONG_NHOM') ON CONFLICT DO NOTHING")


def downgrade():
    op.execute("DELETE FROM virtual_agents WHERE code = 'TRUONG_NHOM'")
    op.drop_index('ix_team_tasks_seen', table_name='team_tasks')
    op.drop_index('ix_team_tasks_owner', table_name='team_tasks')
    op.drop_index('ux_team_tasks_open', table_name='team_tasks')
    op.drop_table('team_tasks')
