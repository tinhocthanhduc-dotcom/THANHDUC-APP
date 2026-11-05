"""Trợ lý Zalo: gắn Zalo với nhân viên/khách, cấu hình bật tắt, nhật ký câu hỏi và đơn nháp (TUẤN duyệt 05/11/2026).

Revision ID: 0077
Revises: 0076
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0077'
down_revision = '0076'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('zalo_chats', sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))
    op.add_column('zalo_chats', sa.Column('customer_code', sa.String(16), sa.ForeignKey('customers.code'), nullable=True))
    op.add_column('zalo_chats', sa.Column('linked_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))
    op.add_column('zalo_chats', sa.Column('linked_at', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint('ck_zalo_chats_one_link', 'zalo_chats', 'user_id is null or customer_code is null')
    op.create_table(
        'zalo_tro_ly',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('draft_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('id = 1', name='ck_zalo_tro_ly_one_row'),
    )
    op.create_table(
        'zalo_agent_logs',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('chat_pk', sa.Integer(), sa.ForeignKey('zalo_chats.id'), nullable=False),
        sa.Column('message_id', sa.BigInteger(), sa.ForeignKey('zalo_messages.id'), nullable=False),
        sa.Column('asker', sa.String(12), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('customer_code', sa.String(16), sa.ForeignKey('customers.code'), nullable=True),
        sa.Column('question', sa.Text(), server_default='', nullable=False),
        sa.Column('tools', postgresql.JSONB(), server_default='[]', nullable=False),
        sa.Column('answer', sa.Text(), server_default='', nullable=False),
        sa.Column('result', sa.String(16), nullable=False),
        sa.Column('order_id', sa.BigInteger(), sa.ForeignKey('sales_orders.id'), nullable=True),
        sa.Column('need_pick', sa.Text(), server_default='', nullable=False),
        sa.Column('tokens_in', sa.Integer(), server_default='0', nullable=False),
        sa.Column('tokens_out', sa.Integer(), server_default='0', nullable=False),
        sa.Column('cost_vnd', sa.Integer(), server_default='0', nullable=False),
        sa.Column('error', sa.Text(), server_default='', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('message_id', name='uq_zalo_agent_logs_message'),
        sa.CheckConstraint("asker in ('NHAN_VIEN','KHACH','CHUA_GAN')", name='ck_zalo_agent_logs_asker'),
    )
    op.create_index('ix_zalo_agent_logs_created', 'zalo_agent_logs', ['created_at', 'id'])
    op.create_index('ix_zalo_agent_logs_chat', 'zalo_agent_logs', ['chat_pk', 'created_at'])
    op.create_index('ix_zalo_agent_logs_order', 'zalo_agent_logs', ['order_id'], postgresql_where=sa.text('order_id is not null'))


def downgrade():
    op.drop_index('ix_zalo_agent_logs_order', table_name='zalo_agent_logs')
    op.drop_index('ix_zalo_agent_logs_chat', table_name='zalo_agent_logs')
    op.drop_index('ix_zalo_agent_logs_created', table_name='zalo_agent_logs')
    op.drop_table('zalo_agent_logs')
    op.drop_table('zalo_tro_ly')
    op.drop_constraint('ck_zalo_chats_one_link', 'zalo_chats', type_='check')
    for col in ('linked_at', 'linked_by', 'customer_code', 'user_id'):
        op.drop_column('zalo_chats', col)
