"""CXL2a: CI chạy pytest; không nới kiểm tra tiền/kho/quyền/rollback cũ."""
from datetime import UTC,datetime,timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select,func
from app import (alerts,attachments,finance_reports,my_work,pending_dispositions as svc,pending_items,stock_docs,stock_posting)
from app.models import (CashDoc,FundLedger,PendingDisposition,PendingItem,PendingItemMove,StockBalance,StockDoc,StockDocLine,StockLedger,Warehouse)
from app.schemas import StockDocIn
from app.config import get_settings
from test_my_work import people,purchase_people
from test_cxl1_pending_items import assert_pending_invariant,defect,manual,error
from test_stock_reservations import stock
from test_web import service_login,d5_image


@pytest.fixture
def lot(db,people):
    p=people;defect(db);db.add(Warehouse(code='NCC',name='Đang gửi NCC',kind='LOI',supplier_hold=True));db.commit();stock(db,p,10)
    item=manual(db,p,10);return p,item


def make(db,p,item,kind,qty=None,actor='warehouse',**kw):
    return svc.create(db,p[actor],item.id,kind,item.qty_left if qty is None else qty,item.version,**kw)


def child(db,row):return db.scalar(select(PendingItem).where(PendingItem.stock_doc_id==row.stock_doc_id))


def counts(db):return tuple(db.query(model).count() for model in (StockDoc,StockLedger,PendingDisposition,PendingItemMove,PendingItem,CashDoc,FundLedger))


def test_repair_one_ck_moves_stock_cost_and_closed_item(db,lot):
    p,item=lot;before=db.scalar(select(func.sum(StockBalance.qty)))
    row=make(db,p,item,'SUA_XONG',to_warehouse_code='K58');doc=db.get(StockDoc,row.stock_doc_id)
    assert row.status=='DA_GHI' and doc.kind=='CK' and doc.status=='DA_GHI' and item.status=='XONG' and item.qty_left==0
    assert db.scalar(select(func.sum(StockBalance.qty)))==before and db.get(StockBalance,('K58','TD-0001')).qty==10
    move=db.scalars(select(PendingItemMove)).one();assert (move.item_id,move.qty,move.stock_doc_id,move.disposition_id)==(item.id,10,doc.id,row.id)
    assert row.cost_value==1000 and db.query(CashDoc).count()==db.query(FundLedger).count()==0;assert_pending_invariant(db)


def test_send_ncc_child_preserves_origin_date_and_sent_stamp(db,lot):
    p,item=lot;original_date=datetime.now(UTC)-timedelta(days=90);item.entered_at=original_date;db.commit()
    error('SUPPLIER_REQUIRED',make,db,p,item,'GUI_NCC',3,to_warehouse_code='NCC')
    row=make(db,p,item,'GUI_NCC',3,to_warehouse_code='NCC',supplier_code='NCC-0001');new=child(db,row);doc=db.get(StockDoc,row.stock_doc_id)
    assert item.qty_left==7 and new.qty_left==3 and new.warehouse_code=='NCC' and new.group_code=='CHO_BH_NCC'
    assert (new.source,new.source_ref,new.entered_at,new.supplier_code,new.sent_at)==(item.source,item.source_ref,original_date,'NCC-0001',doc.posted_at)
    assert not next(entry for entry in pending_items.search(db,p['warehouse'],view='ncc') if entry['item'].id==new.id)['overdue']
    assert_pending_invariant(db)


@pytest.mark.parametrize('kind,destination',[('NCC_TRA_VE','K58'),('NCC_KHONG_NHAN','CXL')])
def test_supplier_return_or_rejection(db,lot,kind,destination):
    p,item=lot;sent=make(db,p,item,'GUI_NCC',4,to_warehouse_code='NCC',supplier_code='NCC-0001');new=child(db,sent)
    row=make(db,p,new,kind,to_warehouse_code=destination,reason='NCC không nhận bảo hành')
    assert new.qty_left==0 and new.status=='XONG' and db.get(StockBalance,('NCC','TD-0001')).qty==0
    if kind=='NCC_KHONG_NHAN':assert child(db,row).group_code=='HANG_LOI' and child(db,row).qty_left==4
    else:assert db.get(StockBalance,('K58','TD-0001')).qty==4
    assert_pending_invariant(db)


