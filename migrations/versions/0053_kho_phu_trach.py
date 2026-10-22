"""Kho phụ trách của từng tài khoản: chỉ lọc Việc của tôi/chuông của thủ kho.

Revision ID: 0053
Revises: 0052
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0053'
down_revision = '0052'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('warehouse_codes', postgresql.ARRAY(sa.String(16)), nullable=False, server_default='{}'))


def downgrade():
    op.drop_column('users', 'warehouse_codes')
