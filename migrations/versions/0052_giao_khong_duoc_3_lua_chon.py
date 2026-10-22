"""Thợ giao không được: thêm lý do DE_NGHI_HUY (thợ đề nghị hủy, bán hàng duyệt).

Revision ID: 0052
Revises: 0051
"""
from alembic import op

revision = '0052'
down_revision = '0051'
branch_labels = None
depends_on = None

OLD = "reason in ('KHACH_VANG','KHACH_TU_CHOI','SAI_DIA_CHI','HEN_LAI','KHAC')"
NEW = "reason in ('KHACH_VANG','KHACH_TU_CHOI','SAI_DIA_CHI','HEN_LAI','KHAC','DE_NGHI_HUY')"


def upgrade():
    op.drop_constraint('ck_sales_delivery_attempts_reason', 'sales_delivery_attempts', type_='check')
    op.create_check_constraint('ck_sales_delivery_attempts_reason', 'sales_delivery_attempts', NEW)


def downgrade():
    op.execute("update sales_delivery_attempts set reason='KHAC' where reason='DE_NGHI_HUY'")
    op.drop_constraint('ck_sales_delivery_attempts_reason', 'sales_delivery_attempts', type_='check')
    op.create_check_constraint('ck_sales_delivery_attempts_reason', 'sales_delivery_attempts', OLD)
