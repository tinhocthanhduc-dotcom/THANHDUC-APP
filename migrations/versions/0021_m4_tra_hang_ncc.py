"""m4_tra_hang_ncc

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa


revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('purchase_returns',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('receipt_id', sa.BigInteger(), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('supplier_code', sa.String(length=16), nullable=False),
    sa.Column('warehouse_code', sa.String(length=16), nullable=False),
    sa.Column('stock_doc_id', sa.BigInteger(), nullable=False),
    sa.Column('return_date', sa.Date(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
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
    sa.CheckConstraint("status in ('DA_GHI','DA_HUY')", name='ck_purchase_returns_status'),
    sa.CheckConstraint('amount >= 0 and vat_amount >= 0 and total >= 0', name='ck_purchase_returns_money'),
    sa.CheckConstraint('length(trim(reason)) > 0', name='ck_purchase_returns_reason'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['order_id'], ['purchase_orders.id'], ),
    sa.ForeignKeyConstraint(['receipt_id'], ['purchase_receipts.id'], ),
    sa.ForeignKeyConstraint(['stock_doc_id'], ['stock_docs.id'], ),
    sa.ForeignKeyConstraint(['supplier_code'], ['suppliers.code'], ),
    sa.ForeignKeyConstraint(['voided_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['warehouse_code'], ['warehouses.code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number'),
    sa.UniqueConstraint('stock_doc_id')
    )
    op.create_index(op.f('ix_purchase_returns_order_id'), 'purchase_returns', ['order_id'], unique=False)
    op.create_index(op.f('ix_purchase_returns_receipt_id'), 'purchase_returns', ['receipt_id'], unique=False)
    op.create_index(op.f('ix_purchase_returns_supplier_code'), 'purchase_returns', ['supplier_code'], unique=False)
    op.create_table('purchase_return_lines',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('return_id', sa.BigInteger(), nullable=False),
    sa.Column('receipt_line_id', sa.BigInteger(), nullable=False),
    sa.Column('qty', sa.Numeric(precision=14, scale=3), nullable=False),
    sa.Column('unit_cost', sa.BigInteger(), nullable=False),
    sa.Column('vat_rate', sa.Integer(), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('vat_amount', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('qty > 0', name='ck_purchase_return_lines_qty'),
    sa.CheckConstraint('unit_cost >= 0 and amount >= 0 and vat_amount >= 0', name='ck_purchase_return_lines_money'),
    sa.CheckConstraint('vat_rate in (0,5,8,10)', name='ck_purchase_return_lines_vat'),
    sa.ForeignKeyConstraint(['receipt_line_id'], ['purchase_receipt_lines.id'], ),
    sa.ForeignKeyConstraint(['return_id'], ['purchase_returns.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('return_id', 'receipt_line_id', name='uq_purchase_return_lines_receipt_line')
    )
    op.create_index(op.f('ix_purchase_return_lines_receipt_line_id'), 'purchase_return_lines', ['receipt_line_id'], unique=False)
    op.create_index(op.f('ix_purchase_return_lines_return_id'), 'purchase_return_lines', ['return_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_purchase_return_lines_return_id'), table_name='purchase_return_lines')
    op.drop_index(op.f('ix_purchase_return_lines_receipt_line_id'), table_name='purchase_return_lines')
    op.drop_table('purchase_return_lines')
    op.drop_index(op.f('ix_purchase_returns_supplier_code'), table_name='purchase_returns')
    op.drop_index(op.f('ix_purchase_returns_receipt_id'), table_name='purchase_returns')
    op.drop_index(op.f('ix_purchase_returns_order_id'), table_name='purchase_returns')
    op.drop_table('purchase_returns')
