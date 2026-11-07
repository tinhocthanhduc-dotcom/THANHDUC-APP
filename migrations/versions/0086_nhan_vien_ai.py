"""Đổi tên tài khoản hệ thống "Nhân viên ảo" thành "Nhân viên AI" (TUẤN 07/11/2026).

Revision ID: 0086
Revises: 0085
"""
from alembic import op

revision = '0086'
down_revision = '0085'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE users SET full_name = 'Nhân viên AI' WHERE username = 'nhan-vien-ao'")


def downgrade():
    op.execute("UPDATE users SET full_name = 'Nhân viên ảo' WHERE username = 'nhan-vien-ao'")
