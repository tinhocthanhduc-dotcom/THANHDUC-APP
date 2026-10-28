"""Đơn mua nhập giá đã gồm VAT (TUẤN 28/10/2026): cờ trên đơn, giá nhập gốc trên dòng.

Revision ID: 0067
Revises: 0066
"""
from alembic import op
import sqlalchemy as sa

revision = '0067'
down_revision = '0066'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('purchase_orders', sa.Column('price_includes_vat', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('purchase_order_lines', sa.Column('entered_price', sa.BigInteger(), nullable=True))
    op.create_check_constraint('ck_purchase_order_lines_entered', 'purchase_order_lines', 'entered_price is null or entered_price >= 0')


def downgrade():
    op.drop_constraint('ck_purchase_order_lines_entered', 'purchase_order_lines', type_='check')
    op.drop_column('purchase_order_lines', 'entered_price')
    op.drop_column('purchase_orders', 'price_includes_vat')
