"""Thẻ B: lỗi gặp / cách xử lý trên phiếu, GPS lượt đến khách, nháp báo cáo thợ.
Revision ID: 0046
Revises: 0045
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='0046'
down_revision='0045'
branch_labels=None
depends_on=None


def _guard(extra):
    return f"""
        create or replace function service_visits_guard() returns trigger language plpgsql as $$
        begin
          if TG_OP = 'UPDATE' then
            if OLD.ended_at is null and NEW.ended_at is not null and NEW.outcome is not null
               and NEW.version = OLD.version + 1
               and (to_jsonb(NEW) - ARRAY['ended_at','outcome','note','next_appointment_at','version'])
                 = (to_jsonb(OLD) - ARRAY['ended_at','outcome','note','next_appointment_at','version']) then
              return NEW;
            end if;{extra}
          end if;
          raise exception 'service_visits bat bien; chi chot luot mo mot lan';
        end $$;"""


# Thẻ B: lượt đang mở được gắn vị trí lúc đến đúng một lần; mọi cột khác vẫn bất biến.
GPS_ONCE = """
            if OLD.ended_at is null and NEW.ended_at is null and OLD.arrive_lat is null and NEW.arrive_lat is not null
               and NEW.version = OLD.version + 1
               and (to_jsonb(NEW) - ARRAY['arrive_lat','arrive_lng','arrive_accuracy','version'])
                 = (to_jsonb(OLD) - ARRAY['arrive_lat','arrive_lng','arrive_accuracy','version']) then
              return NEW;
            end if;"""


def upgrade():
    op.add_column('service_tickets',sa.Column('fault_found',sa.Text(),nullable=False,server_default=''))
    op.add_column('service_tickets',sa.Column('fix_done',sa.Text(),nullable=False,server_default=''))
    op.add_column('service_visits',sa.Column('arrive_lat',sa.Numeric(9,6)))
    op.add_column('service_visits',sa.Column('arrive_lng',sa.Numeric(9,6)))
    op.add_column('service_visits',sa.Column('arrive_accuracy',sa.Integer()))
    op.create_check_constraint('ck_service_visits_gps','service_visits',
        '(arrive_lat is null) = (arrive_lng is null) and (arrive_lat is null or (arrive_lat between -90 and 90 and arrive_lng between -180 and 180)) and (arrive_accuracy is null or arrive_accuracy >= 0)')
    op.create_table('service_drafts',
        sa.Column('ticket_id',sa.BigInteger(),sa.ForeignKey('service_tickets.id'),primary_key=True),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),primary_key=True),
        sa.Column('data',postgresql.JSONB(),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.execute(_guard(GPS_ONCE))


def downgrade():
    op.execute(_guard(''))
    op.drop_table('service_drafts')
    op.drop_constraint('ck_service_visits_gps','service_visits')
    for column in ('arrive_accuracy','arrive_lng','arrive_lat'):op.drop_column('service_visits',column)
    for column in ('fix_done','fault_found'):op.drop_column('service_tickets',column)
