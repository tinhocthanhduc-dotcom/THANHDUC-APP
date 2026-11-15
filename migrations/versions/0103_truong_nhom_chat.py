"""Đội AI đợt 3: Trưởng nhóm AI chat Zalo với giám đốc (TUẤN duyệt 15/11/2026, Q8 làm ngay + Hoàn tác, Q9 chỉ giám đốc).

Giám đốc nhắn dời hạn thì giữ hạn đó (Trưởng nhóm AI không tính lại), kèm lý do.

Revision ID: 0103
Revises: 0102
"""
from alembic import op
import sqlalchemy as sa

revision = '0103'
down_revision = '0102'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('team_tasks', sa.Column('due_override', sa.DateTime(timezone=True), nullable=True))
    op.add_column('team_tasks', sa.Column('note', sa.Text(), nullable=False, server_default=''))


def downgrade():
    op.drop_column('team_tasks', 'note')
    op.drop_column('team_tasks', 'due_override')
