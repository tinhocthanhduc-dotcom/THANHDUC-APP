"""CXL1: hồ sơ cho mọi lô hàng trong kho LOI.

Revision ID: 0038
Revises: 0037
"""
from alembic import op
import sqlalchemy as sa

revision='0038'
down_revision='0037'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('pending_items',
        sa.Column('id',sa.BigInteger(),primary_key=True),sa.Column('number',sa.Text(),nullable=False,unique=True),
        sa.Column('warehouse_code',sa.String(16),sa.ForeignKey('warehouses.code'),nullable=False),
        sa.Column('product_code',sa.String(16),sa.ForeignKey('products.code'),nullable=False),
        sa.Column('qty_in',sa.Numeric(14,3),nullable=False),sa.Column('qty_left',sa.Numeric(14,3),nullable=False),
        sa.Column('unit_cost',sa.BigInteger(),nullable=False),sa.Column('group_code',sa.Text(),nullable=False),
        sa.Column('source',sa.Text(),nullable=False),sa.Column('source_ref',sa.Text(),nullable=False),
        sa.Column('stock_doc_id',sa.BigInteger(),sa.ForeignKey('stock_docs.id')),
        sa.Column('supplier_code',sa.String(16),sa.ForeignKey('suppliers.code')),sa.Column('reason',sa.Text(),nullable=False),
        sa.Column('entered_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('created_by',sa.Integer(),sa.ForeignKey('users.id')),
        sa.Column('status',sa.Text(),nullable=False),sa.Column('version',sa.Integer(),nullable=False),
        sa.CheckConstraint('qty_in > 0',name='ck_pending_items_qty_in'),
        sa.CheckConstraint('qty_left >= 0 and qty_left <= qty_in',name='ck_pending_items_qty_left'),
        sa.CheckConstraint('unit_cost >= 0',name='ck_pending_items_cost'),
        sa.CheckConstraint("group_code in ('HANG_LOI','CHO_BH_NCC','THANH_LY','THU_HOI','CHUA_PHAN_LOAI')",name='ck_pending_items_group'),
        sa.CheckConstraint("source in ('THU_HOI','THO_BAO_HU','KHACH_TRA','CHUYEN_TAY','KIEM_KE','TON_CU')",name='ck_pending_items_source'),
        sa.CheckConstraint("(status='DANG_CHO' and qty_left>0) or (status in ('XONG','DA_HUY') and qty_left=0)",name='ck_pending_items_status'))
    op.create_index('ix_pending_items_stock','pending_items',['warehouse_code','product_code','status'])
    op.create_index('ix_pending_items_group','pending_items',['group_code','status'])
    # Số cấp cùng transaction và cùng doc_counters; không cần người/phiếu giả cho tồn cũ.
    op.execute("""DO $$ DECLARE r record; n bigint; y integer := extract(year from current_timestamp at time zone 'Asia/Ho_Chi_Minh');
    BEGIN FOR r IN SELECT b.* FROM stock_balances b JOIN warehouses w ON w.code=b.warehouse_code
        WHERE w.kind='LOI' AND b.qty>0 ORDER BY b.warehouse_code,b.product_code LOOP
        INSERT INTO doc_counters(prefix,year,last) VALUES ('CXL',y,1)
        ON CONFLICT(prefix,year) DO UPDATE SET last=doc_counters.last+1 RETURNING last INTO n;
        INSERT INTO pending_items(number,warehouse_code,product_code,qty_in,qty_left,unit_cost,group_code,source,source_ref,reason,entered_at,status,version)
        VALUES ('CXL-'||to_char(y%100,'FM00')||'-'||lpad(n::text,greatest(5,length(n::text)),'0'),r.warehouse_code,r.product_code,r.qty,r.qty,r.avg_cost,
        'CHUA_PHAN_LOAI','TON_CU','','Tồn trước khi có hồ sơ chờ xử lý',current_timestamp,'DANG_CHO',1);
    END LOOP; END $$""")
    op.execute("""CREATE FUNCTION pending_item_warehouse() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF NOT EXISTS(SELECT 1 FROM warehouses WHERE code=NEW.warehouse_code AND kind='LOI') THEN
        RAISE EXCEPTION 'pending_item requires LOI warehouse'; END IF; RETURN NEW; END $$;
      CREATE TRIGGER pending_item_warehouse BEFORE INSERT OR UPDATE ON pending_items
      FOR EACH ROW EXECUTE FUNCTION pending_item_warehouse();""")
    # Kiểm tra ở cuối transaction: sổ và hồ sơ được cập nhật theo thứ tự nào cũng được.
    op.execute("""CREATE FUNCTION pending_stock_invariant() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE w text; p text; actual numeric; waiting numeric;
      BEGIN
        FOR w,p IN SELECT DISTINCT x.w,x.p FROM (VALUES
          (CASE WHEN TG_OP<>'INSERT' THEN OLD.warehouse_code END,CASE WHEN TG_OP<>'INSERT' THEN OLD.product_code END),
          (CASE WHEN TG_OP<>'DELETE' THEN NEW.warehouse_code END,CASE WHEN TG_OP<>'DELETE' THEN NEW.product_code END)) x(w,p)
          WHERE x.w IS NOT NULL LOOP
          IF EXISTS(SELECT 1 FROM warehouses WHERE code=w AND kind='LOI') THEN
            SELECT coalesce(sum(qty),0) INTO actual FROM stock_balances WHERE warehouse_code=w AND product_code=p;
            SELECT coalesce(sum(qty_left),0) INTO waiting FROM pending_items WHERE warehouse_code=w AND product_code=p AND status='DANG_CHO';
            IF actual<>waiting THEN RAISE EXCEPTION 'CXL_INVARIANT: %, % stock %, pending %',w,p,actual,waiting; END IF;
          END IF;
        END LOOP; RETURN NULL;
      END $$;
      CREATE CONSTRAINT TRIGGER pending_stock_invariant AFTER INSERT OR UPDATE OR DELETE ON stock_balances
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION pending_stock_invariant();
      CREATE CONSTRAINT TRIGGER pending_item_invariant AFTER INSERT OR UPDATE OR DELETE ON pending_items
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION pending_stock_invariant();""")


def downgrade():
    op.execute('DROP TRIGGER pending_stock_invariant ON stock_balances; DROP FUNCTION pending_stock_invariant() CASCADE; DROP FUNCTION pending_item_warehouse() CASCADE')
    op.drop_table('pending_items')
