"""Hoa hồng và KPI thợ giao đơn bán (TUẤN 27/10/2026): nguồn GIAO_DON, loại quy tắc Giao đơn."""
from alembic import op
import sqlalchemy as sa
revision='0066'
down_revision='0065'
branch_labels=None
depends_on=None

KINDS_OLD="service_kind in ('SUA_TAI_CHO','MANG_VE','BAO_HANH','MUC_DINH_KY','SAN_XUAT')"
KINDS_NEW="service_kind in ('SUA_TAI_CHO','MANG_VE','BAO_HANH','MUC_DINH_KY','SAN_XUAT','GIAO_DON')"
SOURCE_OLD=("(source='DON_BAN' and order_id is not null and ticket_id is null and production_id is null) or (source='PHIEU_DV' and ticket_id is not null and order_id is null and production_id is null) "
            "or (source='PHIEU_SX' and production_id is not null and order_id is null and ticket_id is null) or (source='DIEU_CHINH' and order_id is null and ticket_id is null and production_id is null and length(trim(note))>0)")
SOURCE_NEW=("(source='DON_BAN' and order_id is not null and ticket_id is null and production_id is null and delivery_id is null)"
    " or (source='PHIEU_DV' and ticket_id is not null and order_id is null and production_id is null and delivery_id is null)"
    " or (source='PHIEU_SX' and production_id is not null and order_id is null and ticket_id is null and delivery_id is null)"
    " or (source='GIAO_DON' and delivery_id is not null and order_id is null and ticket_id is null and production_id is null)"
    " or (source='DIEU_CHINH' and order_id is null and ticket_id is null and production_id is null and delivery_id is null and length(trim(note))>0)")


def upgrade():
    op.add_column('commission_lines',sa.Column('delivery_id',sa.BigInteger(),nullable=True))
    op.create_foreign_key('fk_commission_delivery','commission_lines','sales_deliveries',['delivery_id'],['id'])
    op.drop_constraint('ck_commission_rule_kind','commission_rules',type_='check')
    op.create_check_constraint('ck_commission_rule_kind','commission_rules',KINDS_NEW)
    op.drop_constraint('ck_commission_line_source','commission_lines',type_='check')
    op.create_check_constraint('ck_commission_line_source','commission_lines',SOURCE_NEW)
    op.execute("CREATE UNIQUE INDEX uq_commission_line_delivery ON commission_lines (month, delivery_id) WHERE source='GIAO_DON'")


def downgrade():
    # Giữ số tiền đã khóa: dòng giao đơn thành điều chỉnh có ghi chú.
    op.execute("alter table commission_lines disable trigger commission_lines_guard")
    op.execute("update commission_lines l set source='DIEU_CHINH', delivery_id=null, rule_id=null, "
               "note='Hoa hồng giao đơn ' || d.number || ' (hạ migration 0066). ' || l.note "
               "from sales_deliveries d where l.source='GIAO_DON' and l.delivery_id=d.id")
    op.execute("alter table commission_lines enable trigger commission_lines_guard")
    op.drop_index('uq_commission_line_delivery',table_name='commission_lines')
    op.drop_constraint('ck_commission_line_source','commission_lines',type_='check')
    op.drop_constraint('fk_commission_delivery','commission_lines',type_='foreignkey')
    op.drop_column('commission_lines','delivery_id')
    op.create_check_constraint('ck_commission_line_source','commission_lines',SOURCE_OLD)
    op.execute("alter table commission_rules disable trigger commission_rules_guard")
    op.execute("delete from commission_rules where service_kind='GIAO_DON'")
    op.execute("alter table commission_rules enable trigger commission_rules_guard")
    op.drop_constraint('ck_commission_rule_kind','commission_rules',type_='check')
    op.create_check_constraint('ck_commission_rule_kind','commission_rules',KINDS_OLD)
