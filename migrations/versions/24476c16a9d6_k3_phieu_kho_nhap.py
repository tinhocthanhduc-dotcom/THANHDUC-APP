"""K3 phieu kho nhap

Revision ID: 24476c16a9d6
Revises: 894bbf0454b0
"""
from alembic import op
import sqlalchemy as sa


revision = '24476c16a9d6'
down_revision = '894bbf0454b0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('stock_docs',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=2), nullable=False),
    sa.Column('warehouse_code', sa.String(length=16), nullable=False),
    sa.Column('to_warehouse_code', sa.String(length=16), nullable=True),
    sa.Column('doc_date', sa.Date(), nullable=False),
    sa.Column('supplier_code', sa.String(length=16), nullable=True),
    sa.Column('customer_code', sa.String(length=16), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('posted_by', sa.Integer(), nullable=True),
    sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('void_reason', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("kind in ('PN','PX','CK','KK')", name='ck_stock_docs_kind'),
    sa.CheckConstraint("status in ('NHAP','DA_GHI','DA_HUY')", name='ck_stock_docs_status'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['customer_code'], ['customers.code'], ),
    sa.ForeignKeyConstraint(['posted_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['supplier_code'], ['suppliers.code'], ),
    sa.ForeignKeyConstraint(['to_warehouse_code'], ['warehouses.code'], ),
    sa.ForeignKeyConstraint(['warehouse_code'], ['warehouses.code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number')
    )
    op.create_table('stock_doc_lines',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('doc_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('product_code', sa.String(length=16), nullable=False),
    sa.Column('qty', sa.Numeric(precision=14, scale=3), nullable=False),
    sa.Column('unit_cost', sa.BigInteger(), nullable=True),
    sa.Column('serial', sa.String(length=200), nullable=True),
    sa.Column('note', sa.Text(), nullable=False),
    sa.CheckConstraint('qty >= 0', name='ck_stock_doc_lines_qty'),
    sa.CheckConstraint('unit_cost >= 0', name='ck_stock_doc_lines_cost'),
    sa.ForeignKeyConstraint(['doc_id'], ['stock_docs.id'], ),
    sa.ForeignKeyConstraint(['product_code'], ['products.code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('doc_id', 'line_no', name='uq_stock_doc_lines_no')
    )


def downgrade() -> None:
    op.drop_table('stock_doc_lines')
    op.drop_table('stock_docs')
