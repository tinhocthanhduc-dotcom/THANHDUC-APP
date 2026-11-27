"""Bảo hành riêng phiếu khi Duyệt giá (TUẤN duyệt mẫu STA8zzFHSzoMeKoxbfeohE 27/11/2026).

Giám đốc hoặc kinh doanh chọn số tháng bảo hành cho từng báo giá sửa chữa (0, 1, 2, 3, 6, 12); trống = theo gói
trong bảng giá như cũ. Thợ đổi gói/cấp thì bảo hành riêng tự bỏ.
Cùng đợt: Giá chốt gõ được giá đã gồm VAT; custom_gross giữ đúng tổng khách trả khi tách VAT lệch 1 đồng.

Revision ID: 0113
Revises: 0112
"""
from alembic import op
import sqlalchemy as sa

revision = '0113'
down_revision = '0112'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('repair_inspections', sa.Column('custom_warranty', sa.Integer(), nullable=True))
    op.create_check_constraint('ck_repair_inspections_custom_warranty', 'repair_inspections',
                               'custom_warranty is null or custom_warranty in (0,1,2,3,6,12)')
    op.add_column('repair_inspection_items', sa.Column('custom_gross', sa.BigInteger(), nullable=True))
    op.create_check_constraint('ck_repair_inspection_items_custom_gross', 'repair_inspection_items',
                               'custom_gross is null or (custom_price is not null and abs(custom_gross - custom_price * 1.08) <= 1)')


def downgrade():
    op.drop_constraint('ck_repair_inspection_items_custom_gross', 'repair_inspection_items', type_='check')
    op.drop_column('repair_inspection_items', 'custom_gross')
    op.drop_constraint('ck_repair_inspections_custom_warranty', 'repair_inspections', type_='check')
    op.drop_column('repair_inspections', 'custom_warranty')
