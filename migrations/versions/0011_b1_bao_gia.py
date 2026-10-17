"""b1_bao_gia

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('quotes',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('customer_code', sa.String(length=16), nullable=False),
    sa.Column('contact_name', sa.String(length=200), nullable=False),
    sa.Column('valid_until', sa.Date(), server_default=sa.text("CAST(timezone('Asia/Ho_Chi_Minh', now()) AS DATE) + 7"), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('cancel_reason', sa.Text(), nullable=False),
    sa.Column('replaced_by_id', sa.BigInteger(), nullable=True),
    sa.Column('subtotal', sa.BigInteger(), nullable=False),
    sa.Column('vat_total', sa.BigInteger(), nullable=False),
    sa.Column('total', sa.BigInteger(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("status in ('NHAP','DA_GUI','CHAP_NHAN','TU_CHOI','DA_HUY')", name='ck_quotes_status'),
    sa.CheckConstraint('subtotal >= 0 and vat_total >= 0 and total = subtotal + vat_total', name='ck_quotes_money'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['customer_code'], ['customers.code'], ),
    sa.ForeignKeyConstraint(['replaced_by_id'], ['quotes.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number')
    )
    op.create_index(op.f('ix_quotes_customer_code'), 'quotes', ['customer_code'], unique=False)
    op.create_index('ix_quotes_status_valid', 'quotes', ['status', 'valid_until'], unique=False)
    op.create_table('quote_lines',
    sa.Column('quote_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('product_code', sa.String(length=16), nullable=True),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('qty', sa.Numeric(precision=14, scale=3), nullable=False),
    sa.Column('unit', sa.String(length=32), nullable=False),
    sa.Column('unit_price', sa.BigInteger(), nullable=False),
    sa.Column('vat_rate', sa.Integer(), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('vat_amount', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('qty > 0', name='ck_quote_lines_qty'),
    sa.CheckConstraint('unit_price >= 0 and amount >= 0 and vat_amount >= 0', name='ck_quote_lines_money'),
    sa.CheckConstraint('vat_rate in (0,5,8,10)', name='ck_quote_lines_vat'),
    sa.ForeignKeyConstraint(['product_code'], ['products.code'], ),
    sa.ForeignKeyConstraint(['quote_id'], ['quotes.id'], ),
    sa.PrimaryKeyConstraint('quote_id', 'line_no')
    )


def downgrade() -> None:
    op.drop_table('quote_lines')
    op.drop_index('ix_quotes_status_valid', table_name='quotes')
    op.drop_index(op.f('ix_quotes_customer_code'), table_name='quotes')
    op.drop_table('quotes')
