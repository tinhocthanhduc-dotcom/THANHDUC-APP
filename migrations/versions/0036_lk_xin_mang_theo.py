"""lk_xin_mang_theo: yêu cầu linh kiện không gắn phiếu (thợ xin hàng mang theo)

Revision ID: 0036
Revises: 0035
"""
from alembic import op


revision = '0036'
down_revision = '0035'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('part_requests', 'ticket_id', nullable=True)


def downgrade() -> None:
    op.execute("delete from part_requests where ticket_id is null")
    op.alter_column('part_requests', 'ticket_id', nullable=False)
