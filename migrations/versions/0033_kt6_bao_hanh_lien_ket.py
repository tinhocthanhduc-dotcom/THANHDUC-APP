"""kt6_bao_hanh_lien_ket

Revision ID: 0033
Revises: 0032
"""
from alembic import op
import sqlalchemy as sa


revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('service_recovered_parts',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('product_code', sa.String(length=16), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('holder_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('warehouse_code', sa.String(length=16), nullable=True),
    sa.Column('stock_doc_id', sa.BigInteger(), nullable=True),
    sa.Column('received_by', sa.Integer(), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('void_reason', sa.Text(), server_default='', nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.CheckConstraint("(status = 'DA_HUY') = (btrim(void_reason) <> '')", name='ck_recovered_parts_reason'),
    sa.CheckConstraint("(status = 'DA_NHAN' and warehouse_code is not null and stock_doc_id is not null and received_by is not null and received_at is not null) or (status <> 'DA_NHAN' and warehouse_code is null and stock_doc_id is null and received_by is null and received_at is null)", name='ck_recovered_parts_receipt'),
    sa.CheckConstraint("status in ('CHO_NOP','DA_NHAN','DA_HUY')", name='ck_recovered_parts_status'),
    sa.CheckConstraint('qty > 0', name='ck_recovered_parts_qty'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['holder_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['product_code'], ['products.code'], ),
    sa.ForeignKeyConstraint(['received_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['stock_doc_id'], ['stock_docs.id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['service_tickets.id'], ),
    sa.ForeignKeyConstraint(['warehouse_code'], ['warehouses.code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_doc_id')
    )
    op.create_index('ix_recovered_parts_holder_status', 'service_recovered_parts', ['holder_id', 'status'], unique=False)
    op.create_index(op.f('ix_service_recovered_parts_ticket_id'), 'service_recovered_parts', ['ticket_id'], unique=False)
    op.add_column('service_tickets', sa.Column('warranty_of_id', sa.BigInteger(), nullable=True))
    op.add_column('service_tickets', sa.Column('warranty_basis', sa.String(length=16), nullable=True))
    op.create_index(op.f('ix_service_tickets_warranty_of_id'), 'service_tickets', ['warranty_of_id'], unique=False)
    op.create_foreign_key('fk_service_tickets_warranty_of', 'service_tickets', 'service_tickets', ['warranty_of_id'], ['id'])
    op.create_check_constraint('ck_service_tickets_warranty', 'service_tickets', "(warranty_of_id is null and warranty_basis is null) or (kind = 'BAO_HANH' and warranty_basis in ('PHIEU_TRUOC','MAY_CON_HAN','HET_HAN'))")
    op.create_check_constraint('ck_service_tickets_warranty_self', 'service_tickets', 'warranty_of_id is null or warranty_of_id <> id')
    op.execute("""
        create function service_recovered_parts_guard() returns trigger language plpgsql as $$
        begin
          if TG_OP = 'UPDATE' then
            if NEW.version = OLD.version + 1 and
               (to_jsonb(NEW) - ARRAY['status','warehouse_code','stock_doc_id','received_by','received_at','void_reason','version']) =
               (to_jsonb(OLD) - ARRAY['status','warehouse_code','stock_doc_id','received_by','received_at','void_reason','version']) then
              if OLD.status = 'CHO_NOP' and NEW.status = 'DA_HUY' then return NEW; end if;
              if OLD.status = 'CHO_NOP' and NEW.status = 'DA_NHAN' and exists (
                  select 1 from warehouses w join stock_docs d on d.warehouse_code = w.code
                  where w.code = NEW.warehouse_code and w.kind = 'LOI'
                    and d.id = NEW.stock_doc_id and d.kind = 'PN' and d.status = 'DA_GHI') then return NEW; end if;
              if OLD.status = 'DA_NHAN' and NEW.status = 'CHO_NOP' and exists (
                  select 1 from stock_docs where id = OLD.stock_doc_id and status = 'DA_HUY') then return NEW; end if;
            end if;
          end if;
          raise exception 'service_recovered_parts khong xoa; chi nhan, huy cho nop, hoac hoan khi huy PN';
        end $$;
        create trigger service_recovered_parts_readonly before update or delete on service_recovered_parts
          for each row execute function service_recovered_parts_guard();
    """)


def downgrade() -> None:
    op.execute('drop trigger service_recovered_parts_readonly on service_recovered_parts')
    op.execute('drop function service_recovered_parts_guard()')
    op.drop_constraint('ck_service_tickets_warranty_self', 'service_tickets', type_='check')
    op.drop_constraint('ck_service_tickets_warranty', 'service_tickets', type_='check')
    op.drop_constraint('fk_service_tickets_warranty_of', 'service_tickets', type_='foreignkey')
    op.drop_index(op.f('ix_service_tickets_warranty_of_id'), table_name='service_tickets')
    op.drop_column('service_tickets', 'warranty_basis')
    op.drop_column('service_tickets', 'warranty_of_id')
    op.drop_index(op.f('ix_service_recovered_parts_ticket_id'), table_name='service_recovered_parts')
    op.drop_index('ix_recovered_parts_holder_status', table_name='service_recovered_parts')
    op.drop_table('service_recovered_parts')
