"""SX2 (TUẤN 31/10/2026): phiếu sản xuất nhiều thành phẩm; thợ hoàn thành không xuất vật tư có lý do.

Revision ID: 0069
Revises: 0068
"""
from alembic import op
import sqlalchemy as sa

revision = '0069'
down_revision = '0068'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('production_orders', sa.Column('no_material_reason', sa.Text(), server_default='', nullable=False))
    op.drop_constraint('ck_production_px', 'production_orders', type_='check')
    # Dòng đã hoàn thành mà thiếu phiếu xuất (chỉ có sau một lần hạ rồi nâng lại) nhận lý do mặc định để thỏa luật mới.
    op.execute("update production_orders set no_material_reason='Không rõ, phiếu trước SX2' "
               "where status in ('HOAN_THANH','DA_NHAP') and px_doc_id is null and length(trim(no_material_reason))=0")
    op.create_check_constraint('ck_production_px', 'production_orders',
        "status not in ('HOAN_THANH','DA_NHAP') or px_doc_id is not null or length(trim(no_material_reason))>0")
    op.create_table('production_outputs',
        sa.Column('order_id', sa.BigInteger(), nullable=False),
        sa.Column('line_no', sa.Integer(), nullable=False),
        sa.Column('product_code', sa.String(length=16), nullable=False),
        sa.Column('qty_plan', sa.Integer(), nullable=False),
        sa.Column('qty_good', sa.Integer(), server_default='0', nullable=False),
        sa.Column('qty_scrap', sa.Integer(), server_default='0', nullable=False),
        sa.Column('unit_cost', sa.BigInteger(), server_default='0', nullable=False),
        sa.CheckConstraint('qty_plan>0 and qty_good>=0 and qty_scrap>=0 and qty_good+qty_scrap<=qty_plan and unit_cost>=0', name='ck_production_output_qty'),
        sa.ForeignKeyConstraint(['order_id'], ['production_orders.id']),
        sa.ForeignKeyConstraint(['product_code'], ['products.code']),
        sa.PrimaryKeyConstraint('order_id', 'line_no'),
        sa.UniqueConstraint('order_id', 'product_code', name='uq_production_output_product'))
    # Phiếu cũ = một thành phẩm, số liệu giữ nguyên.
    op.execute('insert into production_outputs (order_id, line_no, product_code, qty_plan, qty_good, qty_scrap, unit_cost) '
               'select id, 1, product_code, qty_plan, qty_good, qty_scrap, unit_cost from production_orders')
    op.execute('create trigger production_output_no_delete before delete on production_outputs for each row execute function production_no_delete()')


def downgrade():
    op.execute('drop trigger production_output_no_delete on production_outputs')
    op.drop_table('production_outputs')
    op.drop_constraint('ck_production_px', 'production_orders', type_='check')
    # Phiếu đã hoàn thành không vật tư không thỏa luật cũ; giữ dữ liệu, ràng buộc không kiểm dòng cũ.
    op.execute("alter table production_orders add constraint ck_production_px check (status not in ('HOAN_THANH','DA_NHAP') or px_doc_id is not null) not valid")
    op.drop_column('production_orders', 'no_material_reason')
