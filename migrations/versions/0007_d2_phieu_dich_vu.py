"""D2: phiếu dịch vụ và lịch sử các bước

Revision ID: 0007
Revises: 4e3377aa798b
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '4e3377aa798b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('service_tickets',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('customer_code', sa.String(length=16), nullable=False),
    sa.Column('site_id', sa.BigInteger(), nullable=True),
    sa.Column('serial', sa.String(length=64), nullable=True),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('priority', sa.String(length=8), nullable=False),
    sa.Column('symptom', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('assignee_id', sa.Integer(), nullable=True),
    sa.Column('appointment_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status_reason', sa.Text(), nullable=False),
    sa.Column('transfer_request', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("kind in ('SUA_TAI_CHO','MANG_VE','BAO_HANH','MUC_DINH_KY')", name='ck_service_tickets_kind'),
    sa.CheckConstraint("priority in ('KHAN','GAP','THUONG')", name='ck_service_tickets_priority'),
    sa.CheckConstraint("status = 'MOI' or assignee_id is not null or status = 'DA_HUY'", name='ck_service_tickets_assignee'),
    sa.CheckConstraint("status in ('MOI','DA_GIAO','DA_NHAN','DA_HEN','DANG_LAM','CHO_LINH_KIEN','HOAN_THANH','KHONG_LAM_DUOC','DA_HUY')", name='ck_service_tickets_status'),
    sa.ForeignKeyConstraint(['assignee_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['customer_code'], ['customers.code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('number')
    )
    op.create_index(op.f('ix_service_tickets_assignee_id'), 'service_tickets', ['assignee_id'], unique=False)
    op.create_index(op.f('ix_service_tickets_customer_code'), 'service_tickets', ['customer_code'], unique=False)
    op.create_index('ix_service_tickets_open', 'service_tickets', ['status', 'due_at'], unique=False)
    op.create_table('service_steps',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('from_status', sa.String(length=16), nullable=True),
    sa.Column('to_status', sa.String(length=16), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=False),
    sa.Column('assignee_id', sa.Integer(), nullable=True),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['assignee_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['service_tickets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_service_steps_ticket_id'), 'service_steps', ['ticket_id'], unique=False)
    # Lịch sử phiếu chỉ thêm, giống stock_ledger.
    op.execute("""
        create function service_steps_readonly() returns trigger language plpgsql as $$
        begin raise exception 'service_steps chi duoc them, khong sua hoac xoa'; end $$;
        create trigger service_steps_readonly before update or delete on service_steps
          for each row execute function service_steps_readonly();
    """)


def downgrade() -> None:
    op.execute('drop trigger if exists service_steps_readonly on service_steps')
    op.execute('drop function if exists service_steps_readonly()')
    op.drop_index(op.f('ix_service_steps_ticket_id'), table_name='service_steps')
    op.drop_table('service_steps')
    op.drop_index('ix_service_tickets_open', table_name='service_tickets')
    op.drop_index(op.f('ix_service_tickets_customer_code'), table_name='service_tickets')
    op.drop_index(op.f('ix_service_tickets_assignee_id'), table_name='service_tickets')
    op.drop_table('service_tickets')
