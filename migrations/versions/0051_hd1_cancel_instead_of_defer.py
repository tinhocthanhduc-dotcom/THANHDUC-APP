"""HD1: migrate deferred tickets to cancelled and retire the old status.

Revision ID: 0051
Revises: 0050
"""
from alembic import op

revision = '0051'
down_revision = '0050'
branch_labels = None
depends_on = None

STATUSES = "'MOI','DA_GIAO','DA_NHAN','DA_HEN','DANG_LAM','CHO_LINH_KIEN','TAM_DUNG','HOAN_THANH','DA_DONG','KHONG_LAM_DUOC','DA_HUY'"


def upgrade():
    # Existing users provide the required FK actor: first active director, or the
    # original creator if the installation currently has no active director.
    op.execute("""
        with previous as materialized (
            select t.id,to_jsonb(t) as old from service_tickets t
            where status='KHACH_HEN_SAU' for update
        ), cancelled as (
            update service_tickets t set status='DA_HUY',version=version+1
            from previous p where t.id=p.id
            returning t.id,t.assignee_id,t.created_by,p.old,to_jsonb(t) as new
        ), actors as (
            select c.*,coalesce((select id from users where active and 'director'=any(roles)
                order by id limit 1),c.created_by) as actor from cancelled c
        ), steps as (
            insert into service_steps(ticket_id,action,from_status,to_status,actor_id,assignee_id,note)
            select id,'cancel','KHACH_HEN_SAU','DA_HUY',actor,assignee_id,'Đổi từ Khách hẹn lần sau' from actors
        )
        insert into audit_log(user_id,action,entity,entity_id,before,after,reason)
        select actor,'service_ticket.cancel','service_ticket',id::text,old,new,'Đổi từ Khách hẹn lần sau' from actors
    """)
    op.drop_constraint('ck_service_tickets_status', 'service_tickets', type_='check')
    op.create_check_constraint('ck_service_tickets_status', 'service_tickets', 'status in ('+STATUSES+')')


def downgrade():
    op.drop_constraint('ck_service_tickets_status', 'service_tickets', type_='check')
    op.create_check_constraint('ck_service_tickets_status', 'service_tickets', 'status in ('+STATUSES+",'KHACH_HEN_SAU')")
