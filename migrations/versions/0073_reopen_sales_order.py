"""TUẤN 03/11/2026: mở lại đơn bán đã chốt để sửa.

Lịch sử giữ hàng (stock_reservations) không được xóa, nên không còn ràng buộc khóa ngoại
tới từng dòng đơn: đơn mở lại về Nháp phải thay được dòng. Dòng giữ hàng tự lưu mã hàng, kho, số lượng;
vẫn ràng buộc tới đơn bán (order_id).

Revision ID: 0073
Revises: 0072
"""
from alembic import op

revision = '0073'
down_revision = '0072'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('stock_reservations_order_id_order_line_id_fkey', 'stock_reservations', type_='foreignkey')


def downgrade():
    op.create_foreign_key('stock_reservations_order_id_order_line_id_fkey', 'stock_reservations', 'sales_order_lines',
                          ['order_id', 'order_line_id'], ['order_id', 'line_no'])
