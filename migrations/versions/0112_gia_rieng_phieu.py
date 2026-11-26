"""Giá riêng phiếu khi Duyệt giá (TUẤN duyệt mẫu G296xR5EspFChNAWzbvvTy 26/11/2026).

Thợ/kho chỉ chọn gói, cấp, linh kiện; giám đốc hoặc kinh doanh gõ giá chốt cho khách (chưa VAT) cho từng gói,
khác giá bảng thì bắt buộc lý do. Trống = giá bảng như cũ. Thợ đổi gói/cấp thì dòng kiểm tra lập lại nên giá riêng tự bỏ.

Revision ID: 0112
Revises: 0111
"""
from alembic import op
import sqlalchemy as sa

revision = '0112'
down_revision = '0111'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('repair_inspection_items', sa.Column('custom_price', sa.BigInteger(), nullable=True))
    op.add_column('repair_inspection_items', sa.Column('custom_reason', sa.Text(), nullable=False, server_default=''))
    op.create_check_constraint('ck_repair_inspection_items_custom', 'repair_inspection_items',
                               "custom_price is null or (custom_price > 0 and btrim(custom_reason) <> '')")


def downgrade():
    op.drop_constraint('ck_repair_inspection_items_custom', 'repair_inspection_items', type_='check')
    op.drop_column('repair_inspection_items', 'custom_reason')
    op.drop_column('repair_inspection_items', 'custom_price')
