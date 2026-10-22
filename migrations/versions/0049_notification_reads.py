"""Thông báo: đánh dấu đã xem theo người dùng.

Revision ID: 0049
Revises: 0048
"""
from alembic import op
import sqlalchemy as sa

revision = '0049'
down_revision = '0048'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('notification_reads',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('item_id', sa.Text(), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('user_id', 'item_id', name='uq_notification_reads_user_item'))


def downgrade():
    op.drop_table('notification_reads')
