"""Thợ tự nhận phiếu dịch vụ chưa giao trong 30 phút (TUẤN 04/11/2026).

Revision ID: 0075
Revises: 0074
"""
from alembic import op
import sqlalchemy as sa

revision = '0075'
down_revision = '0074'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('service_tickets', sa.Column('self_claim_until', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('service_tickets', 'self_claim_until')
