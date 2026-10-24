"""XG1: workshop quotation, customer approval and ticket warranty."""
from alembic import op
import sqlalchemy as sa

revision = '0059'
down_revision = '0058'
branch_labels = None
depends_on = None

OLD = "hold_reason in ('','CHO_KHACH_DUYET','CHO_KY_THUAT_KHAC','CHO_DOI_TAC','HEN_NGAY_KHAC','KHAC','SUA_XONG_CHO_TRA')"


def upgrade():
    for name in ('quote_approved_at', 'declined_at'):
        op.add_column('service_tickets', sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.add_column('service_tickets', sa.Column('quote_approved_by', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_service_tickets_quote_approved_by', 'service_tickets', 'users', ['quote_approved_by'], ['id'])
    op.add_column('service_tickets', sa.Column('promised_return_on', sa.Date(), nullable=True))
    op.add_column('service_tickets', sa.Column('warranty_months', sa.SmallInteger(), nullable=True))
    op.add_column('service_tickets', sa.Column('warranty_scope', sa.String(200), nullable=True, server_default=''))
    op.create_check_constraint('ck_service_tickets_warranty_months', 'service_tickets', 'warranty_months is null or warranty_months in (0,1,2,3,6,12)')
    op.drop_constraint('ck_service_tickets_hold_reason', 'service_tickets', type_='check')
    op.create_check_constraint('ck_service_tickets_hold_reason', 'service_tickets', OLD[:-1] + ",'CHO_SUA_XUONG')")


def downgrade():
    op.execute("update service_tickets set hold_reason='KHAC' where hold_reason='CHO_SUA_XUONG'")
    op.drop_constraint('ck_service_tickets_hold_reason', 'service_tickets', type_='check')
    op.create_check_constraint('ck_service_tickets_hold_reason', 'service_tickets', OLD)
    op.drop_constraint('ck_service_tickets_warranty_months', 'service_tickets', type_='check')
    op.drop_constraint('fk_service_tickets_quote_approved_by', 'service_tickets', type_='foreignkey')
    for name in ('warranty_scope','warranty_months','promised_return_on','quote_approved_by','declined_at','quote_approved_at'):
        op.drop_column('service_tickets', name)
