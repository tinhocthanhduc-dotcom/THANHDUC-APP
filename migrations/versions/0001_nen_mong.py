"""Nền móng: tài khoản, nhật ký, khách KTD, hàng mã TD

Revision ID: 0001
Revises: 
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('create extension if not exists pg_trgm')
    # Mã KTD cấp theo dãy số; scripts/import_sample.py đẩy dãy lên sau khi nhập mã cũ.
    op.execute('create sequence ktd_seq start 1')
    op.create_table('products',
    sa.Column('code', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('group_name', sa.String(length=100), nullable=False),
    sa.Column('unit', sa.String(length=32), nullable=False),
    sa.Column('sale_price', sa.BigInteger(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('is_test', sa.Boolean(), nullable=False),
    sa.Column('search_text', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("code ~ '^TD-[0-9]{4,}$'", name='ck_products_code'),
    sa.CheckConstraint('sale_price >= 0', name='ck_products_price'),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_index('ix_products_search', 'products', ['search_text'], unique=False, postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('password_hash', sa.String(length=200), nullable=False),
    sa.Column('roles', postgresql.ARRAY(sa.String(length=32)), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('entity', sa.String(length=32), nullable=False),
    sa.Column('entity_id', sa.String(length=64), nullable=False),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_log_at'), 'audit_log', ['at'], unique=False)
    op.create_index(op.f('ix_audit_log_entity_id'), 'audit_log', ['entity_id'], unique=False)
    op.create_table('customers',
    sa.Column('code', sa.String(length=16), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('tax_code', sa.String(length=13), nullable=False),
    sa.Column('email', sa.String(length=200), nullable=False),
    sa.Column('address', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('allow_debt', sa.Boolean(), nullable=False),
    sa.Column('credit_limit', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('merged_into', sa.String(length=16), nullable=True),
    sa.Column('old_codes', postgresql.ARRAY(sa.String(length=64)), nullable=False),
    sa.Column('is_test', sa.Boolean(), nullable=False),
    sa.Column('search_text', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'DA_GOP') = (merged_into is not null)", name='ck_customers_merge'),
    sa.CheckConstraint("kind in ('CA_NHAN','CONG_TY')", name='ck_customers_kind'),
    sa.CheckConstraint("status in ('DANG_DUNG','NGUNG','DA_GOP')", name='ck_customers_status'),
    sa.CheckConstraint('credit_limit >= 0', name='ck_customers_credit_limit'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['merged_into'], ['customers.code'], ),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_index('ix_customers_search', 'customers', ['search_text'], unique=False, postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})
    op.create_index('uq_customers_tax', 'customers', ['tax_code'], unique=True, postgresql_where="tax_code <> '' and status <> 'DA_GOP'")
    op.create_table('customer_contacts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('customer_code', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('phones', postgresql.ARRAY(sa.String(length=11)), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['customer_code'], ['customers.code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_customer_contacts_customer_code'), 'customer_contacts', ['customer_code'], unique=False)
    op.create_table('customer_phones',
    sa.Column('customer_code', sa.String(length=16), nullable=False),
    sa.Column('phone', sa.String(length=11), nullable=False),
    sa.Column('is_contact', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['customer_code'], ['customers.code'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('customer_code', 'phone')
    )
    op.create_index(op.f('ix_customer_phones_phone'), 'customer_phones', ['phone'], unique=False)
    # Nhật ký chỉ được thêm, không sửa, không xóa.
    op.execute(
        """
        create function audit_log_readonly() returns trigger language plpgsql as $$
        begin
          raise exception 'audit_log chỉ được thêm, không sửa hoặc xóa';
        end $$;
        create trigger audit_log_readonly before update or delete on audit_log
          for each row execute function audit_log_readonly();
        """
    )


def downgrade() -> None:
    op.execute('drop trigger if exists audit_log_readonly on audit_log')
    op.execute('drop function if exists audit_log_readonly()')
    op.drop_index(op.f('ix_customer_phones_phone'), table_name='customer_phones')
    op.drop_table('customer_phones')
    op.drop_index(op.f('ix_customer_contacts_customer_code'), table_name='customer_contacts')
    op.drop_table('customer_contacts')
    op.drop_index('uq_customers_tax', table_name='customers', postgresql_where="tax_code <> '' and status <> 'DA_GOP'")
    op.drop_index('ix_customers_search', table_name='customers', postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})
    op.drop_table('customers')
    op.drop_index(op.f('ix_audit_log_entity_id'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_at'), table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_table('users')
    op.drop_index('ix_products_search', table_name='products', postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})
    op.drop_table('products')
    op.execute('drop sequence if exists ktd_seq')
