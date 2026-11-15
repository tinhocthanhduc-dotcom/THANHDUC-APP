"""Bảng giá màn thợ 3 tab: Giá sửa · Giá dịch vụ · Giá mực (TUẤN duyệt mẫu D1–D3, 15/11/2026).

price_services (kind PHI) thêm:
- is_fee: Phụ phí (hiện ở ô "Chọn phụ phí") hay Dịch vụ (hiện ở tab Giá dịch vụ của thợ).
  Dòng cũ có tên bắt đầu bằng "Phụ phí" hoặc "Phí" được đánh dấu phụ phí; còn lại là dịch vụ.
- by_model: giá theo nhóm máy / model (nạp mực, thay trống…), lưu ở repair_prices cấp 1, model ghi đè nhóm.
  Không bật thì một giá (cột price) như cũ.

Revision ID: 0099
Revises: 0098
"""
import sqlalchemy as sa
from alembic import op

revision = '0099'
down_revision = '0098'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('price_services', sa.Column('is_fee', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('price_services', sa.Column('by_model', sa.Boolean(), nullable=False, server_default='false'))
    op.execute("update price_services set is_fee = true where kind = 'PHI' and "
               "(lower(name) like 'phụ phí%' or lower(name) like 'phu phi%' or lower(name) like 'phí %' or lower(name) like 'phi %')")


def downgrade():
    op.drop_column('price_services', 'by_model')
    op.drop_column('price_services', 'is_fee')
