"""Tin gửi khách loại 4 "Báo sửa xong" (TUẤN duyệt mẫu 16/11/2026): chỉ nới danh sách loại tin, không đổi dữ liệu.

Revision ID: 0104
Revises: 0103
"""
from alembic import op

revision = '0104'
down_revision = '0103'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('ck_customer_messages_kind', 'customer_messages', type_='check')
    op.create_check_constraint('ck_customer_messages_kind', 'customer_messages', "kind in ('NHAC_NO','BAO_GIA','HOI_THAM','SUA_XONG')")


def downgrade():
    op.execute("DELETE FROM customer_messages WHERE kind = 'SUA_XONG'")
    op.drop_constraint('ck_customer_messages_kind', 'customer_messages', type_='check')
    op.create_check_constraint('ck_customer_messages_kind', 'customer_messages', "kind in ('NHAC_NO','BAO_GIA','HOI_THAM')")
