"""t1_quy_danh_muc

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa


revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('cash_categories',
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('direction', sa.String(length=3), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('search_text', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("direction in ('THU','CHI')", name='ck_cash_categories_direction'),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('funds',
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('bank_name', sa.String(length=300), nullable=False),
    sa.Column('account_no', sa.String(length=100), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('search_text', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("kind <> 'NGAN_HANG' or length(trim(account_no)) > 0", name='ck_funds_account'),
    sa.CheckConstraint("kind in ('TIEN_MAT','NGAN_HANG')", name='ck_funds_kind'),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('finance_mappings',
    sa.Column('key', sa.String(length=32), nullable=False),
    sa.Column('fund_code', sa.String(length=32), nullable=True),
    sa.Column('category_code', sa.String(length=32), nullable=True),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("(key in ('THU_TIEN_MAT','THU_CHUYEN_KHOAN') and category_code is null) or (key in ('DANH_MUC_BAN_HANG','DANH_MUC_DICH_VU') and fund_code is null)", name='ck_finance_mappings_target'),
    sa.CheckConstraint("key in ('THU_TIEN_MAT','THU_CHUYEN_KHOAN','DANH_MUC_BAN_HANG','DANH_MUC_DICH_VU')", name='ck_finance_mappings_key'),
    sa.ForeignKeyConstraint(['category_code'], ['cash_categories.code'], ),
    sa.ForeignKeyConstraint(['fund_code'], ['funds.code'], ),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('key')
    )
    mappings = sa.table('finance_mappings',sa.column('key',sa.String()),
                        sa.column('version',sa.Integer()))
    op.bulk_insert(mappings,[{'key':key,'version':1} for key in (
        'THU_TIEN_MAT','THU_CHUYEN_KHOAN','DANH_MUC_BAN_HANG','DANH_MUC_DICH_VU')])


def downgrade() -> None:
    op.drop_table('finance_mappings')
    op.drop_table('funds')
    op.drop_table('cash_categories')
