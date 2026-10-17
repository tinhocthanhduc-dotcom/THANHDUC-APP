"""K1 danh muc kho va nha cung cap

Revision ID: 894bbf0454b0
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '894bbf0454b0'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE ncc_seq")
    op.create_table('suppliers',
    sa.Column('code', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('tax_code', sa.String(length=13), nullable=False),
    sa.Column('phones', postgresql.ARRAY(sa.String(length=11)), nullable=False),
    sa.Column('address', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('search_text', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_index('ix_suppliers_search', 'suppliers', ['search_text'], unique=False, postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})
    op.create_index('uq_suppliers_tax', 'suppliers', ['tax_code'], unique=True, postgresql_where="tax_code <> ''")
    op.create_table('warehouses',
    sa.Column('code', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('address', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('code')
    )


def downgrade() -> None:
    op.execute("DROP SEQUENCE ncc_seq")
    op.drop_table('warehouses')
    op.drop_index('uq_suppliers_tax', table_name='suppliers', postgresql_where="tax_code <> ''")
    op.drop_index('ix_suppliers_search', table_name='suppliers', postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})
    op.drop_table('suppliers')
