"""Nguồn khách trên báo giá và đơn bán cho Growth OS (TUẤN duyệt 16/11/2026, Phần B).

Thêm (không bắt buộc): lead_source, campaign_code, lead_ref cho quotes và sales_orders; web_order_ref cho sales_orders.
Thêm updated_at cho cả hai để Growth OS đọc lại bản ghi bị sửa (Phần C). Không đổi tiền, kho, nợ.

Revision ID: 0106
Revises: 0105
"""
from alembic import op
import sqlalchemy as sa

revision = '0106'
down_revision = '0105'
branch_labels = None
depends_on = None

SOURCES = "lead_source is null or lead_source in ('WEB','GOOGLE_MAPS','FACEBOOK','ZALO','SHOPEE','TIKTOK_SHOP','KHACH_CU','GIOI_THIEU','KHAC')"


def upgrade():
    for table in ('quotes', 'sales_orders'):
        op.add_column(table, sa.Column('lead_source', sa.String(length=16), nullable=True))
        op.add_column(table, sa.Column('campaign_code', sa.String(length=40), nullable=True))
        op.add_column(table, sa.Column('lead_ref', sa.String(length=40), nullable=True))
        op.add_column(table, sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
        op.create_check_constraint(f'ck_{table}_lead_source', table, SOURCES)
        op.create_index(f'ix_{table}_campaign_code', table, ['campaign_code'], postgresql_where=sa.text('campaign_code is not null'))
        op.create_index(f'ix_{table}_updated_at', table, ['updated_at', 'id'])
    op.add_column('sales_orders', sa.Column('web_order_ref', sa.String(length=60), nullable=True))
    op.create_index('ix_sales_orders_web_order_ref', 'sales_orders', ['web_order_ref'], postgresql_where=sa.text('web_order_ref is not null'))


def downgrade():
    op.drop_index('ix_sales_orders_web_order_ref', table_name='sales_orders')
    op.drop_column('sales_orders', 'web_order_ref')
    for table in ('quotes', 'sales_orders'):
        op.drop_index(f'ix_{table}_updated_at', table_name=table)
        op.drop_index(f'ix_{table}_campaign_code', table_name=table)
        op.drop_constraint(f'ck_{table}_lead_source', table, type_='check')
        for col in ('updated_at', 'lead_ref', 'campaign_code', 'lead_source'):
            op.drop_column(table, col)
