"""S4: phân công giao đơn, giữ hàng ở nhiều kho và lịch sử thất bại bất biến."""
from alembic import op
import sqlalchemy as sa

revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('sales_orders', sa.Column('delivery_assignee_id', sa.Integer(), nullable=True))
    op.add_column('sales_orders', sa.Column('delivery_assigned_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('sales_orders', sa.Column('service_ticket_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key('fk_sales_orders_delivery_assignee', 'sales_orders', 'users', ['delivery_assignee_id'], ['id'])
    op.create_foreign_key('fk_sales_orders_service_ticket', 'sales_orders', 'service_tickets', ['service_ticket_id'], ['id'])
    op.create_table('sales_delivery_attempts',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('order_id', sa.BigInteger(), sa.ForeignKey('sales_orders.id'), nullable=False),
        sa.Column('assignee_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('result', sa.String(20), nullable=False),
        sa.Column('reason', sa.String(20), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("result = 'KHONG_GIAO_DUOC'", name='ck_sales_delivery_attempts_result'),
        sa.CheckConstraint("reason in ('KHACH_VANG','KHACH_TU_CHOI','SAI_DIA_CHI','HEN_LAI','KHAC')", name='ck_sales_delivery_attempts_reason'))
    op.create_index('ix_sales_delivery_attempts_order_id','sales_delivery_attempts',['order_id'])
    op.execute("""CREATE FUNCTION protect_sales_delivery_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'sales_delivery_attempts is append only'; END $$""")
    op.execute('CREATE TRIGGER sales_delivery_attempt_readonly BEFORE UPDATE OR DELETE ON sales_delivery_attempts FOR EACH ROW EXECUTE FUNCTION protect_sales_delivery_attempt()')
    op.drop_index('uq_stock_reservations_active_line', table_name='stock_reservations')
    op.create_index('uq_stock_reservations_active_line','stock_reservations',['order_id','order_line_id','warehouse_code'],unique=True,postgresql_where=sa.text("status = 'GIU'"))


def downgrade():
    # S1 nạp lại chỉ chấp nhận một GIU/dòng. Từ chối thay vì làm mất giữ hàng.
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM stock_reservations WHERE status='GIU'
        GROUP BY order_id,order_line_id HAVING count(*)>1) THEN
        RAISE EXCEPTION 'Return technician stock before downgrading S4'; END IF; END $$""")
    op.drop_index('uq_stock_reservations_active_line', table_name='stock_reservations')
    op.create_index('uq_stock_reservations_active_line','stock_reservations',['order_id','order_line_id'],unique=True,postgresql_where=sa.text("status = 'GIU'"))
    op.drop_table('sales_delivery_attempts')
    op.execute('DROP FUNCTION protect_sales_delivery_attempt()')
    op.drop_constraint('fk_sales_orders_service_ticket','sales_orders',type_='foreignkey')
    op.drop_constraint('fk_sales_orders_delivery_assignee','sales_orders',type_='foreignkey')
    for column in ('service_ticket_id','delivery_assigned_at','delivery_assignee_id'):
        op.drop_column('sales_orders',column)
