"""Đổi tên 7 nhóm máy theo TUẤN 14/11/2026; 2 nhóm phun theo hãng: Máy in phun Canon, Máy in phun Epson.

Phun hộp mực (PHUN_HOP) → Máy in phun Canon (PHUN_CANON); Phun bình mực (PHUN_BINH) → Máy in phun Epson (PHUN_EPSON).
Model Canon đang nằm ở nhóm phun Epson (vd Canon PIXMA G2010 từ bộ mẫu) chuyển sang nhóm phun Canon.
Hiện tượng mẫu "Báo đầy bộ đệm thải" mở cho mọi nhóm (máy phun Canon cũng có bình mực). Giá không đổi.

Revision ID: 0094
Revises: 0093
"""
from alembic import op

revision = '0094'
down_revision = '0093'
branch_labels = None
depends_on = None

NEW = (
    ('LASER_A4_DON', 'LASER_A4_DON', 'Laser đen trắng A4 đơn năng', 'Máy in Laser đen trắng A4 đơn năng'),
    ('LASER_A4_DA', 'LASER_A4_DA', 'Laser đen trắng A4 đa năng', 'Máy in Laser đen trắng A4 đa năng'),
    ('LASER_MAU', 'LASER_MAU', 'Laser màu A4', 'Máy in Laser màu A4'),
    ('LASER_A3', 'LASER_A3', 'Laser A3', 'Máy in laser khổ A3'),
    ('PHUN_HOP', 'PHUN_CANON', 'Phun hộp mực', 'Máy in phun Canon'),
    ('PHUN_BINH', 'PHUN_EPSON', 'Phun bình mực', 'Máy in phun Epson'),
    ('KIM_NHIET', 'KIM_NHIET', 'In kim, nhiệt', 'Máy in kim, in nhiệt'),
)


def _q(text):
    return text.replace("'", "''")


def upgrade():
    for old_code, new_code, old_name, new_name in NEW:
        # Chỉ đổi tên nếu giám đốc chưa tự đổi tên nhóm.
        op.execute(f"update repair_groups set code = '{new_code}', "
                   f"name = case when name = '{_q(old_name)}' then '{_q(new_name)}' else name end where code = '{old_code}'")
    op.execute("update repair_models set group_id = (select id from repair_groups where code = 'PHUN_CANON') "
               "where lower(name) like 'canon%' and group_id = (select id from repair_groups where code = 'PHUN_EPSON')")
    op.execute("update repair_symptoms set group_id = null where lower(name) = lower('Báo đầy bộ đệm thải, mực không xuống') "
               "and group_id = (select id from repair_groups where code = 'PHUN_EPSON')")


def downgrade():
    for old_code, new_code, old_name, new_name in NEW:
        op.execute(f"update repair_groups set code = '{old_code}', "
                   f"name = case when name = '{_q(new_name)}' then '{_q(old_name)}' else name end where code = '{new_code}'")
