"""Báo giá gộp nhiều máy một khách (TUẤN duyệt mẫu 15/11/2026).

repair_quote_groups: một link /bgg/<mã>, một tin Zalo, một bản A4 cho nhiều phiếu dịch vụ của cùng khách.
Mỗi máy vẫn có lần báo giá riêng trong repair_quote_versions (cột group_id mới trỏ về nhóm); tiền vẫn ở từng phiếu.

Revision ID: 0100
Revises: 0099
"""
import sqlalchemy as sa
from alembic import op

revision = '0100'
down_revision = '0099'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('repair_quote_groups',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('number', sa.String(length=20), nullable=False, unique=True),
        sa.Column('customer_code', sa.String(length=16), sa.ForeignKey('customers.code'), nullable=False, index=True),
        sa.Column('token', sa.String(length=40), nullable=False, unique=True),
        sa.Column('status', sa.String(length=8), nullable=False, server_default='CHO'),
        sa.Column('text', sa.Text(), nullable=False, server_default=''),
        sa.Column('valid_until', sa.Date(), nullable=False),
        sa.Column('sent_via', sa.String(length=8), nullable=False, server_default=''),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sent_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('opened_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_opened_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status in ('CHO','XONG','THU_HOI')", name='ck_repair_quote_groups_status'))
    op.add_column('repair_quote_versions', sa.Column('group_id', sa.BigInteger(),
                  sa.ForeignKey('repair_quote_groups.id', ondelete='SET NULL'), nullable=True))
    op.create_index('ix_repair_quote_versions_group_id', 'repair_quote_versions', ['group_id'])


def downgrade():
    op.drop_index('ix_repair_quote_versions_group_id', table_name='repair_quote_versions')
    op.drop_column('repair_quote_versions', 'group_id')
    op.drop_table('repair_quote_groups')
