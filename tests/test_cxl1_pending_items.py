"""CXL1: test giao dịch trên PostgreSQL; CI chạy toàn bộ theo yêu cầu thẻ."""
import importlib.util
from datetime import UTC,datetime,timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func,select,text
from sqlalchemy.exc import DBAPIError

from app import alerts,attachments,dashboard,pending_items as svc,sales_returns,service_recovered_parts,stock_docs,stock_posting,technician_returns
from app.config import get_settings
from app.errors import BusinessError
from app.models import AuditLog,FundLedger,PendingItem,StockBalance,StockDoc,StockLedger,Warehouse
from app.schemas import RecoveredPartReceive,StockDocIn
from test_my_work import people,purchase_people,ticket
from test_recovered_parts import recover
from test_sales_returns import sold,receive
from test_stock_reservations import stock
from test_technician_stock import held,transfer
from test_ux1c_hen import supplied
from test_ux1d_bao_hu import closed
from test_web import service_login,d5_image


def assert_pending_invariant(db):
    """Dùng chung ở cuối từng luồng, gồm các kho LOI không còn hàng."""
    stock_qty={
        (w,p):q for w,p,q in db.execute(select(StockBalance.warehouse_code,StockBalance.product_code,StockBalance.qty).join(Warehouse).where(Warehouse.kind=='LOI'))}
    pending_qty={(w,p):q for w,p,q in db.execute(select(PendingItem.warehouse_code,PendingItem.product_code,func.sum(PendingItem.qty_left)).where(PendingItem.status=='DANG_CHO').group_by(PendingItem.warehouse_code,PendingItem.product_code))}
    assert all(stock_qty.get(key,0)==pending_qty.get(key,0) for key in stock_qty.keys()|pending_qty.keys())


def defect(db,recovered_only=False):
    row=Warehouse(code='CXL',name='Kho Chờ Xử lý',kind='LOI',recovered_only=recovered_only);db.add(row);db.commit();return row


def error(code,fn,*args,**kwargs):
    with pytest.raises(BusinessError) as exc:fn(*args,**kwargs)
    assert exc.value.code==code
    return exc.value


def manual(db,p,qty=3,group='HANG_LOI'):
    return svc.transfer_in(db,p['warehouse'],'K58',[dict(product_code='TD-0001',qty=qty)],group,'Trống in bị trầy')[0]


def manual_receipt(db,p,qty=3):
    # LG1 locks transfer_in's generated CK; these tests exercise manual receipts.
    doc=stock_docs.create(db,p['warehouse'],StockDocIn(kind='CK',warehouse_code='K58',to_warehouse_code='CXL',
        doc_date=datetime.now(UTC).date(),reason='Chuyển tay vào CXL',lines=[dict(product_code='TD-0001',qty=qty)]))
    stock_posting.post(db,p['warehouse'],doc.id,doc.version)
    assert doc.source_kind is None
    return db.scalar(select(PendingItem).where(PendingItem.stock_doc_id==doc.id))


def counts(db):return tuple(db.query(model).count() for model in (StockDoc,StockLedger,PendingItem,AuditLog,FundLedger))


