"""m3_cong_no_ncc

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa


revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def _mapping_checks(include_purchase):
    categories="'DANH_MUC_BAN_HANG','DANH_MUC_DICH_VU'" + (",'DANH_MUC_MUA_HANG'" if include_purchase else '')
    op.drop_constraint('ck_finance_mappings_key','finance_mappings',type_='check')
    op.drop_constraint('ck_finance_mappings_target','finance_mappings',type_='check')
    op.create_check_constraint('ck_finance_mappings_key','finance_mappings',"key in ('THU_TIEN_MAT','THU_CHUYEN_KHOAN',"+categories+")")
    op.create_check_constraint('ck_finance_mappings_target','finance_mappings',
        "(key in ('THU_TIEN_MAT','THU_CHUYEN_KHOAN') and category_code is null) or (key in ("+categories+") and fund_code is null)")


def upgrade() -> None:
    _mapping_checks(True)
    op.execute("insert into finance_mappings (key,version) values ('DANH_MUC_MUA_HANG',1)")
    op.create_table('supplier_opening_debts',
    sa.Column('supplier_code', sa.String(length=16), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint('amount >= 0', name='ck_supplier_opening_debts_amount'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['supplier_code'], ['suppliers.code'], ),
    sa.PrimaryKeyConstraint('supplier_code')
    )
    op.create_table('supplier_payments',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('supplier_code', sa.String(length=16), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=True),
    sa.Column('fund_code', sa.String(length=32), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('paid_at', sa.Date(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('cash_doc_id', sa.BigInteger(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('void_reason', sa.Text(), nullable=False),
    sa.Column('voided_by', sa.Integer(), nullable=True),
    sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("status in ('DA_GHI','DA_HUY')", name='ck_supplier_payments_status'),
    sa.CheckConstraint('amount > 0', name='ck_supplier_payments_amount'),
    sa.ForeignKeyConstraint(['cash_doc_id'], ['cash_docs.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['fund_code'], ['funds.code'], ),
    sa.ForeignKeyConstraint(['order_id'], ['purchase_orders.id'], ),
    sa.ForeignKeyConstraint(['supplier_code'], ['suppliers.code'], ),
    sa.ForeignKeyConstraint(['voided_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('cash_doc_id'),
    sa.UniqueConstraint('number')
    )
    op.create_index(op.f('ix_supplier_payments_order_id'), 'supplier_payments', ['order_id'], unique=False)
    op.create_index(op.f('ix_supplier_payments_supplier_code'), 'supplier_payments', ['supplier_code'], unique=False)


def downgrade() -> None:
    op.execute("delete from finance_mappings where key='DANH_MUC_MUA_HANG'")
    _mapping_checks(False)
    op.drop_index(op.f('ix_supplier_payments_supplier_code'), table_name='supplier_payments')
    op.drop_index(op.f('ix_supplier_payments_order_id'), table_name='supplier_payments')
    op.drop_table('supplier_payments')
    op.drop_table('supplier_opening_debts')
