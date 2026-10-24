"""MM1: one service ticket for each selected customer machine."""
from alembic import op
import sqlalchemy as sa
revision='0061'
down_revision='0060'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('service_tickets',sa.Column('split_from_id',sa.BigInteger(),nullable=True))
    op.create_foreign_key('fk_service_tickets_split_from','service_tickets','service_tickets',['split_from_id'],['id'])
    op.create_index('ix_service_tickets_split_from_id','service_tickets',['split_from_id'])


def downgrade():
    op.drop_index('ix_service_tickets_split_from_id',table_name='service_tickets')
    op.drop_constraint('fk_service_tickets_split_from','service_tickets',type_='foreignkey')
    op.drop_column('service_tickets','split_from_id')
