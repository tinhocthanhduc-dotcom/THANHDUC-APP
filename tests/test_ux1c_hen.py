"""UX1c: khung hẹn và hủy đơn/trả kho trên PostgreSQL thật."""
from datetime import datetime, timedelta
from decimal import Decimal
import csv, io
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from app import (attachments, equipment, equipment_custody, my_work, part_requests, sales_receipts,
    service_appointments as appointments, service_charges, service_tickets as svc, stock_docs, stock_posting, technician_returns as returns)
from app.errors import BusinessError
from app.models import (AuditLog, CustomerSite, FundLedger, SalesReceipt, ServiceStep, ServiceTicket,
    ServiceVisit, StockDoc, StockLedger, TechnicianReturn, TechnicianReturnLine)
from app.schemas import (CustodyIntake, EquipmentIn, PartRequestIn, SalesReceiptReport,
    ServiceCharges, ServiceTicketIn, StockDocIn)
from app.web import dispatch
from app.config import Settings, get_settings
from test_my_work import people, purchase_people, ticket, NOW, DAY, Page
from test_technician_stock import held, balance
from test_web import service_login, d5_image


def error(code, fn, *args, **kwargs):
    with pytest.raises(BusinessError) as exc: fn(*args, **kwargs)
    assert exc.value.code == code
    return exc.value


def new_data(csrf, **kwargs):
    return dict(csrf=csrf, customer_code='KTD-000001', kind='SUA_TAI_CHO', priority='THUONG',
        symptom='Máy bị kẹt giấy', **kwargs)


def scheduled(db,p,slot='CHIEU'):
    start=datetime.combine(NOW.date()+timedelta(days=1),appointments.bounds(slot)[0],svc.service_sla.TZ)
    return svc.create(db,p['dispatcher'],ServiceTicketIn(customer_code='KTD-000001',kind='SUA_TAI_CHO',
        symptom='Máy kẹt giấy',appointment_at=start,appointment_slot=slot))


@pytest.fixture
def supplied(db,held):
    p,own,_=held;t=ticket(db,p,'DANG_LAM')
    r=part_requests.create(db,p['technician'],t.id,PartRequestIn(version=t.version,product_code='TD-0001',qty=2,reason='Cần drum'))
    part_requests.supply(db,p['warehouse'],r.id,'K58',r.version)
    svc.transition(db,p['technician'],t.id,'DANG_LAM',t.version)
    service_charges.set_charges(db,p['technician'],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',lines=[
        dict(kind='PHI',description='Công sửa',qty=1,unit_price=200000,vat_rate=8),
        dict(kind='VAT_TU',product_code='TD-0001',warehouse_code=own.code,qty=1,unit_price=100000,vat_rate=8)]))
    return p,own,t


def state(db,t):
    db.expire_all();db.refresh(t)
    return (svc.snapshot(t),[(l.id,l.description,str(l.qty),l.unit_price,l.stock_doc_id) for l in t.lines],
        [(v.id,v.ended_at,v.outcome,v.note,v.next_appointment_at) for v in db.scalars(select(ServiceVisit).where(ServiceVisit.ticket_id==t.id))],
        *(db.scalar(select(func.count()).select_from(model)) for model in (StockDoc,StockLedger,TechnicianReturn,TechnicianReturnLine,SalesReceipt,FundLedger,AuditLog)))


def test_create_requires_appointment_and_tomorrow_afternoon(client,db,people):
    p=people;csrf=service_login(client,p['dispatcher'])
    response=client.post('/dich-vu/moi',data=new_data(csrf))
    assert response.status_code==400 and 'APPOINTMENT_REQUIRED' in response.text
    assert db.query(ServiceTicket).count()==0
    choice=f'CHIEU:{(NOW.date()+timedelta(days=1)).isoformat()}'
    response=client.post('/dich-vu/moi',data=new_data(csrf,appointment_choice=choice),follow_redirects=False)
    assert response.status_code==303
    t=db.scalars(select(ServiceTicket)).one()
    assert t.appointment_slot=='CHIEU' and t.appointment_at.astimezone(svc.service_sla.TZ)==NOW.replace(hour=13,minute=30)+timedelta(days=1)


