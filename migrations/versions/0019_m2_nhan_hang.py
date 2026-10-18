"""m2_nhan_hang

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa


revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('purchase_receipts',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('stock_doc_id', sa.BigInteger(), nullable=False),
    sa.Column('warehouse_code', sa.String(length=16), nullable=False),
    sa.Column('receipt_date', sa.Date(), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('vat_amount', sa.BigInteger(), nullable=False),
    sa.Column('total', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('void_reason', sa.Text(), nullable=False),
    sa.Column('voided_by', sa.Integer(), nullable=True),
    sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("status in ('DA_GHI','DA_HUY')", name='ck_purchase_receipts_status'),
    sa.CheckConstraint('amount >= 0 and vat_amount >= 0 and total >= 0', name='ck_purchase_receipts_money'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['order_id'], ['purchase_orders.id'], ),
    sa.ForeignKeyConstraint(['stock_doc_id'], ['stock_docs.id'], ),
    sa.ForeignKeyConstraint(['voided_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['warehouse_code'], ['warehouses.code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_doc_id')
    )
    op.create_index(op.f('ix_purchase_receipts_order_id'), 'purchase_receipts', ['order_id'], unique=False)
    op.create_table('purchase_receipt_lines',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('receipt_id', sa.BigInteger(), nullable=False),
    sa.Column('order_line_id', sa.BigInteger(), nullable=False),
    sa.Column('qty', sa.Numeric(precision=14, scale=3), nullable=False),
    sa.Column('unit_cost', sa.BigInteger(), nullable=False),
    sa.Column('vat_rate', sa.Integer(), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('vat_amount', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('qty > 0', name='ck_purchase_receipt_lines_qty'),
    sa.CheckConstraint('unit_cost >= 0 and amount >= 0 and vat_amount >= 0', name='ck_purchase_receipt_lines_money'),
    sa.CheckConstraint('vat_rate in (0,5,8,10)', name='ck_purchase_receipt_lines_vat'),
    sa.ForeignKeyConstraint(['order_line_id'], ['purchase_order_lines.id'], ),
    sa.ForeignKeyConstraint(['receipt_id'], ['purchase_receipts.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('receipt_id', 'order_line_id', name='uq_purchase_receipt_lines_order_line')
    )
    op.create_index(op.f('ix_purchase_receipt_lines_order_line_id'), 'purchase_receipt_lines', ['order_line_id'], unique=False)
    op.create_index(op.f('ix_purchase_receipt_lines_receipt_id'), 'purchase_receipt_lines', ['receipt_id'], unique=False)
    op.add_column('purchase_orders', sa.Column('close_reason', sa.Text(), server_default=sa.text("''"), nullable=False))


def downgrade() -> None:
    op.drop_column('purchase_orders', 'close_reason')
    op.drop_index(op.f('ix_purchase_receipt_lines_receipt_id'), table_name='purchase_receipt_lines')
    op.drop_index(op.f('ix_purchase_receipt_lines_order_line_id'), table_name='purchase_receipt_lines')
    op.drop_table('purchase_receipt_lines')
    op.drop_index(op.f('ix_purchase_receipts_order_id'), table_name='purchase_receipts')
    op.drop_table('purchase_receipts')