def test_destruction_request_approval_and_reject_no_cash(db,lot):
    p,item=lot;before=counts(db);row=make(db,p,item,'HUY',3,reason='Hỏng không sửa được')
    assert row.status=='CHO_DUYET' and row.stock_doc_id is None and item.qty_left==10 and counts(db)[:2]==before[:2]
    for role in ('warehouse','finance','sales','technician'):assert error('FORBIDDEN',svc.approve,db,p[role],row.id,row.version).status==403
    svc.approve(db,p['director'],row.id,row.version);doc=db.get(StockDoc,row.stock_doc_id)
    assert doc.kind=='PX' and doc.reason==f'Hủy hàng · {row.number}' and item.qty_left==7
    ledger=db.scalar(select(StockLedger).where(StockLedger.doc_id==doc.id,StockLedger.qty_out>0));assert row.cost_value==-ledger.amount==300
    second=make(db,p,item,'HUY',2,reason='Chờ kiểm tra');error('REASON_REQUIRED',svc.reject,db,p['director'],second.id,second.version,' ')
    svc.reject(db,p['director'],second.id,second.version,'Hàng vẫn dùng được');assert second.status=='TU_CHOI' and second.stock_doc_id is None and item.qty_left==7
    assert db.query(CashDoc).count()==db.query(FundLedger).count()==0;assert_pending_invariant(db)


def test_director_destruction_immediately_posts(db,lot):
    p,item=lot;row=make(db,p,item,'HUY',1,actor='director',reason='Hàng vỡ')
    assert row.status=='DA_GHI' and row.decided_by==p['director'].id and item.qty_left==9;assert_pending_invariant(db)


def test_pending_destruction_reserved_for_other_actions_and_split(db,lot):
    p,item=lot;row=make(db,p,item,'HUY',8,reason='Chờ hủy')
    before=counts(db);error('CXL_QTY_RESERVED',make,db,p,item,'SUA_XONG',3,to_warehouse_code='K58');assert counts(db)==before
    error('CXL_QTY_RESERVED',pending_items.split,db,p['warehouse'],item.id,3,'THANH_LY',item.version)
    repair=make(db,p,item,'SUA_XONG',2,to_warehouse_code='K58');assert item.qty_left==8 and repair.status=='DA_GHI'
    svc.approve(db,p['director'],row.id,row.version);assert item.qty_left==0;assert_pending_invariant(db)


def test_damage_cost_period_and_void_removes_non_cash_expense(db,lot,client):
    p,item=lot;row=make(db,p,item,'HUY',4,actor='director',reason='Vỡ trống')
    today=row.decided_at.astimezone(finance_reports.TZ).date()
    assert finance_reports.damage_cost(db,p['finance'],today,today)==400
    assert finance_reports.damage_cost(db,p['finance'],today-timedelta(days=2),today-timedelta(days=1))==0
    service_login(client,p['finance']);r=client.get(f'/bao-cao/tai-chinh?from={today}&to={today}');assert r.status_code==200 and 'Chi phí hàng hư hỏng (không dùng tiền)' in r.text
    svc.void(db,p['director'],row.id,row.version,'Hủy do kiểm tra lại');assert finance_reports.damage_cost(db,p['finance'],today,today)==0
    assert item.qty_left==10 and db.query(CashDoc).count()==db.query(FundLedger).count()==0;assert_pending_invariant(db)


def test_zero_cost_recovered_destruction_zero_expense(db,lot):
    from test_recovered_parts import recover
    from test_my_work import ticket
    from app import service_recovered_parts
    from app.schemas import RecoveredPartReceive
    p,item=lot;t=ticket(db,p,'DANG_LAM');recovered=recover(db,p,t)
    db.add(Warehouse(code='REC',name='Kho Thu hồi',kind='LOI',recovered_only=True));db.commit()
    service_recovered_parts.receive(db,p['warehouse'],recovered.id,RecoveredPartReceive(version=recovered.version,warehouse_code='REC'))
    new=db.scalar(select(PendingItem).where(PendingItem.stock_doc_id==recovered.stock_doc_id))
    assert db.get(StockBalance,('CXL','TD-0001')).avg_cost==100 and db.get(StockBalance,('REC','TD-0001')).avg_cost==0
    row=make(db,p,new,'HUY',actor='director',reason='Linh kiện hỏng');assert row.cost_value==0;assert_pending_invariant(db)


