"""Theo dõi serial tách khỏi số tháng bảo hành (TUẤN 03/11/2026): hộp mực Renew có bảo hành 1 tháng nhưng không có serial.

Revision ID: 0074
Revises: 0073
"""
from alembic import op
import sqlalchemy as sa

revision = '0074'
down_revision = '0073'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('products', sa.Column('track_serial', sa.Boolean(), server_default='false', nullable=False))
    # Giữ nguyên nếp cũ cho máy: mã có bảo hành > 0 vẫn bắt serial, trừ các nhóm hộp mực / mực.
    op.execute("update products set track_serial = true where warranty_months > 0 "
               "and group_name not ilike '%mực%' and group_name not ilike '%muc%'")


def downgrade():
    op.drop_column('products', 'track_serial')
