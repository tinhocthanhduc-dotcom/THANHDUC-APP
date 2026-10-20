"""kt5_luot_den_khach

Revision ID: 0032
Revises: 0031
"""
from alembic import op
import sqlalchemy as sa


revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('service_visits',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('technician_id', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('outcome', sa.String(length=24), nullable=True),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('next_appointment_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.CheckConstraint("outcome in ('XONG','HEN_LAI','KHONG_GAP_KHACH','CHO_LINH_KIEN','TAM_DUNG','KHONG_LAM_DUOC','CHUYEN_THO','HUY')", name='ck_service_visits_outcome'),
    sa.CheckConstraint("outcome not in ('HEN_LAI','KHONG_GAP_KHACH') or (next_appointment_at is not null and btrim(note) <> '')", name='ck_service_visits_appointment'),
    sa.CheckConstraint('(ended_at is null) = (outcome is null)', name='ck_service_visits_closed'),
    sa.CheckConstraint('ended_at is null or ended_at >= started_at', name='ck_service_visits_time'),
    sa.ForeignKeyConstraint(['technician_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['service_tickets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_service_visits_technician_started', 'service_visits', ['technician_id', 'started_at'], unique=False)
    op.create_index(op.f('ix_service_visits_ticket_id'), 'service_visits', ['ticket_id'], unique=False)
    op.create_index('uq_service_visits_open_ticket', 'service_visits', ['ticket_id'], unique=True, postgresql_where=sa.text('ended_at is null'))
    # Lấy đúng lần vào DANG_LAM gần nhất; phiếu cũ thiếu bước dùng ngày tạo.
    op.execute("""
        insert into service_visits (ticket_id, technician_id, started_at)
        select t.id, t.assignee_id, coalesce((
            select s.at from service_steps s
            where s.ticket_id = t.id and s.to_status = 'DANG_LAM'
              and s.from_status is distinct from 'DANG_LAM'
            order by s.at desc, s.id desc limit 1), t.created_at)
        from service_tickets t where t.status = 'DANG_LAM';
        create function service_visits_guard() returns trigger language plpgsql as $$
        begin
          if TG_OP = 'UPDATE' then
            if OLD.ended_at is null and NEW.ended_at is not null and NEW.outcome is not null
               and NEW.version = OLD.version + 1
               and (to_jsonb(NEW) - ARRAY['ended_at','outcome','note','next_appointment_at','version'])
                 = (to_jsonb(OLD) - ARRAY['ended_at','outcome','note','next_appointment_at','version']) then
              return NEW;
            end if;
          end if;
          raise exception 'service_visits bat bien; chi chot luot mo mot lan';
        end $$;
        create trigger service_visits_readonly before update or delete on service_visits
          for each row execute function service_visits_guard();
    """)


def downgrade() -> None:
    op.execute('drop trigger service_visits_readonly on service_visits')
    op.execute('drop function service_visits_guard()')
    op.drop_index('uq_service_visits_open_ticket', table_name='service_visits', postgresql_where=sa.text('ended_at is null'))
    op.drop_index(op.f('ix_service_visits_ticket_id'), table_name='service_visits')
    op.drop_index('ix_service_visits_technician_started', table_name='service_visits')
    op.drop_table('service_visits')