def test_void_restores_exact_item_moves_and_reopens(db,lot):
    p,item=lot;row=make(db,p,item,'SUA_XONG',to_warehouse_code='K58');assert item.status=='XONG'
    error('SOURCE_LOCKED',stock_posting.void,db,p['director'],row.stock_doc_id,'Không hủy riêng',None)
    svc.void(db,p['director'],row.id,row.version,'Nhập nhầm kết quả')
    assert row.status=='DA_HUY' and item.status=='DANG_CHO' and item.qty_left==10 and db.get(StockBalance,('CXL','TD-0001')).qty==10
    error('BAD_STATUS',svc.void,db,p['director'],row.id,row.version,'Không hủy hai lần');assert_pending_invariant(db)


def test_void_send_untouched_child_and_block_after_child_disposition(db,lot):
    p,item=lot;row=make(db,p,item,'GUI_NCC',3,to_warehouse_code='NCC',supplier_code='NCC-0001');new=child(db,row)
    svc.void(db,p['director'],row.id,row.version,'Gửi nhầm nhà cung cấp');assert new.status=='DA_HUY' and item.qty_left==10;assert_pending_invariant(db)
    again=make(db,p,item,'GUI_NCC',3,to_warehouse_code='NCC',supplier_code='NCC-0001');new=child(db,again)
    make(db,p,new,'NCC_TRA_VE',1,to_warehouse_code='K58');before=counts(db)
    error('CXL_DA_XU_LY',svc.void,db,p['director'],again.id,again.version,'NCC trả một phần');assert counts(db)==before;assert_pending_invariant(db)


@pytest.mark.parametrize('kind,destination',[('SUA_XONG','K58'),('GUI_NCC','NCC'),('HUY',None)])
def test_failure_after_stock_post_rolls_back_everything(db,lot,monkeypatch,kind,destination):
    p,item=lot;before=counts(db);apply=svc.apply_posted_in_tx
    def fail(*args,**kw):apply(*args,**kw);raise RuntimeError('Lỗi sau khi ghi sổ')
    monkeypatch.setattr(svc,'apply_posted_in_tx',fail)
    with pytest.raises(RuntimeError):make(db,p,item,kind,3,actor='director',to_warehouse_code=destination,supplier_code='NCC-0001',reason='Hỏng')
    assert counts(db)==before and item.qty_left==10 and db.get(StockBalance,('CXL','TD-0001')).qty==10;assert_pending_invariant(db)


@pytest.mark.parametrize('warehouse',['CXL','NCC'])
@pytest.mark.parametrize('kind',['PX','CK'])
def test_manual_outbound_still_blocked(db,lot,warehouse,kind):
    p,item=lot;before=counts(db)
    error('CXL_DUNG_LUONG_RIENG',stock_docs.create,db,p['warehouse'],StockDocIn(kind=kind,warehouse_code=warehouse,to_warehouse_code='K58' if kind=='CK' else None,doc_date=datetime.now(UTC).date(),lines=[dict(product_code='TD-0001',qty=1)]))
    assert counts(db)==before;assert_pending_invariant(db)


@pytest.mark.parametrize('role',['technician','sales','finance'])
def test_non_warehouse_cannot_create_or_approve(db,lot,role,client):
    p,item=lot;assert error('FORBIDDEN',make,db,p,item,'SUA_XONG',1,actor=role,to_warehouse_code='K58').status==403
    csrf=service_login(client,p[role]);r=client.post(f'/cho-xu-ly/{item.id}/xu-ly',data=dict(csrf=csrf,kind='SUA_XONG',qty='1',version=item.version,to_warehouse_code='K58'));assert r.status_code==403
    assert client.get('/cho-xu-ly/duyet-huy').status_code==403;assert_pending_invariant(db)


def test_supplier_alert_sent_date_and_director_work_queue(db,lot,monkeypatch):
    p,item=lot;row=make(db,p,item,'GUI_NCC',2,to_warehouse_code='NCC',supplier_code='NCC-0001');new=child(db,row)
    now=datetime.now(UTC);new.sent_at=now-timedelta(days=31);db.commit();monkeypatch.setattr(get_settings(),'supplier_hold_alert_days',30)
    found=alerts.pending_notices(db,p['warehouse'],now=now);assert found.counts['CHO_NCC_QUA_HAN']==1 and any(r['kind']=='CHO_NCC_QUA_HAN' for r in found)
    assert not alerts.pending_notices(db,p['technician'],now=now)
    request=make(db,p,item,'HUY',1,reason='Không sửa được')
    groups={g['key']:g for g in my_work.collect(db,p['director'])};assert groups['pending_destruction']['rows'][0]['id']==request.id;assert_pending_invariant(db)


