"""CXL2a: phiếu xử lý nội bộ và dấu vết trừ hồ sơ.
Revision ID: 0039
Revises: 0038
"""
from alembic import op
import sqlalchemy as sa
revision='0039'
down_revision='0038'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('warehouses',sa.Column('supplier_hold',sa.Boolean(),server_default=sa.text('false'),nullable=False))
    op.create_check_constraint('ck_warehouses_supplier_hold','warehouses',"not supplier_hold or kind='LOI'")
    op.add_column('warehouses',sa.Column('recovered_only',sa.Boolean(),server_default=sa.text('false'),nullable=False))
    op.create_check_constraint('ck_warehouses_recovered_only','warehouses',"not recovered_only or kind='LOI'")
    op.add_column('pending_items',sa.Column('sent_at',sa.DateTime(timezone=True)))
    op.create_table('pending_dispositions',
        sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('number',sa.Text(),unique=True,nullable=False),
        sa.Column('item_id',sa.BigInteger(),sa.ForeignKey('pending_items.id'),nullable=False),
        sa.Column('kind',sa.Text(),nullable=False),sa.Column('qty',sa.Numeric(14,3),nullable=False),
        sa.Column('to_warehouse_code',sa.String(16),sa.ForeignKey('warehouses.code')),
        sa.Column('supplier_code',sa.String(16),sa.ForeignKey('suppliers.code')),
        sa.Column('stock_doc_id',sa.BigInteger(),sa.ForeignKey('stock_docs.id'),unique=True),
        sa.Column('cost_value',sa.BigInteger(),nullable=False),sa.Column('status',sa.Text(),nullable=False),sa.Column('reason',sa.Text(),nullable=False),
        sa.Column('requested_by',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),sa.Column('requested_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.Column('decided_by',sa.Integer(),sa.ForeignKey('users.id')),sa.Column('decided_at',sa.DateTime(timezone=True)),
        sa.Column('decision_note',sa.Text(),nullable=False),sa.Column('void_reason',sa.Text(),nullable=False),sa.Column('version',sa.Integer(),nullable=False),
        sa.CheckConstraint("kind in ('SUA_XONG','GUI_NCC','NCC_TRA_VE','NCC_KHONG_NHAN','HUY')",name='ck_pending_dispositions_kind'),
        sa.CheckConstraint("status in ('CHO_DUYET','DA_GHI','TU_CHOI','DA_HUY')",name='ck_pending_dispositions_status'),
        sa.CheckConstraint('qty>0',name='ck_pending_dispositions_qty'),sa.CheckConstraint('cost_value>=0',name='ck_pending_dispositions_cost'))
    op.create_index('ix_pending_dispositions_item_status','pending_dispositions',['item_id','status'])
    op.create_table('pending_item_moves',sa.Column('id',sa.BigInteger(),primary_key=True),
        sa.Column('item_id',sa.BigInteger(),sa.ForeignKey('pending_items.id'),nullable=False),
        sa.Column('disposition_id',sa.BigInteger(),sa.ForeignKey('pending_dispositions.id'),nullable=False),
        sa.Column('stock_doc_id',sa.BigInteger(),sa.ForeignKey('stock_docs.id'),nullable=False),sa.Column('qty',sa.Numeric(14,3),nullable=False),
        sa.CheckConstraint('qty>0',name='ck_pending_item_moves_qty'),sa.UniqueConstraint('disposition_id','item_id',name='uq_pending_item_moves_disposition_item'))


def downgrade():
    op.drop_table('pending_item_moves');op.drop_table('pending_dispositions')
    op.drop_constraint('ck_warehouses_recovered_only','warehouses',type_='check');op.drop_column('warehouses','recovered_only')
    op.drop_column('pending_items','sent_at');op.drop_constraint('ck_warehouses_supplier_hold','warehouses',type_='check');op.drop_column('warehouses','supplier_hold')
