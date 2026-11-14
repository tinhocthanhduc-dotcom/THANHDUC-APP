"""Báo giá nhanh HG (TUẤN duyệt mẫu B1 14/11/2026, đợt 2 quy trình báo giá sửa chữa).

Khách hỏi giá khi chưa gửi máy: model + hiện tượng + sửa ở đâu → khoảng giá tham khảo (gồm VAT) lấy từ bảng giá chung.
Lưu ảnh chụp số liệu lúc báo (lines) để bảng giá đổi sau không làm sai tin đã gửi. Chuyển thành phiếu thì ghi ticket_id.

Revision ID: 0093
Revises: 0092
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0093'
down_revision = '0092'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('quick_quotes',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('number', sa.String(length=32), nullable=False, unique=True),
        sa.Column('customer_code', sa.String(length=16), sa.ForeignKey('customers.code'), nullable=True),
        sa.Column('contact', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('model_name', sa.String(length=160), nullable=False),
        sa.Column('model_id', sa.Integer(), sa.ForeignKey('repair_models.id', ondelete='SET NULL'), nullable=True),
        sa.Column('place', sa.String(length=8), nullable=False, server_default='CUA_HANG'),
        sa.Column('symptoms', JSONB(), nullable=False, server_default='[]'),
        sa.Column('note', sa.Text(), nullable=False, server_default=''),
        sa.Column('lines', JSONB(), nullable=False, server_default='[]'),
        sa.Column('text', sa.Text(), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='MOI'),
        sa.Column('sent_via', sa.String(length=8), nullable=False, server_default=''),
        sa.Column('ticket_id', sa.BigInteger(), sa.ForeignKey('service_tickets.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('decided_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status in ('MOI','DA_GUI','CHUYEN_PHIEU','KHONG_SUA')", name='ck_quick_quotes_status'),
        sa.CheckConstraint("place in ('CUA_HANG','TAN_NOI')", name='ck_quick_quotes_place'),
        sa.CheckConstraint("sent_via in ('','BOT','CA_NHAN')", name='ck_quick_quotes_sent_via'))
    op.create_index('ix_quick_quotes_status', 'quick_quotes', ['status', 'created_at'])


def downgrade():
    op.drop_index('ix_quick_quotes_status', table_name='quick_quotes')
    op.drop_table('quick_quotes')
