"""kt3_yeu_cau_linh_kien

Revision ID: 0024
Revises: 0023
"""
from alembic import op
import sqlalchemy as sa


revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None

OLD_STATUSES = "'MOI','DA_GIAO','DA_NHAN','DA_HEN','DANG_LAM','CHO_LINH_KIEN','HOAN_THANH','DA_DONG','KHONG_LAM_DUOC','DA_HUY'"


def upgrade() -> None:
    op.create_table('part_requests',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('requester_id', sa.Integer(), nullable=False),
    sa.Column('product_code', sa.String(length=16), nullable=False),
    sa.Column('qty', sa.Numeric(precision=14, scale=3), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('from_warehouse_code', sa.String(length=16), nullable=True),
    sa.Column('to_warehouse_code', sa.String(length=16), nullable=True),
    sa.Column('stock_doc_id', sa.BigInteger(), nullable=True),
    sa.Column('decided_by', sa.Integer(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("btrim(reason) <> ''", name='ck_part_requests_reason'),
    sa.CheckConstraint("status in ('CHO_CAP','DA_CAP','HET_HANG','DA_HUY')", name='ck_part_requests_status'),
    sa.CheckConstraint('qty > 0', name='ck_part_requests_qty'),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['from_warehouse_code'], ['warehouses.code'], ),
    sa.ForeignKeyConstraint(['to_warehouse_code'], ['warehouses.code'], ),
    sa.ForeignKeyConstraint(['product_code'], ['products.code'], ),
    sa.ForeignKeyConstraint(['requester_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['stock_doc_id'], ['stock_docs.id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['service_tickets.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number')
    )
    op.create_index('ix_part_requests_queue', 'part_requests', ['status', 'created_at', 'id'], unique=False)
    op.create_index('ix_part_requests_ticket_id', 'part_requests', ['ticket_id'], unique=False)
    op.add_column('service_tickets', sa.Column('hold_reason', sa.String(length=24), server_default='', nullable=False))
    op.drop_constraint('ck_service_tickets_status', 'service_tickets', type_='check')
    op.create_check_constraint('ck_service_tickets_status', 'service_tickets', f"status in ({OLD_STATUSES},'TAM_DUNG')")
    op.create_check_constraint('ck_service_tickets_hold_reason', 'service_tickets', "hold_reason in ('','CHO_KHACH_DUYET','CHO_KY_THUAT_KHAC','CHO_DOI_TAC','HEN_NGAY_KHAC','KHAC')")


def downgrade() -> None:
    # Bản cũ không biết TAM_DUNG; giữ phiếu mở và giữ nguyên lịch sử bước.
    op.execute("update service_tickets set status='CHO_LINH_KIEN' where status='TAM_DUNG'")
    op.drop_constraint('ck_service_tickets_hold_reason', 'service_tickets', type_='check')
    op.drop_constraint('ck_service_tickets_status', 'service_tickets', type_='check')
    op.create_check_constraint('ck_service_tickets_status', 'service_tickets', f"status in ({OLD_STATUSES})")
    op.drop_column('service_tickets', 'hold_reason')
    op.drop_index('ix_part_requests_ticket_id', table_name='part_requests')
    op.drop_index('ix_part_requests_queue', table_name='part_requests')
    op.drop_table('part_requests')
