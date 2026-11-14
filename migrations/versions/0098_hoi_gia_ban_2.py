"""Hỏi giá sửa bản 2 (TUẤN duyệt mẫu HG1–HG3, 14/11/2026).

quick_quotes thêm: price_mode (tin ghi KHOANG = khoảng giá, CAP = từng cấp), token (link khách xem bản báo giá, hết
hạn theo valid_days), updated_at/updated_by/edits (sửa phiếu giữ số HG). Giá từng ô cấp, ô giám đốc sửa và hạng mục
ngoài bảng giá nằm trong lines (bản chụp lúc lưu), không thêm bảng.

Revision ID: 0098
Revises: 0097
"""
import sqlalchemy as sa
from alembic import op

revision = '0098'
down_revision = '0097'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('quick_quotes', sa.Column('price_mode', sa.String(8), nullable=False, server_default='KHOANG'))
    op.add_column('quick_quotes', sa.Column('token', sa.String(40), nullable=True))
    op.add_column('quick_quotes', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('quick_quotes', sa.Column('updated_by', sa.Integer, sa.ForeignKey('users.id'), nullable=True))
    op.add_column('quick_quotes', sa.Column('edits', sa.Integer, nullable=False, server_default='0'))
    op.create_check_constraint('ck_quick_quotes_price_mode', 'quick_quotes', "price_mode in ('KHOANG','CAP')")
    op.create_check_constraint('ck_quick_quotes_edits', 'quick_quotes', 'edits >= 0')
    op.create_unique_constraint('uq_quick_quotes_token', 'quick_quotes', ['token'])


def downgrade():
    op.drop_constraint('uq_quick_quotes_token', 'quick_quotes', type_='unique')
    op.drop_constraint('ck_quick_quotes_edits', 'quick_quotes', type_='check')
    op.drop_constraint('ck_quick_quotes_price_mode', 'quick_quotes', type_='check')
    for col in ('edits', 'updated_by', 'updated_at', 'token', 'price_mode'):
        op.drop_column('quick_quotes', col)
