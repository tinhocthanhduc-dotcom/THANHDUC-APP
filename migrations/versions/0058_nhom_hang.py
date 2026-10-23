"""Danh mục nhóm hàng riêng, thêm/sửa/xóa được; lấy sẵn các nhóm đang có trên mã hàng.

Revision ID: 0058
Revises: 0057
"""
import sqlalchemy as sa
from alembic import op

revision = '0058'
down_revision = '0057'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'product_groups',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("update products set group_name = btrim(group_name) where group_name <> btrim(group_name)")
    op.execute("insert into product_groups (name) select distinct group_name from products where group_name <> '' order by 1")


def downgrade():
    op.drop_table('product_groups')