@pytest.mark.parametrize('hour,hidden',[(12,'Sáng nay'),(18,'Chiều nay')])
def test_expired_quick_buttons_hidden(client,people,monkeypatch,hour,hidden):
    monkeypatch.setattr(svc,'_now',lambda:NOW.replace(hour=hour,minute=30))
    service_login(client,people['dispatcher']);page=client.get('/dich-vu/moi')
    assert page.status_code==200 and hidden not in page.text
    assert 'Sáng mai' in page.text and 'Chiều mai' in page.text


def test_custom_past_time_rejected(client,db,people):
    csrf=service_login(client,people['dispatcher'])
    response=client.post('/dich-vu/moi',data=new_data(csrf,appointment_choice='GIO',appointment_at=(NOW-timedelta(minutes=1)).isoformat()))
    assert response.status_code==400 and 'BAD_APPOINTMENT' in response.text and db.query(ServiceTicket).count()==0


def test_scheduled_assignment_keeps_appointment_accept_then_arrive(client,db,people):
    p=people;t=scheduled(db,p);appointment=t.appointment_at
    svc.assign(db,p['dispatcher'],t.id,p['technician'].id,t.version)
    assert t.appointment_at==appointment and t.appointment_slot=='CHIEU'
    svc.transition(db,p['technician'],t.id,'DA_NHAN',t.version)
    service_login(client,p['technician']);page=client.get(f'/tho/phieu/{t.id}')
    assert 'service-DANG_LAM' in Page(page.text).forms
    assert 'service-DA_HEN' not in Page(page.text).forms and '<li >Hẹn</li>' not in page.text
    svc.transition(db,p['technician'],t.id,'DANG_LAM',t.version)
    assert db.query(ServiceVisit).filter_by(ticket_id=t.id,ended_at=None).count()==1
    legacy=ticket(db,p,'DA_NHAN')
    error('BAD_TRANSITION',svc.transition,db,p['technician'],legacy.id,'DANG_LAM',legacy.version)


def test_morning_deadline_not_start(client,db,people,monkeypatch):
    p=people;t=scheduled(db,p,'SANG');svc.assign(db,p['dispatcher'],t.id,p['technician'].id,t.version)
    svc.transition(db,p['technician'],t.id,'DA_NHAN',t.version)
    day=t.appointment_at.astimezone(svc.service_sla.TZ)
    for hour,minute,overdue in ((11,59,False),(12,1,True)):
        now=day.replace(hour=hour,minute=minute);monkeypatch.setattr(svc,'_now',lambda:now);monkeypatch.setattr(dispatch,'_now',lambda:now)
        service_login(client,p['technician']);page=client.get('/tho')
        assert ('service-overdue' in page.text)==overdue and 'Sáng' in page.text
        service_login(client,p['dispatcher']);page=client.get('/dieu-phoi?view=overdue')
        assert (t.number in page.text)==overdue


def test_reschedule_before_work_records_step_and_permission(db,people):
    p=people;t=scheduled(db,p);stamp=t.appointment_at+timedelta(days=1)
    svc.reschedule(db,p['dispatcher'],t.id,t.version,stamp,'CHIEU','Khách đổi ngày')
    assert t.status=='MOI' and t.appointment_at==stamp
    assert svc.load(db,p['dispatcher'],t.id).steps[-1].action=='reschedule'
    error('FORBIDDEN',svc.reschedule,db,p['technician'],t.id,t.version,stamp,'CHIEU')
    working=ticket(db,p,'DANG_LAM');error('BAD_STATUS',svc.reschedule,db,p['dispatcher'],working.id,working.version,stamp,'CHIEU')


