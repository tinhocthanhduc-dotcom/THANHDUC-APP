"""HD1: technician cancellation is atomic; old deferral paths are retired."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app import part_requests, service_tickets as svc, technician_returns as returns
from app.models import (AuditLog, FundLedger, PartRequest, SalesReceipt, ServiceStep,
    ServiceTicket, ServiceVisit, StockDoc, StockLedger, TechnicianReturn)
from app.schemas import PartRequestIn
from app.security import issue_token
from test_my_work import people, purchase_people, ticket, DAY, Page
from test_technician_stock import held, balance
from test_ux1c_hen import supplied, state, error
from test_web import service_login


def submit(client,p,t,transport,user='technician',**extra):
    data=dict(version=t.version,to_status='DA_HUY',note='Khách không muốn sửa nữa',**extra)
    if transport=='api':
        return client.post(f'/service-tickets/{t.id}/transition',json=data,
            headers={'Authorization':'Bearer '+issue_token(p[user])})
    data['csrf']=service_login(client,p[user])
    return client.post(f'/dich-vu/{t.id}/transition',data=data,follow_redirects=False)


@pytest.mark.parametrize('transport',['api','web','in_tx'])
def test_cancel_returns_materials_and_cancels_pending_request(db,client,supplied,transport):
    p,own,t=supplied
    pending=part_requests.create(db,p['technician'],t.id,PartRequestIn(version=t.version,
        product_code='TD-0001',qty=1,reason='Xin thêm drum'))
    svc.transition(db,p['technician'],t.id,'DANG_LAM',t.version)
    t.receivable=123456;db.commit()
    counts=tuple(db.query(m).count() for m in (StockDoc,StockLedger,SalesReceipt,FundLedger))
    if transport=='in_tx':
        with svc._transaction(db):
            locked=svc._get(db,p['technician'],t.id,lock=True)
            svc.transition_in_tx(db,p['technician'],locked,'DA_HUY','Khách không muốn sửa nữa')
    else:
        r=submit(client,p,t,transport)
        assert r.status_code==(200 if transport=='api' else 303),r.text
    db.expire_all();db.refresh(t);db.refresh(pending)
    assert t.status=='DA_HUY' and t.receivable==0 and t.appointment_at is None and t.appointment_slot is None
    assert pending.status=='DA_HUY' and pending.decision_note=='Hủy đơn: Khách không muốn sửa nữa'
    row=db.scalars(select(TechnicianReturn)).one()
    assert row.ticket_id==t.id and row.technician_id==p['technician'].id and row.status=='CHO_NHAN'
    assert [(l.product_code,l.qty) for l in row.lines]==[('TD-0001',2)]
    assert balance(db,own.code)==2 and balance(db,'K58')==8
    assert tuple(db.query(m).count() for m in (StockDoc,StockLedger,SalesReceipt,FundLedger))==counts
    visit=db.scalar(select(ServiceVisit).where(ServiceVisit.ticket_id==t.id).order_by(ServiceVisit.id.desc()))
    assert visit.outcome=='HUY' and visit.ended_at and visit.note=='Khách không muốn sửa nữa'
    assert db.query(ServiceVisit).filter_by(ticket_id=t.id,ended_at=None).count()==0
    step=db.scalar(select(ServiceStep).where(ServiceStep.ticket_id==t.id).order_by(ServiceStep.id.desc()))
    assert (step.action,step.to_status,step.actor_id)==('cancel','DA_HUY',p['technician'].id)


@pytest.mark.parametrize('status',['DANG_LAM','CHO_LINH_KIEN','TAM_DUNG'])
@pytest.mark.parametrize('transport',['api','web'])
def test_assigned_technician_can_cancel_allowed_states(client,db,people,status,transport):
    t=ticket(db,people,'DANG_LAM' if status=='TAM_DUNG' else status)
    if status=='TAM_DUNG':svc.transition(db,people['technician'],t.id,status,t.version,hold_reason='HEN_NGAY_KHAC')
    r=submit(client,people,t,transport)
    assert r.status_code==(200 if transport=='api' else 303),r.text
    db.refresh(t);assert t.status=='DA_HUY'


@pytest.mark.parametrize('status',['DA_GIAO','DA_NHAN','DA_HEN','HOAN_THANH','DA_DONG'])
@pytest.mark.parametrize('transport',['api','web'])
def test_technician_cannot_cancel_other_states(client,db,people,status,transport):
    t=ticket(db,people,status);before=state(db,t)
    r=submit(client,people,t,transport)
    assert r.status_code==403
    assert state(db,t)==before


@pytest.mark.parametrize('transport',['api','web'])
def test_other_technician_cannot_cancel(client,db,supplied,transport):
    p,_,t=supplied;before=state(db,t)
    assert submit(client,p,t,transport,'other_tech').status_code==403
    assert state(db,t)==before


@pytest.mark.parametrize('reason',['','ab','  '])
def test_invalid_reason_leaves_pending_requests_and_stock_unchanged(db,supplied,reason):
    p,own,t=supplied
    pending=part_requests.create(db,p['technician'],t.id,PartRequestIn(version=t.version,
        product_code='TD-0001',qty=1,reason='Xin thêm drum'))
    before=state(db,t)
    error('REASON_REQUIRED',returns.cancel_by_technician,db,p['technician'],t.id,t.version,reason)
    db.refresh(pending)
    assert pending.status=='CHO_CAP' and state(db,t)==before and balance(db,own.code)==2


def test_failure_after_return_request_rolls_back_part_request_cancellation(db,supplied,monkeypatch):
    p,own,t=supplied
    pending=part_requests.create(db,p['technician'],t.id,PartRequestIn(version=t.version,
        product_code='TD-0001',qty=1,reason='Xin thêm drum'))
    before=state(db,t);record=returns.record
    def fail(*args,**kwargs):
        record(*args,**kwargs)
        raise svc.BusinessError('TEST_CANCEL_FAILURE','Lỗi sau tạo TK')
    monkeypatch.setattr(returns,'record',fail)
    error('TEST_CANCEL_FAILURE',returns.cancel_by_technician,db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa')
    db.refresh(pending)
    assert pending.status=='CHO_CAP' and state(db,t)==before and balance(db,own.code)==2


@pytest.mark.parametrize('status',['CHO_XAC_NHAN','DA_XAC_NHAN','TU_CHOI','DA_HUY'])
def test_any_existing_receipt_blocks_technician_cancellation(client,db,supplied,status):
    p,_,t=supplied
    db.add(SalesReceipt(number='BT-HD1',ticket_id=t.id,method='TIEN_MAT',amount=1000,
        received_at=DAY,status=status,reported_by=p['technician'].id));db.commit()
    before=state(db,t)
    r=submit(client,p,t,'api')
    assert r.status_code==409 and r.json()['error']=='HAS_RECEIPTS'
    assert state(db,t)==before


@pytest.mark.parametrize('transport',['api','web'])
def test_removed_status_and_appointment_choice_are_rejected(client,db,supplied,transport):
    p,_,t=supplied;before=state(db,t)
    if transport=='api':
        r=client.post(f'/service-tickets/{t.id}/transition',json=dict(to_status='KHACH_HEN_SAU',version=t.version,note='Khách bận'),
            headers={'Authorization':'Bearer '+issue_token(p['technician'])})
    else:
        csrf=service_login(client,p['technician'])
        r=client.post(f'/dich-vu/{t.id}/transition',data=dict(csrf=csrf,to_status='KHACH_HEN_SAU',version=t.version,note='Khách bận'))
    assert r.status_code in (400,409)
    assert state(db,t)==before
    if transport=='web':
        for target in ('DA_HEN','DA_HUY'):
            r=client.post(f'/dich-vu/{t.id}/transition',data=dict(csrf=csrf,to_status=target,version=t.version,
                appointment_choice='LAN_SAU',note='Khách bận'))
            assert r.status_code==400 and 'BAD_APPOINTMENT' in r.text
            assert state(db,t)==before


def test_dispatcher_cancellation_keeps_existing_behavior(db,supplied):
    p,own,t=supplied
    pending=part_requests.create(db,p['technician'],t.id,PartRequestIn(version=t.version,
        product_code='TD-0001',qty=1,reason='Xin thêm drum'))
    t.receivable=123456;appointment=t.appointment_at;db.commit()
    before=tuple(db.query(m).count() for m in (StockDoc,StockLedger,FundLedger))
    svc.transition(db,p['dispatcher'],t.id,'DA_HUY',t.version,'Khách gọi hủy')
    assert t.status=='DA_HUY' and t.receivable==123456 and t.appointment_at==appointment
    assert pending.status=='DA_HUY' and db.query(TechnicianReturn).count()==0
    assert balance(db,own.code)==2 and balance(db,'K58')==8
    assert tuple(db.query(m).count() for m in (StockDoc,StockLedger,FundLedger))==before


def test_ui_has_cancel_swipe_and_no_deferral_or_followup(client,db,supplied):
    p,_,t=supplied
    service_login(client,p['technician'])
    html=client.get(f'/tho/phieu/{t.id}').text
    form=Page(html).forms['service-DA_HUY']
    assert form['fields']['to_status']=='DA_HUY' and 'data-tone="danger"' in html
    assert 'Vuốt để đóng phiếu: Hủy đơn' in html and 'data-return-fields' in html
    for reason in ('Khách không muốn sửa nữa','Khách hẹn chưa rõ ngày','Khách đổi máy mới','Khác'):assert reason in html
    for url in (f'/tho/phieu/{t.id}',f'/dich-vu/{t.id}'):
        page=client.get(url);assert page.status_code==200
        assert 'hẹn lần sau' not in page.text.lower() and 'LAN_SAU' not in page.text and 'Tạo phiếu tiếp' not in page.text
    service_login(client,p['dispatcher'])
    for url in ('/dieu-phoi?view=open',f'/dich-vu/{t.id}','/dich-vu/moi'):
        page=client.get(url);assert page.status_code==200
        assert 'hẹn lần sau' not in page.text.lower() and 'LAN_SAU' not in page.text and 'Tạo phiếu tiếp' not in page.text
    assert client.get('/dieu-phoi?view=deferred').status_code==404
    for query in ('tiep_theo=','tiep_theo=invalid',f'tiep_theo={t.id}'):
        assert client.get('/dich-vu/moi?'+query).status_code==404


def test_migration_from_0050_preserves_reason_money_stock_and_history(db,engine,people):
    p=people;t=ticket(db,p,'DANG_LAM');reason='Khách chưa quyết định'
    cfg=Config(str(Path(__file__).parents[1]/'alembic.ini'))
    cfg.attributes['database_url']=engine.url.render_as_string(hide_password=False)
    ident=t.id
    db.commit();command.downgrade(cfg,'0050')
    try:
        # Revision 0050 does not contain columns added by later ORM models.
        db.execute(text("update service_tickets set status='KHACH_HEN_SAU',status_reason=:reason where id=:id"),dict(reason=reason,id=ident));db.commit()
        values=db.execute(text('select receivable,subtotal,vat_total,total from service_tickets where id=:id'),dict(id=ident)).one()
        before=(*values,db.query(StockLedger).count(),db.query(FundLedger).count())
        db.commit();command.upgrade(cfg,'head');db.expire_all();db.refresh(t)
        assert t.status=='DA_HUY' and t.status_reason==reason
        assert (t.receivable,t.subtotal,t.vat_total,t.total,db.query(StockLedger).count(),db.query(FundLedger).count())==before
        history=list(db.scalars(select(ServiceStep).where(ServiceStep.ticket_id==t.id,ServiceStep.note=='Đổi từ Khách hẹn lần sau')))
        assert len(history)==1 and history[0].from_status=='KHACH_HEN_SAU' and history[0].to_status=='DA_HUY'
        assert history[0].actor_id==p['director'].id
        with pytest.raises(IntegrityError):
            with db.begin_nested():db.execute(text("update service_tickets set status='KHACH_HEN_SAU' where id=:id"),dict(id=t.id))
        db.commit();command.downgrade(cfg,'0050')
        assert db.scalar(text('select status from service_tickets where id=:id'),dict(id=ident))=='DA_HUY'
    finally:
        db.rollback();command.upgrade(cfg,'head')
    db.refresh(t)
    assert t.status=='DA_HUY' and db.query(ServiceStep).filter_by(ticket_id=t.id,note='Đổi từ Khách hẹn lần sau').count()==1
