"""MM1: one machine per dossier; batch intake uses a single real transaction."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from urllib.parse import parse_qs, urlsplit
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session
from app import attachments, equipment, equipment_custody, service_tickets as svc, service_workshop
from app.errors import BusinessError
from app.models import (AuditLog, Customer, CustomerSite, Equipment, EquipmentCustody, FundLedger, SalesReceipt, ServiceStep,
    ServiceTicket, ServiceVisit, StockBalance, StockDoc, StockLedger, User)
from app.schemas import CustodyIntake, EquipmentIn, ServiceMachineSet, ServiceTicketIn
from test_my_work import people, purchase_people, ticket
from test_technician_stock import held, transfer, balance
from test_mv1_workshop import onsite, photo, intake
from test_stock_reservations import fail
from test_web import service_login, d5_image


@pytest.fixture
def job(db,onsite):
    p,own,_,t,eq=onsite;t.kind='MANG_VE'
    db.add(Customer(code='KTD-000002',name='Khách khác'));db.commit()
    others=[equipment.create(db,p['dispatcher'],EquipmentIn(serial='MM1-'+str(n),product_code='TD-0001',customer_code=t.customer_code)) for n in (2,3)]
    return p,own,t,[eq,*others]


def state(db):
    db.expire_all()
    return ([svc.snapshot(t) for t in db.scalars(select(ServiceTicket).order_by(ServiceTicket.id))],
        [equipment.snapshot(eq) for eq in db.scalars(select(Equipment).order_by(Equipment.serial))],
        [(r.id,r.ticket_id,r.started_at,r.ended_at,r.outcome) for r in db.scalars(select(ServiceVisit).order_by(ServiceVisit.id))],
        [(b.warehouse_code,b.product_code,str(b.qty),b.avg_cost) for b in db.scalars(select(StockBalance).order_by(StockBalance.warehouse_code,StockBalance.product_code))],
        *(db.query(m).count() for m in (ServiceStep,EquipmentCustody,StockDoc,StockLedger,SalesReceipt,FundLedger,AuditLog)))


def split(db,p,t,machines,role='technician',new=None):
    return svc.set_machines(db,p[role],t.id,t.version,[m.serial for m in machines],new)


def items(tickets,machines):
    return [dict(ticket_id=t.id,version=t.version,machine_version=m.version,accessories=['DAY_NGUON'],
        accessories_note='',condition_note='Vỏ trầy '+m.serial) for t,m in zip(tickets,machines)]


def test_three_machines_same_assignee_state_history_visits_and_audit(db,job,monkeypatch):
    p,_,t,eqs=job;start=db.scalar(select(ServiceVisit.started_at).where(ServiceVisit.ticket_id==t.id))
    clock=svc._now();monkeypatch.setattr(svc,'_now',lambda:clock+timedelta(hours=2))
    count=db.query(ServiceTicket).count();results=split(db,p,t,eqs)
    assert results[0].id==t.id and db.query(ServiceTicket).count()==count+2
    assert [r.serial for r in results]==[e.serial for e in eqs] and t.split_from_id is None
    for child in results[1:]:
        assert child.split_from_id==t.id and child.assignee_id==t.assignee_id and child.status=='DANG_LAM'
        assert (child.customer_code,child.site_id,child.kind,child.priority,child.symptom,child.appointment_at,child.appointment_slot)==(t.customer_code,t.site_id,t.kind,t.priority,t.symptom,t.appointment_at,t.appointment_slot)
        steps=list(db.scalars(select(ServiceStep).where(ServiceStep.ticket_id==child.id).order_by(ServiceStep.id)))
        assert [s.to_status for s in steps]==['MOI','DA_GIAO','DA_NHAN','DA_HEN','DANG_LAM']
        assert all(s.actor_id==p['technician'].id and s.note=='Tách từ '+t.number for s in steps)
        visit=db.scalars(select(ServiceVisit).where(ServiceVisit.ticket_id==child.id)).one()
        assert visit.ended_at is None and visit.started_at==start and visit.technician_id==t.assignee_id
        assert child.total is None and child.receivable is None and not child.lines
    audit=db.scalars(select(AuditLog).where(AuditLog.action=='service_ticket.split')).one()
    assert audit.user_id==p['technician'].id and audit.after['split_tickets']==[c.number for c in results[1:]]


@pytest.mark.parametrize('status',['MOI','DA_GIAO','DA_NHAN','DA_HEN','CHO_LINH_KIEN','TAM_DUNG'])
def test_dispatcher_preserves_original_open_state(db,job,status):
    p,_,original,eqs=job
    # Independent source ticket with no serial so no other open-ticket conflict.
    t=ticket(db,p,'DANG_LAM' if status=='TAM_DUNG' else status)
    if status=='TAM_DUNG':svc.transition(db,p['dispatcher'],t.id,'TAM_DUNG',t.version,'Chờ khách',hold_reason='KHAC')
    results=split(db,p,t,eqs[1:],role='dispatcher')
    assert all(r.status==status for r in results)
    assert results[1].split_from_id==t.id and results[1].assignee_id==t.assignee_id


@pytest.mark.parametrize('invalid',['duplicate','customer','missing','site'])
def test_bad_selection_rolls_back_everything(db,job,invalid):
    p,_,t,eqs=job;values=[e.serial for e in eqs]
    if invalid=='duplicate':values=[eqs[0].serial,' '+eqs[0].serial.lower()+' '];code='SERIAL_DUPLICATE'
    elif invalid=='customer':eqs[1].customer_code='KTD-000002';db.commit();code='BAD_SERIAL'
    elif invalid=='missing':values[1]='NO-SUCH-MACHINE';code='BAD_SERIAL'
    else:
        site=CustomerSite(customer_code=t.customer_code,name='Chỗ khác',created_by=p['dispatcher'].id);db.add(site);db.flush();eqs[1].site_id=site.id;db.commit();code='BAD_SERIAL'
    before=state(db)
    fail(code,svc.set_machines,db,p['technician'],t.id,t.version,values,None)
    assert state(db)==before


def test_serial_busy_includes_other_ticket_number(db,job):
    p,_,t,eqs=job
    other=svc.create(db,p['dispatcher'],ServiceTicketIn(customer_code=t.customer_code,serial=eqs[1].serial,symptom='Việc khác'))
    before=state(db);error=fail('SERIAL_BUSY',split,db,p,t,eqs)
    assert error.status==409 and other.number in error.message and state(db)==before


@pytest.mark.parametrize('role',['other_tech','sales','warehouse','reception'])
def test_other_staff_forbidden(db,job,role):
    p,_,t,eqs=job;before=state(db)
    assert fail('FORBIDDEN',split,db,p,t,eqs,role=role).status==403
    assert state(db)==before


@pytest.mark.parametrize('blocked',['custody','warranty','stale','closed'])
def test_machine_preconditions_atomic(db,job,blocked):
    p,_,t,eqs=job;version=t.version
    if blocked=='custody':photo(db,p,t);equipment_custody.receive(db,p['technician'],t.id,intake(eqs[0]));code='SERIAL_LOCKED';version=t.version
    elif blocked=='warranty':t.kind='BAO_HANH';t.warranty_basis='HET_HAN';db.commit();code='WARRANTY_MISMATCH';version=t.version
    elif blocked=='stale':version-=1;code='VERSION_CONFLICT'
    else:t.status='DA_HUY';db.commit();version=t.version;code='BAD_STATUS'
    before=state(db);fail(code,svc.set_machines,db,p['technician'],t.id,version,[e.serial for e in eqs],None)
    assert state(db)==before


def test_new_machine_created_with_split_in_same_transaction(db,job):
    p,_,t,eqs=job
    result=split(db,p,t,eqs[:1],new=EquipmentIn(serial='MM1-NEW',product_code='TD-0001',customer_code='KTD-000002'))
    eq=db.get(Equipment,'MM1-NEW');assert eq.customer_code==t.customer_code and eq.site_id==t.site_id
    assert result[0].serial==eqs[0].serial and result[1].serial==eq.serial


def test_new_machine_rolled_back_when_later_create_fails(db,job,monkeypatch):
    p,_,t,eqs=job;before=state(db)
    def no_create(*args,**kwargs):raise BusinessError('TEST_FAILURE','Lỗi sau chọn máy')
    monkeypatch.setattr(svc,'create_in_tx',no_create)
    fail('TEST_FAILURE',split,db,p,t,eqs[:1],new=EquipmentIn(serial='ROLLBACK-NEW',product_code='TD-0001'))
    assert state(db)==before and db.get(Equipment,'ROLLBACK-NEW') is None


def test_empty_mang_ve_needs_machine_and_single_keeps_sv1_behavior(db,job):
    p,_,t,eqs=job;before=state(db)
    fail('SERIAL_REQUIRED',svc.set_machines,db,p['technician'],t.id,t.version,[],None);assert state(db)==before
    n=db.query(ServiceTicket).count();version=t.version
    svc.set_machine(db,p['technician'],t.id,ServiceMachineSet(version=t.version,serial=eqs[1].serial))
    assert t.version==version+1 and t.serial==eqs[1].serial and db.query(ServiceTicket).count()==n


def test_bring_two_moves_both_to_workshop_materials_once(db,job):
    p,own,t,eqs=job;eqs=eqs[:2];tickets=split(db,p,t,eqs)
    for child in tickets:photo(db,p,child)
    before=balance(db,own.code)
    result=service_workshop.bring_trip(db,p['technician'],t.id,t.version,items(tickets,eqs),[dict(product_code='TD-0001',qty=2)])
    assert [r.id for r in result]==[r.id for r in tickets]
    assert all(r.status=='TAM_DUNG' and r.hold_reason=='CHO_SUA_XUONG' for r in tickets)
    for child,eq in zip(tickets,eqs):
        pending=db.scalars(select(EquipmentCustody).where(EquipmentCustody.ticket_id==child.id,EquipmentCustody.status=='CHO_NHAN')).one()
        assert pending.serial==eq.serial and pending.location=='KHO_KY_THUAT'
        assert db.scalars(select(ServiceVisit).where(ServiceVisit.ticket_id==child.id)).one().outcome=='TAM_DUNG'
    assert len(t.lines)==1 and t.lines[0].qty==2 and not tickets[1].lines
    assert db.get(StockDoc,t.lines[0].stock_doc_id).status=='DA_GHI'
    assert balance(db,own.code)==before-2 and db.query(StockDoc).filter_by(kind='PX',status='DA_GHI').count()==1
    assert db.query(SalesReceipt).count()==db.query(FundLedger).count()==0


@pytest.mark.parametrize('invalid',['photo','ticket_version','machine_version','condition','stock'])
def test_bring_trip_failure_rolls_back_all_machines_stock_and_visits(db,job,invalid):
    p,_,t,eqs=job;eqs=eqs[:2];tickets=split(db,p,t,eqs)
    for child in tickets[:1] if invalid=='photo' else tickets:photo(db,p,child)
    parts=items(tickets,eqs);materials=[dict(product_code='TD-0001',qty=2)]
    if invalid=='ticket_version':parts[1]['version']-=1;code='VERSION_CONFLICT'
    elif invalid=='machine_version':parts[1]['machine_version']+=1;code='VERSION_CONFLICT'
    elif invalid=='condition':parts[1]['condition_note']='';code='CONDITION_REQUIRED'
    elif invalid=='stock':materials[0]['qty']=100;code='NEGATIVE_STOCK'
    else:code='PHOTO_REQUIRED'
    before=state(db)
    fail(code,service_workshop.bring_trip,db,p['technician'],t.id,t.version,parts,materials)
    assert state(db)==before


@pytest.mark.parametrize('invalid',['foreign','other_assignee','not_working','omitted','duplicate'])
def test_trip_rejects_unrelated_or_ineligible_machine(db,job,invalid):
    p,_,t,eqs=job;eqs=eqs[:2];tickets=split(db,p,t,eqs);parts=items(tickets,eqs)
    if invalid=='foreign':parts[1]['ticket_id']=ticket(db,p,'DANG_LAM').id
    elif invalid=='other_assignee':tickets[1].assignee_id=p['other_tech'].id;db.commit()
    elif invalid=='not_working':tickets[1].status='DA_HEN';db.commit()
    elif invalid=='omitted':parts=parts[:1]
    else:parts.append(parts[1])
    before=state(db);fail('BAD_TRIP',service_workshop.bring_trip,db,p['technician'],t.id,t.version,parts,[])
    assert state(db)==before


@pytest.mark.parametrize('entry',['/dich-vu','/tho/phieu'])
def test_web_multi_pick_and_new_redirect_to_original_with_numbers(db,job,client,entry):
    p,_,t,eqs=job;role='technician' if entry.startswith('/tho') else 'dispatcher';csrf=service_login(client,p[role])
    response=client.post(f'{entry}/{t.id}/may',data=dict(csrf=csrf,version=t.version,machine_pick=['SERIAL:'+eqs[0].serial,'NEW'],new_serial='WEB-NEW',new_product_code='TD-0001'),follow_redirects=False)
    assert response.status_code==303
    query=parse_qs(urlsplit(response.headers['location']).query)
    db.expire_all();child=db.scalars(select(ServiceTicket).where(ServiceTicket.split_from_id==t.id)).one()
    assert child.number==query['tach'][0] and child.serial=='WEB-NEW' and response.headers['location'].startswith(f'{entry}/{t.id}?')


def test_trip_route_child_photo_upload_and_prefixed_fields(db,job,client):
    from web_forms import key_for
    p,_,t,eqs=job;eqs=eqs[:2];tickets=split(db,p,t,eqs);csrf=service_login(client,p['technician'])
    for child in tickets:
        path=f'/tho/phieu/{child.id}/anh';fk=key_for(client,path)
        response=client.post(path,data=dict(csrf=csrf,form_key=fk,tag='NHAN_MAY'),files={'file':('may.jpg',d5_image(),'image/jpeg')})
        assert response.status_code==201
    data=dict(csrf=csrf,version=t.version,material_product='TD-0001',material_qty='1')
    for part in items(tickets,eqs):
        n=part.pop('ticket_id');data.update({f'trip-{n}-{key}':value for key,value in part.items()})
    response=client.post(f'/tho/phieu/{t.id}/mang-ve-xuong',data=data,follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/tho'
    db.expire_all();assert all(child.hold_reason=='CHO_SUA_XUONG' for child in tickets)


def test_concurrent_multi_selections_only_one_claims_machine(db,job,engine):
    p,_,t,eqs=job;other=ticket(db,p,'DANG_LAM');ids=[(t.id,t.version),(other.id,other.version)];uid=p['technician'].id
    serials=[eqs[1].serial,eqs[2].serial];db.rollback();barrier=Barrier(2)
    def run(pair):
        with Session(engine) as session:
            user=session.get(User,uid);barrier.wait(timeout=10)
            try:svc.set_machines(session,user,*pair,serials,None);return 'OK'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,ids))
    assert sorted(results)==['OK','SERIAL_BUSY']
    assert db.scalar(select(func.count()).select_from(ServiceTicket).where(ServiceTicket.split_from_id.is_not(None)))==1


def test_clear_pilot_handles_self_link(db,job,monkeypatch):
    import sys
    from scripts import clear_pilot_data
    p,_,t,eqs=job;split(db,p,t,eqs);db.close()
    monkeypatch.setattr(sys,'argv',['clear_pilot_data','--yes']);clear_pilot_data.main()
    assert db.query(ServiceTicket).count()==0 and db.query(Equipment).count()==3


def test_split_does_not_grant_public_create_assign_or_past_appointment(db,job,client):
    from app.security import issue_token
    p,_,t,eqs=job;auth={'Authorization':'Bearer '+issue_token(p['technician'])}
    response=client.post('/service-tickets',headers=auth,json=dict(customer_code=t.customer_code,kind='SUA_TAI_CHO',symptom='Nhận đơn khác'))
    assert response.status_code==403
    before=state(db)
    assert fail('FORBIDDEN',svc.assign,db,p['technician'],t.id,t.assignee_id,t.version).status==403
    assert state(db)==before
    other=ticket(db,p,'DA_NHAN')
    fail('BAD_APPOINTMENT',svc.transition,db,p['technician'],other.id,'DA_HEN',other.version,appointment_at=svc._now()-timedelta(days=1))


def test_web_trip_error_does_not_change_any_machine(db,job,client):
    p,_,t,eqs=job;eqs=eqs[:2];tickets=split(db,p,t,eqs);photo(db,p,t);csrf=service_login(client,p['technician'])
    data=dict(csrf=csrf,version=t.version,machine_version=eqs[0].version,accessories='KHONG_CO',condition_note='Máy gốc',material_product='TD-0001',material_qty='1')
    for part in items(tickets[1:],eqs[1:]):
        n=part.pop('ticket_id');data.update({f'trip-{n}-{key}':value for key,value in part.items()})
    before=state(db)
    response=client.post(f'/tho/phieu/{t.id}/mang-ve-xuong',data=data,follow_redirects=False)
    assert response.status_code==400 and 'PHOTO_REQUIRED' in response.text and state(db)==before
