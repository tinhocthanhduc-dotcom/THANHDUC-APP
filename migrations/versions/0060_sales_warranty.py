"""BH1: sale equipment enrollment and CARE warranty metadata."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='0060'
down_revision='0059'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('products',sa.Column('warranty_months',sa.Integer(),nullable=False,server_default='0'))
    op.create_check_constraint('ck_products_warranty_months','products','warranty_months between 0 and 60')
    op.drop_constraint('ck_products_code','products',type_='check')
    op.create_check_constraint('ck_products_code','products',"code ~ '^TD-[0-9]{4,}$' or code = 'MAY-CU-CARE'")
    op.add_column('equipment',sa.Column('support_until',sa.Date(),nullable=True))
    op.add_column('sales_return_lines',sa.Column('serials',postgresql.ARRAY(sa.String(64)),nullable=False,server_default='{}'))
    op.create_table('sales_delivery_serials',
        sa.Column('id',sa.BigInteger(),primary_key=True),
        sa.Column('delivery_id',sa.BigInteger(),sa.ForeignKey('sales_deliveries.id'),nullable=False),
        sa.Column('order_line_id',sa.Integer(),nullable=False),
        sa.Column('serial',sa.String(64),sa.ForeignKey('equipment.serial'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.Column('created_equipment',sa.Boolean(),nullable=False),
        sa.Column('equipment_before',postgresql.JSONB(),nullable=True),
        sa.Column('equipment_after',postgresql.JSONB(),nullable=False),
        sa.ForeignKeyConstraint(['delivery_id','order_line_id'],['sales_delivery_lines.delivery_id','sales_delivery_lines.order_line_no']),
        sa.UniqueConstraint('delivery_id','serial',name='uq_sales_delivery_serials_delivery_serial'))
    for col in ('delivery_id','serial'):
        op.create_index('ix_sales_delivery_serials_'+col,'sales_delivery_serials',[col])
    op.execute("""create function sales_delivery_serials_guard() returns trigger language plpgsql as $$
        begin raise exception 'Sales delivery serial history is immutable'; end $$;
        create trigger sales_delivery_serials_readonly before update or delete on sales_delivery_serials
        for each row execute function sales_delivery_serials_guard()""")


def downgrade():
    op.drop_table('sales_delivery_serials')
    op.execute('drop function sales_delivery_serials_guard()')
    op.drop_column('sales_return_lines','serials')
    op.drop_column('equipment','support_until')
    op.drop_constraint('ck_products_warranty_months','products',type_='check')
    op.drop_column('products','warranty_months')
    # Do not delete imported equipment or rename a referenced CARE product on rollback.
    op.execute("""do $$ begin if not exists(select 1 from products where code='MAY-CU-CARE') then
      alter table products drop constraint ck_products_code;
      alter table products add constraint ck_products_code check(code ~ '^TD-[0-9]{4,}$');
      end if; end $$""")
