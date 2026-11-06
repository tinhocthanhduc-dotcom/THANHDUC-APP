"""Phiếu dịch vụ / mực nháp từ Zalo (TUẤN duyệt 06/11/2026, Trợ lý Zalo đợt 2).

Revision ID: 0082
Revises: 0081
"""
from alembic import op
import sqlalchemy as sa

revision = '0082'
down_revision = '0081'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('zalo_tro_ly', sa.Column('ticket_drafts', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.create_table(
        'zalo_ticket_drafts',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('number', sa.String(32), nullable=False, unique=True),
        sa.Column('message_id', sa.BigInteger(), sa.ForeignKey('zalo_messages.id'), nullable=False),
        sa.Column('log_id', sa.BigInteger(), sa.ForeignKey('zalo_agent_logs.id'), nullable=True),
        sa.Column('source', sa.String(10), nullable=False),
        sa.Column('sender_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('customer_code', sa.String(16), sa.ForeignKey('customers.code'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=True),
        sa.Column('priority', sa.String(8), nullable=False, server_default='THUONG'),
        sa.Column('symptom', sa.Text(), nullable=False),
        sa.Column('want_date', sa.Date(), nullable=True),
        sa.Column('slot', sa.String(5), nullable=True),
        sa.Column('ink_hint', sa.Text(), nullable=False, server_default=''),
        sa.Column('question', sa.Text(), nullable=False, server_default=''),
        sa.Column('status', sa.String(8), nullable=False, server_default='CHO'),
        sa.Column('ticket_id', sa.BigInteger(), sa.ForeignKey('service_tickets.id'), nullable=True),
        sa.Column('drop_reason', sa.String(10), nullable=False, server_default=''),
        sa.Column('drop_note', sa.Text(), nullable=False, server_default=''),
        sa.Column('decided_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('message_id', name='uq_zalo_ticket_drafts_message'),
        sa.CheckConstraint("status in ('CHO','DA_TAO','DA_BO')", name='ck_zalo_ticket_drafts_status'),
        sa.CheckConstraint("source in ('NHAN_VIEN','KHACH')", name='ck_zalo_ticket_drafts_source'),
        sa.CheckConstraint("kind is null or kind in ('SUA_TAI_CHO','MANG_VE','MUC_DINH_KY')", name='ck_zalo_ticket_drafts_kind'),
        sa.CheckConstraint("priority in ('KHAN','GAP','THUONG')", name='ck_zalo_ticket_drafts_priority'),
        sa.CheckConstraint("slot is null or slot in ('SANG','CHIEU')", name='ck_zalo_ticket_drafts_slot'),
        sa.CheckConstraint("drop_reason = '' or drop_reason in ('TRUNG','KHACH_HUY','HIEU_SAI','KHAC')", name='ck_zalo_ticket_drafts_drop_reason'),
        sa.CheckConstraint("(status = 'DA_TAO') = (ticket_id is not null)", name='ck_zalo_ticket_drafts_ticket'),
    )
    op.create_index('ix_zalo_ticket_drafts_status', 'zalo_ticket_drafts', ['status', 'created_at', 'id'])
    op.create_index('ix_zalo_ticket_drafts_customer_code', 'zalo_ticket_drafts', ['customer_code'])


def downgrade():
    op.drop_index('ix_zalo_ticket_drafts_customer_code', table_name='zalo_ticket_drafts')
    op.drop_index('ix_zalo_ticket_drafts_status', table_name='zalo_ticket_drafts')
    op.drop_table('zalo_ticket_drafts')
    op.drop_column('zalo_tro_ly', 'ticket_drafts')
