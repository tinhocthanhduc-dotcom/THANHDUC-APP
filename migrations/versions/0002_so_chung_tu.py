"""Số chứng từ theo loại và năm

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('doc_counters',
    sa.Column('prefix', sa.String(length=4), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('last', sa.Integer(), nullable=False),
    sa.CheckConstraint('last >= 0', name='ck_doc_counters_last'),
    sa.PrimaryKeyConstraint('prefix', 'year')
    )


def downgrade() -> None:
    op.drop_table('doc_counters')
