"""NS4: immutable commission rules/lines and permanent monthly snapshots.
Revision ID: 0044
Revises: 0043
"""
from alembic import op
import sqlalchemy as sa
revision='0044'
down_revision='0043'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('commission_rules',
        sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('target',sa.String(16),nullable=False),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id')),sa.Column('service_kind',sa.String(24)),
        sa.Column('pct',sa.Numeric(5,2),nullable=False),sa.Column('fixed_amount',sa.BigInteger(),nullable=False),
        sa.Column('effective_from',sa.Date(),nullable=False),sa.Column('reason',sa.Text(),nullable=False),
        sa.Column('created_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.Column('replaces_id',sa.BigInteger(),sa.ForeignKey('commission_rules.id'),unique=True),sa.Column('replaced_at',sa.DateTime(timezone=True)),
        sa.CheckConstraint("target in ('KINH_DOANH','THO')",name='ck_commission_rule_target'),
        sa.CheckConstraint('pct between 0 and 50 and fixed_amount >= 0',name='ck_commission_rule_money'),
        sa.CheckConstraint("target <> 'KINH_DOANH' or (fixed_amount=0 and service_kind is null)",name='ck_commission_rule_sales'),
        sa.CheckConstraint('length(trim(reason))>0',name='ck_commission_rule_reason'),
        sa.CheckConstraint("service_kind in ('SUA_TAI_CHO','MANG_VE','BAO_HANH','MUC_DINH_KY')",name='ck_commission_rule_kind'))
    op.create_index('uq_commission_rule_effective','commission_rules',['target',sa.text('coalesce(user_id,0)'),sa.text("coalesce(service_kind,'')"),'effective_from'],unique=True,postgresql_where=sa.text('replaced_at is null'))
    op.create_table('commission_months',sa.Column('month',sa.Date(),primary_key=True),
        sa.Column('locked_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('locked_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('total',sa.BigInteger(),nullable=False),sa.CheckConstraint('extract(day from month)=1',name='ck_commission_month_first'))
    op.create_table('commission_lines',sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('month',sa.Date(),nullable=False),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('source',sa.String(16),nullable=False),
        sa.Column('order_id',sa.BigInteger(),sa.ForeignKey('sales_orders.id')),sa.Column('ticket_id',sa.BigInteger(),sa.ForeignKey('service_tickets.id')),
        sa.Column('rule_id',sa.BigInteger(),sa.ForeignKey('commission_rules.id')),
        sa.Column('base',sa.BigInteger(),nullable=False),sa.Column('pct',sa.Numeric(5,2),nullable=False),sa.Column('fixed_amount',sa.BigInteger(),nullable=False),
        sa.Column('paid_ratio',sa.Numeric(7,6),nullable=False),sa.Column('entitled',sa.BigInteger(),nullable=False),sa.Column('previous',sa.BigInteger(),nullable=False),
        sa.Column('amount',sa.BigInteger(),nullable=False),sa.Column('note',sa.Text(),nullable=False),
        sa.Column('created_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.CheckConstraint('extract(day from month)=1',name='ck_commission_line_month'),
        sa.CheckConstraint("(source='DON_BAN' and order_id is not null and ticket_id is null) or (source='PHIEU_DV' and ticket_id is not null and order_id is null) or (source='DIEU_CHINH' and order_id is null and ticket_id is null and length(trim(note))>0)",name='ck_commission_line_source'),
        sa.CheckConstraint('pct between 0 and 50 and fixed_amount>=0 and paid_ratio between 0 and 1',name='ck_commission_line_values'),
        sa.CheckConstraint('amount=entitled-previous and amount<>0',name='ck_commission_line_amount'))
    op.create_index('uq_commission_line_order','commission_lines',['month','order_id'],unique=True,postgresql_where=sa.text("source='DON_BAN'"))
    op.create_index('uq_commission_line_ticket','commission_lines',['month','ticket_id'],unique=True,postgresql_where=sa.text("source='PHIEU_DV'"))
    op.create_index('ix_commission_line_user_month','commission_lines',['user_id','month'])
    op.execute("""
    create function commission_guard() returns trigger language plpgsql as $$
    begin
      perform pg_advisory_xact_lock(836004,0);
      if TG_TABLE_NAME='commission_rules' then
        if TG_OP='INSERT' then return NEW; end if;
        if TG_OP='UPDATE' then
          if OLD.replaced_at is null and NEW.replaced_at is not null
             and (to_jsonb(OLD)-'replaced_at')=(to_jsonb(NEW)-'replaced_at') then return NEW; end if;
        end if;
        raise exception 'COMMISSION_RULE_IMMUTABLE: chỉ được thay bản quy tắc';
      end if;
      if TG_OP<>'INSERT' then raise exception 'COMMISSION_IMMUTABLE: không sửa hoặc xóa hoa hồng'; end if;
      if exists(select 1 from commission_months where month=NEW.month) then
        raise exception 'DA_KHOA: tháng hoa hồng đã khóa';
      end if;
      if TG_TABLE_NAME='commission_months' then
        if NEW.total<>(select coalesce(sum(amount),0) from commission_lines where month=NEW.month) then
          raise exception 'COMMISSION_TOTAL_MISMATCH: tổng khóa không khớp các dòng';
        end if;
      end if;
      return NEW;
    end $$;
    create trigger commission_rules_guard before insert or update or delete on commission_rules for each row execute function commission_guard();
    create trigger commission_lines_guard before insert or update or delete on commission_lines for each row execute function commission_guard();
    create trigger commission_months_guard before insert or update or delete on commission_months for each row execute function commission_guard();
    """)


def downgrade():
    op.drop_table('commission_lines');op.drop_table('commission_months');op.drop_table('commission_rules')
    op.execute('drop function commission_guard()')
