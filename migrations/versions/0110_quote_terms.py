"""Điều kiện bảo hành trên báo giá sửa gửi khách (TUẤN duyệt mẫu Hc9Cnfxxq5MJh2fFps8xfN 20/11/2026).

Bảng cấu hình nhỏ: key DIEU_KIEN_BH, mỗi dòng một điều kiện; giám đốc/người sửa bảng giá chỉnh trong Bảng giá.
Chưa có dòng thì app dùng 5 dòng chuẩn. Không đổi dữ liệu cũ.

Revision ID: 0110
Revises: 0109
"""
from alembic import op
import sqlalchemy as sa

revision = '0110'
down_revision = '0109'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'quote_terms',
        sa.Column('key', sa.String(32), primary_key=True),
        sa.Column('text', sa.Text(), nullable=False, server_default=''),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
    )


def downgrade():
    op.drop_table('quote_terms')
