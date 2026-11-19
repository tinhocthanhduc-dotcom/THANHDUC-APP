"""Ghi nhớ máy đã bị từ chối bảo hành (TUẤN chọn phương án 1, 19/11/2026).

Thêm vào thiết bị: ngày từ chối, lý do, phiếu đã từ chối. Có dấu này thì lần sau phiếu Bảo hành của máy
không tự Miễn phí (tính như hết hạn) và mọi màn nhận máy hiện cảnh báo đỏ; điều phối/giám đốc vẫn chọn Miễn phí được.
Không đổi dữ liệu cũ (mọi máy đang có để trống).

Revision ID: 0108
Revises: 0107
"""
from alembic import op
import sqlalchemy as sa

revision = '0108'
down_revision = '0107'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('equipment', sa.Column('warranty_void_on', sa.Date(), nullable=True))
    op.add_column('equipment', sa.Column('warranty_void_reason', sa.Text(), nullable=False, server_default=''))
    op.add_column('equipment', sa.Column('warranty_void_ticket_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key('fk_equipment_warranty_void_ticket', 'equipment', 'service_tickets', ['warranty_void_ticket_id'], ['id'])


def downgrade():
    op.drop_constraint('fk_equipment_warranty_void_ticket', 'equipment', type_='foreignkey')
    for col in ('warranty_void_ticket_id', 'warranty_void_reason', 'warranty_void_on'):
        op.drop_column('equipment', col)
