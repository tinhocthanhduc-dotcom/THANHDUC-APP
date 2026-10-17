"""D1 dia diem thiet bi

Revision ID: 0006
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('customer_sites',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('customer_code', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('address', sa.Text(), nullable=False),
    sa.Column('contact_name', sa.String(length=200), nullable=False),
    sa.Column('contact_phone', sa.String(length=32), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['customer_code'], ['customers.code'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_customer_sites_customer_code'), 'customer_sites', ['customer_code'], unique=False)
    op.create_table('equipment',
    sa.Column('serial', sa.String(length=64), nullable=False),
    sa.Column('product_code', sa.String(length=16), nullable=False),
    sa.Column('customer_code', sa.String(length=16), nullable=True),
    sa.Column('site_id', sa.BigInteger(), nullable=True),
    sa.Column('warranty_until', sa.Date(), nullable=True),
    sa.Column('condition', sa.String(length=3), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('search_text', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("condition in ('MOI','CU')", name='ck_equipment_condition'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['customer_code'], ['customers.code'], ),
    sa.ForeignKeyConstraint(['product_code'], ['products.code'], ),
    sa.ForeignKeyConstraint(['site_id'], ['customer_sites.id'], ),
    sa.PrimaryKeyConstraint('serial')
    )
    op.create_index(op.f('ix_equipment_customer_code'), 'equipment', ['customer_code'], unique=False)
    op.create_index('ix_equipment_search', 'equipment', ['search_text'], unique=False, postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})


def downgrade() -> None:
    op.drop_index('ix_equipment_search', table_name='equipment', postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})
    op.drop_index(op.f('ix_equipment_customer_code'), table_name='equipment')
    op.drop_table('equipment')
    op.drop_index(op.f('ix_customer_sites_customer_code'), table_name='customer_sites')
    op.drop_table('customer_sites')
