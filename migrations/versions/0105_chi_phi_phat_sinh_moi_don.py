"""Chi phí phát sinh trên mọi đơn (TUẤN duyệt 16/11/2026, mẫu 3jpHcVYJarVVHsxfm461sD).

Mở rộng service_expenses: ghi trên phiếu dịch vụ HOẶC đơn bán (kèm lần giao), ai trả (người ghi ứng / công ty trả
đối tác sau), hóa đơn VAT 0/8/10 → giá vốn tính chưa VAT (cost). Thêm 2 loại: trả đối tác sửa, phí gửi hàng.
Hạn mức duyệt thêm khóa CHI_PHI (kế toán duyệt tới mức này, mặc định 500.000).

Revision ID: 0105
Revises: 0104
"""
from alembic import op
import sqlalchemy as sa

revision = '0105'
down_revision = '0104'
branch_labels = None
depends_on = None

OLD_KINDS = "kind in ('LINH_KIEN','GUI_XE','CHO_MAY','KHAC')"
NEW_KINDS = "kind in ('LINH_KIEN','DOI_TAC','GUI_HANG','GUI_XE','CHO_MAY','KHAC')"
OLD_KEYS = "key in ('BO_SUNG','NGHI','TAM_UNG','HUY_HANG')"
NEW_KEYS = "key in ('BO_SUNG','NGHI','TAM_UNG','HUY_HANG','CHI_PHI')"


def upgrade():
    op.alter_column('service_expenses', 'ticket_id', existing_type=sa.BigInteger(), nullable=True)
    op.add_column('service_expenses', sa.Column('order_id', sa.BigInteger(), sa.ForeignKey('sales_orders.id'), nullable=True))
    op.add_column('service_expenses', sa.Column('delivery_id', sa.BigInteger(), sa.ForeignKey('sales_deliveries.id'), nullable=True))
    op.add_column('service_expenses', sa.Column('payer', sa.String(length=8), nullable=False, server_default='NV_UNG'))
    op.add_column('service_expenses', sa.Column('vat_rate', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('service_expenses', sa.Column('cost', sa.BigInteger(), nullable=True))
    op.add_column('service_expenses', sa.Column('note', sa.Text(), nullable=False, server_default=''))
    op.execute('UPDATE service_expenses SET cost = amount')
    op.alter_column('service_expenses', 'cost', existing_type=sa.BigInteger(), nullable=False)
    op.create_index('ix_service_expenses_order_id', 'service_expenses', ['order_id'])
    op.drop_constraint('ck_service_expenses_kind', 'service_expenses', type_='check')
    op.create_check_constraint('ck_service_expenses_kind', 'service_expenses', NEW_KINDS)
    op.create_check_constraint('ck_service_expenses_target', 'service_expenses',
                               '(ticket_id is null) <> (order_id is null) and (delivery_id is null or order_id is not null)')
    op.create_check_constraint('ck_service_expenses_payer', 'service_expenses', "payer in ('NV_UNG','DOI_TAC')")
    op.create_check_constraint('ck_service_expenses_partner', 'service_expenses', "payer <> 'DOI_TAC' or btrim(place) <> ''")
    op.create_check_constraint('ck_service_expenses_vat', 'service_expenses', 'vat_rate in (0,8,10)')
    op.create_check_constraint('ck_service_expenses_cost', 'service_expenses', 'cost > 0 and cost <= amount')
    op.drop_constraint('ck_approval_limits_key', 'approval_limits', type_='check')
    op.create_check_constraint('ck_approval_limits_key', 'approval_limits', NEW_KEYS)


def downgrade():
    op.execute("DELETE FROM approval_limits WHERE key='CHI_PHI'")
    op.drop_constraint('ck_approval_limits_key', 'approval_limits', type_='check')
    op.create_check_constraint('ck_approval_limits_key', 'approval_limits', OLD_KEYS)
    # Khoản ghi trên đơn bán không có chỗ trong bảng cũ: xóa trước khi siết lại.
    op.execute('DELETE FROM service_expenses WHERE order_id IS NOT NULL')
    op.execute("UPDATE service_expenses SET kind='KHAC' WHERE kind IN ('DOI_TAC','GUI_HANG')")
    for name in ('cost', 'vat', 'partner', 'payer', 'target', 'kind'):
        op.drop_constraint(f'ck_service_expenses_{name}', 'service_expenses', type_='check')
    op.create_check_constraint('ck_service_expenses_kind', 'service_expenses', OLD_KINDS)
    op.drop_index('ix_service_expenses_order_id', table_name='service_expenses')
    for col in ('note', 'cost', 'vat_rate', 'payer', 'delivery_id', 'order_id'):
        op.drop_column('service_expenses', col)
    op.alter_column('service_expenses', 'ticket_id', existing_type=sa.BigInteger(), nullable=False)
