"""Lãi ròng tháng (TUẤN duyệt 08/11/2026): cờ tính lãi ròng trên danh mục chi, chi phí ngoài sổ, lương giám đốc mục tiêu.

Revision ID: 0089
Revises: 0088
"""
import sqlalchemy as sa
from alembic import op

revision = '0089'
down_revision = '0088'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('cash_categories', sa.Column('pl_kind', sa.String(length=16), nullable=False, server_default='CHI_PHI'))
    op.add_column('cash_categories', sa.Column('pl_group', sa.String(length=100), nullable=False, server_default=''))
    op.create_check_constraint('ck_cash_categories_pl_kind', 'cash_categories', "pl_kind in ('CHI_PHI','KHONG_TINH','CHU_SO_HUU')")
    # Danh mục thu không tính; danh mục chi đang ghép Mua hàng / Hoàn tiền bán / Lương / Chi phí thợ đã nằm trong giá vốn,
    # doanh thu hoặc bảng lương nên đặt sẵn Không tính để khỏi trùng. Kế toán sửa được sau.
    op.execute("update cash_categories set pl_kind='KHONG_TINH' where direction='THU'")
    op.execute("update cash_categories set pl_kind='KHONG_TINH' where code in (select category_code from finance_mappings "
               "where category_code is not null and key in ('DANH_MUC_MUA_HANG','DANH_MUC_HOAN_TIEN_BAN','DANH_MUC_LUONG','DANH_MUC_CHI_PHI_THO'))")
    op.create_table('pl_adjustments',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('month', sa.Date(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('pl_group', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('amount', sa.BigInteger(), nullable=False),
        sa.Column('note', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.CheckConstraint('extract(day from month)=1', name='ck_pl_adjustments_month'),
        sa.CheckConstraint('amount > 0', name='ck_pl_adjustments_amount'),
        sa.CheckConstraint("btrim(name) <> ''", name='ck_pl_adjustments_name'))
    op.create_index('ix_pl_adjustments_month', 'pl_adjustments', ['month'])
    op.create_table('pl_settings',
        sa.Column('key', sa.String(length=32), primary_key=True),
        sa.Column('amount', sa.BigInteger(), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.CheckConstraint('amount >= 0', name='ck_pl_settings_amount'))
    op.execute("insert into pl_settings (key, amount) values ('LUONG_GIAM_DOC', 45000000)")


def downgrade():
    op.drop_table('pl_settings')
    op.drop_index('ix_pl_adjustments_month', table_name='pl_adjustments')
    op.drop_table('pl_adjustments')
    op.drop_constraint('ck_cash_categories_pl_kind', 'cash_categories', type_='check')
    op.drop_column('cash_categories', 'pl_group')
    op.drop_column('cash_categories', 'pl_kind')
