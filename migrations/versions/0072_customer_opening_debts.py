"""TUẤN 02/11/2026: nợ đầu kỳ khách hàng (nợ cũ trước khi dùng app), Báo thu trừ dần.

Revision ID: 0072
Revises: 0071
"""
from alembic import op
import sqlalchemy as sa

revision = '0072'
down_revision = '0071'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('customer_opening_debts',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('number', sa.String(length=32), nullable=False),
        sa.Column('customer_code', sa.String(length=16), nullable=False),
        sa.Column('amount', sa.BigInteger(), nullable=False),
        sa.Column('as_of', sa.Date(), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.CheckConstraint('amount > 0', name='ck_customer_opening_debts_amount'),
        sa.ForeignKeyConstraint(['customer_code'], ['customers.code']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('number'),
        sa.UniqueConstraint('customer_code'))
    op.add_column('sales_receipts', sa.Column('opening_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key('sales_receipts_opening_id_fkey', 'sales_receipts', 'customer_opening_debts', ['opening_id'], ['id'])
    op.create_index('ix_sales_receipts_opening_status', 'sales_receipts', ['opening_id', 'status'])
    op.drop_constraint('ck_sales_receipts_source', 'sales_receipts', type_='check')
    op.create_check_constraint('ck_sales_receipts_source', 'sales_receipts',
        '(order_id is not null)::int + (ticket_id is not null)::int + (opening_id is not null)::int = 1')


def downgrade():
    op.drop_constraint('ck_sales_receipts_source', 'sales_receipts', type_='check')
    op.execute('delete from sales_receipts where opening_id is not null')
    op.create_check_constraint('ck_sales_receipts_source', 'sales_receipts', '(order_id is not null) <> (ticket_id is not null)')
    op.drop_index('ix_sales_receipts_opening_status', table_name='sales_receipts')
    op.drop_constraint('sales_receipts_opening_id_fkey', 'sales_receipts', type_='foreignkey')
    op.drop_column('sales_receipts', 'opening_id')
    op.drop_table('customer_opening_debts')
