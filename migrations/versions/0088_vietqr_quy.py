"""QR chuyển khoản VietQR (TUẤN duyệt 08/11/2026): quỹ ngân hàng có mã BIN NAPAS và chủ tài khoản.

Revision ID: 0088
Revises: 0087
"""
import sqlalchemy as sa
from alembic import op

revision = '0088'
down_revision = '0087'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('funds', sa.Column('bank_bin', sa.String(length=8), nullable=False, server_default=''))
    op.add_column('funds', sa.Column('account_name', sa.String(length=100), nullable=False, server_default=''))
    op.create_check_constraint('ck_funds_bank_bin', 'funds', "bank_bin = '' or bank_bin ~ '^[0-9]{6}$'")


def downgrade():
    op.drop_constraint('ck_funds_bank_bin', 'funds', type_='check')
    op.drop_column('funds', 'account_name')
    op.drop_column('funds', 'bank_bin')