def test_return_tomorrow_morning_keeps_money_and_stock(client,db,supplied):
    p,own,t=supplied;before=[(l.id,l.description,l.unit_price,l.qty) for l in t.lines];stock=balance(db,own.code)
    csrf=service_login(client,p['technician']);day=(NOW.date()+timedelta(days=1)).isoformat()
    response=client.post(f'/dich-vu/{t.id}/transition',data=dict(csrf=csrf,version=t.version,to_status='DA_HEN',
        appointment_choice='SANG:'+day,note='Khách có việc, hẹn ngày mai',return_to='tho'),follow_redirects=False)
    assert response.status_code==303
    db.expire_all();db.refresh(t)
    assert t.status=='DA_HEN' and t.appointment_slot=='SANG'
    last=db.scalars(select(ServiceVisit).where(ServiceVisit.ticket_id==t.id).order_by(ServiceVisit.id.desc())).first()
    assert last.outcome=='HEN_LAI' and last.next_appointment_at.astimezone(svc.service_sla.TZ)==NOW.replace(hour=8)+timedelta(days=1)
    assert [(l.id,l.description,l.unit_price,l.qty) for l in t.lines]==before and balance(db,own.code)==stock
    assert t.priced_at is None and db.query(StockDoc).filter_by(kind='PX').count()==0


def test_no_show_on_new_scheduled_accepted_ticket(client,db,people):
    p=people;t=scheduled(db,p);svc.assign(db,p['dispatcher'],t.id,p['technician'].id,t.version)
    svc.transition(db,p['technician'],t.id,'DA_NHAN',t.version)
    csrf=service_login(client,p['technician']);page=client.get(f'/tho/phieu/{t.id}')
    assert 'service-no-show' in Page(page.text).forms
    response=client.post(f'/dich-vu/{t.id}/no-show',data=dict(csrf=csrf,version=t.version,
        appointment_choice='SANG:'+(NOW.date()+timedelta(days=1)).isoformat(),note='Gọi cô Lan, không gặp',return_to='tho'),follow_redirects=False)
    assert response.status_code==303
    db.expire_all();db.refresh(t);assert t.status=='DA_HEN' and t.appointment_slot=='SANG'
    visit=db.scalars(select(ServiceVisit).where(ServiceVisit.ticket_id==t.id)).one()
    assert visit.outcome=='KHONG_GAP_KHACH' and visit.ended_at


