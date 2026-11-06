"""Định mức vật tư sản xuất (TUẤN duyệt 06/11/2026): vật tư cho 1 thành phẩm tốt, chỉ để điền sẵn phiếu SX.

Revision ID: 0081
Revises: 0080
"""
from alembic import op
import sqlalchemy as sa

revision = '0081'
down_revision = '0080'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'production_boms',
        sa.Column('product_code', sa.String(16), sa.ForeignKey('products.code'), primary_key=True),
        sa.Column('note', sa.Text(), nullable=False, server_default=''),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'production_bom_lines',
        sa.Column('product_code', sa.String(16), sa.ForeignKey('production_boms.product_code', ondelete='CASCADE'), primary_key=True),
        sa.Column('material_code', sa.String(16), sa.ForeignKey('products.code'), primary_key=True),
        sa.Column('line_no', sa.Integer(), nullable=False),
        sa.Column('qty', sa.Numeric(14, 3), nullable=False),
        sa.CheckConstraint('qty > 0', name='ck_production_bom_lines_qty'),
        sa.CheckConstraint('material_code <> product_code', name='ck_production_bom_lines_self'),
    )


def downgrade():
    op.drop_table('production_bom_lines')
    op.drop_table('production_boms')
