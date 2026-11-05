"""Gợi ý thợ + nhắc sắp trễ hẹn (TUẤN duyệt 05/11/2026): nhớ tin Zalo đã báo điều phối để không gửi lặp.

Revision ID: 0078
Revises: 0077
"""
from alembic import op
import sqlalchemy as sa

revision = '0078'
down_revision = '0077'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'dispatch_zalo_sent',
        sa.Column('mark', sa.String(120), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('message_id', sa.BigInteger(), sa.ForeignKey('zalo_messages.id'), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_dispatch_zalo_sent_at', 'dispatch_zalo_sent', ['sent_at'])


def downgrade():
    op.drop_index('ix_dispatch_zalo_sent_at', table_name='dispatch_zalo_sent')
    op.drop_table('dispatch_zalo_sent')