def test_stale_item_and_disposition_versions_no_changes(db,lot):
    p,item=lot;version=item.version;make(db,p,item,'SUA_XONG',1,to_warehouse_code='K58');before=counts(db)
    error('VERSION_CONFLICT',svc.create,db,p['warehouse'],item.id,'SUA_XONG',1,version,to_warehouse_code='K58');assert counts(db)==before
    row=make(db,p,item,'HUY',1,reason='Hỏng');error('VERSION_CONFLICT',svc.approve,db,p['director'],row.id,row.version+1);assert row.status=='CHO_DUYET';assert_pending_invariant(db)


def test_rejection_requires_reason_and_approval_rechecks_qty(db,lot):
    p,item=lot;sent=make(db,p,item,'GUI_NCC',2,to_warehouse_code='NCC',supplier_code='NCC-0001');new=child(db,sent);before=counts(db)
    error('REASON_REQUIRED',make,db,p,new,'NCC_KHONG_NHAN',1,to_warehouse_code='CXL')
    assert counts(db)==before
    row=make(db,p,item,'HUY',3,reason='Chờ hủy')
    doc=stock_docs.create(db,p['warehouse'],StockDocIn(kind='KK',warehouse_code='CXL',doc_date=datetime.now(UTC).date(),reason='Kiểm kê thiếu',lines=[dict(product_code='TD-0001',qty=1,unit_cost=100)]))
    stock_posting.post(db,p['director'],doc.id,doc.version);before=counts(db)
    error('CXL_QTY_RESERVED',svc.approve,db,p['director'],row.id,row.version)
    assert counts(db)==before and row.status=='CHO_DUYET' and row.stock_doc_id is None;assert_pending_invariant(db)


def test_concurrent_destruction_requests_do_not_over_reserve(db,lot,engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    from app.models import User
    p,item=lot;barrier=Barrier(2);user_id=p['warehouse'].id;item_id=item.id;version=item.version
    def request(_):
        with Session(engine) as session:
            user=session.get(User,user_id);barrier.wait(timeout=10)
            try:svc.create(session,user,item_id,'HUY',6,version,reason='Hỏng');return 'ok'
            except Exception as exc:
                if hasattr(exc,'code'):return exc.code
                raise
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(request,[1,2]))
    assert sorted(results)==['CXL_QTY_RESERVED','ok']
    assert svc.reserved(db,item.id)==6 and item.qty_left==10;assert_pending_invariant(db)


def test_disposition_photos_permissions_and_unselected_supplier(db,lot,client):
    p,item=lot;csrf=service_login(client,p['warehouse']);before=counts(db)
    r=client.post(f'/cho-xu-ly/{item.id}/xu-ly',data=dict(csrf=csrf,kind='GUI_NCC',qty='1',version=item.version,to_warehouse_code='NCC',supplier_query='NCC mới',supplier_code=''))
    assert r.status_code==400 and 'SUPPLIER_REQUIRED' in r.text and counts(db)==before
    row=make(db,p,item,'HUY',1,reason='Hỏng');photo=attachments.add(db,p['warehouse'],'pending_disposition',str(row.id),'KHAC','hong.png',d5_image())
    assert attachments.list_for(db,p['finance'],'pending_disposition',str(row.id))[0].id==photo.id
    error('FORBIDDEN',attachments.add,db,p['finance'],'pending_disposition',str(row.id),'KHAC','hong.png',d5_image())
    assert_pending_invariant(db)


def test_supplier_hold_catalog_and_manual_inbound_guard(db,lot):
    from app import inventory_catalogs
    from app.schemas import WarehouseIn
    p,item=lot
    warehouse=inventory_catalogs.create(db,p['warehouse'],Warehouse,WarehouseIn(code='K999',name='Đang gửi NCC mới',kind='LOI',supplier_hold=True))
    assert warehouse.supplier_hold
    error('BAD_WAREHOUSE',inventory_catalogs.create,db,p['warehouse'],Warehouse,WarehouseIn(code='K998',name='Sai loại',kind='KHO',supplier_hold=True))
    before=counts(db)
    error('CXL_DUNG_LUONG_RIENG',stock_docs.create,db,p['warehouse'],StockDocIn(kind='CK',warehouse_code='K58',to_warehouse_code='NCC',doc_date=datetime.now(UTC).date(),lines=[dict(product_code='TD-0001',qty=1)]))
    assert counts(db)==before;assert_pending_invariant(db)


