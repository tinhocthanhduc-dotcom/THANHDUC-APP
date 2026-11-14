"""Đổi tên 7 nhóm máy theo TUẤN 14/11/2026; 2 nhóm phun theo hãng: Máy in phun Canon, Máy in phun Epson.

Phun hộp mực (PHUN_HOP) → Máy in phun Canon (PHUN_CANON); Phun bình mực (PHUN_BINH) → Máy in phun Epson (PHUN_EPSON).
Model Canon đang nằm ở nhóm phun Epson (vd Canon PIXMA G2010 từ bộ mẫu) chuyển sang nhóm phun Canon.
Thêm nhóm thứ 8 "Máy in phun hãng khác" (PHUN_KHAC, HP, Brother…); model phun không phải Canon/Epson chuyển vào đó.
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
    op.execute("update repair_groups set sort = 8 where code = 'KIM_NHIET'")
    op.execute("insert into repair_groups (code, name, sort, active) select 'PHUN_KHAC', 'Máy in phun hãng khác', 7, true "
               "where not exists (select 1 from repair_groups where code = 'PHUN_KHAC' or lower(name) = lower('Máy in phun hãng khác'))")
    op.execute("update repair_models set group_id = (select id from repair_groups where code = 'PHUN_KHAC') "
               "where lower(name) not like 'canon%' and lower(name) not like 'epson%' "
               "and group_id in (select id from repair_groups where code in ('PHUN_CANON', 'PHUN_EPSON')) "
               "and exists (select 1 from repair_groups where code = 'PHUN_KHAC')")
    op.execute("update repair_symptoms set group_id = null where lower(name) = lower('Báo đầy bộ đệm thải, mực không xuống') "
               "and group_id = (select id from repair_groups where code = 'PHUN_EPSON')")


def downgrade():
    # Model đã chuyển vào nhóm hãng khác về lại nhóm phun Epson; bỏ nhóm nếu chưa có giá nào dùng.
    op.execute("update repair_models set group_id = (select id from repair_groups where code = 'PHUN_EPSON') "
               "where group_id = (select id from repair_groups where code = 'PHUN_KHAC')")
    op.execute("delete from repair_groups where code = 'PHUN_KHAC' and not exists (select 1 from repair_prices p "
               "join repair_groups g on g.id = p.group_id where g.code = 'PHUN_KHAC') and not exists (select 1 from repair_symptoms s "
               "join repair_groups g on g.id = s.group_id where g.code = 'PHUN_KHAC')")
    op.execute("update repair_groups set sort = 7 where code = 'KIM_NHIET'")
    for old_code, new_code, old_name, new_name in NEW:
        op.execute(f"update repair_groups set code = '{old_code}', "
                   f"name = case when name = '{_q(new_name)}' then '{_q(old_name)}' else name end where code = '{new_code}'")
