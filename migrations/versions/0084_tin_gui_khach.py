"""Trợ lý Zalo đợt 4: tin soạn sẵn gửi khách (nhắc nợ, báo giá, hỏi thăm sau sửa) (TUẤN duyệt mẫu 06/11/2026).

Revision ID: 0084
Revises: 0083
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0084'
down_revision = '0083'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('zalo_tro_ly', sa.Column('bank_line', sa.Text(), nullable=False, server_default=''))
    # Lượt viết lại tin cho khách không thuộc tin Zalo nào: log_id được để trống.
    op.alter_column('zalo_ai_jobs', 'log_id', existing_type=sa.BigInteger(), nullable=True)
    op.create_table(
        'customer_messages',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('number', sa.String(32), nullable=False, unique=True),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('customer_code', sa.String(16), sa.ForeignKey('customers.code'), nullable=False),
        sa.Column('ref_type', sa.String(16), nullable=False, server_default=''),
        sa.Column('ref_id', sa.BigInteger(), nullable=True),
        sa.Column('facts', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('draft_text', sa.Text(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('source', sa.String(6), nullable=False, server_default='APP'),
        sa.Column('status', sa.String(8), nullable=False, server_default='CHO'),
        sa.Column('sent_via', sa.String(8), nullable=False, server_default=''),
        sa.Column('chat_pk', sa.Integer(), sa.ForeignKey('zalo_chats.id'), nullable=True),
        sa.Column('zalo_message_id', sa.BigInteger(), sa.ForeignKey('zalo_messages.id'), nullable=True),
        sa.Column('ai_job_id', sa.BigInteger(), sa.ForeignKey('zalo_ai_jobs.id'), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('edited_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decided_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('drop_note', sa.Text(), nullable=False, server_default=''),
        sa.Column('reply_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.CheckConstraint("kind in ('NHAC_NO','BAO_GIA','HOI_THAM')", name='ck_customer_messages_kind'),
        sa.CheckConstraint("status in ('CHO','DA_GUI','DA_BO')", name='ck_customer_messages_status'),
        sa.CheckConstraint("source in ('APP','ZALO')", name='ck_customer_messages_source'),
        sa.CheckConstraint("sent_via in ('','BOT','CA_NHAN')", name='ck_customer_messages_sent_via'),
        sa.CheckConstraint("(status = 'DA_GUI') = (sent_via <> '')", name='ck_customer_messages_sent'),
    )
    op.create_index('ix_customer_messages_status', 'customer_messages', ['status', 'created_at', 'id'])
    op.create_index('ix_customer_messages_customer', 'customer_messages', ['customer_code', 'kind', 'decided_at'])
    op.create_index('ix_customer_messages_creator', 'customer_messages', ['created_by', 'status'])


def downgrade():
    op.drop_index('ix_customer_messages_creator', table_name='customer_messages')
    op.drop_index('ix_customer_messages_customer', table_name='customer_messages')
    op.drop_index('ix_customer_messages_status', table_name='customer_messages')
    op.drop_table('customer_messages')
    op.execute('delete from zalo_ai_jobs where log_id is null')
    op.alter_column('zalo_ai_jobs', 'log_id', existing_type=sa.BigInteger(), nullable=False)
    op.drop_column('zalo_tro_ly', 'bank_line')