def test_recovered_receive_requires_dedicated_warehouse_and_defaults_to_it(db,lot):
    from test_recovered_parts import recover
    from test_my_work import ticket
    from app import service_recovered_parts
    from app.schemas import RecoveredPartReceive
    p,item=lot;t=ticket(db,p,'DANG_LAM');part=recover(db,p,t);before=counts(db)
    exc=error('NO_DEFECT_WAREHOUSE',service_recovered_parts.receive,db,p['warehouse'],part.id,RecoveredPartReceive(version=part.version))
    assert 'Chưa có Kho Thu hồi' in exc.message and counts(db)==before and part.status=='CHO_NOP'
    db.add(Warehouse(code='REC',name='Kho Thu hồi',kind='LOI',recovered_only=True));db.commit()
    error('NO_DEFECT_WAREHOUSE',service_recovered_parts.receive,db,p['warehouse'],part.id,RecoveredPartReceive(version=part.version,warehouse_code='CXL'))
    assert counts(db)==before and part.stock_doc_id is None
    service_recovered_parts.receive(db,p['warehouse'],part.id,RecoveredPartReceive(version=part.version))
    assert part.warehouse_code=='REC' and db.get(StockBalance,('REC','TD-0001')).avg_cost==0
    assert db.get(StockBalance,('CXL','TD-0001')).avg_cost==100;assert_pending_invariant(db)


def test_recovered_routes_cannot_mix_company_cost_pool(db,lot):
    from test_recovered_parts import recover
    from test_my_work import ticket
    from app import service_recovered_parts,inventory_catalogs
    from app.schemas import RecoveredPartReceive,WarehouseIn
    p,item=lot
    error('BAD_WAREHOUSE',inventory_catalogs.create,db,p['warehouse'],Warehouse,WarehouseIn(code='K997',name='Sai loại',kind='KHO',recovered_only=True))
    db.add_all([Warehouse(code='REC',name='Thu hồi',kind='LOI',recovered_only=True),Warehouse(code='RNC',name='Thu hồi gửi NCC',kind='LOI',supplier_hold=True,recovered_only=True)]);db.commit()
    part=recover(db,p,ticket(db,p,'DANG_LAM'));service_recovered_parts.receive(db,p['warehouse'],part.id,RecoveredPartReceive(version=part.version))
    recovered=db.scalar(select(PendingItem).where(PendingItem.stock_doc_id==part.stock_doc_id));before=counts(db)
    error('BAD_KIND',make,db,p,recovered,'SUA_XONG',to_warehouse_code='K58')
    error('BAD_WAREHOUSE',make,db,p,recovered,'GUI_NCC',to_warehouse_code='NCC',supplier_code='NCC-0001')
    error('BAD_WAREHOUSE',make,db,p,item,'GUI_NCC',1,to_warehouse_code='RNC',supplier_code='NCC-0001')
    error('CXL_DUNG_LUONG_RIENG',stock_docs.create,db,p['warehouse'],StockDocIn(kind='PN',warehouse_code='REC',supplier_code='NCC-0001',doc_date=datetime.now(UTC).date(),lines=[dict(product_code='TD-0001',qty=1,unit_cost=100)]))
    error('NO_DEFECT_WAREHOUSE',pending_items.transfer_in,db,p['warehouse'],'K58',[dict(product_code='TD-0001',qty=1)],'HANG_LOI','Không trộn vốn',warehouse_code='REC')
    assert counts(db)==before
    sent=make(db,p,recovered,'GUI_NCC',to_warehouse_code='RNC',supplier_code='NCC-0001');waiting=child(db,sent)
    assert db.get(StockBalance,('RNC','TD-0001')).avg_cost==sent.cost_value==0
    error('BAD_WAREHOUSE',make,db,p,waiting,'NCC_KHONG_NHAN',to_warehouse_code='CXL',reason='Không nhận; không được trộn vốn')
    destroyed=make(db,p,waiting,'HUY',actor='director',reason='Không sửa được');assert destroyed.cost_value==0
    assert db.get(StockBalance,('CXL','TD-0001')).avg_cost==100;assert_pending_invariant(db)
