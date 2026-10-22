"""KT4 trên PostgreSQL: mọi dòng giữ máy tách khỏi sổ kho/quỹ."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import attachments, equipment, equipment_custody as svc, my_work, service_charges, service_tickets
from app.errors import BusinessError
from app.models import AuditLog, Equipment, EquipmentCustody, FundLedger, StockBalance, StockDoc, StockLedger, User
from app.schemas import CustodyHandover, CustodyIntake, EquipmentIn, ServiceCharges, ServiceTicketIn
from test_my_work import people, purchase_people, NOW
from test_web import d5_image, service_login


@pytest.fixture(autouse=True)
def no_company_ledger_writes(db):
    yield
    db.rollback()
    for model in (StockDoc,StockBalance,StockLedger,FundLedger):assert db.query(model).count()==0


@pytest.fixture
def machine(db,people):
    eq=equipment.create(db,people['dispatcher'],EquipmentIn(serial='AB123',product_code='TD-0001',customer_code='KTD-000001'))
    return people,eq


def ticket(db,p,serial='AB123',who='technician',status='DANG_LAM'):
    t=service_tickets.create(db,p['dispatcher'],ServiceTicketIn(customer_code='KTD-000001',serial=serial,kind='MANG_VE',symptom='Máy cần mang về sửa'))
    service_tickets.assign(db,p['dispatcher'],t.id,p[who].id,t.version)
    if status=='DA_GIAO':return t
    service_tickets.transition(db,p[who],t.id,'DA_NHAN',t.version)
    service_tickets.transition(db,p[who],t.id,'DA_HEN',t.version,appointment_at=service_tickets._now()+timedelta(days=1))
    return service_tickets.transition(db,p[who],t.id,'DANG_LAM',t.version)


def photo(db,p,t,tag='NHAN_MAY',who='technician'):
    return attachments.add(db,p[who],'equipment_custody',str(t.id),tag,tag+'.png',d5_image())


def receive(db,p,eq,t,who='technician',accessories=None):
    photo(db,p,t,who=who)
    return svc.receive(db,p[who],t.id,CustodyIntake(version=eq.version,accessories=accessories if accessories is not None else ['DAY_NGUON','KHAY_GIAY'],condition_note='Vỏ trầy, máy không lên nguồn'))


def handover(db,p,eq,t,location='KHO_KY_THUAT',who='technician',**kw):
    return svc.handover(db,p[who],t.id,CustodyHandover(version=eq.version,location=location,**kw))


def error(code,fn,*args,**kw):
    with pytest.raises(BusinessError) as e:fn(*args,**kw)
    assert e.value.code==code
    return e.value


def test_receive_requires_image_condition_accessories_and_updates_cache_audit(db,machine):
    p,eq=machine;t=ticket(db,p)
    body=CustodyIntake(version=eq.version,accessories=['DAY_NGUON'],condition_note='Vỏ trầy')
    error('PHOTO_REQUIRED',svc.receive,db,p['technician'],t.id,body)
    assert eq.custody_location=='TAI_KHACH' and db.query(EquipmentCustody).count()==0
    photo(db,p,t)
    error('CONDITION_REQUIRED',svc.receive,db,p['technician'],t.id,body.model_copy(update={'condition_note':''}))
    with pytest.raises(ValidationError):CustodyIntake(version=eq.version,condition_note='Máy trầy')
    row=svc.receive(db,p['technician'],t.id,body)
    assert row.location=='THO_GIU' and row.status=='DA_NHAN' and row.holder_id==p['technician'].id
    assert eq.custody_location=='THO_GIU' and eq.custody_holder_id==p['technician'].id and eq.custody_ticket_id==t.id
    assert db.scalar(select(AuditLog).where(AuditLog.action=='custody.receive')).after['accessories']==['DAY_NGUON']
    assert db.scalar(select(AuditLog).where(AuditLog.action=='equipment.custody')).after['custody_location']=='THO_GIU'


def test_other_ticket_held(db,machine):
    p,eq=machine;a=ticket(db,p);b=ticket(db,p)
    receive(db,p,eq,a)
    photo(db,p,b)
    error('MACHINE_HELD',svc.receive,db,p['technician'],b.id,CustodyIntake(version=eq.version,accessories=[],condition_note='Máy trầy'))
    assert db.query(EquipmentCustody).count()==1 and eq.custody_ticket_id==a.id


def test_handover_confirmation_permissions_pending_and_version(db,machine):
    p,eq=machine;t=ticket(db,p);receive(db,p,eq,t)
    row=handover(db,p,eq,t);old=row.version
    assert eq.custody_location=='THO_GIU' and row.status=='CHO_NHAN'
    error('FORBIDDEN',svc.confirm,db,p['other_tech'],row.id,row.version)
    error('HANDOVER_PENDING',handover,db,p,eq,t)
    svc.confirm(db,p['warehouse'],row.id,row.version)
    assert eq.custody_location=='KHO_KY_THUAT' and eq.custody_holder_id is None
    assert row.confirmed_by==p['warehouse'].id
    error('VERSION_CONFLICT',svc.confirm,db,p['warehouse'],row.id,old)
    assert db.query(EquipmentCustody).count()==2


def test_sender_only_cancel_and_immutable_history_trigger(db,machine):
    p,eq=machine;t=ticket(db,p);received=receive(db,p,eq,t);row=handover(db,p,eq,t)
    error('FORBIDDEN',svc.cancel,db,p['dispatcher'],row.id,row.version)
    for sql in [f"update equipment_custody set condition_note='sua' where id={row.id}",
                f"delete from equipment_custody where id={received.id}",
                f"update equipment_custody set status='DA_HUY', confirmed_by={p['technician'].id}, confirmed_at=now(), version=version+1 where id={received.id}"]:
        with pytest.raises(DBAPIError):
            with db.begin_nested():db.execute(text(sql))
    svc.cancel(db,p['technician'],row.id,row.version)
    assert row.status=='DA_HUY' and eq.custody_location=='THO_GIU'
    error('BAD_STATUS',svc.confirm,db,p['warehouse'],row.id,row.version)
    assert db.scalar(select(AuditLog).where(AuditLog.action=='custody.cancel')).before['status']=='CHO_NHAN'


def test_partner_requires_name_dispatcher_send_receive_history(db,machine):
    p,eq=machine;t=ticket(db,p);receive(db,p,eq,t,who='dispatcher')
    error('PARTNER_REQUIRED',handover,db,p,eq,t,location='DOI_TAC',who='dispatcher')
    row=handover(db,p,eq,t,location='DOI_TAC',who='dispatcher',partner_name='Đối tác An',expected_back=NOW.date())
    assert eq.custody_location=='THO_GIU'
    error('FORBIDDEN',svc.confirm,db,p['warehouse'],row.id,row.version)
    svc.confirm(db,p['dispatcher'],row.id,row.version)
    assert eq.custody_location=='DOI_TAC' and row.partner_name=='Đối tác An'
    back=handover(db,p,eq,t,location='KHO_KY_THUAT',who='dispatcher')
    svc.confirm(db,p['dispatcher'],back.id,back.version)
    assert eq.custody_location=='KHO_KY_THUAT'
    assert [r.location for r in svc.history(db,eq.serial)]==['THO_GIU','DOI_TAC','KHO_KY_THUAT']


def test_other_holder_can_return_but_cannot_change_service_ticket_or_charges(db,machine):
    p,eq=machine;t=ticket(db,p);receive(db,p,eq,t)
    row=handover(db,p,eq,t);svc.confirm(db,p['warehouse'],row.id,row.version)
    row=handover(db,p,eq,t,location='THO_GIU',who='warehouse',holder_id=p['other_tech'].id)
    error('FORBIDDEN',svc.confirm,db,p['director'],row.id,row.version)
    svc.confirm(db,p['other_tech'],row.id,row.version)
    photo(db,p,t,'TRA_MAY',who='other_tech')
    svc.return_customer(db,p['other_tech'],t.id,CustodyIntake(version=eq.version,accessories=['DAY_NGUON','KHAY_GIAY']))
    assert eq.custody_location=='TAI_KHACH' and eq.custody_holder_id is None and t.assignee_id==p['technician'].id
    error('FORBIDDEN',service_tickets.transition,db,p['other_tech'],t.id,'HOAN_THANH',t.version)
    error('FORBIDDEN',service_charges.set_charges,db,p['other_tech'],t.id,ServiceCharges(billing='MIEN_PHI',free_reason='Bảo hành',version=t.version))
    assert svc.load(db,p['other_tech'],t.id).id==t.id


def test_return_photo_missing_accessories_note_and_pending_guard(db,machine):
    p,eq=machine;t=ticket(db,p);receive(db,p,eq,t)
    full=CustodyIntake(version=eq.version,accessories=['DAY_NGUON','KHAY_GIAY'])
    error('PHOTO_REQUIRED',svc.return_customer,db,p['technician'],t.id,full)
    photo(db,p,t,'TRA_MAY')
    error('MISSING_ACCESSORIES',svc.return_customer,db,p['technician'],t.id,full.model_copy(update={'accessories':['KHAY_GIAY']}))
    row=handover(db,p,eq,t)
    error('HANDOVER_PENDING',svc.return_customer,db,p['technician'],t.id,full)
    svc.cancel(db,p['technician'],row.id,row.version)
    svc.return_customer(db,p['technician'],t.id,full.model_copy(update={'accessories':['KHAY_GIAY'],'accessories_note':'Khách không lấy dây nguồn, đã xác nhận'}))
    assert eq.custody_location=='TAI_KHACH' and svc.latest(db,eq.serial).accessories==['KHAY_GIAY']


@pytest.mark.parametrize('target',['HOAN_THANH','KHONG_LAM_DUOC','DA_HUY','DA_DONG'])
def test_machine_blocks_all_end_states_then_return_allows_valid_transition(db,machine,target):
    p,eq=machine;t=ticket(db,p);receive(db,p,eq,t)
    error('MACHINE_NOT_RETURNED',service_tickets.transition,db,p['dispatcher'],t.id,target,t.version,'Kết thúc sau trả máy')
    assert t.status=='DANG_LAM'
    photo(db,p,t,'TRA_MAY')
    svc.return_customer(db,p['technician'],t.id,CustodyIntake(version=eq.version,accessories=['DAY_NGUON','KHAY_GIAY']))
    if target in ('HOAN_THANH','DA_DONG'):
        service_charges.set_charges(db,p['dispatcher'],t.id,ServiceCharges(version=t.version,billing='MIEN_PHI',free_reason='Bảo hành'))
        attachments.add(db,p['technician'],'service_ticket',str(t.id),'SAU','sau.png',d5_image())
        service_tickets.transition(db,p['technician'],t.id,'HOAN_THANH',t.version)
        if target=='DA_DONG':service_tickets.transition(db,p['dispatcher'],t.id,target,t.version)
    else:service_tickets.transition(db,p['dispatcher'],t.id,target,t.version,'Không sửa, trả máy')
    assert t.status==target


def test_custody_pages_work_groups_age_and_access(client,db,machine,monkeypatch):
    p,eq=machine;t=ticket(db,p);old=NOW-timedelta(days=9)
    monkeypatch.setattr(svc,'_now',lambda:old)
    receive(db,p,eq,t)
    monkeypatch.setattr(svc,'_now',lambda:NOW)
    row=handover(db,p,eq,t)
    groups={g['key']:g for g in my_work.collect(db,p['technician'])}
    assert groups['machines_held']['count']==1
    groups={g['key']:g for g in my_work.collect(db,p['warehouse'])}
    assert groups['machines_warehouse']['count']==1
    service_login(client,p['technician'])
    assert client.get('/may-dang-giu').status_code==403
    service_login(client,p['warehouse'])
    response=client.get('/may-dang-giu');assert response.status_code==200 and 'AB123' in response.text and 'service-overdue' in response.text
    assert 'AB123' not in client.get('/may-dang-giu?location=DOI_TAC').text
    assert client.get('/giu-may/'+str(t.id)).status_code==200
    svc.confirm(db,p['warehouse'],row.id,row.version)
    # Bàn giao mới không đặt lại tuổi máy từ lúc nhận khách.
    assert svc.held(db,p['warehouse'],today=NOW.date())[0]['days']==9
    row=handover(db,p,eq,t,location='THO_GIU',who='warehouse',holder_id=p['other_tech'].id)
    groups={g['key']:g for g in my_work.collect(db,p['other_tech'])}
    assert groups['machines_pending']['count']==1 and groups['machines_held']['count']==0


def test_concurrent_handover_one_pending(engine,db,machine):
    p,eq=machine;t=ticket(db,p);receive(db,p,eq,t)
    ident,version,uid=t.id,eq.version,p['technician'].id;db.rollback();gate=Barrier(2)
    def run():
        with Session(engine) as s:
            user=s.get(User,uid);gate.wait(timeout=10)
            try:svc.handover(s,user,ident,CustodyHandover(version=version,location='KHO_KY_THUAT'));return 'OK'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:run(),range(2)))
    assert sorted(results)==['HANDOVER_PENDING','OK']
    assert db.scalar(select(func.count()).select_from(EquipmentCustody).where(EquipmentCustody.status=='CHO_NHAN'))==1


def test_web_receive_confirm_return_photos_csrf_and_separate_holder_scope(client,db,machine):
    p,eq=machine;t=ticket(db,p);csrf=service_login(client,p['technician'])
    assert 'Máy khách' in client.get(f'/dich-vu/{t.id}').text
    data=dict(version=eq.version,accessories=['DAY_NGUON'],condition_note='Vỏ trầy')
    assert client.post(f'/giu-may/{t.id}/nhan',data=data).status_code==403
    response=client.post(f'/giu-may/{t.id}/nhan',data={**data,'csrf':csrf})
    assert response.status_code==400 and 'PHOTO_REQUIRED' in response.text and 'Vỏ trầy' in response.text
    response=client.post(f'/giu-may/{t.id}/anh/tai',data=dict(csrf=csrf,tag='NHAN_MAY'),files={'file':('nhan.png',d5_image(),'image/png')})
    assert response.status_code==200
    response=client.post(f'/giu-may/{t.id}/nhan',data={**data,'csrf':csrf});assert response.status_code==200
    db.refresh(eq)
    response=client.post(f'/giu-may/{t.id}/ban-giao',data=dict(csrf=csrf,version=eq.version,location='THO_GIU',holder_id=p['other_tech'].id))
    assert response.status_code==200
    row=svc.pending(db,eq.serial);old=row.version
    csrf=service_login(client,p['other_tech'])
    response=client.get(f'/giu-may/{t.id}')
    assert response.status_code==200 and 'Xác nhận đã nhận' in response.text
    assert 'service-charges' not in response.text and 'Chuyển trạng thái' not in response.text
    assert client.get(f'/dich-vu/{t.id}').status_code==404
    assert client.post(f'/giu-may/{t.id}/xac-nhan',data=dict(custody_id=row.id,version=old)).status_code==403
    response=client.post(f'/giu-may/{t.id}/xac-nhan',data=dict(csrf=csrf,custody_id=row.id,version=old));assert response.status_code==200
    response=client.post(f'/giu-may/{t.id}/xac-nhan',data=dict(csrf=csrf,custody_id=row.id,version=old))
    assert response.status_code==409 and 'VERSION_CONFLICT' in response.text
    response=client.post(f'/dich-vu/{t.id}/transition',data=dict(csrf=csrf,version=t.version,to_status='HOAN_THANH'))
    assert response.status_code==403
    response=client.post(f'/giu-may/{t.id}/anh/tai',data=dict(csrf=csrf,tag='TRA_MAY'),files={'file':('tra.png',d5_image(),'image/png')})
    assert response.status_code==200
    db.refresh(eq)
    response=client.post(f'/giu-may/{t.id}/tra-khach',data=dict(csrf=csrf,version=eq.version,accessories='DAY_NGUON'))
    assert response.status_code==200 and 'Tại khách' in response.text
    db.refresh(eq);assert eq.custody_location=='TAI_KHACH'
    assert client.get('/thiet-bi/AB123').status_code==200 and 'Lịch sử giữ máy' in client.get('/thiet-bi/AB123').text


def test_pdf_and_unrelated_technician_cannot_supply_custody_proof(db,machine):
    p,eq=machine;t=ticket(db,p)
    error('FORBIDDEN',attachments.add,db,p['other_tech'],'equipment_custody',str(t.id),'NHAN_MAY','nhan.png',d5_image())
    error('BAD_FILE_TYPE',attachments.add,db,p['technician'],'equipment_custody',str(t.id),'NHAN_MAY','fake.pdf',b'%PDF-1.4 test')
    error('BAD_TAG',attachments.add,db,p['technician'],'equipment_custody',str(t.id),'SAU','sau.png',d5_image())


def test_concurrent_receive_same_serial_on_two_tickets_only_one(engine,db,machine):
    p,eq=machine;a=ticket(db,p);b=ticket(db,p);photo(db,p,a);photo(db,p,b)
    ids=[a.id,b.id];version=eq.version;uid=p['technician'].id;db.rollback();gate=Barrier(2)
    def run(ident):
        with Session(engine) as s:
            user=s.get(User,uid);gate.wait(timeout=10)
            try:svc.receive(s,user,ident,CustodyIntake(version=version,accessories=[],condition_note='Máy trầy'));return 'OK'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,ids))
    assert results.count('OK')==1 and set(results)-{'OK'} <= {'MACHINE_HELD','VERSION_CONFLICT'}
    assert db.query(EquipmentCustody).count()==1


def test_hidden_or_previous_cycle_images_cannot_replace_fresh_proof(db,machine):
    p,eq=machine;t=ticket(db,p)
    image=photo(db,p,t)
    attachments.hide(db,p['technician'],image.id,'Ảnh bị mờ',image.version)
    body=CustodyIntake(version=eq.version,accessories=[],condition_note='Vỏ trầy')
    error('PHOTO_REQUIRED',svc.receive,db,p['technician'],t.id,body)
    receive(db,p,eq,t,accessories=[])
    photo(db,p,t,'TRA_MAY')
    svc.return_customer(db,p['technician'],t.id,CustodyIntake(version=eq.version,accessories=[]))
    error('PHOTO_REQUIRED',svc.receive,db,p['technician'],t.id,CustodyIntake(version=eq.version,accessories=[],condition_note='Vỏ trầy'))
    receive(db,p,eq,t,accessories=[])
    error('PHOTO_REQUIRED',svc.return_customer,db,p['technician'],t.id,CustodyIntake(version=eq.version,accessories=[]))


def test_expected_back_warning_and_cancelled_recipient_scope(db,machine,monkeypatch):
    p,eq=machine;t=ticket(db,p);receive(db,p,eq,t)
    row=handover(db,p,eq,t,location='THO_GIU',holder_id=p['other_tech'].id)
    assert svc.can_view(db,p['other_tech'],t)
    svc.cancel(db,p['technician'],row.id,row.version)
    assert not svc.can_view(db,p['other_tech'],t)
    now=datetime.now(service_tickets.service_sla.TZ)
    row=handover(db,p,eq,t,location='DOI_TAC',who='dispatcher',partner_name='Đối tác A',expected_back=now.date()-timedelta(days=1))
    svc.confirm(db,p['dispatcher'],row.id,row.version)
    result=svc.held(db,p['dispatcher'],today=now.date())
    assert result[0]['overdue'] and result[0]['days']==0


def test_receiving_closed_ticket_or_by_wrong_technician_rejected(db,machine):
    p,eq=machine;t=ticket(db,p)
    body=CustodyIntake(version=eq.version,accessories=[],condition_note='Máy trầy')
    error('FORBIDDEN',svc.receive,db,p['other_tech'],t.id,body)
    service_tickets.transition(db,p['dispatcher'],t.id,'DA_HUY',t.version,'Khách hủy sửa')
    error('BAD_STATUS',svc.receive,db,p['technician'],t.id,body)
    assert db.query(EquipmentCustody).count()==0


def test_custody_holder_can_read_custody_photos_but_not_service_photos(client,db,machine):
    p,eq=machine;t=ticket(db,p);proof=photo(db,p,t)
    svc.receive(db,p['technician'],t.id,CustodyIntake(version=eq.version,accessories=[],condition_note='Vỏ trầy'))
    private=attachments.add(db,p['technician'],'service_ticket',str(t.id),'SAU','sau.png',d5_image())
    row=handover(db,p,eq,t,location='THO_GIU',holder_id=p['other_tech'].id)
    svc.confirm(db,p['other_tech'],row.id,row.version)
    service_login(client,p['other_tech'])
    assert client.get(f'/attachments/{proof.id}/file').status_code==200
    assert client.get(f'/attachments/{private.id}/file').status_code==404
    assert client.get(f'/dich-vu/{t.id}').status_code==404


def test_receive_and_complete_cannot_leave_closed_ticket_holding_machine(engine,db,machine):
    p,eq=machine;t=ticket(db,p);photo(db,p,t)
    service_charges.set_charges(db,p['technician'],t.id,ServiceCharges(version=t.version,billing='MIEN_PHI',free_reason='Bảo hành'))
    attachments.add(db,p['technician'],'service_ticket',str(t.id),'SAU','sau.png',d5_image())
    ident,eqv,tv,uid=t.id,eq.version,t.version,p['technician'].id;db.rollback();gate=Barrier(2)
    def run(action):
        with Session(engine) as s:
            user=s.get(User,uid);gate.wait(timeout=10)
            try:
                if action=='receive':svc.receive(s,user,ident,CustodyIntake(version=eqv,accessories=[],condition_note='Vỏ trầy'))
                else:service_tickets.transition(s,user,ident,'HOAN_THANH',tv)
                return 'OK'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,['receive','complete']))
    assert results.count('OK')==1 and set(results)-{'OK'} <= {'MACHINE_NOT_RETURNED','BAD_STATUS'}
    db.refresh(t);db.refresh(eq)
    assert (t.status,eq.custody_location) in [('DANG_LAM','THO_GIU'),('HOAN_THANH','TAI_KHACH')]


def test_handover_declares_accessories_for_recipient_and_compares_original_at_return(client,db,machine):
    p,eq=machine;t=ticket(db,p);receive(db,p,eq,t)
    csrf=service_login(client,p['technician'])
    response=client.post(f'/giu-may/{t.id}/ban-giao',data=dict(csrf=csrf,version=eq.version,location='THO_GIU',holder_id=p['other_tech'].id,
        accessories='KHAY_GIAY',accessories_note='Dây nguồn để lại tại kho kỹ thuật',condition_note='Đã tháo vỏ kiểm tra'))
    assert response.status_code==200
    row=svc.pending(db,eq.serial)
    assert row.accessories==['KHAY_GIAY'] and eq.custody_holder_id==p['technician'].id
    service_login(client,p['other_tech'])
    response=client.get(f'/giu-may/{t.id}')
    assert 'Nội dung bàn giao chờ nhận' in response.text and 'Đã tháo vỏ kiểm tra' in response.text
    svc.confirm(db,p['other_tech'],row.id,row.version)
    photo(db,p,t,'TRA_MAY',who='other_tech')
    error('MISSING_ACCESSORIES',svc.return_customer,db,p['other_tech'],t.id,CustodyIntake(version=eq.version,accessories=['KHAY_GIAY']))