def test_migration_legacy_each_pair_and_down_up(db,people):
    defect(db)
    spec=importlib.util.spec_from_file_location('cxl1_migration',Path(__file__).parents[1]/'migrations/versions/0038_cxl1_ho_so_cho_xu_ly.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    newer_spec=importlib.util.spec_from_file_location('cxl2a_migration',Path(__file__).parents[1]/'migrations/versions/0039_cxl2a_xu_ly_noi_bo.py')
    newer=importlib.util.module_from_spec(newer_spec);newer_spec.loader.exec_module(newer)
    latest_spec=importlib.util.spec_from_file_location('cxl2b_migration',Path(__file__).parents[1]/'migrations/versions/0040_cxl2b_tra_ncc_thanh_ly.py')
    latest=importlib.util.module_from_spec(latest_spec);latest_spec.loader.exec_module(latest)
    with Operations.context(MigrationContext.configure(db.connection())):
        latest.downgrade()
        newer.downgrade()
        migration.downgrade()
        db.execute(text("insert into stock_balances(warehouse_code,product_code,qty,value,avg_cost) values ('CXL','TD-0001',4,400,100),('CXL','TD-0002',2,600,300)"))
        migration.upgrade()
        rows=db.execute(text('select product_code,qty_in,qty_left,unit_cost,source,group_code from pending_items order by product_code')).all()
        assert [tuple(r) for r in rows]==[('TD-0001',4,4,100,'TON_CU','CHUA_PHAN_LOAI'),('TD-0002',2,2,300,'TON_CU','CHUA_PHAN_LOAI')]
        migration.downgrade();migration.upgrade();newer.upgrade();latest.upgrade()
    db.commit();assert db.query(PendingItem).count()==2;assert_pending_invariant(db)


def test_recovered_receipt_zero_cost_and_source(db,people):
    p=people;defect(db,recovered_only=True);t=ticket(db,p,'DANG_LAM');row=recover(db,p,t,qty=2)
    service_recovered_parts.receive(db,p['warehouse'],row.id,RecoveredPartReceive(version=row.version))
    item=db.scalars(select(PendingItem)).one();doc=db.get(StockDoc,row.stock_doc_id)
    assert (item.qty_in,item.unit_cost,item.group_code,item.source,item.source_ref,item.entered_at)==(2,0,'THU_HOI','THU_HOI',t.number,doc.posted_at)
    assert_pending_invariant(db)


def test_damaged_technician_return_uses_posted_cost_reason(db,supplied):
    defect(db);p,own,t,row=closed(db,supplied)
    technician_returns.receive(db,p['warehouse'],row.id,row.version,{})
    item=db.scalars(select(PendingItem)).one();ledger=db.scalar(select(StockLedger).where(StockLedger.doc_id==row.defect_stock_doc_id,StockLedger.qty_in>0))
    assert (item.source,item.group_code,item.source_ref,item.reason,item.unit_cost)==('THO_BAO_HU','HANG_LOI',row.number,'rơi vỡ',ledger.unit_cost)
    assert_pending_invariant(db)


def test_customer_defect_return_source_and_cost(db,sold):
    p,o,gh=sold;defect(db);row=receive(db,p,o,gh,condition='LOI')
    item=db.scalars(select(PendingItem)).one();assert (item.source,item.source_ref,item.group_code,item.unit_cost)==('KHACH_TRA',row.number,'HANG_LOI',100)
    assert_pending_invariant(db)


def test_transfer_cost_total_stock_and_required_reason(db,people):
    p=people;defect(db);stock(db,p,10);before=counts(db)
    error('REASON_REQUIRED',svc.transfer_in,db,p['warehouse'],'K58',[dict(product_code='TD-0001',qty=3)],'THANH_LY',' ')
    assert counts(db)==before
    item=manual(db,p,3,'THANH_LY');assert item.unit_cost==100 and item.source=='CHUYEN_TAY'
    assert db.get(StockBalance,('K58','TD-0001')).qty==7 and db.get(StockBalance,('CXL','TD-0001')).qty==3
    assert db.scalar(select(func.sum(StockBalance.qty)).where(StockBalance.product_code=='TD-0001'))==10
    assert_pending_invariant(db)


def test_transfer_reservations_and_bad_group_no_writes(db,sold):
    from app import sales_orders
    from app.schemas import SalesOrderIn
    p,o,gh=sold;defect(db);stock(db,p,5)
    order=sales_orders.create(db,p['sales'],SalesOrderIn(customer_code=o.customer_code,lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=4)]))
    sales_orders.confirm(db,p['sales'],order.id,order.version);before=counts(db)
    error('RESERVED_STOCK',manual,db,p,2)
    assert counts(db)==before and db.get(StockBalance,('K58','TD-0001')).qty==5
    error('BAD_GROUP',manual,db,p,1,'THU_HOI');assert counts(db)==before;assert_pending_invariant(db)


@pytest.mark.parametrize('role',['technician','sales','finance'])
def test_transfer_permissions_403(db,people,client,role):
    defect(db);p=people
    assert error('FORBIDDEN',svc.transfer_in,db,p[role],'K58',[dict(product_code='TD-0001',qty=1)],'HANG_LOI','Hỏng').status==403
    csrf=service_login(client,p[role])
    assert client.post('/cho-xu-ly/chuyen',data=dict(csrf=csrf,from_warehouse_code='K58',warehouse_code='CXL',product_code='TD-0001',qty='1',group_code='HANG_LOI',reason='Hỏng')).status_code==403
    assert_pending_invariant(db)


