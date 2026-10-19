"""R3: bản tóm tắt ngày.

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('daily_digests',
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('data', postgresql.JSONB(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('day'))


def downgrade() -> None:
    op.drop_table('daily_digests')
