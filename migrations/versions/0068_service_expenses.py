"""Chi phí phát sinh thợ tự trả theo phiếu dịch vụ (TUẤN 30/10/2026): bảng mới + ghép danh mục chi hoàn tiền thợ.

Revision ID: 0068
Revises: 0067
"""
from alembic import op
import sqlalchemy as sa

revision = '0068'
down_revision = '0067'
branch_labels = None
depends_on = None

OLD = "'DANH_MUC_BAN_HANG','DANH_MUC_DICH_VU','DANH_MUC_MUA_HANG','DANH_MUC_HOAN_TIEN_BAN','DANH_MUC_LUONG'"
NEW = OLD + ",'DANH_MUC_CHI_PHI_THO'"


def _mapping_checks(keys):
    op.drop_constraint('ck_finance_mappings_key', 'finance_mappings', type_='check')
    op.drop_constraint('ck_finance_mappings_target', 'finance_mappings', type_='check')
    op.create_check_constraint('ck_finance_mappings_key', 'finance_mappings', f"key in ('THU_TIEN_MAT','THU_CHUYEN_KHOAN',{keys})")
    op.create_check_constraint('ck_finance_mappings_target', 'finance_mappings',
        f"(key in ('THU_TIEN_MAT','THU_CHUYEN_KHOAN') and category_code is null) or (key in ({keys}) and fund_code is null)")


def upgrade():
    _mapping_checks(NEW)
    op.execute("INSERT INTO finance_mappings (key,version) VALUES ('DANH_MUC_CHI_PHI_THO',1)")
    op.create_table('service_expenses',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('ticket_id', sa.BigInteger(), nullable=False),
        sa.Column('technician_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('place', sa.Text(), server_default='', nullable=False),
        sa.Column('amount', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.String(length=16), server_default='CHO_DUYET', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('decided_by', sa.Integer(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decision_note', sa.Text(), server_default='', nullable=False),
        sa.Column('cash_doc_id', sa.BigInteger(), nullable=True),
        sa.Column('paid_by', sa.Integer(), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        sa.CheckConstraint("kind in ('LINH_KIEN','GUI_XE','CHO_MAY','KHAC')", name='ck_service_expenses_kind'),
        sa.CheckConstraint("status in ('CHO_DUYET','DA_DUYET','TU_CHOI','DA_HUY','DA_HOAN')", name='ck_service_expenses_status'),
        sa.CheckConstraint('amount > 0', name='ck_service_expenses_amount'),
        sa.CheckConstraint("btrim(description) <> ''", name='ck_service_expenses_description'),
        sa.CheckConstraint("(status = 'DA_HOAN') = (cash_doc_id is not null and paid_at is not null and paid_by is not null)", name='ck_service_expenses_paid'),
        sa.CheckConstraint("status <> 'TU_CHOI' or btrim(decision_note) <> ''", name='ck_service_expenses_reject_note'),
        sa.ForeignKeyConstraint(['ticket_id'], ['service_tickets.id']),
        sa.ForeignKeyConstraint(['technician_id'], ['users.id']),
        sa.ForeignKeyConstraint(['decided_by'], ['users.id']),
        sa.ForeignKeyConstraint(['paid_by'], ['users.id']),
        sa.ForeignKeyConstraint(['cash_doc_id'], ['cash_docs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_service_expenses_ticket_id', 'service_expenses', ['ticket_id'])
    op.create_index('ix_service_expenses_status_tech', 'service_expenses', ['status', 'technician_id'])


def downgrade():
    op.drop_index('ix_service_expenses_status_tech', table_name='service_expenses')
    op.drop_index('ix_service_expenses_ticket_id', table_name='service_expenses')
    op.drop_table('service_expenses')
    op.execute("DELETE FROM finance_mappings WHERE key='DANH_MUC_CHI_PHI_THO'")
    _mapping_checks(OLD)
