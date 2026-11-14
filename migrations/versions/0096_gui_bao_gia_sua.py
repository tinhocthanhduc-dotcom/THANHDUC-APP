"""Gửi báo giá sửa chữa cho khách (đợt 4, mẫu M5–M7 14/11/2026).

repair_quote_versions: mỗi lần gửi khách là một bản chụp bản khách (lần 1, lần 2…) với link mã ngẫu nhiên có hạn,
tin Zalo đã gửi, số lần khách mở, và câu trả lời của khách (đồng ý PA nào / chưa đồng ý vì sao / không sửa).
Bản chụp chỉ có phần cho khách (tên dịch vụ, mô tả, giá, VAT, bảo hành); không có linh kiện, giá vốn, lãi.

Revision ID: 0096
Revises: 0095
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0096'
down_revision = '0095'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('repair_quote_versions',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('ticket_id', sa.BigInteger(), sa.ForeignKey('service_tickets.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('no', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(length=40), nullable=False, unique=True),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='CHO'),
        sa.Column('snapshot', JSONB(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False, server_default=''),
        sa.Column('ai_job_id', sa.BigInteger(), sa.ForeignKey('zalo_ai_jobs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('valid_until', sa.Date(), nullable=False),
        sa.Column('sent_via', sa.String(length=8), nullable=False, server_default=''),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sent_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('opened_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_opened_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reply_choice', sa.String(length=4), nullable=False, server_default=''),
        sa.Column('reply_via', sa.String(length=12), nullable=False, server_default=''),
        sa.Column('reply_person', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('reply_reason', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('reply_note', sa.Text(), nullable=False, server_default=''),
        sa.Column('wanted_price', sa.BigInteger(), nullable=True),
        sa.Column('replied_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('replied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status in ('CHO','DONG_Y','CHUA_DONG_Y','KHONG_SUA','THAY_THE','THU_HOI')", name='ck_repair_quote_versions_status'),
        sa.CheckConstraint("reply_choice in ('','PA1','PA2')", name='ck_repair_quote_versions_choice'),
        sa.CheckConstraint('wanted_price is null or wanted_price >= 0', name='ck_repair_quote_versions_wanted'),
        sa.UniqueConstraint('ticket_id', 'no', name='uq_repair_quote_versions_no'))
    op.add_column('repair_inspections', sa.Column('recheck_reason', sa.Text(), nullable=False, server_default=''))


def downgrade():
    op.drop_column('repair_inspections', 'recheck_reason')
    op.drop_table('repair_quote_versions')
