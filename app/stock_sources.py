"""LG1: provenance supplied only by trusted business callers, never by form/API input."""
from sqlalchemy import text

from .errors import BusinessError

# The number on a closed reservation is an immutable reference to a unique CK.
# There is no stock_moves table or persisted for_order_id in the legacy schema.
HANDOVER_REFERENCE = """
    select o.id, o.number from stock_reservations r
    join sales_orders o on o.id=r.order_id
    where r.status='DA_NHA'
      and r.reason in ('Chuyển cho thợ ' || :number, 'Thợ trả kho ' || :number)
      and r.warehouse_code in (:warehouse, :destination)
    order by r.id limit 1
"""

LOOKUPS = {
    'PART_REQUEST': ('yêu cầu linh kiện', '/yeu-cau-linh-kien',
        'select id, number from part_requests where stock_doc_id=:id order by id limit 1',
        '/yeu-cau-linh-kien?status=DA_CAP#yc-{id}'),
    'SERVICE_TICKET': ('phiếu dịch vụ', '/dich-vu',
        'select t.id, t.number from service_tickets t join service_lines l on l.ticket_id=t.id where l.stock_doc_id=:id order by t.id limit 1',
        '/dich-vu/{id}'),
    'TECHNICIAN_DELIVERY': ('bàn giao / trả kho đơn giao thợ', '/don-ban',
        HANDOVER_REFERENCE, '/giao-don/{id}'),
    'TECHNICIAN_RETURN': ('yêu cầu thợ trả kho', '/viec-cua-toi?nhom=technician_returns',
        'select id, number from technician_returns where stock_doc_id=:id or defect_stock_doc_id=:id order by id limit 1',
        '/tho-tra-hang/{id}'),
    'RECOVERED_PART': ('linh kiện thu hồi', '/linh-kien-thu-hoi',
        'select t.id, t.number from service_recovered_parts r join service_tickets t on t.id=r.ticket_id where r.stock_doc_id=:id order by r.id limit 1',
        '/dich-vu/{id}'),
    'PENDING_ITEM': ('hồ sơ Chờ xử lý', '/cho-xu-ly',
        'select id, number from pending_items where stock_doc_id=:id order by id limit 1',
        '/cho-xu-ly/{id}'),
    'PENDING_DISPOSITION': ('phiếu xử lý Chờ xử lý', '/cho-xu-ly',
        "select case when x.kind='THANH_LY' then g.id else x.item_id end as id, "
        "case when x.kind='THANH_LY' then g.number else x.number end as number, x.kind "
        'from pending_dispositions x left join sales_deliveries g on g.id=x.delivery_id '
        'where x.stock_doc_id=:id order by x.id limit 1',
        '/cho-xu-ly/{id}'),
    'PURCHASE_RECEIPT': ('lần nhận đơn mua', '/don-mua',
        'select o.id, o.number from purchase_orders o join purchase_receipts r on r.order_id=o.id where r.stock_doc_id=:id order by r.id limit 1',
        '/don-mua/{id}/nhan#nhan-hang'),
    'PURCHASE_RETURN': ('phiếu trả NCC trên đơn mua', '/don-mua',
        'select order_id as id, number from purchase_returns where stock_doc_id=:id order by id limit 1',
        '/don-mua/{id}'),
    'SALES_DELIVERY': ('lần giao đơn bán', '/don-ban',
        'select g.id, g.number from sales_deliveries g join sales_delivery_lines l on l.delivery_id=g.id where l.stock_doc_id=:id order by g.id limit 1',
        '/don-ban/giao/{id}'),
    'SALES_RETURN': ('phiếu khách trả hàng', '/don-ban',
        'select r.id, r.number from sales_returns r join sales_return_lines l on l.return_id=r.id where l.stock_doc_id=:id order by r.id limit 1',
        '/tra-hang/{id}'),
    'STOCK_OPENING': ('tồn đầu kỳ', '/ton-dau-ky', None, None),
}


def describe(session, doc):
    if doc.source_kind is None:
        return None
    label, href, query, detail = LOOKUPS.get(doc.source_kind,
        ('nghiệp vụ nguồn ' + doc.source_kind, '/phieu-kho', None, None))
    if query:
        row = session.execute(text(query), dict(id=doc.id, number=doc.number,
            warehouse=doc.warehouse_code, destination=doc.to_warehouse_code)).mappings().first()
        if row:
            if doc.source_kind == 'PENDING_DISPOSITION' and row['kind'] == 'THANH_LY':
                label, detail = 'lần giao thanh lý', '/don-ban/giao/{id}'
            href = detail.format(id=row['id'])
            if row['number']:
                label += ' ' + row['number']
    return dict(label=label, href=href)


def check_public_void(session, doc, recovered):
    # CXL1 permits this PN only through the existing locked recovery transaction.
    if doc.source_kind is not None and not (doc.source_kind == 'RECOVERED_PART' and doc.kind == 'PN' and recovered is not None):
        source = describe(session, doc)
        raise BusinessError('SOURCE_LOCKED',
            'Phiếu này sinh từ ' + source['label'] + ', hãy xử lý tại đó.',
            dict(source_kind=doc.source_kind, source_url=source['href']), status=409)