@pytest.mark.parametrize('kind',['PX','CK'])
def test_free_outbound_blocked_create_and_post_legacy_draft(db,people,kind):
    from app.models import StockDocLine
    p=people;defect(db);before=counts(db)
    body=StockDocIn(kind=kind,warehouse_code='CXL',to_warehouse_code='K58' if kind=='CK' else None,doc_date=datetime.now(UTC).date(),lines=[dict(product_code='TD-0001',qty=1)])
    error('CXL_DUNG_LUONG_RIENG',stock_docs.create,db,p['warehouse'],body);assert counts(db)==before
    doc=StockDoc(number='OLD-'+kind,kind=kind,warehouse_code='CXL',to_warehouse_code=body.to_warehouse_code,doc_date=body.doc_date,
        created_by=p['warehouse'].id,lines=[StockDocLine(line_no=1,product_code='TD-0001',qty=1)])
    db.add(doc);db.commit();before=counts(db)
    error('CXL_DUNG_LUONG_RIENG',stock_posting.post,db,p['warehouse'],doc.id,doc.version);assert counts(db)==before and doc.status=='NHAP';assert_pending_invariant(db)


def inventory(db,p,qty,actor='director'):
    doc=stock_docs.create(db,p['warehouse'],StockDocIn(kind='KK',warehouse_code='CXL',doc_date=datetime.now(UTC).date(),reason='Kiểm kê cuối tháng',lines=[dict(product_code='TD-0001',qty=qty,unit_cost=100)]))
    return stock_posting.post(db,p[actor],doc.id,doc.version)


def test_inventory_fifo_shortage_and_surplus(db,people):
    p=people;defect(db);stock(db,p,10);first=manual(db,p,3);second=manual(db,p,2)
    first.entered_at=datetime.now(UTC)-timedelta(days=40);second.entered_at=datetime.now(UTC)-timedelta(days=10);db.commit()
    inventory(db,p,1);assert (first.qty_left,first.status,second.qty_left)==(0,'XONG',1)
    assert db.scalar(select(AuditLog.reason).where(AuditLog.entity_id==str(first.id),AuditLog.action=='pending_item.inventory_shortage'))=='Kiểm kê thiếu'
    assert_pending_invariant(db)
    inventory(db,p,4);added=db.scalar(select(PendingItem).where(PendingItem.source=='KIEM_KE'))
    assert added.qty_in==3 and added.group_code=='CHUA_PHAN_LOAI' and added.unit_cost==100;assert_pending_invariant(db)


def test_inventory_only_director_and_no_changes(db,people):
    p=people;defect(db);before=counts(db)
    error('FORBIDDEN',inventory,db,p,1,'warehouse')
    assert db.query(StockLedger).count()==before[1] and db.query(PendingItem).count()==0;assert_pending_invariant(db)


def test_split_preserves_source_date_cost_and_prevents_void(db,people):
    p=people;defect(db);stock(db,p,10);item=manual_receipt(db,p,10);doc=db.get(StockDoc,item.stock_doc_id)
    child=svc.split(db,p['warehouse'],item.id,3,'CHO_BH_NCC',item.version)
    assert item.qty_left==7 and child.qty_left==3
    assert (child.source,child.source_ref,child.entered_at,child.unit_cost)==(item.source,item.source_ref,item.entered_at,item.unit_cost)
    before=counts(db);error('CXL_DA_XU_LY',stock_posting.void,db,p['warehouse'],doc.id,'Nhập nhầm',doc.version)
    assert counts(db)==before and doc.status=='DA_GHI';assert_pending_invariant(db)
    error('BAD_QTY',svc.split,db,p['warehouse'],item.id,8,'THANH_LY',item.version);assert_pending_invariant(db)


def test_group_stale_409_and_closed_rejected(db,people):
    p=people;defect(db);stock(db,p,3);item=manual(db,p);version=item.version
    svc.set_group(db,p['warehouse'],item.id,'CHO_BH_NCC',version,'Chờ hỏi NCC')
    assert error('VERSION_CONFLICT',svc.set_group,db,p['warehouse'],item.id,'THANH_LY',version).status==409
    inventory(db,p,0);error('BAD_STATUS',svc.set_group,db,p['warehouse'],item.id,'THANH_LY',item.version);assert_pending_invariant(db)


