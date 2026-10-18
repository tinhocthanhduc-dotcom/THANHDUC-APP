"""m1_don_mua

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa


revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('purchase_orders',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('supplier_code', sa.String(length=16), nullable=False),
    sa.Column('order_date', sa.Date(), nullable=False),
    sa.Column('expected_date', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('subtotal', sa.BigInteger(), nullable=False),
    sa.Column('vat_total', sa.BigInteger(), nullable=False),
    sa.Column('total', sa.BigInteger(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ordered_by', sa.Integer(), nullable=True),
    sa.Column('ordered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_reason', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("status in ('NHAP','DA_DAT','NHAN_MOT_PHAN','DA_NHAN','DA_HUY')", name='ck_purchase_orders_status'),
    sa.CheckConstraint('subtotal >= 0 and vat_total >= 0 and total >= 0', name='ck_purchase_orders_money'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['ordered_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['supplier_code'], ['suppliers.code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number')
    )
    op.create_table('purchase_order_lines',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('product_code', sa.String(length=16), nullable=False),
    sa.Column('warehouse_code', sa.String(length=16), nullable=False),
    sa.Column('qty', sa.Numeric(precision=14, scale=3), nullable=False),
    sa.Column('unit_cost', sa.BigInteger(), nullable=False),
    sa.Column('vat_rate', sa.Integer(), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('vat_amount', sa.BigInteger(), nullable=False),
    sa.Column('received_qty', sa.Numeric(precision=14, scale=3), server_default=sa.text('0'), nullable=False),
    sa.CheckConstraint('qty > 0', name='ck_purchase_order_lines_qty'),
    sa.CheckConstraint('received_qty >= 0 and received_qty <= qty', name='ck_purchase_order_lines_received'),
    sa.CheckConstraint('unit_cost >= 0 and amount >= 0 and vat_amount >= 0', name='ck_purchase_order_lines_money'),
    sa.CheckConstraint('vat_rate in (0,5,8,10)', name='ck_purchase_order_lines_vat'),
    sa.ForeignKeyConstraint(['order_id'], ['purchase_orders.id'], ),
    sa.ForeignKeyConstraint(['product_code'], ['products.code'], ),
    sa.ForeignKeyConstraint(['warehouse_code'], ['warehouses.code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('order_id', 'line_no', name='uq_purchase_order_lines_number')
    )
    op.create_index(op.f('ix_purchase_order_lines_order_id'), 'purchase_order_lines', ['order_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_purchase_order_lines_order_id'), table_name='purchase_order_lines')
    op.drop_table('purchase_order_lines')
    op.drop_table('purchase_orders')
