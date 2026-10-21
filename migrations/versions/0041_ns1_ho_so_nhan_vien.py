"""NS1: hồ sơ nhân viên và lịch sử lương bất biến.
Revision ID: 0041
Revises: 0040
"""
from alembic import op
import sqlalchemy as sa

revision = '0041'
down_revision = '0040'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('create sequence employee_code_seq')
    op.create_table('employees',
        sa.Column('id',sa.BigInteger(),primary_key=True), sa.Column('code',sa.String(32),nullable=False,unique=True),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False,unique=True),
        sa.Column('position',sa.Text(),nullable=False),sa.Column('start_date',sa.Date(),nullable=False),
        sa.Column('end_date',sa.Date()),sa.Column('birth_date',sa.Date()),
        sa.Column('id_number',sa.Text(),nullable=False,server_default=''),sa.Column('id_issued_on',sa.Date()),
        *[sa.Column(k,sa.Text(),nullable=False,server_default='') for k in ('address','emergency_name','emergency_phone','note')],
        sa.Column('version',sa.Integer(),nullable=False,server_default='1'),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.Column('created_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.CheckConstraint("position in ('Kỹ thuật','Kinh doanh','Kho','Kế toán','Điều phối','Lễ tân','Quản lý','Khác')",name='ck_employees_position'),
        sa.CheckConstraint('end_date is null or end_date >= start_date',name='ck_employees_dates'),
        sa.CheckConstraint("id_number = '' or id_number ~ '^([0-9]{9}|[0-9]{12})$'",name='ck_employees_id_number'))
    op.create_index('uq_employees_id_number','employees',['id_number'],unique=True,postgresql_where=sa.text("id_number <> ''"))
    op.create_table('employee_pay',
        sa.Column('employee_id',sa.BigInteger(),sa.ForeignKey('employees.id'),primary_key=True),
        *[sa.Column(k,sa.Text(),nullable=False,server_default='') for k in ('bank_name','bank_account','bank_holder')],
        sa.Column('version',sa.Integer(),nullable=False,server_default='1'),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.CheckConstraint("bank_account = '' or bank_account ~ '^[0-9]{6,20}$'",name='ck_employee_pay_bank_account'))
    op.create_table('employee_salary_versions',
        sa.Column('id',sa.BigInteger(),primary_key=True),
        sa.Column('employee_id',sa.BigInteger(),sa.ForeignKey('employees.id'),nullable=False),
        sa.Column('effective_from',sa.Date(),nullable=False),
        sa.Column('base_salary',sa.BigInteger(),nullable=False),
        *[sa.Column(k,sa.BigInteger(),nullable=False,server_default='0') for k in ('meal_allowance','fuel_allowance','phone_allowance','other_allowance')],
        sa.Column('standard_days',sa.Numeric(4,1),nullable=False,server_default='26'),
        sa.Column('reason',sa.Text(),nullable=False),sa.Column('created_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.Column('replaces_id',sa.BigInteger(),sa.ForeignKey('employee_salary_versions.id')),
        sa.Column('replaced_at',sa.DateTime(timezone=True)),
        sa.CheckConstraint('base_salary between 0 and 1000000000000 and meal_allowance between 0 and 1000000000000 and fuel_allowance between 0 and 1000000000000 and phone_allowance between 0 and 1000000000000 and other_allowance between 0 and 1000000000000',name='ck_employee_salary_money'),
        sa.CheckConstraint('standard_days between 1 and 31',name='ck_employee_salary_days'),
        sa.CheckConstraint('length(trim(reason)) > 0',name='ck_employee_salary_reason'))
    op.create_index('uq_employee_salary_effective','employee_salary_versions',['employee_id','effective_from'],unique=True,postgresql_where=sa.text('replaced_at is null'))
    op.execute("""
    create function employee_salary_immutable() returns trigger language plpgsql as $$
    begin
      if TG_OP = 'UPDATE' then
        if OLD.replaced_at is null and NEW.replaced_at is not null
           and (to_jsonb(OLD) - 'replaced_at') = (to_jsonb(NEW) - 'replaced_at') then
          return NEW;
        end if;
      end if;
      raise exception 'EMPLOYEE_SALARY_IMMUTABLE: chỉ được thay bản lương, không sửa hoặc xóa lịch sử';
    end $$;
    create trigger employee_salary_immutable before update or delete on employee_salary_versions
      for each row execute function employee_salary_immutable();
    """)


def downgrade():
    op.drop_table('employee_salary_versions')
    op.execute('drop function employee_salary_immutable()')
    op.drop_table('employee_pay')
    op.drop_table('employees')
    op.execute('drop sequence employee_code_seq')