@pytest.mark.parametrize('flow',['recovered','technician','customer','manual'])
def test_failure_open_rolls_back_stock_and_source(db,people,supplied,sold,monkeypatch,flow):
    # One fixture graph; all source rows exist before the failed posting.
    p=people;defect(db,recovered_only=flow=='recovered')
    if flow=='recovered':
        t=ticket(db,p,'DANG_LAM');row=recover(db,p,t)
        action=lambda:service_recovered_parts.receive(db,p['warehouse'],row.id,RecoveredPartReceive(version=row.version))
    elif flow=='technician':
        p,own,t,row=closed(db,supplied);action=lambda:technician_returns.receive(db,p['warehouse'],row.id,row.version,{})
    elif flow=='customer':
        p,o,gh=sold;action=lambda:receive(db,p,o,gh,condition='LOI')
    else:
        stock(db,p,10);action=lambda:manual(db,p)
    receivable=o.receivable if flow=='customer' else None
    before=counts(db);balances=db.execute(text('select * from stock_balances order by warehouse_code,product_code')).all()
    def broken(*args,**kwargs):raise RuntimeError('lỗi mở hồ sơ sau ghi sổ')
    monkeypatch.setattr(svc,'open_in_tx',broken)
    with pytest.raises(RuntimeError):action()
    assert counts(db)==before and db.execute(text('select * from stock_balances order by warehouse_code,product_code')).all()==balances
    if flow=='customer':assert o.receivable==receivable
    elif flow=='recovered':assert row.status=='CHO_NOP' and row.stock_doc_id is None
    elif flow=='technician':assert row.status=='CHO_NHAN' and row.stock_doc_id is None and row.defect_stock_doc_id is None
    assert_pending_invariant(db)


def test_void_untouched_receipt_closes_pending_cancelled(db,people):
    p=people;defect(db);stock(db,p,3);item=manual_receipt(db,p);doc=db.get(StockDoc,item.stock_doc_id)
    stock_posting.void(db,p['warehouse'],doc.id,'Nhập nhầm',doc.version)
    assert item.status=='DA_HUY' and item.qty_left==0 and doc.status=='DA_HUY'
    assert db.scalar(select(AuditLog.reason).where(AuditLog.action=='pending_item.cancel'))=='Nhập nhầm';assert_pending_invariant(db)


def test_alert_days_boundary_visibility_and_dashboard(db,people,monkeypatch,client):
    p=people;defect(db);stock(db,p,3);item=manual(db,p);now=datetime.now(UTC);item.entered_at=now-timedelta(days=30);db.commit()
    monkeypatch.setattr(get_settings(),'pending_alert_days',30)
    assert not alerts.pending_notices(db,p['warehouse'],now=now)
    found=alerts.pending_notices(db,p['warehouse'],now=now+timedelta(seconds=1));assert len(found)==1 and found[0]['kind']=='CHO_XU_LY_QUA_HAN' and found[0]['link']=='/cho-xu-ly?min_days=30'
    assert found.counts['CHO_XU_LY_QUA_HAN']==1
    assert not alerts.pending_notices(db,p['technician'],now=now+timedelta(days=1))
    assert 'CHO_XU_LY_QUA_HAN' not in alerts.collect(db,p['finance']).counts
    assert dashboard.summary(db,p['director'])['pending_items']['value']==300
    item.entered_at=now-timedelta(days=31);db.commit();service_login(client,p['warehouse'])
    assert 'Hàng chờ xử lý quá hạn' in client.get('/viec-cua-toi').text
    service_login(client,p['technician']);assert 'CHO_XU_LY_QUA_HAN' not in client.get('/tho').text
    assert_pending_invariant(db)


def test_search_accent_group_and_web_permissions_hidden_cost(db,people,client):
    p=people;defect(db);stock(db,p,3);item=manual(db,p)
    assert svc.search(db,p['dispatcher'],q=item.number)[0]['item'].id==item.id
    assert svc.search(db,p['warehouse'],q='may in')
    for role in ('technician','sales'):
        service_login(client,p[role]);assert client.get('/cho-xu-ly').status_code==403
    for role in ('warehouse','finance','dispatcher','director'):
        service_login(client,p[role]);r=client.get('/cho-xu-ly');assert r.status_code==200 and item.number in r.text
        detail=client.get(f'/cho-xu-ly/{item.id}');assert detail.status_code==200
        assert ('Giá vốn đơn vị' in detail.text)==(role!='dispatcher')
    assert_pending_invariant(db)


