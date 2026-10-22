"""AD1: atomic automatic closure without changing money, stock or debt."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text

from app import (equipment, inventory_catalogs, my_work, sales_receipts, service_charges,
    service_completion, service_recovered_parts as recovered, service_tickets as svc)
from app.errors import BusinessError
from app.models import (AuditLog, EquipmentCustody, FundLedger, ServiceRecoveredPart, ServiceStep,
    ServiceTicket, StockDoc, StockLedger, Warehouse)
from app.schemas import (EquipmentIn, RecoveredPartIn, RecoveredPartReceive, SalesReceiptReport,
    ServiceCharges, WarehouseIn)
from app.web import steps
from test_my_work import people, purchase_people, ticket
from test_technician_stock import held, transfer, balance
from test_ux1b_hoan_thanh_gop import job, snapshot
from test_ux1e_gon_man_tho import data, post
from test_web import service_login


def recover(db,p,t):
    return recovered.create(db,p['technician'],t.id,RecoveredPartIn(
        product_code='TD-0001',qty=1,note='Drum đã tháo',version=t.version))


def finish(client,db,p,t,mode='FULL'):
    response=post(client,t,data(client,p,t,collection_mode=mode,
        partial_amount='100.000' if mode=='PARTIAL' else '',free_reason='Bảo hành' if mode=='FREE' else ''))
    assert response.status_code==303
    db.refresh(t)


def close_steps(db,t):
    return list(db.scalars(select(ServiceStep).where(ServiceStep.ticket_id==t.id,ServiceStep.action=='close').order_by(ServiceStep.id)))


@pytest.mark.parametrize('mode,amount',[('FULL',540000),('PARTIAL',100000),('',0),('FREE',0)])
def test_technician_completion_closes_after_receipt_and_keeps_debt(client,db,job,configured_finance,mode,amount):
    p,_,t=job;finish(client,db,p,t,mode)
    assert t.status=='DA_DONG' and t.due_at is None
    history=list(db.scalars(select(ServiceStep).where(ServiceStep.ticket_id==t.id).order_by(ServiceStep.id)))
    assert [s.to_status for s in history[-2:]]==['HOAN_THANH','DA_DONG']
    closed=history[-1]
    assert closed.action=='close' and closed.note=='Tự đóng: đủ điều kiện' and closed.actor_id==p['technician'].id
    reports=sales_receipts.for_ticket(db,p['technician'],t.id)['receipts']
    assert [r.amount for r in reports]==([amount] if amount else [])
    if amount:
        logs=list(db.scalars(select(AuditLog).where(AuditLog.action.in_(('sales_receipt.report','service_ticket.close'))).order_by(AuditLog.id)))
        assert [a.action for a in logs]==['sales_receipt.report','service_ticket.close']
    assert db.query(FundLedger).count()==0
    if mode in ('PARTIAL',''):
        if reports:sales_receipts.confirm(db,p['finance'],reports[0].id,reports[0].version)
        owing=next(r for r in service_charges.receivables(db,p['finance']) if r.id==t.id)
        assert owing.remaining==540000-amount
        assert any(r['id']==t.id for g in my_work.collect(db,p['technician']) if g['key']=='service_unreported' for r in g['rows'])
        extra=sales_receipts.report_for_ticket(db,p['technician'],t.id,
            SalesReceiptReport(version=t.version,amount=540000-amount,method='TIEN_MAT'))
        assert extra.amount==540000-amount and t.status=='DA_DONG'
    with svc._transaction(db):
        locked=svc._get(db,p['technician'],t.id,lock=True)
        assert svc.auto_close_in_tx(db,p['technician'],locked) is False
    assert len(close_steps(db,t))==1


def test_legacy_completion_wrapper_also_reports_before_closing(db,job):
    p,_,t=job
    service_completion.complete(db,p['technician'],t.id,ServiceCharges(version=t.version,
        billing='THU_TIEN',lines=[dict(kind='PHI',description='Công sửa',qty=1,unit_price=100000,vat_rate=0)]),
        receipt=SalesReceiptReport(version=t.version,amount=50000,method='TIEN_MAT'),fault='Kẹt giấy',fix='Vệ sinh máy')
    assert t.status=='DA_DONG' and sales_receipts.balance(t)['pending']==50000
    assert close_steps(db,t)[0].actor_id==p['technician'].id


@pytest.mark.parametrize('last',['receive','cancel'])
def test_last_recovered_line_closes_in_same_transaction(client,db,job,last):
    p,_,t=job;rows=[recover(db,p,t),recover(db,p,t)]
    inventory_catalogs.create(db,p['warehouse'],Warehouse,WarehouseIn(code='K900',name='Thu hồi',kind='LOI',recovered_only=True))
    finish(client,db,p,t,'')
    assert t.status=='HOAN_THANH' and not close_steps(db,t)
    group=next(g for g in my_work.collect(db,p['dispatcher']) if g['key']=='service_close')
    assert group['title']=='Dịch vụ chờ nộp linh kiện thu hồi' and group['count']==1
    assert group['rows'][0]['recovered_pending']==2 and group['rows'][0]['link']==f'/linh-kien-thu-hoi?ticket_id={t.id}'
    service_login(client,p['dispatcher']);html=client.get('/viec-cua-toi?nhom=service_close').text
    assert 'Còn 2 linh kiện thu hồi chưa nộp · thợ '+p['technician'].full_name in html
    page=client.get(f'/dich-vu/{t.id}').text
    assert 'Tự đóng khi đủ:' in page and '<strong>Linh kiện thu hồi đã nộp</strong>' in page
    assert 'service-DA_DONG' not in page and 'Máy khách đã trả</strong>' not in page
    recovered.receive(db,p['warehouse'],rows[0].id,RecoveredPartReceive(version=rows[0].version))
    assert t.status=='HOAN_THANH' and not close_steps(db,t)
    if last=='receive':recovered.receive(db,p['warehouse'],rows[1].id,RecoveredPartReceive(version=rows[1].version))
    else:recovered.cancel(db,p['technician'],rows[1].id,rows[1].version,'Ghi nhầm linh kiện')
    assert t.status=='DA_DONG' and len(close_steps(db,t))==1
    assert close_steps(db,t)[0].actor_id==p['warehouse' if last=='receive' else 'technician'].id
    assert next(g for g in my_work.collect(db,p['dispatcher']) if g['key']=='service_close')['count']==0


@pytest.mark.parametrize('target',['DANG_LAM','DA_HUY'])
def test_dispatcher_can_reopen_or_cancel_closed_and_restore_stock(client,db,job,target):
    p,own,t=job;finish(client,db,p,t,'FREE')
    doc_id=next(l.stock_doc_id for l in t.lines if l.kind=='VAT_TU')
    before=snapshot(db,t)
    with pytest.raises(BusinessError) as e:svc.transition(db,p['technician'],t.id,target,t.version,'Xin sửa lại')
    assert e.value.code=='FORBIDDEN' and e.value.status==403 and snapshot(db,t)==before
    with pytest.raises(BusinessError) as e:svc.transition(db,p['dispatcher'],t.id,target,t.version)
    assert e.value.code=='REASON_REQUIRED' and snapshot(db,t)==before
    service_login(client,p['dispatcher']);html=client.get(f'/dich-vu/{t.id}').text
    assert 'Đã đóng' in html and '>Mở lại</button>' in html and 'service-DA_DONG' not in html
    svc.transition(db,p['dispatcher'],t.id,target,t.version,'Sửa lại vật tư')
    assert t.status==target and t.priced_at is None and t.receivable is None
    assert db.get(StockDoc,doc_id).status=='DA_HUY' and balance(db,own.code)==3
    assert all(l.stock_doc_id is None for l in t.lines)


@pytest.mark.parametrize('confirmed',[False,True])
def test_closed_ticket_with_receipt_keeps_reopen_guard(client,db,job,configured_finance,confirmed):
    p,_,t=job;finish(client,db,p,t)
    r=sales_receipts.for_ticket(db,p['technician'],t.id)['receipts'][0]
    if confirmed:sales_receipts.confirm(db,p['finance'],r.id,r.version)
    before=snapshot(db,t)
    for target in ('DANG_LAM','DA_HUY'):
        with pytest.raises(BusinessError) as e:svc.transition(db,p['dispatcher'],t.id,target,t.version,'Sửa lại phiếu')
        assert e.value.code=='HAS_RECEIPTS' and snapshot(db,t)==before


def test_direct_dispatcher_transition_and_auto_close_failure_roll_back(db,job,monkeypatch):
    p,_,t=job
    service_charges.set_charges(db,p['dispatcher'],t.id,ServiceCharges(version=t.version,billing='MIEN_PHI',free_reason='Bảo hành'))
    before=snapshot(db,t);original=svc.auto_close_in_tx
    def fail(*args):
        original(*args)
        raise BusinessError('TEST_AUTO_CLOSE','Lỗi sau tự đóng')
    monkeypatch.setattr(svc,'auto_close_in_tx',fail)
    with pytest.raises(BusinessError):svc.transition(db,p['dispatcher'],t.id,'HOAN_THANH',t.version)
    assert snapshot(db,t)==before and not close_steps(db,t)
    monkeypatch.setattr(svc,'auto_close_in_tx',original)
    svc.transition(db,p['dispatcher'],t.id,'HOAN_THANH',t.version)
    assert t.status=='DA_DONG' and close_steps(db,t)[0].actor_id==p['dispatcher'].id


@pytest.mark.parametrize('holding',['warehouse','pending'])
def test_unreturned_machine_returns_false_without_any_write(db,people,holding):
    p=people;t=ticket(db,p,'HOAN_THANH')
    eq=equipment.create(db,p['dispatcher'],EquipmentIn(serial='AD1-MAY',product_code='TD-0001',customer_code=t.customer_code))
    t.serial=eq.serial
    if holding=='warehouse':eq.custody_location='KHO_KY_THUAT'
    else:db.add(EquipmentCustody(serial=eq.serial,ticket_id=t.id,location='KHO_KY_THUAT',
        status='CHO_NHAN',created_by=p['warehouse'].id,accessories=[],condition_note='Chờ nhận'))
    db.commit()
    row=db.scalar(select(ServiceRecoveredPart).where(ServiceRecoveredPart.ticket_id==t.id))
    recovered.cancel(db,p['technician'],row.id,row.version,'Dữ liệu test')
    db.refresh(t)
    before=(svc.snapshot(t),db.query(AuditLog).count(),db.query(ServiceStep).count())
    with svc._transaction(db):
        t=svc._get(db,p['warehouse'],t.id,lock=True)
        assert svc.auto_close_in_tx(db,p['warehouse'],t) is False
    assert (svc.snapshot(t),db.query(AuditLog).count(),db.query(ServiceStep).count())==before
    assert [c['key'] for c in steps.service_checks(db,t,None)]==['machine']


@pytest.mark.parametrize('trigger',['complete','receive','cancel'])
def test_auto_close_failure_rolls_back_primary_transaction(client,db,job,monkeypatch,trigger):
    p,_,t=job;row=None
    if trigger!='complete':
        row=recover(db,p,t)
        inventory_catalogs.create(db,p['warehouse'],Warehouse,WarehouseIn(code='K900',name='Thu hồi',kind='LOI',recovered_only=True))
        finish(client,db,p,t,'FREE')
    before=snapshot(db,t);old_row=recovered.snapshot(row) if row else None
    original=svc.auto_close_in_tx
    def fail(*args):
        assert original(*args) is True
        raise BusinessError('TEST_AUTO_CLOSE','Lỗi sau tự đóng')
    monkeypatch.setattr(svc,'auto_close_in_tx',fail)
    if trigger=='complete':
        response=post(client,t,data(client,p,t))
        assert response.status_code==400 and 'TEST_AUTO_CLOSE' in response.text
    else:
        with pytest.raises(BusinessError) as e:
            if trigger=='receive':recovered.receive(db,p['warehouse'],row.id,RecoveredPartReceive(version=row.version))
            else:recovered.cancel(db,p['technician'],row.id,row.version,'Ghi nhầm')
        assert e.value.code=='TEST_AUTO_CLOSE'
    assert snapshot(db,t)==before and not close_steps(db,t)
    if row:db.refresh(row);assert recovered.snapshot(row)==old_row


def test_data_migration_closes_only_eligible_and_is_idempotent(db,engine,people,make_user,monkeypatch):
    p=people;eligible=ticket(db,p,'DA_DONG');pending=ticket(db,p,'HOAN_THANH')
    machine_ticket=ticket(db,p,'DA_DONG');waiting_ticket=ticket(db,p,'DA_DONG');returned_ticket=ticket(db,p,'DA_DONG')
    eq=equipment.create(db,p['dispatcher'],EquipmentIn(serial='AD1-OLD',product_code='TD-0001',customer_code=eligible.customer_code))
    machine_ticket.serial=eq.serial;eq.custody_location='KHO_KY_THUAT'
    for row,serial in [(waiting_ticket,'AD1-WAIT'),(returned_ticket,'AD1-RETURNED')]:
        machine=equipment.create(db,p['dispatcher'],EquipmentIn(serial=serial,product_code='TD-0001',customer_code=row.customer_code))
        row.serial=machine.serial
    db.add(EquipmentCustody(serial='AD1-WAIT',ticket_id=waiting_ticket.id,location='KHO_KY_THUAT',
        status='CHO_NHAN',created_by=p['warehouse'].id,accessories=[],condition_note='Chờ nhận'))
    p['director'].active=False;actor=make_user('ad1.active.director','director');db.commit()
    ids=[eligible.id,machine_ticket.id,waiting_ticket.id,returned_ticket.id]
    db.execute(text("update service_tickets set status='HOAN_THANH' where id=any(:ids)"),dict(ids=ids));db.commit()
    before=db.query(StockLedger).count(),db.query(FundLedger).count()
    spec=importlib.util.spec_from_file_location('ad1_migration',Path(__file__).parents[1]/'migrations/versions/0050_ad1_auto_close_service.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with engine.begin() as conn:
        monkeypatch.setattr(module,'op',Operations(MigrationContext.configure(conn)))
        module.upgrade();module.downgrade();module.upgrade()
    db.expire_all()
    assert db.get(ServiceTicket,eligible.id).status=='DA_DONG'
    assert db.get(ServiceTicket,pending.id).status==db.get(ServiceTicket,machine_ticket.id).status=='HOAN_THANH'
    assert db.get(ServiceTicket,waiting_ticket.id).status=='HOAN_THANH'
    assert db.get(ServiceTicket,returned_ticket.id).status=='DA_DONG'
    migrated=[s for s in close_steps(db,eligible) if s.note=='Tự đóng: dữ liệu cũ']
    assert len(migrated)==1 and migrated[0].actor_id==actor.id
    assert (db.query(StockLedger).count(),db.query(FundLedger).count())==before
    log=db.scalar(select(AuditLog).where(AuditLog.action=='service_ticket.close',AuditLog.entity_id==str(eligible.id)).order_by(AuditLog.id.desc()))
    assert log.user_id==actor.id and log.before['status']=='HOAN_THANH' and log.after['status']=='DA_DONG'
    assert log.before['receivable']==log.after['receivable']==eligible.receivable


def test_data_migration_requires_active_director_before_writing(db,engine,people,monkeypatch):
    p=people;t=ticket(db,p,'DA_DONG');p['director'].active=False;db.commit()
    db.execute(text("update service_tickets set status='HOAN_THANH' where id=:id"),dict(id=t.id));db.commit();db.refresh(t)
    before=svc.snapshot(t),db.query(ServiceStep).count(),db.query(AuditLog).count()
    spec=importlib.util.spec_from_file_location('ad1_no_actor',Path(__file__).parents[1]/'migrations/versions/0050_ad1_auto_close_service.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with pytest.raises(RuntimeError,match='giám đốc đang hoạt động'):
        with engine.begin() as conn:
            monkeypatch.setattr(module,'op',Operations(MigrationContext.configure(conn)))
            module.upgrade()
    db.refresh(t)
    assert (svc.snapshot(t),db.query(ServiceStep).count(),db.query(AuditLog).count())==before
