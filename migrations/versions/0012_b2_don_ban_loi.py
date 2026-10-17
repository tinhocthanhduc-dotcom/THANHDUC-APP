"""b2_don_ban_loi

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('sales_orders',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('customer_code', sa.String(length=16), nullable=False),
    sa.Column('quote_id', sa.BigInteger(), nullable=True),
    sa.Column('contact_name', sa.String(length=200), nullable=False),
    sa.Column('delivery_address', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('cancel_reason', sa.Text(), nullable=False),
    sa.Column('subtotal', sa.BigInteger(), nullable=False),
    sa.Column('vat_total', sa.BigInteger(), nullable=False),
    sa.Column('total', sa.BigInteger(), nullable=False),
    sa.Column('receivable', sa.BigInteger(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('confirmed_by', sa.Integer(), nullable=True),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delivered_by', sa.Integer(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("status in ('NHAP','DA_CHOT','DA_GIAO','DA_HUY')", name='ck_sales_orders_status'),
    sa.CheckConstraint('receivable is null or receivable >= 0', name='ck_sales_orders_receivable'),
    sa.CheckConstraint('subtotal >= 0 and vat_total >= 0 and total = subtotal + vat_total', name='ck_sales_orders_money'),
    sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['customer_code'], ['customers.code'], ),
    sa.ForeignKeyConstraint(['delivered_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['quote_id'], ['quotes.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number')
    )
    op.create_index(op.f('ix_sales_orders_customer_code'), 'sales_orders', ['customer_code'], unique=False)
    op.create_index('ix_sales_orders_status_created', 'sales_orders', ['status', 'created_at'], unique=False)
    op.create_index('uq_sales_orders_active_quote', 'sales_orders', ['quote_id'], unique=True, postgresql_where=sa.text("quote_id is not null and status <> 'DA_HUY'"))
    op.create_table('sales_order_lines',
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('product_code', sa.String(length=16), nullable=True),
    sa.Column('warehouse_code', sa.String(length=16), nullable=True),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('qty', sa.Numeric(precision=14, scale=3), nullable=False),
    sa.Column('unit', sa.String(length=32), nullable=False),
    sa.Column('unit_price', sa.BigInteger(), nullable=False),
    sa.Column('vat_rate', sa.Integer(), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('vat_amount', sa.BigInteger(), nullable=False),
    sa.Column('stock_doc_id', sa.BigInteger(), nullable=True),
    sa.CheckConstraint("(kind = 'HANG' and product_code is not null) or (kind = 'KHAC' and product_code is null and warehouse_code is null)", name='ck_sales_order_lines_product_warehouse'),
    sa.CheckConstraint("kind = 'HANG' or stock_doc_id is null", name='ck_sales_order_lines_stock_doc'),
    sa.CheckConstraint("kind in ('HANG','KHAC')", name='ck_sales_order_lines_kind'),
    sa.CheckConstraint('qty > 0', name='ck_sales_order_lines_qty'),
    sa.CheckConstraint('unit_price >= 0 and amount >= 0 and vat_amount >= 0', name='ck_sales_order_lines_money'),
    sa.CheckConstraint('vat_rate in (0,5,8,10)', name='ck_sales_order_lines_vat'),
    sa.ForeignKeyConstraint(['order_id'], ['sales_orders.id'], ),
    sa.ForeignKeyConstraint(['product_code'], ['products.code'], ),
    sa.ForeignKeyConstraint(['stock_doc_id'], ['stock_docs.id'], ),
    sa.ForeignKeyConstraint(['warehouse_code'], ['warehouses.code'], ),
    sa.PrimaryKeyConstraint('order_id', 'line_no')
    )


def downgrade() -> None:
    op.drop_table('sales_order_lines')
    op.drop_index('uq_sales_orders_active_quote', table_name='sales_orders', postgresql_where=sa.text("quote_id is not null and status <> 'DA_HUY'"))
    op.drop_index('ix_sales_orders_status_created', table_name='sales_orders')
    op.drop_index(op.f('ix_sales_orders_customer_code'), table_name='sales_orders')
    op.drop_table('sales_orders')
