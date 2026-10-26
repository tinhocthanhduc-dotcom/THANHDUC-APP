"""SX1 production orders, stock provenance and commission source."""
from alembic import op
import sqlalchemy as sa
revision='0063'
down_revision='0062'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("CREATE TABLE production_orders (\n\tid BIGSERIAL NOT NULL, \n\tnumber VARCHAR(24) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tproduct_code VARCHAR(16) NOT NULL, \n\tqty_plan INTEGER NOT NULL, \n\tqty_good INTEGER DEFAULT '0' NOT NULL, \n\tqty_scrap INTEGER DEFAULT '0' NOT NULL, \n\ttechnician_id INTEGER, \n\tfrom_warehouse_code VARCHAR(16), \n\tto_warehouse_code VARCHAR(16) NOT NULL, \n\tstatus VARCHAR(16) DEFAULT 'MOI' NOT NULL, \n\tpx_doc_id BIGINT, \n\tpn_doc_id BIGINT, \n\tmaterial_cost BIGINT DEFAULT '0' NOT NULL, \n\tunit_cost BIGINT DEFAULT '0' NOT NULL, \n\tnote TEXT DEFAULT '' NOT NULL, \n\tcreated_by INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE, \n\tfinished_at TIMESTAMP WITH TIME ZONE, \n\treceived_by INTEGER, \n\treceived_at TIMESTAMP WITH TIME ZONE, \n\tvoid_reason TEXT DEFAULT '' NOT NULL, \n\tversion INTEGER DEFAULT '1' NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_production_kind CHECK (kind in ('DO_MUC','TAN_TRANG','LAP_RAP')), \n\tCONSTRAINT ck_production_status CHECK (status in ('MOI','DANG_LAM','HOAN_THANH','DA_NHAP','DA_HUY')), \n\tCONSTRAINT ck_production_qty CHECK (qty_plan>0 and qty_good>=0 and qty_scrap>=0 and qty_good+qty_scrap<=qty_plan), \n\tCONSTRAINT ck_production_cost CHECK (material_cost>=0 and unit_cost>=0), \n\tCONSTRAINT ck_production_px CHECK (status not in ('HOAN_THANH','DA_NHAP') or px_doc_id is not null), \n\tCONSTRAINT ck_production_pn CHECK (status<>'DA_NHAP' or pn_doc_id is not null), \n\tCONSTRAINT ck_production_void CHECK (status<>'DA_HUY' or length(trim(void_reason))>0), \n\tUNIQUE (number), \n\tFOREIGN KEY(product_code) REFERENCES products (code), \n\tFOREIGN KEY(technician_id) REFERENCES users (id), \n\tFOREIGN KEY(from_warehouse_code) REFERENCES warehouses (code), \n\tFOREIGN KEY(to_warehouse_code) REFERENCES warehouses (code), \n\tUNIQUE (px_doc_id), \n\tFOREIGN KEY(px_doc_id) REFERENCES stock_docs (id), \n\tUNIQUE (pn_doc_id), \n\tFOREIGN KEY(pn_doc_id) REFERENCES stock_docs (id), \n\tFOREIGN KEY(created_by) REFERENCES users (id), \n\tFOREIGN KEY(received_by) REFERENCES users (id)\n)")
    op.execute('CREATE INDEX ix_production_technician_status ON production_orders (technician_id, status)')
    op.execute("CREATE TABLE production_order_lines (\n\torder_id BIGINT NOT NULL, \n\tline_no INTEGER NOT NULL, \n\tproduct_code VARCHAR(16) NOT NULL, \n\tqty_plan NUMERIC(14, 3) DEFAULT '0' NOT NULL, \n\tqty_used NUMERIC(14, 3) DEFAULT '0' NOT NULL, \n\tnote TEXT DEFAULT '' NOT NULL, \n\tPRIMARY KEY (order_id, line_no), \n\tCONSTRAINT ck_production_line_qty CHECK (qty_plan>=0 and qty_used>=0), \n\tFOREIGN KEY(order_id) REFERENCES production_orders (id), \n\tFOREIGN KEY(product_code) REFERENCES products (code)\n)")
    op.add_column('commission_lines',sa.Column('production_id',sa.BigInteger(),nullable=True))
    op.create_foreign_key('fk_commission_production','commission_lines','production_orders',['production_id'],['id'])
    op.drop_constraint('ck_commission_rule_kind','commission_rules',type_='check')
    op.drop_constraint('ck_commission_line_source','commission_lines',type_='check')
    op.create_check_constraint('ck_commission_rule_kind','commission_rules',"service_kind in ('SUA_TAI_CHO','MANG_VE','BAO_HANH','MUC_DINH_KY','SAN_XUAT')")
    op.create_check_constraint('ck_commission_line_source','commission_lines',"(source='DON_BAN' and order_id is not null and ticket_id is null and production_id is null) or (source='PHIEU_DV' and ticket_id is not null and order_id is null and production_id is null) or (source='PHIEU_SX' and production_id is not null and order_id is null and ticket_id is null) or (source='DIEU_CHINH' and order_id is null and ticket_id is null and production_id is null and length(trim(note))>0)")
    op.execute("CREATE UNIQUE INDEX uq_commission_line_production ON commission_lines (month, production_id) WHERE source='PHIEU_SX'")
    op.execute("create function production_no_delete() returns trigger language plpgsql as $$ begin raise exception 'Production records cannot be deleted'; end $$")
    op.execute('create trigger production_no_delete before delete on production_orders for each row execute function production_no_delete()')
    op.execute('create trigger production_line_no_delete before delete on production_order_lines for each row execute function production_no_delete()')


def downgrade():
    # Preserve locked commission amounts/totals when the old schema loses the SX source.
    op.execute("alter table commission_lines disable trigger commission_lines_guard")
    op.execute("update commission_lines l set source='DIEU_CHINH', production_id=null, rule_id=null, "
               "note='Hoa hồng sản xuất ' || p.number || ' (hạ migration SX1). ' || l.note "
               "from production_orders p where l.source='PHIEU_SX' and l.production_id=p.id")
    op.execute("alter table commission_lines enable trigger commission_lines_guard")
    op.drop_index('uq_commission_line_production',table_name='commission_lines')
    op.drop_constraint('ck_commission_line_source','commission_lines',type_='check')
    op.drop_constraint('fk_commission_production','commission_lines',type_='foreignkey')
    op.drop_column('commission_lines','production_id')
    op.create_check_constraint('ck_commission_line_source','commission_lines',"(source='DON_BAN' and order_id is not null and ticket_id is null) or (source='PHIEU_DV' and ticket_id is not null and order_id is null) or (source='DIEU_CHINH' and order_id is null and ticket_id is null and length(trim(note))>0)")
    op.execute("alter table commission_rules disable trigger commission_rules_guard")
    op.execute("delete from commission_rules where service_kind='SAN_XUAT'")
    op.execute("alter table commission_rules enable trigger commission_rules_guard")
    op.drop_constraint('ck_commission_rule_kind','commission_rules',type_='check')
    op.create_check_constraint('ck_commission_rule_kind','commission_rules',"service_kind in ('SUA_TAI_CHO','MANG_VE','BAO_HANH','MUC_DINH_KY')")
    op.drop_table('production_order_lines')
    op.drop_table('production_orders')
    op.execute('drop function production_no_delete()')
