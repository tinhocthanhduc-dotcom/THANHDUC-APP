"""KS1: classify a service ticket before completion instead of at intake."""
from alembic import op
import sqlalchemy as sa

revision = '0057'
down_revision = '0056'
branch_labels = None
depends_on = None

KINDS = "kind in ('SUA_TAI_CHO','MANG_VE','BAO_HANH','MUC_DINH_KY')"
WARRANTY = "(warranty_of_id is null and warranty_basis is null) or (kind = 'BAO_HANH' and warranty_basis in ('PHIEU_TRUOC','MAY_CON_HAN','HET_HAN'))"


def upgrade():
    op.alter_column('service_tickets', 'kind', existing_type=sa.String(16), nullable=True)
    op.drop_constraint('ck_service_tickets_kind', 'service_tickets', type_='check')
    op.create_check_constraint('ck_service_tickets_kind', 'service_tickets', 'kind is null or ' + KINDS)
    op.drop_constraint('ck_service_tickets_warranty', 'service_tickets', type_='check')
    op.create_check_constraint('ck_service_tickets_warranty', 'service_tickets',
                               WARRANTY.replace("(kind =", "(kind is not null and kind ="))


def downgrade():
    op.execute("update service_tickets set kind='SUA_TAI_CHO' where kind is null")
    op.alter_column('service_tickets', 'kind', existing_type=sa.String(16), nullable=False)
    op.drop_constraint('ck_service_tickets_kind', 'service_tickets', type_='check')
    op.create_check_constraint('ck_service_tickets_kind', 'service_tickets', KINDS)
    op.drop_constraint('ck_service_tickets_warranty', 'service_tickets', type_='check')
    op.create_check_constraint('ck_service_tickets_warranty', 'service_tickets', WARRANTY)
