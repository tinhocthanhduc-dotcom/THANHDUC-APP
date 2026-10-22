"""AD1: close eligible existing completed tickets; preserve all financial data.

Revision ID: 0050
Revises: 0049
"""
from alembic import op
import sqlalchemy as sa

revision = '0050'
down_revision = '0049'
branch_labels = None
depends_on = None

ELIGIBLE = """
    t.status='HOAN_THANH'
    and not exists(select 1 from service_recovered_parts r where r.ticket_id=t.id and r.status='CHO_NOP')
    and (t.serial is null or (
        exists(select 1 from equipment e where e.serial=t.serial and e.custody_location='TAI_KHACH')
        and not exists(select 1 from equipment_custody c where c.serial=t.serial and c.status='CHO_NHAN')))
"""


def upgrade():
    conn=op.get_bind()
    if not conn.scalar(sa.text('select exists(select 1 from service_tickets t where '+ELIGIBLE+')')):
        return
    actor=conn.scalar(sa.text("select id from users where active and 'director'=any(roles) order by id limit 1"))
    if actor is None:
        raise RuntimeError('AD1: cần giám đốc đang hoạt động để ghi lịch sử tự đóng phiếu cũ.')
    conn.execute(sa.text("""
        with eligible as materialized (
            select t.id,to_jsonb(t) as old from service_tickets t where """+ELIGIBLE+""" for update
        ), closed as (
            update service_tickets t set status='DA_DONG',due_at=null,transfer_request='',version=version+1
            from eligible e where t.id=e.id returning t.id,t.assignee_id,e.old,to_jsonb(t) as new
        ), steps as (
            insert into service_steps(ticket_id,action,from_status,to_status,actor_id,assignee_id,note)
            select id,'close','HOAN_THANH','DA_DONG',:actor,assignee_id,'Tự đóng: dữ liệu cũ' from closed
        )
        insert into audit_log(user_id,action,entity,entity_id,before,after,reason)
        select :actor,'service_ticket.close','service_ticket',id::text,old,new,'' from closed
    """),dict(actor=actor))


def downgrade():
    # A business closure and its append-only history must not be undone by DDL rollback.
    pass
