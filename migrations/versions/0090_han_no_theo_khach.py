"""Hạn nợ theo khách (TUẤN duyệt 11/11/2026): số ngày nợ mặc định của khách được nợ.

Đơn bán mới của khách được nợ tự điền Công nợ N ngày; phiếu dịch vụ và nợ đầu kỳ tính hạn theo N (khách không được nợ: 30 ngày như cũ).

Revision ID: 0090
Revises: 0089
"""
import sqlalchemy as sa
from alembic import op

revision = '0090'
down_revision = '0089'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('customers', sa.Column('due_days', sa.Integer(), nullable=False, server_default='30'))
    op.create_check_constraint('ck_customers_due_days', 'customers', 'due_days between 0 and 180')


def downgrade():
    op.drop_constraint('ck_customers_due_days', 'customers', type_='check')
    op.drop_column('customers', 'due_days')
