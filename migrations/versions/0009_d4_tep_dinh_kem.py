"""D4: tệp đính kèm

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('attachments',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('entity', sa.String(length=32), nullable=False),
    sa.Column('entity_id', sa.String(length=64), nullable=False),
    sa.Column('tag', sa.String(length=16), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=64), nullable=False),
    sa.Column('size', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('path', sa.String(length=255), nullable=False),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('hidden', sa.Boolean(), nullable=False),
    sa.Column('hidden_reason', sa.Text(), nullable=False),
    sa.Column('hidden_by', sa.Integer(), nullable=True),
    sa.Column('hidden_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("hidden = (hidden_reason <> '')", name='ck_attachments_hidden'),
    sa.CheckConstraint('size > 0', name='ck_attachments_size'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['hidden_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_attachments_entity', 'attachments', ['entity', 'entity_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_attachments_entity', table_name='attachments')
    op.drop_table('attachments')
