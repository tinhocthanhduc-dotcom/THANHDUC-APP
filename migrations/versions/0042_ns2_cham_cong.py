"""NS2: chấm công bất biến, yêu cầu, ngày lễ và khóa tháng.
Revision ID: 0042
Revises: 0041
"""
from alembic import op
import sqlalchemy as sa
revision='0042'
down_revision='0041'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('attendance_requests',
        sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('number',sa.String(32),nullable=False,unique=True),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('kind',sa.Text(),nullable=False),
        sa.Column('punch_at',sa.DateTime(timezone=True)),sa.Column('from_date',sa.Date()),sa.Column('to_date',sa.Date()),
        sa.Column('part',sa.Text(),nullable=False,server_default='CA_NGAY'),sa.Column('reason',sa.Text(),nullable=False),
        sa.Column('status',sa.Text(),nullable=False,server_default='CHO_DUYET'),
        sa.Column('decided_by',sa.Integer(),sa.ForeignKey('users.id')),sa.Column('decided_at',sa.DateTime(timezone=True)),
        sa.Column('decision_note',sa.Text(),nullable=False,server_default=''),sa.Column('version',sa.Integer(),nullable=False,server_default='1'),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.CheckConstraint("kind in ('BO_SUNG_VAO','BO_SUNG_RA','NGHI_PHEP','NGHI_KHONG_LUONG')",name='ck_attendance_request_kind'),
        sa.CheckConstraint("status in ('CHO_DUYET','DA_DUYET','TU_CHOI','DA_HUY')",name='ck_attendance_request_status'),
        sa.CheckConstraint("part in ('CA_NGAY','SANG','CHIEU')",name='ck_attendance_request_part'),
        sa.CheckConstraint("(kind in ('BO_SUNG_VAO','BO_SUNG_RA') and punch_at is not null and from_date is null and to_date is null) or (kind in ('NGHI_PHEP','NGHI_KHONG_LUONG') and punch_at is null and from_date is not null and to_date is not null and from_date <= to_date and (part='CA_NGAY' or from_date=to_date))",name='ck_attendance_request_dates'),
        sa.CheckConstraint('length(trim(reason)) > 0',name='ck_attendance_request_reason'))
    op.create_index('ix_attendance_requests_user_id','attendance_requests',['user_id'])
    op.create_index('ix_attendance_request_queue','attendance_requests',['status','created_at'])
    op.create_table('attendance_punches',
        sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('kind',sa.Text(),nullable=False),sa.Column('at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('lat',sa.Numeric(10,7)),sa.Column('lng',sa.Numeric(10,7)),sa.Column('accuracy_m',sa.Integer()),
        sa.Column('source',sa.Text(),nullable=False),sa.Column('request_id',sa.BigInteger(),sa.ForeignKey('attendance_requests.id'),unique=True),
        sa.Column('off_hours',sa.Boolean(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.CheckConstraint("kind in ('IN','OUT')",name='ck_attendance_punch_kind'),
        sa.CheckConstraint("source in ('APP','BO_SUNG') and ((source='APP' and request_id is null) or (source='BO_SUNG' and request_id is not null))",name='ck_attendance_punch_source'),
        sa.CheckConstraint('(lat is null and lng is null) or (lat is not null and lng is not null and lat between -90 and 90 and lng between -180 and 180)',name='ck_attendance_punch_location'),
        sa.CheckConstraint('accuracy_m is null or accuracy_m >= 0',name='ck_attendance_punch_accuracy'))
    op.create_index('ix_attendance_punch_user_at','attendance_punches',['user_id','at'])
    op.create_table('attendance_holidays',sa.Column('day',sa.Date(),primary_key=True),sa.Column('name',sa.Text(),nullable=False))
    op.create_table('attendance_month_locks',sa.Column('month',sa.Date(),primary_key=True),
        sa.Column('locked_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('locked_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('unlocked_by',sa.Integer(),sa.ForeignKey('users.id')),sa.Column('unlocked_at',sa.DateTime(timezone=True)),
        sa.Column('unlock_reason',sa.Text(),nullable=False,server_default=''),sa.CheckConstraint('extract(day from month) = 1',name='ck_attendance_lock_month'))
    op.execute("""create function attendance_punch_immutable() returns trigger language plpgsql as $$
      begin raise exception 'ATTENDANCE_PUNCH_IMMUTABLE: không sửa hoặc xóa lần chấm công'; end $$;
      create trigger attendance_punch_immutable before update or delete on attendance_punches
        for each row execute function attendance_punch_immutable();""")


def downgrade():
    op.drop_table('attendance_punches');op.execute('drop function attendance_punch_immutable()')
    op.drop_table('attendance_requests');op.drop_table('attendance_holidays');op.drop_table('attendance_month_locks')