def test_defer_and_warehouse_receives_only_then_changes_stock(client,db,supplied):
    p,own,t=supplied;lines=[l.id for l in t.lines]
    assert returns.proposal(db,t)['rows'][0]['qty']==2
    returns.cancel_by_technician(db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa')
    assert t.status=='DA_HUY' and t.receivable==0 and t.priced_at is None
    assert [l.id for l in t.lines]==lines and db.query(StockDoc).filter_by(kind='PX').count()==0
    row=db.scalars(select(TechnicianReturn)).one()
    assert row.status=='CHO_NHAN' and row.to_warehouse_code=='K58' and row.lines[0].qty==2
    assert balance(db,own.code)==2 and balance(db,'K58')==8
    visit=db.scalars(select(ServiceVisit).where(ServiceVisit.ticket_id==t.id).order_by(ServiceVisit.id.desc())).first()
    assert visit.outcome=='HUY' and visit.next_appointment_at is None and visit.ended_at
    work={g['key']:g for g in my_work.collect(db,p['warehouse'])}
    assert work['technician_returns']['count']==1 and work['technician_returns']['rows'][0]['link']==f'/tho-tra-hang/{row.id}'
    service_login(client,p['warehouse']);assert 'Thợ trả hàng' in client.get('/viec-cua-toi').text
    returns.receive(db,p['warehouse'],row.id,row.version,{'TD-0001':'2'})
    assert row.status=='DA_NHAN' and row.lines[0].received_qty==2
    ck=db.get(StockDoc,row.stock_doc_id);assert ck.kind=='CK' and ck.status=='DA_GHI'
    assert balance(db,own.code)==0 and balance(db,'K58')==10 and db.query(FundLedger).count()==0
    error('BAD_STATUS',sales_receipts.report_for_ticket,db,p['technician'],t.id,SalesReceiptReport(version=t.version,method='TIEN_MAT',amount=1))


@pytest.mark.parametrize('reason,qty,code',[('',2,'REASON_REQUIRED'),('Khách không muốn sửa nữa',3,'BAD_RETURN_QTY')])
def test_invalid_close_rolls_back(db,supplied,reason,qty,code):
    p,own,t=supplied;before=state(db,t)
    error(code,returns.cancel_by_technician,db,p['technician'],t.id,t.version,reason,[dict(product_code='TD-0001',qty=qty,to_warehouse_code='K58')])
    assert state(db,t)==before and balance(db,own.code)==2


def test_mid_transaction_failure_rolls_back_close_and_request(db,supplied,monkeypatch):
    p,own,t=supplied;before=state(db,t);record=returns.record
    def fail(*args,**kwargs):
        record(*args,**kwargs);raise BusinessError('INJECTED_FAILURE','Lỗi sau khi tạo yêu cầu')
    monkeypatch.setattr(returns,'record',fail)
    error('INJECTED_FAILURE',returns.cancel_by_technician,db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa')
    assert state(db,t)==before and balance(db,own.code)==2


def test_shortage_at_receive_and_partial_note(db,supplied):
    p,own,t=supplied;returns.cancel_by_technician(db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa')
    row=db.scalars(select(TechnicianReturn)).one()
    doc=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PX',warehouse_code=own.code,customer_code=t.customer_code,doc_date=DAY,
        lines=[dict(product_code='TD-0001',qty=1)]));stock_posting.post(db,p['warehouse'],doc.id,doc.version)
    before=state(db,t);version=row.version
    error('NEGATIVE_STOCK',returns.receive,db,p['warehouse'],row.id,version,{'TD-0001':'2'})
    assert state(db,t)==before and row.status=='CHO_NHAN' and balance(db,own.code)==1
    error('REASON_REQUIRED',returns.receive,db,p['warehouse'],row.id,version,{'TD-0001':'1'})
    assert state(db,t)==before
    returns.receive(db,p['warehouse'],row.id,version,{'TD-0001':'1'},'Thợ đã dùng 1 drum cho việc khác')
    assert row.lines[0].received_qty==1 and row.note and balance(db,own.code)==0 and balance(db,'K58')==9


def test_receive_permission_replay_and_cancel_no_delete(client,db,supplied):
    p,_,t=supplied;returns.cancel_by_technician(db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa');row=db.scalars(select(TechnicianReturn)).one()
    error('FORBIDDEN',returns.receive,db,p['technician'],row.id,row.version,{'TD-0001':'2'})
    service_login(client,p['technician']);assert 'return-receive' not in Page(client.get(f'/tho-tra-hang/{row.id}').text).forms
    service_login(client,p['other_tech']);assert client.get(f'/tho-tra-hang/{row.id}').status_code==403
    csrf=service_login(client,p['warehouse']);page=client.get(f'/tho-tra-hang/{row.id}');f=Page(page.text).forms['return-receive'];data={**f['fields'],'qty_TD-0001':'2'}
    first=client.post(f['action'],data=data,follow_redirects=False);count=db.query(StockDoc).count()
    second=client.post(f['action'],data=data,follow_redirects=False)
    assert first.status_code==second.status_code==303 and db.query(StockDoc).count()==count
    for table in ('technician_returns','technician_return_lines'):
        with pytest.raises(DBAPIError):db.execute(text(f'delete from {table}'))
        db.rollback()


def test_cancel_requires_reason_and_preserves_stock(db,supplied):
    p,own,t=supplied;returns.cancel_by_technician(db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa');row=db.scalars(select(TechnicianReturn)).one()
    error('REASON_REQUIRED',returns.cancel,db,p['technician'],row.id,row.version,'')
    returns.cancel(db,p['technician'],row.id,row.version,'Khách nhận lại hàng')
    assert row.status=='DA_HUY' and row.void_reason and balance(db,own.code)==2


def test_machine_not_returned_and_completed_ticket_blocked(db,people):
    p=people;t=ticket(db,p,'DANG_LAM');eq=equipment.create(db,p['dispatcher'],EquipmentIn(serial='UX1C-MAY',product_code='TD-0001',customer_code=t.customer_code))
    t.kind='MANG_VE';t.serial=eq.serial;db.commit()
    attachments.add(db,p['technician'],'equipment_custody',str(t.id),'NHAN_MAY','may.png',d5_image())
    equipment_custody.receive(db,p['technician'],t.id,CustodyIntake(version=eq.version,accessories=[],condition_note='Vỏ xước'))
    before=state(db,t);error('MACHINE_NOT_RETURNED',returns.cancel_by_technician,db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa')
    assert state(db,t)==before
    completed=ticket(db,p,'HOAN_THANH');error('FORBIDDEN',returns.cancel_by_technician,db,p['technician'],completed.id,completed.version,'Khách không muốn sửa nữa')


def test_cancel_has_no_followup_or_deferred_column_and_d7(client,db,supplied):
    p,_,t=supplied;returns.cancel_by_technician(db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa')
    service_login(client,p['dispatcher'])
    assert client.get(f'/dich-vu/moi?tiep_theo={t.id}').status_code==404
    assert client.get('/dieu-phoi?view=deferred').status_code==404
    assert db.query(ServiceTicket).count()==1
    step=db.scalar(select(ServiceStep).where(ServiceStep.ticket_id==t.id,ServiceStep.to_status=='DA_HUY'))
    date=NOW.date();since=datetime.combine(date,datetime.min.time(),svc.service_sla.TZ)
    rows,_=dispatch._report(db,date,date,since,since+timedelta(days=1));found=next(r for r in rows if r['user_id']==p['technician'].id)
    assert 'deferred' not in found and found['completed']==found['failed']==0 and found['revenue']==0
    data=client.get(f'/dieu-phoi/bao-cao.csv?from={date}&to={date}').text
    header=next(csv.reader(io.StringIO(data.lstrip('\ufeff'))));assert 'Khách hẹn lần sau' not in header


def test_configurable_slots_and_validation(monkeypatch):
    monkeypatch.setenv('TD_SLOT_SANG','07:15-11:45');get_settings.cache_clear()
    try: assert appointments.bounds('SANG')[0].isoformat()=='07:15:00'
    finally:get_settings.cache_clear()
    with pytest.raises(ValueError):Settings(slot_sang='12:00-08:00')


def test_other_technician_cannot_close_and_original_form_preserved(client,db,supplied):
    p,_,t=supplied;before=state(db,t)
    assert error('FORBIDDEN',returns.cancel_by_technician,db,p['other_tech'],t.id,t.version,'Khách không muốn sửa nữa').status==403
    csrf=service_login(client,p['other_tech'])
    response=client.post(f'/dich-vu/{t.id}/transition',data=dict(csrf=csrf,version=t.version,to_status='DA_HEN',appointment_choice='LAN_SAU',note='Khách không muốn sửa nữa'))
    assert response.status_code==403 and state(db,t)==before


def test_cancel_keeps_machine_site_kind_and_has_no_followup(client,db,people):
    p=people;eq=equipment.create(db,p['dispatcher'],EquipmentIn(serial='UX1C-FOLLOW',product_code='TD-0001',customer_code='KTD-000001'))
    site=CustomerSite(customer_code='KTD-000001',name='Chi nhánh',address='58 Mã Lò',created_by=p['dispatcher'].id);db.add(site);db.commit()
    t=ticket(db,p,'DANG_LAM');t.serial=eq.serial;t.site_id=site.id;t.kind='MANG_VE';db.commit()
    returns.cancel_by_technician(db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa',[])
    service_login(client,p['dispatcher'])
    assert client.get(f'/dich-vu/moi?tiep_theo={t.id}').status_code==404
    db.expire_all();db.refresh(t)
    assert t.status=='DA_HUY' and t.site_id==site.id and t.serial==eq.serial and t.kind=='MANG_VE'
    assert db.query(ServiceTicket).count()==1
    error('BAD_TRANSITION',svc.transition,db,p['dispatcher'],t.id,'DANG_LAM',t.version,'Mở lại')


@pytest.mark.parametrize('status',['CHO_LINH_KIEN','TAM_DUNG'])
def test_defer_from_waiting_without_open_visit(db,supplied,status):
    p,own,t=supplied
    svc.transition(db,p['technician'],t.id,status,t.version,note='Đợi khách phản hồi',hold_reason='KHAC' if status=='TAM_DUNG' else '')
    returns.cancel_by_technician(db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa')
    assert t.status=='DA_HUY' and balance(db,own.code)==2
    assert db.query(ServiceVisit).filter_by(ticket_id=t.id,ended_at=None).count()==0
    assert db.query(TechnicianReturn).count()==1


def test_pending_part_request_cancelled_when_ticket_ends(db,held):
    p,_,_=held;t=ticket(db,p,'DANG_LAM')
    row=part_requests.create(db,p['technician'],t.id,PartRequestIn(version=t.version,product_code='TD-0001',qty=1,reason='Cần drum'))
    returns.cancel_by_technician(db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa',[])
    assert row.status=='DA_HUY' and row.decision_note=='Hủy đơn: Khách không muốn sửa nữa'
    assert {g['key']:g for g in my_work.collect(db,p['warehouse'])}['parts_pending']['count']==0


def test_reported_ticket_cannot_defer(db,supplied):
    p,_,t=supplied
    # Dữ liệu tích hợp có báo thu trước khi kết thúc: cũng không được bỏ qua.
    db.add(SalesReceipt(number='BT-26-00999',ticket_id=t.id,method='TIEN_MAT',amount=1000,
        received_at=DAY,status='CHO_XAC_NHAN',reported_by=p['technician'].id,note='',reason=''));db.commit()
    before=state(db,t);error('HAS_RECEIPTS',returns.cancel_by_technician,db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa')
    assert state(db,t)==before


def test_migration_preserves_legacy_appointment_and_roundtrip(db,people):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    t=ticket(db,people,'DA_HEN');stamp=t.appointment_at
    spec=importlib.util.spec_from_file_location('ux1c_migration',Path('migrations/versions/0034_ux1c_hen_va_tra_kho.py'))
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with db.begin_nested():
        with Operations.context(MigrationContext.configure(db.connection())):
            migration.downgrade()
            assert db.scalar(text('select appointment_at from service_tickets where id=:id'),{'id':t.id})==stamp
            migration.upgrade()
        assert db.execute(text('select appointment_at,appointment_slot from service_tickets where id=:id'),{'id':t.id}).one()==(stamp,None)
        assert db.scalar(text("select to_regclass('technician_returns')"))=='technician_returns'


def test_return_can_include_other_held_product_and_received_cap(db,supplied):
    from app.models import Product, StockBalance
    p,own,t=supplied
    db.add(Product(code='TD-0999',name='Mực mang theo',search_text='muc mang theo'));db.commit()
    pn=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PN',warehouse_code=own.code,supplier_code='NCC-0001',doc_date=DAY,
        lines=[dict(product_code='TD-0999',qty=2,unit_cost=1000)]));stock_posting.post(db,p['warehouse'],pn.id,pn.version)
    suggested={r['product_code']:r for r in returns.proposal(db,t)['rows']}
    assert suggested['TD-0001']['qty']==2 and suggested['TD-0999']['qty']==0 and suggested['TD-0999']['held']==2
    returns.cancel_by_technician(db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa',[
        dict(product_code='TD-0001',qty=2,to_warehouse_code='K58'),dict(product_code='TD-0999',qty=1,to_warehouse_code='K58')])
    row=db.scalars(select(TechnicianReturn)).one();assert len(row.lines)==2
    before=state(db,t)
    error('BAD_QTY',returns.receive,db,p['warehouse'],row.id,row.version,{'TD-0001':3,'TD-0999':1})
    assert state(db,t)==before
    returns.receive(db,p['warehouse'],row.id,row.version,{'TD-0001':2,'TD-0999':1})
    assert db.get(StockBalance,(own.code,'TD-0999')).qty==1


def test_return_groups_each_supply_source(db,supplied):
    from app.models import Warehouse
    p,own,t=supplied
    db.add(Warehouse(code='K59',name='Kho công ty 2',kind='KHO'));db.commit()
    pn=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PN',warehouse_code='K59',supplier_code='NCC-0001',doc_date=DAY,
        lines=[dict(product_code='TD-0001',qty=1,unit_cost=1000)]));stock_posting.post(db,p['warehouse'],pn.id,pn.version)
    r=part_requests.create(db,p['technician'],t.id,PartRequestIn(version=t.version,product_code='TD-0001',qty=1,reason='Cấp thêm drum'))
    part_requests.supply(db,p['warehouse'],r.id,'K59',r.version)
    returns.cancel_by_technician(db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa')
    rows=list(db.scalars(select(TechnicianReturn)))
    assert {r.to_warehouse_code:r.lines[0].qty for r in rows}=={'K58':2,'K59':1}
    assert balance(db,own.code)==3
