"""Hạn mức duyệt (TUẤN duyệt 11/11/2026): kế toán duyệt thay giám đốc trong hạn mức giám đốc đặt.

Revision ID: 0091
Revises: 0090
"""
import sqlalchemy as sa
from alembic import op

revision = '0091'
down_revision = '0090'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('approval_limits',
        sa.Column('key', sa.String(length=16), primary_key=True),
        sa.Column('value', sa.BigInteger(), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("key in ('BO_SUNG','NGHI','TAM_UNG','HUY_HANG')", name='ck_approval_limits_key'),
        sa.CheckConstraint('value >= 0', name='ck_approval_limits_value'))


def downgrade():
    op.drop_table('approval_limits')
