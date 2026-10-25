"""Thiết bị ngoài danh mục hàng hóa: tên máy tự nhập, mã hàng không bắt buộc."""
from alembic import op
import sqlalchemy as sa
revision='0062'
down_revision='0061'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('equipment',sa.Column('model_name',sa.String(160),nullable=False,server_default=''))
    op.alter_column('equipment','product_code',existing_type=sa.String(16),nullable=True)
    op.create_check_constraint('ck_equipment_model','equipment',"product_code is not null or model_name <> ''")


def downgrade():
    op.drop_constraint('ck_equipment_model','equipment',type_='check')
    op.execute("delete from equipment where product_code is null")
    op.alter_column('equipment','product_code',existing_type=sa.String(16),nullable=False)
    op.drop_column('equipment','model_name')
