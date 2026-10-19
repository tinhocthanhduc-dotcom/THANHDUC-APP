"""kt4_giu_may_khach

Revision ID: 0025
Revises: 0024
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('equipment_custody',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('serial', sa.String(length=64), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('location', sa.String(length=16), nullable=False),
    sa.Column('holder_id', sa.Integer(), nullable=True),
    sa.Column('partner_name', sa.String(length=300), nullable=False),
    sa.Column('expected_back', sa.Date(), nullable=True),
    sa.Column('accessories', postgresql.ARRAY(sa.String(length=16)), nullable=False),
    sa.Column('accessories_note', sa.Text(), nullable=False),
    sa.Column('condition_note', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('confirmed_by', sa.Integer(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint("(location = 'THO_GIU') = (holder_id is not null)", name='ck_custody_holder'),
    sa.CheckConstraint("(status = 'CHO_NHAN' and confirmed_by is null and confirmed_at is null) or (status <> 'CHO_NHAN' and confirmed_by is not null and confirmed_at is not null)", name='ck_custody_confirmation'),
    sa.CheckConstraint("accessories <@ ARRAY['DAY_NGUON','KHAY_GIAY','HOP_MUC','DAY_USB','KHAC']::varchar[]", name='ck_custody_accessories'),
    sa.CheckConstraint("location <> 'DOI_TAC' or btrim(partner_name) <> ''", name='ck_custody_partner'),
    sa.CheckConstraint("location in ('TAI_KHACH','THO_GIU','KHO_KY_THUAT','DOI_TAC')", name='ck_custody_location'),
    sa.CheckConstraint("status in ('CHO_NHAN','DA_NHAN','DA_HUY')", name='ck_custody_status'),
    sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['holder_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['serial'], ['equipment.serial'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['service_tickets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_custody_serial_id', 'equipment_custody', ['serial', 'id'], unique=False)
    op.create_index('ix_custody_ticket_id', 'equipment_custody', ['ticket_id'], unique=False)
    op.create_index('uq_custody_pending_serial', 'equipment_custody', ['serial'], unique=True, postgresql_where=sa.text("status = 'CHO_NHAN'"))
    op.add_column('equipment', sa.Column('custody_location', sa.String(length=16), server_default='TAI_KHACH', nullable=False))
    op.add_column('equipment', sa.Column('custody_holder_id', sa.Integer(), nullable=True))
    op.add_column('equipment', sa.Column('custody_ticket_id', sa.BigInteger(), nullable=True))
    op.create_index(op.f('ix_equipment_custody_ticket_id'), 'equipment', ['custody_ticket_id'], unique=False)
    op.create_foreign_key('fk_equipment_custody_ticket', 'equipment', 'service_tickets', ['custody_ticket_id'], ['id'])
    op.create_foreign_key('fk_equipment_custody_holder', 'equipment', 'users', ['custody_holder_id'], ['id'])

    op.create_check_constraint('ck_equipment_custody_location', 'equipment', "custody_location in ('TAI_KHACH','THO_GIU','KHO_KY_THUAT','DOI_TAC')")
    op.create_check_constraint('ck_equipment_custody_holder', 'equipment', "(custody_location = 'THO_GIU') = (custody_holder_id is not null)")
    op.execute("""
        create function equipment_custody_guard() returns trigger language plpgsql as $$
        begin
          if TG_OP = 'UPDATE' then
            if OLD.status = 'CHO_NHAN' and NEW.status in ('DA_NHAN','DA_HUY')
               and NEW.version = OLD.version + 1
               and NEW.confirmed_by is not null and NEW.confirmed_at is not null
               and (to_jsonb(NEW) - ARRAY['status','confirmed_by','confirmed_at','version'])
                   = (to_jsonb(OLD) - ARRAY['status','confirmed_by','confirmed_at','version']) then
              return NEW;
            end if;
          end if;
          raise exception 'equipment_custody bat bien; chi chot dong CHO_NHAN mot lan';
        end $$;
        create trigger equipment_custody_readonly before update or delete on equipment_custody
          for each row execute function equipment_custody_guard();
    """)


def downgrade() -> None:
    op.execute('drop trigger equipment_custody_readonly on equipment_custody')
    op.execute('drop function equipment_custody_guard()')
    op.drop_constraint('ck_equipment_custody_holder', 'equipment', type_='check')
    op.drop_constraint('ck_equipment_custody_location', 'equipment', type_='check')
    op.drop_constraint('fk_equipment_custody_ticket', 'equipment', type_='foreignkey')
    op.drop_constraint('fk_equipment_custody_holder', 'equipment', type_='foreignkey')
    op.drop_index(op.f('ix_equipment_custody_ticket_id'), table_name='equipment')
    op.drop_column('equipment', 'custody_ticket_id')
    op.drop_column('equipment', 'custody_holder_id')
    op.drop_column('equipment', 'custody_location')
    op.drop_index('uq_custody_pending_serial', table_name='equipment_custody', postgresql_where=sa.text("status = 'CHO_NHAN'"))
    op.drop_index('ix_custody_ticket_id', table_name='equipment_custody')
    op.drop_index('ix_custody_serial_id', table_name='equipment_custody')
    op.drop_table('equipment_custody')