def test_raw_sql_mismatch_rejected_at_commit(db,people):
    p=people;defect(db);stock(db,p,3);item=manual(db,p)
    with pytest.raises(DBAPIError):
        db.execute(text('update pending_items set qty_left=qty_left-1 where id=:id'),dict(id=item.id));db.commit()
    db.rollback();assert_pending_invariant(db)


def test_pending_photos_readers_and_writer_permissions(db,people):
    p=people;defect(db);stock(db,p,3);item=manual(db,p)
    photo=attachments.add(db,p['warehouse'],'pending_item',str(item.id),'KHAC','loi.png',d5_image())
    assert attachments.list_for(db,p['dispatcher'],'pending_item',str(item.id))[0].id==photo.id
    for role in ('technician','sales','finance','dispatcher'):
        error('FORBIDDEN',attachments.add,db,p[role],'pending_item',str(item.id),'KHAC','loi.png',d5_image())
        error('FORBIDDEN',attachments.hide,db,p[role],photo.id,'Không dùng ảnh',photo.version)
    assert_pending_invariant(db)


def test_partial_inventory_prevents_receipt_void(db,people):
    p=people;defect(db);stock(db,p,3);item=manual_receipt(db,p);doc=db.get(StockDoc,item.stock_doc_id)
    inventory(db,p,2);before=counts(db)
    error('CXL_DA_XU_LY',stock_posting.void,db,p['warehouse'],doc.id,'Nhập nhầm',doc.version)
    assert item.qty_left==2 and counts(db)==before and doc.status=='DA_GHI';assert_pending_invariant(db)


def test_manual_pn_and_ck_open_unclassified_lots(db,people):
    p=people;defect(db)
    pn=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PN',warehouse_code='CXL',supplier_code='NCC-0001',doc_date=datetime.now(UTC).date(),lines=[dict(product_code='TD-0001',qty=2,unit_cost=100),dict(product_code='TD-0002',qty=1,unit_cost=200)]))
    stock_posting.post(db,p['warehouse'],pn.id,pn.version)
    assert db.query(PendingItem).count()==2 and {r.group_code for r in db.scalars(select(PendingItem))}=={'CHUA_PHAN_LOAI'};assert_pending_invariant(db)
    stock(db,p,3)
    ck=stock_docs.create(db,p['warehouse'],StockDocIn(kind='CK',warehouse_code='K58',to_warehouse_code='CXL',doc_date=datetime.now(UTC).date(),lines=[dict(product_code='TD-0001',qty=1)]))
    stock_posting.post(db,p['warehouse'],ck.id,ck.version)
    assert db.query(PendingItem).count()==3;assert_pending_invariant(db)


def test_multiple_pending_warehouses_requires_destination(db,people):
    p=people;defect(db);db.add(Warehouse(code='CXL2',name='Chờ xử lý 2',kind='LOI'));db.commit();stock(db,p,3);before=counts(db)
    error('WAREHOUSE_REQUIRED',manual,db,p,1);assert counts(db)==before
    rows=svc.transfer_in(db,p['warehouse'],'K58',[dict(product_code='TD-0001',qty=1)],'HANG_LOI','Hỏng',warehouse_code='CXL2')
    assert rows[0].warehouse_code=='CXL2';assert_pending_invariant(db)


def test_unselected_product_is_vietnamese_form_error_no_stock(db,people,client):
    p=people;defect(db);stock(db,p,3);before=counts(db);csrf=service_login(client,p['warehouse'])
    response=client.post('/cho-xu-ly/chuyen',data=dict(csrf=csrf,from_warehouse_code='K58',warehouse_code='CXL',product_query='máy',product_code='',qty='1',group_code='HANG_LOI',reason='Hỏng'))
    assert response.status_code==400 and 'BAD_FORM' in response.text and 'chọn hàng' in response.text
    assert counts(db)==before;assert_pending_invariant(db)
