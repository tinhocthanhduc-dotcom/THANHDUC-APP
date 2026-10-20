"""CXL2b: trả NCC trừ nợ và liên kết thanh lý với lần giao.
Revision ID: 0040
Revises: 0039
"""
from alembic import op
import sqlalchemy as sa
revision='0040'
down_revision='0039'
branch_labels=None
depends_on=None
OLD="kind in ('SUA_XONG','GUI_NCC','NCC_TRA_VE','NCC_KHONG_NHAN','HUY')"
NEW="kind in ('SUA_XONG','GUI_NCC','NCC_TRA_VE','NCC_KHONG_NHAN','HUY','TRA_NCC','THANH_LY')"
def upgrade():
    op.drop_constraint('ck_pending_dispositions_kind','pending_dispositions',type_='check')
    op.create_check_constraint('ck_pending_dispositions_kind','pending_dispositions',NEW)
    op.add_column('pending_dispositions',sa.Column('voided_at',sa.DateTime(timezone=True)))
    for name in ('amount','vat_amount','total'):
        op.add_column('pending_dispositions',sa.Column(name,sa.BigInteger(),server_default='0',nullable=False))
    op.add_column('pending_dispositions',sa.Column('vat_rate',sa.Integer(),server_default='0',nullable=False))
    op.add_column('pending_dispositions',sa.Column('delivery_id',sa.BigInteger(),sa.ForeignKey('sales_deliveries.id',name='fk_pending_dispositions_delivery')))
    op.add_column('pending_dispositions',sa.Column('order_line_no',sa.Integer()))
    op.create_index('ix_pending_dispositions_delivery_id','pending_dispositions',['delivery_id'])
    op.create_check_constraint('ck_pending_dispositions_money','pending_dispositions','amount>=0 and vat_amount>=0 and total=amount+vat_amount and vat_rate in (0,5,8,10)')
    op.create_check_constraint('ck_pending_dispositions_delivery','pending_dispositions',"(kind='THANH_LY') = (delivery_id is not null and order_line_no is not null)")
def downgrade():
    # Never silently delete inventory/debt history created by the newer version.
    if op.get_bind().scalar(sa.text("select count(*) from pending_dispositions where kind in ('TRA_NCC','THANH_LY')")):
        raise RuntimeError('Còn phiếu CXL2b; không thể hạ phiên bản và bỏ lịch sử kho/công nợ.')
    op.drop_constraint('ck_pending_dispositions_delivery','pending_dispositions',type_='check')
    op.drop_constraint('ck_pending_dispositions_money','pending_dispositions',type_='check')
    op.drop_index('ix_pending_dispositions_delivery_id',table_name='pending_dispositions')
    op.drop_constraint('fk_pending_dispositions_delivery','pending_dispositions',type_='foreignkey')
    for name in ('order_line_no','delivery_id','total','vat_amount','vat_rate','amount','voided_at'):op.drop_column('pending_dispositions',name)
    op.drop_constraint('ck_pending_dispositions_kind','pending_dispositions',type_='check')
    op.create_check_constraint('ck_pending_dispositions_kind','pending_dispositions',OLD)
