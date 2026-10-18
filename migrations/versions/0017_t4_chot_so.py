"""t4_chot_so

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa


revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('day_closings',
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('closed_by', sa.Integer(), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('unlocked_by', sa.Integer(), nullable=True),
    sa.Column('unlocked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('unlock_reason', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("status in ('DA_CHOT','DA_MO')", name='ck_day_closings_status'),
    sa.ForeignKeyConstraint(['closed_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['unlocked_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('day')
    )
    op.create_table('cash_corrections',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('doc_id', sa.BigInteger(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('new_amount', sa.BigInteger(), nullable=False),
    sa.Column('new_fund_code', sa.String(length=32), nullable=False),
    sa.Column('new_category_code', sa.String(length=32), nullable=False),
    sa.Column('new_description', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('requested_by', sa.Integer(), nullable=False),
    sa.Column('decided_by', sa.Integer(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decide_reason', sa.Text(), nullable=False),
    sa.Column('new_doc_id', sa.BigInteger(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("status in ('CHO_DUYET','DA_DUYET','TU_CHOI')", name='ck_cash_corrections_status'),
    sa.CheckConstraint('new_amount > 0', name='ck_cash_corrections_amount'),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['doc_id'], ['cash_docs.id'], ),
    sa.ForeignKeyConstraint(['new_category_code'], ['cash_categories.code'], ),
    sa.ForeignKeyConstraint(['new_doc_id'], ['cash_docs.id'], ),
    sa.ForeignKeyConstraint(['new_fund_code'], ['funds.code'], ),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_cash_corrections_pending', 'cash_corrections', ['doc_id'], unique=True, postgresql_where=sa.text("status='CHO_DUYET'"))


def downgrade() -> None:
    op.drop_index('uq_cash_corrections_pending', table_name='cash_corrections', postgresql_where=sa.text("status='CHO_DUYET'"))
    op.drop_table('cash_corrections')
    op.drop_table('day_closings')
