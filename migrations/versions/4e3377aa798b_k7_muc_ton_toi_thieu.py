"""K7 muc ton toi thieu

Revision ID: 4e3377aa798b
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = '4e3377aa798b'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('stock_min_levels',
    sa.Column('warehouse_code', sa.String(length=16), nullable=False),
    sa.Column('product_code', sa.String(length=16), nullable=False),
    sa.Column('min_qty', sa.Numeric(precision=14, scale=3), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_by', sa.Integer(), nullable=False),
    sa.CheckConstraint('min_qty >= 0', name='ck_stock_min_levels_qty'),
    sa.ForeignKeyConstraint(['product_code'], ['products.code'], ),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['warehouse_code'], ['warehouses.code'], ),
    sa.PrimaryKeyConstraint('warehouse_code', 'product_code')
    )


def downgrade() -> None:
    op.drop_table('stock_min_levels')
