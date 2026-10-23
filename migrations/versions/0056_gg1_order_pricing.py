"""GG1: entered prices may include VAT; one discount per quote/order.

Revision ID: 0056
Revises: 0055
"""
from alembic import op
import sqlalchemy as sa

revision = '0056'
down_revision = '0055'
branch_labels = None
depends_on = None


def upgrade():
    for table in ('quotes', 'sales_orders'):
        op.add_column(table, sa.Column('price_includes_vat', sa.Boolean(), nullable=False, server_default=sa.false()))
        op.add_column(table, sa.Column('discount_amount', sa.BigInteger(), nullable=False, server_default=sa.text('0')))
        op.create_check_constraint('ck_'+table+'_discount', table, 'discount_amount >= 0')


def downgrade():
    for table in ('sales_orders', 'quotes'):
        op.drop_constraint('ck_'+table+'_discount', table, type_='check')
        op.drop_column(table, 'discount_amount')
        op.drop_column(table, 'price_includes_vat')
