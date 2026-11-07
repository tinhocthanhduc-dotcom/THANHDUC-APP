"""Đổi hình thức báo thu Tiền mặt ⇄ Chuyển khoản một bước (TUẤN duyệt 07/11/2026).

Revision ID: 0087
Revises: 0086
"""
import sqlalchemy as sa
from alembic import op

revision = '0087'
down_revision = '0086'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('sales_receipts', sa.Column('replaced_by_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key('fk_sales_receipts_replaced_by', 'sales_receipts', 'sales_receipts', ['replaced_by_id'], ['id'])
    op.create_index('ix_sales_receipts_replaced_by', 'sales_receipts', ['replaced_by_id'], unique=True,
                    postgresql_where=sa.text('replaced_by_id IS NOT NULL'))


def downgrade():
    op.drop_index('ix_sales_receipts_replaced_by', table_name='sales_receipts')
    op.drop_constraint('fk_sales_receipts_replaced_by', 'sales_receipts', type_='foreignkey')
    op.drop_column('sales_receipts', 'replaced_by_id')
