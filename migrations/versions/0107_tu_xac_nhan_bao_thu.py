"""Kế toán tự xác nhận báo thu do mình lập trong hạn mức, giám đốc soát sau (TUẤN duyệt mẫu 17/11/2026).

Thêm khóa TU_XAC_NHAN vào bảng hạn mức duyệt và đặt sẵn 3.000.000 đ/phiếu (mức TUẤN duyệt); 0 = tắt.
Không đổi cách ghi quỹ, công nợ; không đổi dữ liệu báo thu đã có.

Revision ID: 0107
Revises: 0106
"""
from alembic import op

revision = '0107'
down_revision = '0106'
branch_labels = None
depends_on = None

OLD_KEYS = "key in ('BO_SUNG','NGHI','TAM_UNG','HUY_HANG','CHI_PHI')"
NEW_KEYS = "key in ('BO_SUNG','NGHI','TAM_UNG','HUY_HANG','CHI_PHI','TU_XAC_NHAN')"


def upgrade():
    op.drop_constraint('ck_approval_limits_key', 'approval_limits', type_='check')
    op.create_check_constraint('ck_approval_limits_key', 'approval_limits', NEW_KEYS)
    op.execute("INSERT INTO approval_limits (key, value) VALUES ('TU_XAC_NHAN', 3000000) ON CONFLICT (key) DO NOTHING")


def downgrade():
    op.execute("DELETE FROM approval_limits WHERE key='TU_XAC_NHAN'")
    op.drop_constraint('ck_approval_limits_key', 'approval_limits', type_='check')
    op.create_check_constraint('ck_approval_limits_key', 'approval_limits', OLD_KEYS)
