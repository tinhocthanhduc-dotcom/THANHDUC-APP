"""s1_giu_hang

Revision ID: 0027
Revises: 0026
"""
from alembic import op
import sqlalchemy as sa


revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('stock_reservations',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('order_line_id', sa.Integer(), nullable=False),
    sa.Column('warehouse_code', sa.String(length=16), nullable=False),
    sa.Column('product_code', sa.String(length=16), nullable=False),
    sa.Column('qty', sa.Numeric(precision=14, scale=3), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('closed_by', sa.Integer(), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(status = 'GIU' and closed_by is null and closed_at is null) or (status <> 'GIU' and closed_by is not null and closed_at is not null)", name='ck_stock_reservations_closed'),
    sa.CheckConstraint("status in ('GIU','DA_XUAT','DA_NHA')", name='ck_stock_reservations_status'),
    sa.CheckConstraint('qty > 0', name='ck_stock_reservations_qty'),
    sa.CheckConstraint("status <> 'DA_NHA' or length(trim(reason)) > 0", name='ck_stock_reservations_release_reason'),
    sa.ForeignKeyConstraint(['closed_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['order_id', 'order_line_id'], ['sales_order_lines.order_id', 'sales_order_lines.line_no'], ),
    sa.ForeignKeyConstraint(['order_id'], ['sales_orders.id'], ),
    sa.ForeignKeyConstraint(['product_code'], ['products.code'], ),
    sa.ForeignKeyConstraint(['warehouse_code'], ['warehouses.code'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_stock_reservations_active_stock', 'stock_reservations', ['warehouse_code', 'product_code'], unique=False, postgresql_where=sa.text("status = 'GIU'"))
    op.create_index('uq_stock_reservations_active_line', 'stock_reservations', ['order_id', 'order_line_id'], unique=True, postgresql_where=sa.text("status = 'GIU'"))
    op.add_column('sales_orders', sa.Column('promised_date', sa.Date(), nullable=True))
    op.execute("""
        CREATE FUNCTION stock_reservations_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Không xóa lịch sử giữ hàng';
            END IF;
            IF OLD.status <> 'GIU' OR NEW.status NOT IN ('DA_XUAT','DA_NHA')
               OR ROW(NEW.id,NEW.order_id,NEW.order_line_id,NEW.warehouse_code,NEW.product_code,NEW.qty,NEW.created_by,NEW.created_at)
                  IS DISTINCT FROM ROW(OLD.id,OLD.order_id,OLD.order_line_id,OLD.warehouse_code,OLD.product_code,OLD.qty,OLD.created_by,OLD.created_at) THEN
                RAISE EXCEPTION 'Chỉ chốt dòng giữ hàng, không sửa lịch sử';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER stock_reservations_readonly BEFORE UPDATE OR DELETE ON stock_reservations
        FOR EACH ROW EXECUTE FUNCTION stock_reservations_guard();
    """)



def downgrade() -> None:
    op.execute('DROP TRIGGER stock_reservations_readonly ON stock_reservations')
    op.execute('DROP FUNCTION stock_reservations_guard()')
    op.drop_column('sales_orders', 'promised_date')
    op.drop_index('uq_stock_reservations_active_line', table_name='stock_reservations', postgresql_where=sa.text("status = 'GIU'"))
    op.drop_index('ix_stock_reservations_active_stock', table_name='stock_reservations', postgresql_where=sa.text("status = 'GIU'"))
    op.drop_table('stock_reservations')
