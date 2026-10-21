"""LG1: provenance, public web/API guards and legacy backfill on PostgreSQL."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text

from app import (attachments, part_requests, pending_dispositions, pending_items, sales_orders,
    sales_returns, service_recovered_parts, service_tickets, stock_docs, stock_opening,
    stock_posting, stock_sources, technician_returns)
from app.errors import BusinessError
from app.models import (AuditLog, FundLedger, PartRequest, PendingDisposition, PendingItem, PendingItemMove,
    PurchaseReceipt, PurchaseReturn, SalesDelivery, SalesDeliveryLine, SalesOrder, SalesReturn, SalesReturnLine,
    ServiceLine, ServiceRecoveredPart, ServiceTicket, StockBalance, StockDoc, StockLedger,
    StockReservation, TechnicianReturn, TechnicianReturnLine, Warehouse)
from app.schemas import PartRequestIn, RecoveredPartReceive, SalesReturnIn, StockDocIn
from app.security import issue_token
from test_my_work import people, purchase_people, ticket, DAY
from test_technician_stock import held, transfer, charges, balance
from test_technician_deliveries import assigned, move
from test_purchase_receipts import placed, receive
from test_purchase_returns import returned
from test_stock_reservations import order
from test_recovered_parts import recover
from test_web import service_login, d5_image


KINDS = {
    'supply': 'PART_REQUEST', 'service': 'SERVICE_TICKET',
    'handover': 'TECHNICIAN_DELIVERY', 'order_return': 'TECHNICIAN_DELIVERY',
    'return_good': 'TECHNICIAN_RETURN', 'return_bad': 'TECHNICIAN_RETURN',
    'recovered': 'RECOVERED_PART', 'pending': 'PENDING_ITEM',
    'disposition': 'PENDING_DISPOSITION', 'purchase': 'PURCHASE_RECEIPT',
    'purchase_return': 'PURCHASE_RETURN', 'sale': 'SALES_DELIVERY',
    'sale_return': 'SALES_RETURN', 'opening': 'STOCK_OPENING',
}


def make_case(db, p, kind):
    if kind == 'supply':
        t = ticket(db, p, 'DANG_LAM')
        r = part_requests.create(db, p['technician'], t.id, PartRequestIn(version=t.version,
            product_code='TD-0001', qty=2, reason='Cần drum'))
        part_requests.supply(db, p['warehouse'], r.id, 'K58', r.version)
        ident = r.stock_doc_id
    elif kind == 'service':
        transfer(db, p, qty=2); t = ticket(db, p, 'DANG_LAM'); charges(db, p, t)
        attachments.add(db, p['technician'], 'service_ticket', str(t.id), 'SAU', 'sau.png', d5_image())
        service_tickets.transition(db, p['technician'], t.id, 'HOAN_THANH', t.version)
        ident = t.lines[0].stock_doc_id
    elif kind in ('handover', 'order_return'):
        o = assigned(db, p, qty=2); move(db, p, o, 2)
        if kind == 'order_return': move(db, p, o, 2, mode='return')
        ident = db.scalar(select(StockDoc.id).where(StockDoc.source_kind=='TECHNICIAN_DELIVERY').order_by(StockDoc.id.desc()).limit(1))
    elif kind in ('return_good', 'return_bad'):
        transfer(db, p, qty=2)
        if kind == 'return_bad':
            db.add(Warehouse(code='CXL', name='Kho Chờ Xử lý', kind='LOI')); db.commit()
        r = technician_returns.create(db, p['technician'], [dict(product_code='TD-0001', qty=2,
            to_warehouse_code='K58', condition='HU_HONG' if kind=='return_bad' else 'TOT', damage_reason='rơi vỡ')])[0]
        technician_returns.receive(db, p['warehouse'], r.id, r.version, {})
        ident = r.defect_stock_doc_id if kind=='return_bad' else r.stock_doc_id
    elif kind == 'recovered':
        db.add(Warehouse(code='REC', name='Kho Thu hồi', kind='LOI', recovered_only=True)); db.commit()
        t = ticket(db, p, 'DANG_LAM'); r = recover(db, p, t)
        service_recovered_parts.receive(db, p['warehouse'], r.id, RecoveredPartReceive(version=r.version, warehouse_code='REC'))
        ident = r.stock_doc_id
    elif kind in ('pending', 'disposition'):
        db.add(Warehouse(code='CXL', name='Kho Chờ Xử lý', kind='LOI')); db.commit()
        item = pending_items.transfer_in(db, p['warehouse'], 'K58', [dict(product_code='TD-0001', qty=2)],
            'HANG_LOI', 'Hàng cần xử lý', warehouse_code='CXL')[0]
        ident = item.stock_doc_id
        if kind == 'disposition':
            r = pending_dispositions.create(db, p['warehouse'], item.id, 'SUA_XONG', 1, item.version, to_warehouse_code='K58')
            ident = r.stock_doc_id
    elif kind in ('purchase', 'purchase_return'):
        o = placed(db, p); r = receive(db, p, o)[0]
        ident = r.stock_doc_id
        if kind == 'purchase_return': ident = returned(db, p, o, r).stock_doc_id
    elif kind in ('sale', 'sale_return'):
        o = order(db, p, qty=2); sales_orders.deliver(db, p['warehouse'], o.id, o.version)
        g = sales_orders.deliveries(db, o.id)[0]; ident = g.lines[0].stock_doc_id
        if kind == 'sale_return':
            r = sales_returns.create(db, p['warehouse'], o.id, SalesReturnIn(version=o.version,
                reason='Khách trả lại', lines=[dict(delivery_id=g.id, order_line_no=1, qty=1, condition='TOT', warehouse_code='K58')]))
            ident = r.lines[0].stock_doc_id
    elif kind == 'opening':
        db.add(Warehouse(code='NEW', name='Kho mới')); db.commit()
        doc = stock_opening.create_opening(db, p['warehouse'], 'NEW', DAY,
            [dict(MA_HANG='TD-0001', SO_LUONG='3', DON_GIA='1000')])[0]
        stock_posting.post(db, p['warehouse'], doc.id, doc.version); ident = doc.id
    else:
        raise AssertionError(kind)
    return db.get(StockDoc, ident)


def state(db):
    db.expire_all()
    return {model.__tablename__: list(db.execute(select(*model.__table__.columns).order_by(*model.__table__.primary_key.columns)))
        for model in (StockBalance, StockDoc, StockLedger, StockReservation, SalesOrder, ServiceTicket,
            PartRequest, TechnicianReturn, TechnicianReturnLine, ServiceRecoveredPart,
            PendingItem, PendingItemMove, PendingDisposition, PurchaseReceipt, PurchaseReturn,
            SalesDelivery, SalesDeliveryLine, SalesReturn, SalesReturnLine, ServiceLine, FundLedger, AuditLog)}


@pytest.mark.parametrize('kind', ['supply','service','handover','order_return','return_good','return_bad'])
@pytest.mark.parametrize('transport', ['api','web'])
def test_generated_documents_cannot_be_voided_publicly(client, db, held, kind, transport):
    p, _, _ = held; doc = make_case(db, p, kind)
    assert doc.source_kind == KINDS[kind]
    source = stock_sources.describe(db, doc)
    assert source and source['href'] != '/phieu-kho'
    csrf = service_login(client, p['warehouse'])
    page = client.get(f'/phieu-kho/{doc.id}')
    assert page.status_code == 200 and 'data-stock-source' in page.text
    assert source['href'] in page.text and 'Sinh từ' in page.text
    assert f'action="/phieu-kho/{doc.id}/void"' not in page.text
    if kind == 'supply':
        ident = source['href'].rsplit('#yc-',1)[1]
        assert f'id="yc-{ident}"' in client.get(source['href']).text
    before = state(db)
    if transport == 'api':
        response = client.post(f'/stock-docs/{doc.id}/void',
            headers={'Authorization': 'Bearer '+issue_token(p['warehouse'])},
            json=dict(reason='Hủy sai đường nguồn', version=doc.version))
        assert response.json()['error'] == 'SOURCE_LOCKED', response.text
        assert response.json()['details']['source_url'] == source['href']
    else:
        response = client.post(f'/phieu-kho/{doc.id}/void', data=dict(csrf=csrf,
            version=doc.version, reason='Hủy sai đường nguồn'))
        assert 'data-error-code="SOURCE_LOCKED"' in response.text
    assert source['label'] in response.text
    assert response.status_code == 409 and state(db) == before


@pytest.mark.parametrize('kind', list(KINDS))
def test_backfill_sources_and_upgrade_downgrade_upgrade(db, engine, held, kind):
    p, _, _ = held; doc = make_case(db, p, kind); ident = doc.id
    assert doc.source_kind == KINDS[kind]
    if kind in ('handover','order_return'):
        # Provenance must survive edits/removal of the prose on the CK itself.
        doc.reason = 'Nội dung khác không chứa số đơn'; db.commit()
    elif kind == 'opening':
        # Opening imports have no source FK; the immutable creation audit retains the marker.
        doc.reason = 'Đã sửa diễn giải sau nhập file'; doc.note = 'Đã kiểm tra'; db.commit()
    before = state(db); db.rollback()
    file = Path(__file__).parents[1]/'migrations/versions/0047_lg1_stock_doc_sources.py'
    spec = importlib.util.spec_from_file_location('lg1_migration', file)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    assert migration.down_revision == '0046'
    with engine.begin() as conn:
        migration.op = Operations(MigrationContext.configure(conn))
        for _ in range(2):
            migration.downgrade(); migration.upgrade()
            assert conn.scalar(text('select source_kind from stock_docs where id=:id'), dict(id=ident)) == KINDS[kind]
    assert state(db) == before  # No quantities, values, holds, statuses, versions or audit changed.


def test_manual_document_still_voids_and_provenance_is_not_client_input(client, db, held):
    p, _, _ = held; doc = transfer(db, p, qty=2)
    assert doc.source_kind is None
    csrf = service_login(client, p['warehouse'])
    assert f'action="/phieu-kho/{doc.id}/void"' in client.get(f'/phieu-kho/{doc.id}').text
    response = client.post(f'/stock-docs/{doc.id}/void', headers={'Authorization':'Bearer '+issue_token(p['warehouse'])},
        json=dict(reason='Chuyển nhầm phiếu tay', version=doc.version))
    assert response.status_code == 200 and balance(db,'K501') == 0 and balance(db,'K58') == 10
    response = client.post('/stock-docs', headers={'Authorization':'Bearer '+issue_token(p['warehouse'])},
        json=dict(kind='PN', warehouse_code='K58', supplier_code='NCC-0001', doc_date=DAY.isoformat(),
            lines=[dict(product_code='TD-0001',qty=1,unit_cost=100)], source_kind='RECOVERED_PART'))
    assert response.status_code == 422


@pytest.mark.parametrize('source_kind',['','UNKNOWN_SOURCE'])
def test_any_non_null_source_is_locked(db, held, source_kind):
    p, _, _ = held; doc = transfer(db,p,qty=2)
    doc.source_kind = source_kind; db.commit(); before = state(db)
    with pytest.raises(BusinessError) as error:
        stock_posting.void(db,p['warehouse'],doc.id,'Không được bỏ qua nguồn lạ',doc.version)
    assert error.value.code == 'SOURCE_LOCKED' and error.value.status == 409
    assert state(db) == before


def test_service_reopen_uses_internal_void(db, held):
    p, own, _ = held; doc = make_case(db, p, 'service')
    t = db.scalar(select(ServiceTicket).where(ServiceTicket.status=='HOAN_THANH'))
    assert balance(db,own.code) == 1
    service_tickets.transition(db,p['dispatcher'],t.id,'DANG_LAM',t.version,'Sửa lại theo yêu cầu khách')
    assert db.get(StockDoc,doc.id,populate_existing=True).status == 'DA_HUY'
    assert balance(db,own.code) == 2 and t.status == 'DANG_LAM'


@pytest.mark.parametrize('processed',['','split','used'])
@pytest.mark.parametrize('transport',['api','web'])
def test_recovered_exception_and_cxl_atomicity(client, db, held, processed, transport):
    p, _, _ = held; doc = make_case(db,p,'recovered')
    item = db.scalar(select(PendingItem).where(PendingItem.stock_doc_id==doc.id))
    recovered = db.scalar(select(ServiceRecoveredPart).where(ServiceRecoveredPart.stock_doc_id==doc.id))
    if processed == 'split': pending_items.split(db,p['warehouse'],item.id,'0.5','HANG_LOI',item.version)
    elif processed == 'used': pending_dispositions.create(db,p['director'],item.id,'HUY','0.5',item.version,reason='Linh kiện thu hồi hỏng')
    csrf = service_login(client,p['warehouse']); before=state(db)
    if transport=='api':
        response=client.post(f'/stock-docs/{doc.id}/void', headers={'Authorization':'Bearer '+issue_token(p['warehouse'])},
            json=dict(reason='Nhận nhầm linh kiện thu hồi',version=doc.version))
    else:
        response=client.post(f'/phieu-kho/{doc.id}/void',data=dict(csrf=csrf,reason='Nhận nhầm linh kiện thu hồi',version=doc.version),follow_redirects=False)
    if processed:
        assert response.status_code==409 and 'CXL_DA_XU_LY' in response.text and state(db)==before
    else:
        assert response.status_code==(200 if transport=='api' else 303)
        db.expire_all()
        assert doc.status=='DA_HUY' and balance(db,'REC')==0
        assert item.status=='DA_HUY' and item.qty_left==0
        assert recovered.status=='CHO_NOP' and recovered.holder_id==p['technician'].id and recovered.stock_doc_id is None


def test_recovered_void_failure_rolls_back_cxl_stock_and_holder(db, held, monkeypatch):
    p, _, _ = held; doc = make_case(db,p,'recovered'); before = state(db)
    def fail(*args, **kwargs):
        raise RuntimeError('Lỗi đảo sổ sau khi đóng hồ sơ CXL')
    monkeypatch.setattr(stock_posting, '_apply', fail)
    with pytest.raises(RuntimeError):
        stock_posting.void(db,p['warehouse'],doc.id,'Nhận nhầm linh kiện thu hồi',doc.version)
    assert state(db) == before


def test_backfill_voided_recovered_receipt_from_immutable_audit(db, engine, held):
    p, _, _ = held; doc = make_case(db,p,'recovered'); ident = doc.id
    stock_posting.void(db,p['warehouse'],doc.id,'Nhận nhầm linh kiện thu hồi',doc.version)
    assert db.scalar(select(ServiceRecoveredPart).where(ServiceRecoveredPart.stock_doc_id==ident)) is None
    before = state(db); db.rollback()
    from alembic import command
    from alembic.config import Config
    cfg=Config('alembic.ini');cfg.attributes['database_url']=str(engine.url)
    command.downgrade(cfg,'0046');command.upgrade(cfg,'head')
    assert db.get(StockDoc,ident,populate_existing=True).source_kind == 'RECOVERED_PART'
    assert state(db) == before


def test_manual_lookalike_and_manual_cxl_receipt_are_not_backfilled(db, engine, held):
    p, own, _ = held; handover=make_case(db,p,'handover')
    manual=transfer(db,p,qty=1); manual.reason=handover.reason
    db.add(Warehouse(code='CXL',name='Kho Chờ Xử lý',kind='LOI'));db.commit()
    manual_cxl=stock_docs.create(db,p['warehouse'],StockDocIn(kind='CK',warehouse_code='K58',to_warehouse_code='CXL',
        doc_date=DAY,reason='Chuyển tay theo phiếu kho',lines=[dict(product_code='TD-0001',qty=1)]))
    stock_posting.post(db,p['warehouse'],manual_cxl.id,manual_cxl.version)
    item=db.scalar(select(PendingItem).where(PendingItem.stock_doc_id==manual_cxl.id))
    pending_items.set_group(db,p['warehouse'],item.id,'HANG_LOI',item.version,reason='Đổi nhóm sau khi nhập')
    ids=[manual.id,manual_cxl.id];db.rollback()
    from alembic import command
    from alembic.config import Config
    cfg=Config('alembic.ini');cfg.attributes['database_url']=str(engine.url)
    command.downgrade(cfg,'0046');command.upgrade(cfg,'head')
    assert all(db.get(StockDoc,ident,populate_existing=True).source_kind is None for ident in ids)
