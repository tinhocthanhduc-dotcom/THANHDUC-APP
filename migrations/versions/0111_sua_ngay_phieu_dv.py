"""Sửa ngày phiếu dịch vụ (TUẤN duyệt mẫu R2BcBQtqyfJGfcHGZAxgXd 26/11/2026).

Lịch sử bước (service_steps) chỉ ghi thêm, không sửa được; nên ngày hoàn thành giám đốc sửa tay lưu ở
service_tickets.done_at. Có giá trị thì thay giờ của bước Hoàn thành khi tính bảo hành, hoa hồng, KPI.
Phiếu hoàn thành lại thì app xóa done_at. Không đổi dữ liệu cũ.

Revision ID: 0111
Revises: 0110
"""
from alembic import op
import sqlalchemy as sa

revision = '0111'
down_revision = '0110'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('service_tickets', sa.Column('done_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('service_tickets', 'done_at')
