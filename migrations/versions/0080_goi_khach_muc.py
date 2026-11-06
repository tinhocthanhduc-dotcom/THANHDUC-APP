"""Khách lâu chưa gọi mực (TUẤN duyệt 06/11/2026): ghi kết quả cuộc gọi hỏi thăm khách quá nhịp lấy mực.

Revision ID: 0080
Revises: 0079
"""
from alembic import op
import sqlalchemy as sa

revision = '0080'
down_revision = '0079'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'customer_ink_calls',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('customer_code', sa.String(16), sa.ForeignKey('customers.code'), nullable=False),
        sa.Column('result', sa.String(16), nullable=False),
        sa.Column('note', sa.Text(), nullable=False, server_default=''),
        sa.Column('called_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('called_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("result in ('DAT_MUC','HEN_LAI','KHONG_NGHE','CON_MUC','CHUYEN_NOI_KHAC')", name='ck_customer_ink_calls_result'),
    )
    op.create_index('ix_customer_ink_calls_customer', 'customer_ink_calls', ['customer_code', 'called_at'])


def downgrade():
    op.drop_index('ix_customer_ink_calls_customer', table_name='customer_ink_calls')
    op.drop_table('customer_ink_calls')
