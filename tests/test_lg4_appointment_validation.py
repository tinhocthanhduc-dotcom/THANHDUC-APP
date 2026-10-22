"""LG4: every DA_NHAN -> DA_HEN validates dates, with or without a slot."""
from datetime import datetime, timedelta

import pytest

from app import service_appointments as appointments, service_tickets as svc
from app.models import AuditLog, FundLedger, ServiceStep, ServiceVisit, StockDoc, StockLedger
from app.security import issue_token
from test_my_work import people, purchase_people, ticket, NOW
from test_web import service_login


def appointment(day,slot):
    return datetime.combine(NOW.date()+timedelta(days=day),appointments.bounds(slot)[0],NOW.tzinfo)


def submit(client,p,t,transport,at,slot):
    body=dict(to_status='DA_HEN',version=t.version,note='Khách chốt giờ hẹn')
    if transport=='api':
        body['appointment_at']=at.isoformat()
        if slot is not None:body['appointment_slot']=slot
        return client.post(f'/service-tickets/{t.id}/transition',json=body,
            headers={'Authorization':'Bearer '+issue_token(p['technician'])})
    csrf=service_login(client,p['technician'])
    body.update(csrf=csrf,appointment_at=at.replace(tzinfo=None).isoformat(timespec='minutes'))
    if slot is not None:body['appointment_slot']=slot
    return client.post(f'/dich-vu/{t.id}/transition',data=body,follow_redirects=False)


@pytest.mark.parametrize('transport',['api','web'])
@pytest.mark.parametrize('at,slot',[
    (NOW-timedelta(days=1),None),
    (appointment(-1,'SANG'),'SANG'),
    (appointment(1,'SANG')+timedelta(minutes=15),'SANG'),
    (NOW,None),
],ids=['yesterday-no-slot','yesterday-morning','tomorrow-wrong-slot-start','exactly-now-no-slot'])
def test_invalid_appointment_rejected_without_mutation(client,db,people,transport,at,slot):
    p=people;t=ticket(db,p,'DA_NHAN');db.refresh(t);before=svc.snapshot(t)
    models=(ServiceStep,ServiceVisit,AuditLog,StockDoc,StockLedger,FundLedger)
    counts=[db.query(m).count() for m in models]
    response=submit(client,p,t,transport,at,slot)
    assert response.status_code==400
    if transport=='api':assert response.json()['error']=='BAD_APPOINTMENT'
    else:assert 'data-error-code="BAD_APPOINTMENT"' in response.text
    db.refresh(t)
    assert svc.snapshot(t)==before and [db.query(m).count() for m in models]==counts


@pytest.mark.parametrize('transport',['api','web'])
@pytest.mark.parametrize('at,slot',[
    (NOW+timedelta(days=1),None),
    (appointment(0,'CHIEU'),'CHIEU'),
    (appointment(0,'SANG'),'SANG'),
],ids=['tomorrow-no-slot','afternoon-today-while-morning','morning-slot-still-open'])
def test_valid_appointment_and_open_slot_are_accepted(client,db,people,transport,at,slot):
    p=people;t=ticket(db,p,'DA_NHAN');old_version=t.version
    response=submit(client,p,t,transport,at,slot)
    assert response.status_code in ((200,) if transport=='api' else (200,303))
    db.refresh(t)
    assert t.status=='DA_HEN' and t.appointment_at==at and t.appointment_slot==slot
    assert t.version==old_version+1 and t.due_at==appointments.end(at,slot)
    assert db.query(ServiceVisit).count()==0
