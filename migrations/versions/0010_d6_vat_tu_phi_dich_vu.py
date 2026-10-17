"""D6: vật tư, phí dịch vụ, số phải thu; khóa ngoại địa điểm và máy cho phiếu dịch vụ

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None

STATUS_OLD = "status in ('MOI','DA_GIAO','DA_NHAN','DA_HEN','DANG_LAM','CHO_LINH_KIEN','HOAN_THANH','KHONG_LAM_DUOC','DA_HUY')"
STATUS_NEW = "status in ('MOI','DA_GIAO','DA_NHAN','DA_HEN','DANG_LAM','CHO_LINH_KIEN','HOAN_THANH','DA_DONG','KHONG_LAM_DUOC','DA_HUY')"


def upgrade() -> None:
    op.create_table('service_lines',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('product_code', sa.String(length=16), nullable=True),
    sa.Column('warehouse_code', sa.String(length=16), nullable=True),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('qty', sa.Numeric(precision=14, scale=3), nullable=False),
    sa.Column('unit_price', sa.BigInteger(), nullable=False),
    sa.Column('vat_rate', sa.Integer(), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('vat_amount', sa.BigInteger(), nullable=False),
    sa.Column('stock_doc_id', sa.BigInteger(), nullable=True),
    sa.CheckConstraint("(kind = 'VAT_TU') = (product_code is not null and warehouse_code is not null)", name='ck_service_lines_part'),
    sa.CheckConstraint("kind in ('PHI','VAT_TU')", name='ck_service_lines_kind'),
    sa.CheckConstraint('qty > 0', name='ck_service_lines_qty'),
    sa.CheckConstraint('unit_price >= 0 and amount >= 0 and vat_amount >= 0', name='ck_service_lines_money'),
    sa.CheckConstraint('vat_rate in (0,5,8,10)', name='ck_service_lines_vat'),
    sa.ForeignKeyConstraint(['product_code'], ['products.code'], ),
    sa.ForeignKeyConstraint(['stock_doc_id'], ['stock_docs.id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['service_tickets.id'], ),
    sa.ForeignKeyConstraint(['warehouse_code'], ['warehouses.code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ticket_id', 'line_no', name='uq_service_lines_no')
    )
    op.create_index(op.f('ix_service_lines_ticket_id'), 'service_lines', ['ticket_id'], unique=False)
    op.add_column('service_tickets', sa.Column('billing', sa.String(length=8), nullable=True))
    op.add_column('service_tickets', sa.Column('free_reason', sa.Text(), nullable=False, server_default=''))
    op.alter_column('service_tickets', 'free_reason', server_default=None)
    op.add_column('service_tickets', sa.Column('subtotal', sa.BigInteger(), nullable=True))
    op.add_column('service_tickets', sa.Column('vat_total', sa.BigInteger(), nullable=True))
    op.add_column('service_tickets', sa.Column('total', sa.BigInteger(), nullable=True))
    op.add_column('service_tickets', sa.Column('receivable', sa.BigInteger(), nullable=True))
    op.add_column('service_tickets', sa.Column('priced_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_service_tickets_serial'), 'service_tickets', ['serial'], unique=False)
    op.create_foreign_key('service_tickets_serial_fkey', 'service_tickets', 'equipment', ['serial'], ['serial'])
    op.create_foreign_key('service_tickets_site_id_fkey', 'service_tickets', 'customer_sites', ['site_id'], ['id'])
    op.create_check_constraint('ck_service_tickets_billing', 'service_tickets', "billing in ('THU_TIEN','MIEN_PHI')")
    op.create_check_constraint('ck_service_tickets_free_reason', 'service_tickets', "billing <> 'MIEN_PHI' or free_reason <> ''")
    op.create_check_constraint('ck_service_tickets_priced', 'service_tickets', "(priced_at is null) = (total is null)")
    op.drop_constraint('ck_service_tickets_status', 'service_tickets', type_='check')
    op.create_check_constraint('ck_service_tickets_status', 'service_tickets', STATUS_NEW)


def downgrade() -> None:
    op.drop_constraint('ck_service_tickets_status', 'service_tickets', type_='check')
    op.create_check_constraint('ck_service_tickets_status', 'service_tickets', STATUS_OLD)
    for name in ('ck_service_tickets_priced', 'ck_service_tickets_free_reason', 'ck_service_tickets_billing'):
        op.drop_constraint(name, 'service_tickets', type_='check')
    op.drop_constraint('service_tickets_site_id_fkey', 'service_tickets', type_='foreignkey')
    op.drop_constraint('service_tickets_serial_fkey', 'service_tickets', type_='foreignkey')
    op.drop_index(op.f('ix_service_tickets_serial'), table_name='service_tickets')
    op.drop_column('service_tickets', 'priced_at')
    op.drop_column('service_tickets', 'receivable')
    op.drop_column('service_tickets', 'total')
    op.drop_column('service_tickets', 'vat_total')
    op.drop_column('service_tickets', 'subtotal')
    op.drop_column('service_tickets', 'free_reason')
    op.drop_column('service_tickets', 'billing')
    op.drop_index(op.f('ix_service_lines_ticket_id'), table_name='service_lines')
    op.drop_table('service_lines')
