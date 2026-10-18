"""t3_noi_bao_thu

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa


revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('sales_receipts', sa.Column('ticket_id', sa.BigInteger(), nullable=True))
    op.add_column('sales_receipts', sa.Column('cash_doc_id', sa.BigInteger(), nullable=True))
    op.alter_column('sales_receipts', 'order_id',
               existing_type=sa.BIGINT(),
               nullable=True)
    op.create_index('ix_sales_receipts_ticket_status', 'sales_receipts', ['ticket_id', 'status'], unique=False)
    op.create_foreign_key('fk_sales_receipts_cash_doc', 'sales_receipts', 'cash_docs', ['cash_doc_id'], ['id'])
    op.create_foreign_key('fk_sales_receipts_ticket', 'sales_receipts', 'service_tickets', ['ticket_id'], ['id'])

    op.create_check_constraint('ck_sales_receipts_source','sales_receipts','(order_id is not null) <> (ticket_id is not null)')

def downgrade() -> None:
    op.drop_constraint('ck_sales_receipts_source','sales_receipts',type_='check')
    op.drop_constraint('fk_sales_receipts_cash_doc', 'sales_receipts', type_='foreignkey')
    op.drop_constraint('fk_sales_receipts_ticket', 'sales_receipts', type_='foreignkey')
    op.drop_index('ix_sales_receipts_ticket_status', table_name='sales_receipts')
    op.alter_column('sales_receipts', 'order_id',
               existing_type=sa.BIGINT(),
               nullable=False)
    op.drop_column('sales_receipts', 'cash_doc_id')
    op.drop_column('sales_receipts', 'ticket_id')
