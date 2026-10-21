"""LG1: mark generated stock documents, without changing inventory or source statuses.

Revision ID: 0047
Revises: 0046
"""
from alembic import op
import sqlalchemy as sa

revision = '0047'
down_revision = '0046'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('stock_docs', sa.Column('source_kind', sa.Text(), nullable=True))
    # Use direct relationships first, before the generic dossier attached to LOI receipts.
    for kind, table, predicate in (
        ('PART_REQUEST', 'part_requests s', 's.stock_doc_id=d.id'),
        ('SERVICE_TICKET', 'service_lines s', 's.stock_doc_id=d.id'),
        ('TECHNICIAN_RETURN', 'technician_returns s', '(s.stock_doc_id=d.id or s.defect_stock_doc_id=d.id)'),
        ('RECOVERED_PART', 'service_recovered_parts s', 's.stock_doc_id=d.id'),
        ('PENDING_DISPOSITION', 'pending_dispositions s', 's.stock_doc_id=d.id'),
        ('PURCHASE_RECEIPT', 'purchase_receipts s', 's.stock_doc_id=d.id'),
        ('PURCHASE_RETURN', 'purchase_returns s', 's.stock_doc_id=d.id'),
        ('SALES_DELIVERY', 'sales_delivery_lines s', 's.stock_doc_id=d.id'),
        ('SALES_DELIVERY', 'sales_order_lines s', 's.stock_doc_id=d.id'),
        ('SALES_RETURN', 'sales_return_lines s', 's.stock_doc_id=d.id'),
    ):
        op.execute(sa.text(f"update stock_docs d set source_kind='{kind}' where source_kind is null "
                           f'and exists(select 1 from {table} where {predicate})'))
    # A recovered receipt can have its live FK cleared after a legitimate CXL1 void.
    # The append-only audit retains the exact stock_doc_id (no reason parsing).
    op.execute("""
        update stock_docs d set source_kind='RECOVERED_PART'
        where d.source_kind is null and d.kind='PN' and exists (
          select 1 from audit_log a where a.action='recovered_part.receive'
            and a.entity='service_recovered_part' and a.after->>'stock_doc_id'=d.id::text)
    """)
    # Legacy for_order_id was only a posting argument, not a stored stock movement FK.
    # Closed reservation rows retain the unique CK number in their generated reference.
    # Never classify a CK by stock_docs.reason: manual documents can use the same prose.
    op.execute("""
        update stock_docs d set source_kind='TECHNICIAN_DELIVERY'
        where d.source_kind is null and d.kind='CK' and exists (
          select 1 from stock_reservations r join sales_orders o on o.id=r.order_id
          where r.status='DA_NHA'
            and r.reason in ('Chuyển cho thợ ' || d.number, 'Thợ trả kho ' || d.number)
            and r.warehouse_code in (d.warehouse_code,d.to_warehouse_code)
            and exists(select 1 from stock_doc_lines l where l.doc_id=d.id and l.product_code=r.product_code))
    """)
    op.execute("""
        update stock_docs d set source_kind='PENDING_ITEM'
        where d.source_kind is null and d.kind='CK' and exists (
          select 1 from audit_log a where a.entity='pending_item' and a.action='pending_item.create'
            and a.after->>'stock_doc_id'=d.id::text and a.after->>'source'='CHUYEN_TAY'
            and a.after->>'group_code' in ('HANG_LOI','CHO_BH_NCC','THANH_LY'))
    """)
    # K5 has no source table: this is its existing fixed import marker/deduplication key.
    op.execute("""
        update stock_docs d set source_kind='STOCK_OPENING'
        where d.source_kind is null and d.kind='KK' and (
          (d.reason='Kiểm kê đầu kỳ' and (d.note='Nhập từ file' or d.note like 'Nhập từ file, phần %'))
          or exists(select 1 from audit_log a
            where a.entity='stock_doc' and a.entity_id=d.id::text and a.action='stock_doc.create'
              and a.after->>'kind'='KK' and a.after->>'reason'='Kiểm kê đầu kỳ'
              and (a.after->>'note'='Nhập từ file' or a.after->>'note' like 'Nhập từ file, phần %')))
    """)


def downgrade():
    op.drop_column('stock_docs', 'source_kind')
