"""Trợ lý Zalo đợt 3: đọc ảnh nhân viên gửi (tem máy, hộp mực, hóa đơn NCC) bằng Codex/Claude trên Z2 (TUẤN duyệt 06/11/2026).

Revision ID: 0083
Revises: 0082
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0083'
down_revision = '0082'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('zalo_tro_ly', sa.Column('read_images', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('zalo_tro_ly', sa.Column('invoice_po', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('zalo_agent_logs', sa.Column('image_path', sa.String(200), nullable=False, server_default=''))
    op.add_column('zalo_agent_logs', sa.Column('image_kind', sa.String(10), nullable=False, server_default=''))
    op.add_column('zalo_agent_logs', sa.Column('image_read', postgresql.JSONB(), nullable=False, server_default='{}'))
    op.add_column('zalo_agent_logs', sa.Column('purchase_order_id', sa.BigInteger(), sa.ForeignKey('purchase_orders.id'), nullable=True))
    op.create_index('ix_zalo_agent_logs_purchase_order', 'zalo_agent_logs', ['purchase_order_id'],
                    postgresql_where=sa.text('purchase_order_id is not null'))
    op.add_column('zalo_ai_jobs', sa.Column('image_path', sa.String(200), nullable=False, server_default=''))


def downgrade():
    op.drop_column('zalo_ai_jobs', 'image_path')
    op.drop_index('ix_zalo_agent_logs_purchase_order', table_name='zalo_agent_logs')
    op.drop_column('zalo_agent_logs', 'purchase_order_id')
    op.drop_column('zalo_agent_logs', 'image_read')
    op.drop_column('zalo_agent_logs', 'image_kind')
    op.drop_column('zalo_agent_logs', 'image_path')
    op.drop_column('zalo_tro_ly', 'invoice_po')
    op.drop_column('zalo_tro_ly', 'read_images')
