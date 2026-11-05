"""Tin nhắn Zalo Bot: hội thoại và tin nhận/gửi (TUẤN 05/11/2026).

Revision ID: 0076
Revises: 0075
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0076'
down_revision = '0075'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'zalo_chats',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('chat_id', sa.String(64), nullable=False),
        sa.Column('chat_type', sa.String(16), nullable=False),
        sa.Column('display_name', sa.String(200), server_default='', nullable=False),
        sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_text', sa.Text(), server_default='', nullable=False),
        sa.Column('unread', sa.Integer(), server_default='0', nullable=False),
        sa.Column('notice_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('chat_id', name='uq_zalo_chats_chat_id'),
    )
    op.create_index('ix_zalo_chats_last', 'zalo_chats', ['last_message_at', 'id'])
    op.create_table(
        'zalo_messages',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('chat_pk', sa.Integer(), sa.ForeignKey('zalo_chats.id'), nullable=False),
        sa.Column('zalo_message_id', sa.String(64), nullable=True),
        sa.Column('direction', sa.String(3), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('text', sa.Text(), server_default='', nullable=False),
        sa.Column('sender_id', sa.String(64), server_default='', nullable=False),
        sa.Column('sender_name', sa.String(200), server_default='', nullable=False),
        sa.Column('sent_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('error', sa.Text(), server_default='', nullable=False),
        sa.Column('raw', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('message_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('zalo_message_id', name='uq_zalo_messages_zalo_id'),
        sa.CheckConstraint("direction in ('IN','OUT')", name='ck_zalo_messages_direction'),
    )
    op.create_index('ix_zalo_messages_chat', 'zalo_messages', ['chat_pk', 'message_at', 'id'])


def downgrade():
    op.drop_index('ix_zalo_messages_chat', table_name='zalo_messages')
    op.drop_table('zalo_messages')
    op.drop_index('ix_zalo_chats_last', table_name='zalo_chats')
    op.drop_table('zalo_chats')
