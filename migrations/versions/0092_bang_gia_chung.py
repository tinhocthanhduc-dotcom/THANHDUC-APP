"""Bảng giá chung + gói sửa chữa (TUẤN duyệt 14/11/2026, đợt 1 quy trình báo giá sửa chữa).

Nhóm máy → model (ghi đè) → dịch vụ gói 3 cấp; phụ phí; hiện tượng → dịch vụ + cấp có thể cần.
Giá lưu chưa VAT. Lịch sử đổi giá ghi ở audit_log.

Revision ID: 0092
Revises: 0091
"""
import sqlalchemy as sa
from alembic import op

revision = '0092'
down_revision = '0091'
branch_labels = None
depends_on = None

GROUPS = (
    ('LASER_A4_DON', 'Laser đen trắng A4 đơn năng'),
    ('LASER_A4_DA', 'Laser đen trắng A4 đa năng'),
    ('LASER_MAU', 'Laser màu A4'),
    ('LASER_A3', 'Laser A3'),
    ('PHUN_HOP', 'Phun hộp mực'),
    ('PHUN_BINH', 'Phun bình mực'),
    ('KIM_NHIET', 'In kim, nhiệt'),
)


def upgrade():
    groups = op.create_table('repair_groups',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(length=16), nullable=False, unique=True),
        sa.Column('name', sa.String(length=100), nullable=False, unique=True),
        sa.Column('sort', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='true'))
    op.bulk_insert(groups, [dict(id=i + 1, code=c, name=n, sort=i + 1, active=True) for i, (c, n) in enumerate(GROUPS)])
    op.execute("select setval('repair_groups_id_seq', (select max(id) from repair_groups))")

    op.create_table('repair_models',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('group_id', sa.Integer(), sa.ForeignKey('repair_groups.id'), nullable=False),
        sa.Column('note', sa.Text(), nullable=False, server_default=''),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('search_text', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index('uq_repair_models_name', 'repair_models', [sa.text('lower(name)')], unique=True)

    op.create_table('price_services',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(length=4), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('customer_name', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('technical_note', sa.Text(), nullable=False, server_default=''),
        sa.Column('unit', sa.String(length=32), nullable=False, server_default='lần'),
        sa.Column('price', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('warranty_months', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('scope_1', sa.Text(), nullable=False, server_default=''),
        sa.Column('scope_2', sa.Text(), nullable=False, server_default=''),
        sa.Column('scope_3', sa.Text(), nullable=False, server_default=''),
        sa.Column('hours_1', sa.Numeric(4, 1), nullable=False, server_default='0'),
        sa.Column('hours_2', sa.Numeric(4, 1), nullable=False, server_default='0'),
        sa.Column('hours_3', sa.Numeric(4, 1), nullable=False, server_default='0'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('sort', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('search_text', sa.Text(), nullable=False, server_default=''),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind in ('GOI','PHI')", name='ck_price_services_kind'),
        sa.CheckConstraint('price >= 0', name='ck_price_services_price'),
        sa.CheckConstraint('warranty_months between 0 and 60', name='ck_price_services_warranty'))
    op.create_index('uq_price_services_name', 'price_services', ['kind', sa.text('lower(name)')], unique=True)

    op.create_table('repair_prices',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('service_id', sa.Integer(), sa.ForeignKey('price_services.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('group_id', sa.Integer(), sa.ForeignKey('repair_groups.id'), nullable=True),
        sa.Column('model_id', sa.Integer(), sa.ForeignKey('repair_models.id', ondelete='CASCADE'), nullable=True),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('price', sa.BigInteger(), nullable=False),
        sa.Column('price_genuine', sa.BigInteger(), nullable=True),
        sa.Column('warranty_months', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint('level between 1 and 3', name='ck_repair_prices_level'),
        sa.CheckConstraint('price > 0', name='ck_repair_prices_price'),
        sa.CheckConstraint('price_genuine is null or price_genuine > 0', name='ck_repair_prices_genuine'),
        sa.CheckConstraint('warranty_months between 0 and 60', name='ck_repair_prices_warranty'),
        sa.CheckConstraint('(group_id is null) <> (model_id is null)', name='ck_repair_prices_target'))
    op.create_index('uq_repair_prices_group', 'repair_prices', ['service_id', 'group_id', 'level'], unique=True,
                    postgresql_where=sa.text('group_id is not null'))
    op.create_index('uq_repair_prices_model', 'repair_prices', ['service_id', 'model_id', 'level'], unique=True,
                    postgresql_where=sa.text('model_id is not null'))

    op.create_table('repair_options',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('service_id', sa.Integer(), sa.ForeignKey('price_services.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('part_hint', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('is_main', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('sort', sa.Integer(), nullable=False, server_default='0'))

    op.create_table('repair_symptoms',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('service_id', sa.Integer(), sa.ForeignKey('price_services.id', ondelete='CASCADE'), nullable=False),
        sa.Column('group_id', sa.Integer(), sa.ForeignKey('repair_groups.id'), nullable=True),
        sa.Column('level_min', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('level_max', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('note', sa.Text(), nullable=False, server_default=''),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('search_text', sa.Text(), nullable=False, server_default=''),
        sa.CheckConstraint('level_min between 1 and 3 and level_max between level_min and 3', name='ck_repair_symptoms_levels'))


def downgrade():
    op.drop_table('repair_symptoms')
    op.drop_table('repair_options')
    op.drop_table('repair_prices')
    op.drop_table('price_services')
    op.drop_table('repair_models')
    op.drop_table('repair_groups')
