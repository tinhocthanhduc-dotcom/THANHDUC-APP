"""b4_bao_thu

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('sales_receipts',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('method', sa.String(length=16), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('received_at', sa.Date(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('reported_by', sa.Integer(), nullable=False),
    sa.Column('reported_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('decided_by', sa.Integer(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("method in ('TIEN_MAT','CHUYEN_KHOAN')", name='ck_sales_receipts_method'),
    sa.CheckConstraint("status in ('CHO_XAC_NHAN','DA_XAC_NHAN','TU_CHOI','DA_HUY')", name='ck_sales_receipts_status'),
    sa.CheckConstraint('amount > 0', name='ck_sales_receipts_amount'),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['order_id'], ['sales_orders.id'], ),
    sa.ForeignKeyConstraint(['reported_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number')
    )
    op.create_index('ix_sales_receipts_order_status', 'sales_receipts', ['order_id', 'status'], unique=False)
    op.create_index('ix_sales_receipts_status_reported', 'sales_receipts', ['status', 'reported_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_sales_receipts_status_reported', table_name='sales_receipts')
    op.drop_index('ix_sales_receipts_order_status', table_name='sales_receipts')
    op.drop_table('sales_receipts')
