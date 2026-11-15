"""Đội AI: Trưởng nhóm AI đọc tin Zalo (TUẤN duyệt 15/11/2026, Q5 đọc cả nhóm, Q6 hạn 30 phút, Q7 chỉ Z2).

Revision ID: 0102
Revises: 0101
"""
from alembic import op
import sqlalchemy as sa

revision = '0102'
down_revision = '0101'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'zalo_triage',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('message_id', sa.BigInteger(), sa.ForeignKey('zalo_messages.id'), nullable=False, unique=True),
        sa.Column('job_id', sa.BigInteger(), sa.ForeignKey('zalo_ai_jobs.id'), nullable=True),
        sa.Column('status', sa.String(8), nullable=False, server_default='CHO_AI'),
        sa.Column('intent', sa.String(8), nullable=False, server_default=''),
        sa.Column('customer_code', sa.String(16), sa.ForeignKey('customers.code'), nullable=True),
        sa.Column('customer_sure', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('summary', sa.Text(), nullable=False, server_default=''),
        sa.Column('note', sa.Text(), nullable=False, server_default=''),
        sa.Column('draft_id', sa.BigInteger(), sa.ForeignKey('zalo_ticket_drafts.id'), nullable=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('done_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status in ('CHO_AI','XONG','BO_QUA','LOI')", name='ck_zalo_triage_status'),
        sa.CheckConstraint("intent in ('','SUA','MUC','HOI_GIA','CONG_NO','KHAC')", name='ck_zalo_triage_intent'),
    )
    op.create_index('ix_zalo_triage_open', 'zalo_triage', ['owner_id', 'done_at'])
    op.create_index('ix_zalo_triage_status', 'zalo_triage', ['status', 'id'])
    op.add_column('zalo_ticket_drafts', sa.Column('customer_sure', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('zalo_ticket_drafts', sa.Column('from_triage', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('zalo_ticket_drafts', 'from_triage')
    op.drop_column('zalo_ticket_drafts', 'customer_sure')
    op.drop_index('ix_zalo_triage_status', table_name='zalo_triage')
    op.drop_index('ix_zalo_triage_open', table_name='zalo_triage')
    op.drop_table('zalo_triage')
