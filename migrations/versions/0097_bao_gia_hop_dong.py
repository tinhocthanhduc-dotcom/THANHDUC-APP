"""Báo giá hợp đồng sửa chữa (TUẤN duyệt mẫu H1–H3 bản 3, 14/11/2026).

contract_quotes: báo giá trước cho một khách nhiều model (HD-yy-nnnnn), bảng gói × cấp đã chọn, giảm % do giám đốc,
link mã ngẫu nhiên, tin Zalo. Khách ký thì thành giá hợp đồng: máy của khách vào xưởng lấy giá này thay bảng giá chung
trong thời hạn áp giá. Bản chụp chỉ có phần cho khách (tên gói, mô tả, giá, bảo hành), không giá vốn.

Revision ID: 0097
Revises: 0096
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0097'
down_revision = '0096'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('contract_quotes',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('number', sa.String(length=20), nullable=False, unique=True),
        sa.Column('customer_code', sa.String(length=16), sa.ForeignKey('customers.code'), nullable=False, index=True),
        sa.Column('contact_name', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('contact_phone', sa.String(length=40), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=8), nullable=False, server_default='CHO'),
        sa.Column('levels', JSONB(), nullable=False),
        sa.Column('discount_pct', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('valid_until', sa.Date(), nullable=False),
        sa.Column('apply_months', sa.Integer(), nullable=False, server_default='12'),
        sa.Column('free_check', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('payment_terms', sa.Text(), nullable=False, server_default=''),
        sa.Column('snapshot', JSONB(), nullable=False),
        sa.Column('token', sa.String(length=40), nullable=False, unique=True),
        sa.Column('text', sa.Text(), nullable=False, server_default=''),
        sa.Column('sent_via', sa.String(length=8), nullable=False, server_default=''),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sent_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('opened_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_opened_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('signed_on', sa.Date(), nullable=True),
        sa.Column('apply_until', sa.Date(), nullable=True),
        sa.Column('signed_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('signed_note', sa.Text(), nullable=False, server_default=''),
        sa.Column('stopped_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('stopped_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('stop_reason', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.CheckConstraint("status in ('CHO','DA_KY','NGUNG')", name='ck_contract_quotes_status'),
        sa.CheckConstraint('discount_pct between 0 and 50', name='ck_contract_quotes_discount'),
        sa.CheckConstraint('apply_months between 1 and 36', name='ck_contract_quotes_months'),
        sa.CheckConstraint("status <> 'DA_KY' or (signed_on is not null and apply_until is not null)", name='ck_contract_quotes_signed'))


def downgrade():
    op.drop_table('contract_quotes')
