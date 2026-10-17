"""K4: sổ kho và tồn kho, ghi sổ và hủy phiếu

Revision ID: 0005
Revises: 24476c16a9d6
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '24476c16a9d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('stock_balances',
    sa.Column('warehouse_code', sa.String(length=16), nullable=False),
    sa.Column('product_code', sa.String(length=16), nullable=False),
    sa.Column('qty', sa.Numeric(precision=14, scale=3), nullable=False),
    sa.Column('value', sa.BigInteger(), nullable=False),
    sa.Column('avg_cost', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('qty > 0 or value = 0', name='ck_stock_balances_empty'),
    sa.CheckConstraint('qty >= 0', name='ck_stock_balances_qty'),
    sa.CheckConstraint('value >= 0', name='ck_stock_balances_value'),
    sa.ForeignKeyConstraint(['product_code'], ['products.code'], ),
    sa.ForeignKeyConstraint(['warehouse_code'], ['warehouses.code'], ),
    sa.PrimaryKeyConstraint('warehouse_code', 'product_code')
    )
    op.create_table('stock_ledger',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('warehouse_code', sa.String(length=16), nullable=False),
    sa.Column('product_code', sa.String(length=16), nullable=False),
    sa.Column('doc_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('is_reversal', sa.Boolean(), nullable=False),
    sa.Column('qty_in', sa.Numeric(precision=14, scale=3), nullable=False),
    sa.Column('qty_out', sa.Numeric(precision=14, scale=3), nullable=False),
    sa.Column('unit_cost', sa.BigInteger(), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('balance_qty', sa.Numeric(precision=14, scale=3), nullable=False),
    sa.Column('balance_value', sa.BigInteger(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('qty_in >= 0 and qty_out >= 0 and (qty_in > 0) <> (qty_out > 0)', name='ck_stock_ledger_qty'),
    sa.ForeignKeyConstraint(['doc_id'], ['stock_docs.id'], ),
    sa.ForeignKeyConstraint(['product_code'], ['products.code'], ),
    sa.ForeignKeyConstraint(['warehouse_code'], ['warehouses.code'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_stock_ledger_card', 'stock_ledger', ['warehouse_code', 'product_code', 'id'], unique=False)
    op.create_index(op.f('ix_stock_ledger_doc_id'), 'stock_ledger', ['doc_id'], unique=False)
    # Sổ kho chỉ thêm, giống audit_log: sai thì hủy phiếu để sinh dòng đảo ngược.
    op.execute("""
        create function stock_ledger_readonly() returns trigger language plpgsql as $$
        begin raise exception 'stock_ledger chi duoc them, khong sua hoac xoa'; end $$;
        create trigger stock_ledger_readonly before update or delete on stock_ledger
          for each row execute function stock_ledger_readonly();
    """)


def downgrade() -> None:
    op.execute('drop trigger if exists stock_ledger_readonly on stock_ledger')
    op.execute('drop function if exists stock_ledger_readonly()')
    op.drop_index(op.f('ix_stock_ledger_doc_id'), table_name='stock_ledger')
    op.drop_index('ix_stock_ledger_card', table_name='stock_ledger')
    op.drop_table('stock_ledger')
    op.drop_table('stock_balances')
