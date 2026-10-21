"""NS3: mục tiêu KPI; không sửa dữ liệu ghi sổ.
Revision ID: 0043
Revises: 0042
"""
from alembic import op
import sqlalchemy as sa
revision='0043'
down_revision='0042'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('kpi_targets',
        sa.Column('id',sa.BigInteger(),primary_key=True),
        sa.Column('month',sa.Date(),nullable=False),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id')),
        sa.Column('metric_key',sa.String(48),nullable=False),
        sa.Column('target',sa.Numeric(20,4),nullable=False),
        sa.Column('set_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('set_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False,server_default='1'),
        sa.CheckConstraint('extract(day from month) = 1',name='ck_kpi_target_month'),
        sa.CheckConstraint('target >= 0',name='ck_kpi_target_value'))
    op.create_index('uq_kpi_target_person','kpi_targets',['month','user_id','metric_key'],unique=True,postgresql_where=sa.text('user_id is not null'))
    op.create_index('uq_kpi_target_common','kpi_targets',['month','metric_key'],unique=True,postgresql_where=sa.text('user_id is null'))


def downgrade():
    op.drop_table('kpi_targets')
