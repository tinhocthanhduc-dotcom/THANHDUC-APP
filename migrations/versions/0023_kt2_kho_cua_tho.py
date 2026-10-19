"""KT2: kho riêng của kỹ thuật viên.

Revision ID: 0023
Revises: 0022
"""
from alembic import op
import sqlalchemy as sa

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('warehouses', sa.Column('kind', sa.String(3), nullable=False, server_default='KHO'))
    op.add_column('warehouses', sa.Column('holder_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_warehouses_holder_id', 'warehouses', 'users', ['holder_id'], ['id'])
    op.create_check_constraint('ck_warehouses_holder', 'warehouses', "(kind = 'KHO' and holder_id is null) or (kind = 'THO' and holder_id is not null)")
    op.create_index('uq_warehouses_active_holder', 'warehouses', ['holder_id'], unique=True, postgresql_where=sa.text("kind = 'THO' and active"))


def downgrade():
    op.drop_index('uq_warehouses_active_holder', table_name='warehouses')
    op.drop_constraint('ck_warehouses_holder', 'warehouses', type_='check')
    op.drop_constraint('fk_warehouses_holder_id', 'warehouses', type_='foreignkey')
    op.drop_column('warehouses', 'holder_id')
    op.drop_column('warehouses', 'kind')
