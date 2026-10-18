"""t2_thu_chi_so_quy

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa


revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('fund_balances',
    sa.Column('fund_code', sa.String(length=32), nullable=False),
    sa.Column('balance', sa.BigInteger(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint('balance >= 0', name='ck_fund_balances_nonnegative'),
    sa.ForeignKeyConstraint(['fund_code'], ['funds.code'], ),
    sa.PrimaryKeyConstraint('fund_code')
    )
    op.create_table('cash_docs',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=2), nullable=False),
    sa.Column('doc_date', sa.Date(), nullable=False),
    sa.Column('fund_code', sa.String(length=32), nullable=False),
    sa.Column('to_fund_code', sa.String(length=32), nullable=True),
    sa.Column('category_code', sa.String(length=32), nullable=True),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('customer_code', sa.String(length=16), nullable=True),
    sa.Column('supplier_code', sa.String(length=16), nullable=True),
    sa.Column('partner_name', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('is_opening', sa.Boolean(), nullable=False),
    sa.Column('source_type', sa.String(length=64), nullable=True),
    sa.Column('source_id', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('void_reason', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('posted_by', sa.Integer(), nullable=True),
    sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("((kind='CQ' or is_opening) and category_code is null) or (kind in ('PT','PC') and not is_opening and category_code is not null)", name='ck_cash_docs_category'),
    sa.CheckConstraint("(kind='CQ' and to_fund_code is not null and to_fund_code <> fund_code) or (kind<>'CQ' and to_fund_code is null)", name='ck_cash_docs_transfer'),
    sa.CheckConstraint("kind in ('PT','PC','CQ')", name='ck_cash_docs_kind'),
    sa.CheckConstraint("not is_opening or kind <> 'CQ'", name='ck_cash_docs_opening'),
    sa.CheckConstraint("status in ('NHAP','DA_GHI','DA_HUY')", name='ck_cash_docs_status'),
    sa.CheckConstraint('(source_type is null and source_id is null) or (source_type is not null and source_id is not null and length(trim(source_type))>0 and length(trim(source_id))>0)', name='ck_cash_docs_source'),
    sa.CheckConstraint('amount > 0', name='ck_cash_docs_amount'),
    sa.CheckConstraint('customer_code is null or supplier_code is null', name='ck_cash_docs_partner'),
    sa.ForeignKeyConstraint(['category_code'], ['cash_categories.code'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['customer_code'], ['customers.code'], ),
    sa.ForeignKeyConstraint(['fund_code'], ['funds.code'], ),
    sa.ForeignKeyConstraint(['posted_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['supplier_code'], ['suppliers.code'], ),
    sa.ForeignKeyConstraint(['to_fund_code'], ['funds.code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number')
    )
    op.create_index('ix_cash_docs_date', 'cash_docs', ['doc_date', 'id'], unique=False)
    op.create_index('uq_cash_docs_active_source', 'cash_docs', ['source_type', 'source_id'], unique=True, postgresql_where=sa.text("source_type is not null and source_id is not null and status <> 'DA_HUY'"))
    op.create_table('fund_ledger',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('fund_code', sa.String(length=32), nullable=False),
    sa.Column('entry_date', sa.Date(), nullable=False),
    sa.Column('doc_id', sa.BigInteger(), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('balance_after', sa.BigInteger(), nullable=False),
    sa.Column('is_reversal', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('amount <> 0', name='ck_fund_ledger_amount'),
    sa.CheckConstraint('balance_after >= 0', name='ck_fund_ledger_balance'),
    sa.ForeignKeyConstraint(['doc_id'], ['cash_docs.id'], ),
    sa.ForeignKeyConstraint(['fund_code'], ['funds.code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('doc_id', 'fund_code', 'is_reversal', name='uq_fund_ledger_move')
    )
    op.create_index('ix_fund_ledger_statement', 'fund_ledger', ['fund_code', 'entry_date', 'id'], unique=False)
    op.execute('''
        create function fund_ledger_readonly() returns trigger language plpgsql as $$
        begin raise exception 'fund_ledger chi duoc them, khong sua hoac xoa'; end $$;
        create trigger fund_ledger_readonly before update or delete on fund_ledger
          for each row execute function fund_ledger_readonly();
    ''')


def downgrade() -> None:
    op.execute('drop trigger if exists fund_ledger_readonly on fund_ledger')
    op.execute('drop function if exists fund_ledger_readonly()')
    op.drop_index('ix_fund_ledger_statement', table_name='fund_ledger')
    op.drop_table('fund_ledger')
    op.drop_index('uq_cash_docs_active_source', table_name='cash_docs', postgresql_where=sa.text("source_type is not null and source_id is not null and status <> 'DA_HUY'"))
    op.drop_index('ix_cash_docs_date', table_name='cash_docs')
    op.drop_table('cash_docs')
    op.drop_table('fund_balances')
