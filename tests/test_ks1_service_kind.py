"""KS1: optional intake classification, completion guard and unchanged posting rules."""
import re
import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app import (commissions, sales_receipts, service_charges, service_completion,
                 service_tickets as svc, service_workshop, technician_work)
from app.errors import BusinessError
from app.models import AuditLog, ServiceTicket
from app.schemas import ServiceCharges, ServiceKindSet, ServiceTicketIn
from conftest import PASSWORD
from test_my_work import people, purchase_people, ticket, NOW
from test_technician_stock import held, transfer, balance
from test_ux1b_hoan_thanh_gop import job
from test_mv1_workshop import onsite, photo, intake, snapshot
from test_web import service_login


def headers(client, user):
    r=client.post('/auth/login',data=dict(username=user.username,password=PASSWORD))
    assert r.status_code==200
    return dict(Authorization='Bearer '+r.json()['access_token'])


@pytest.fixture
def unclassified(db, job):
    p,wh,t=job
    t.kind=None;db.commit()
    return p,wh,t


@pytest.mark.parametrize('value',[None,'','   '])
def test_api_intake_optional_kind_and_nullable_response(client,db,people,value):
    body=dict(customer_code='KTD-000001',symptom='Máy không in')
    if value is not None:body['kind']=value
    auth=headers(client,people['dispatcher'])
    r=client.post('/service-tickets',headers=auth,json=body)
    assert r.status_code==201,r.text
    assert r.json()['kind'] is None
    ident=r.json()['id']
    assert db.get(ServiceTicket,ident).kind is None
    assert client.get(f'/service-tickets/{ident}',headers=auth).json()['kind'] is None
    assert client.get('/service-tickets',headers=auth).json()[0]['kind'] is None


@pytest.mark.parametrize('with_field',[False,True])
def test_form_intake_without_kind_and_labels_everywhere(client,db,people,with_field):
    p=people;csrf=service_login(client,p['dispatcher'])
    data=dict(csrf=csrf,customer_code='KTD-000001',symptom='Máy không in',priority='THUONG',
              appointment_choice='CHIEU:'+(NOW+timedelta(days=1)).date().isoformat())
    if with_field:data['kind']=''
    r=client.post('/dich-vu/moi',data=data,follow_redirects=False)
    assert r.status_code==303,r.text
    t=db.query(ServiceTicket).one()
    assert t.kind is None and t.status=='MOI'
    svc.assign(db,p['dispatcher'],t.id,p['technician'].id,t.version)
    for path in (f'/dich-vu/{t.id}','/dich-vu','/dieu-phoi?view=open'):
        r=client.get(path)
        assert r.status_code==200 and 'Chưa phân loại' in r.text
    service_login(client,p['technician'])
    for path in ('/tho',f'/tho/phieu/{t.id}'):
        r=client.get(path)
        assert r.status_code==200 and 'Chưa phân loại' in r.text


@pytest.mark.parametrize('entry',['transition','api','web','web-transition','technician_work','service_completion'])
def test_completion_without_kind_rolls_back_all_entries(client,db,unclassified,entry):
    p,wh,t=unclassified
    charges=ServiceCharges(version=t.version,billing='THU_TIEN',lines=[
        dict(kind='PHI',description='Công sửa',qty=1,unit_price=100000,vat_rate=8),
        dict(kind='VAT_TU',description='Drum',product_code='TD-0001',warehouse_code=wh.code,qty=1,unit_price=0,vat_rate=0)])
    before=snapshot(db,t);qty=balance(db,wh.code)
    if entry in ('api','web','web-transition'):
        if entry=='api':
            r=client.post(f'/service-tickets/{t.id}/transition',headers=headers(client,p['technician']),
                          json=dict(to_status='HOAN_THANH',version=t.version))
            assert r.status_code==400 and r.json()['error']=='KIND_REQUIRED'
        elif entry=='web-transition':
            csrf=service_login(client,p['dispatcher'])
            r=client.post(f'/dich-vu/{t.id}/transition',data=dict(csrf=csrf,version=t.version,to_status='HOAN_THANH'))
            assert r.status_code==400 and 'data-error-code="KIND_REQUIRED"' in r.text
        else:
            csrf=service_login(client,p['technician'])
            r=client.post(f'/tho/phieu/{t.id}/hoan-thanh',data=dict(csrf=csrf,return_to='tho',version=t.version,
                customer_amount='108.000',collection_mode='FULL',repair_result='FIXED',
                fault_found='Kẹt giấy',fix_done='Đã vệ sinh máy',material_product=['TD-0001'],material_qty=['1']))
            assert r.status_code==400 and 'data-error-code="KIND_REQUIRED"' in r.text
        assert 'Chọn loại dịch vụ trước khi hoàn thành.' in r.text
    else:
        with pytest.raises(BusinessError) as e:
            if entry=='transition':svc.transition(db,p['technician'],t.id,'HOAN_THANH',t.version)
            elif entry=='technician_work':technician_work.complete(db,p['technician'],t.id,t.version,'108.000','FULL','',
                [dict(product_code='TD-0001',qty=1)],fault='Kẹt giấy',fix='Đã vệ sinh máy')
            else:service_completion.complete(db,p['technician'],t.id,charges,fault='Kẹt giấy',fix='Đã vệ sinh máy')
        assert e.value.code=='KIND_REQUIRED' and e.value.message=='Chọn loại dịch vụ trước khi hoàn thành.'
    assert snapshot(db,t)==before and balance(db,wh.code)==qty


@pytest.mark.parametrize('who',['technician','dispatcher','director'])
def test_set_kind_audits_old_new_actor_and_version(db,unclassified,who):
    p,_,t=unclassified;version=t.version
    svc.set_kind(db,p[who],t.id,ServiceKindSet(kind='MANG_VE',version=version))
    assert t.kind=='MANG_VE' and t.version==version+1 and t.status=='DANG_LAM'
    log=db.scalar(select(AuditLog).where(AuditLog.action=='service_ticket.set_kind',AuditLog.entity_id==str(t.id)))
    assert log.user_id==p[who].id and log.before['kind'] is None and log.after['kind']=='MANG_VE'
    svc.set_kind(db,p[who],t.id,ServiceKindSet(kind='MANG_VE',version=t.version))
    assert t.version==version+1
    with pytest.raises(BusinessError,match='VERSION_CONFLICT'):
        svc.set_kind(db,p[who],t.id,ServiceKindSet(kind='MUC_DINH_KY',version=version))
    assert t.kind=='MANG_VE'


@pytest.mark.parametrize('who',['other_tech','sales','warehouse','finance','reception'])
@pytest.mark.parametrize('entry',['api','web'])
def test_set_kind_forbidden_is_403_without_writes(client,db,unclassified,who,entry):
    p,_,t=unclassified;before=snapshot(db,t)
    if entry=='api':r=client.post(f'/service-tickets/{t.id}/kind',headers=headers(client,p[who]),json=dict(kind='MANG_VE',version=t.version))
    else:
        csrf=service_login(client,p[who])
        r=client.post(f'/dich-vu/{t.id}/kind',data=dict(csrf=csrf,kind='MANG_VE',version=t.version))
    assert r.status_code==403,r.text
    assert snapshot(db,t)==before


def test_assigned_technician_sets_kind_from_screen_then_completes(client,db,unclassified):
    p,_,t=unclassified;csrf=service_login(client,p['technician'])
    page=client.get(f'/tho/phieu/{t.id}').text
    assert re.search(r'<select id="kind-\d+" name="kind" required data-autosubmit>', page) and 'Đã chọn loại dịch vụ' in page
    r=client.post(f'/dich-vu/{t.id}/kind',data=dict(csrf=csrf,return_to='tho',kind='MUC_DINH_KY',version=t.version),follow_redirects=False)
    assert r.status_code==303 and r.headers['location']==f'/tho/phieu/{t.id}?da_luu=1'
    db.refresh(t)
    technician_work.complete(db,p['technician'],t.id,t.version,'108000','FULL','',[],fault='Kẹt giấy',fix='Đã vệ sinh máy')
    assert t.status=='DA_DONG' and t.kind=='MUC_DINH_KY' and t.receivable==108000


def test_api_set_kind_and_stale_version(client,db,unclassified):
    p,_,t=unclassified;auth=headers(client,p['technician']);version=t.version
    r=client.post(f'/service-tickets/{t.id}/kind',headers=auth,json=dict(kind='SUA_TAI_CHO',version=version))
    assert r.status_code==200 and r.json()['kind']=='SUA_TAI_CHO' and r.json()['version']==version+1
    r=client.post(f'/service-tickets/{t.id}/kind',headers=auth,json=dict(kind='MANG_VE',version=version))
    assert r.status_code==409 and r.json()['error']=='VERSION_CONFLICT'
    db.refresh(t);assert t.kind=='SUA_TAI_CHO'


def test_null_kind_bring_button_and_workshop_conversion(client,db,onsite):
    p,_,_,t,eq=onsite;t.kind=None;db.commit();photo(db,p,t)
    service_login(client,p['technician'])
    r=client.get(f'/tho/phieu/{t.id}')
    assert r.status_code==200 and 'Mang máy về xưởng' in r.text and f'/dich-vu/{t.id}/mang-ve-xuong' in r.text
    service_workshop.bring(db,p['technician'],t.id,t.version,intake(eq),[])
    assert t.kind=='MANG_VE' and eq.custody_location=='THO_GIU'
    log=db.scalar(select(AuditLog).where(AuditLog.action=='service_ticket.workshop',AuditLog.entity_id==str(t.id)))
    assert log.before['kind'] is None and log.after['kind']=='MANG_VE'


def test_warranty_links_require_warranty_kind_at_intake_and_change(client,db,people):
    p=people;parent=ticket(db,p,'DA_DONG')
    auth=headers(client,p['dispatcher'])
    r=client.post('/service-tickets',headers=auth,json=dict(customer_code=parent.customer_code,symptom='Máy lại lỗi',warranty_of_id=parent.id))
    assert r.status_code in (400,422)
    csrf=service_login(client,p['dispatcher'])
    r=client.post('/dich-vu/moi',data=dict(csrf=csrf,customer_code=parent.customer_code,symptom='Máy lại lỗi',
        warranty_of_id=parent.id,priority='THUONG',appointment_choice='CHIEU:'+(NOW+timedelta(days=1)).date().isoformat()))
    assert r.status_code==400 and 'Chỉ phiếu Bảo hành được chọn phiếu trước.' in r.text
    assert db.query(ServiceTicket).count()==1
    t=svc.create(db,p['dispatcher'],ServiceTicketIn(customer_code=parent.customer_code,symptom='Máy lại lỗi',kind='BAO_HANH',warranty_of_id=parent.id))
    with pytest.raises(BusinessError) as e:svc.set_kind(db,p['dispatcher'],t.id,ServiceKindSet(kind='SUA_TAI_CHO',version=t.version))
    assert e.value.code=='WARRANTY_MISMATCH' and t.warranty_of_id==parent.id and t.kind=='BAO_HANH'


def test_selecting_warranty_does_not_automatically_link_or_change_billing(db,unclassified):
    p,_,t=unclassified;billing=t.billing,t.free_reason
    svc.set_kind(db,p['technician'],t.id,ServiceKindSet(kind='BAO_HANH',version=t.version))
    assert t.warranty_of_id is None and t.warranty_basis is None and (t.billing,t.free_reason)==billing


def test_closed_ticket_cannot_change_kind_and_invalid_kind_rejected(client,db,people):
    p=people;t=ticket(db,p,'DA_DONG');before=snapshot(db,t)
    auth=headers(client,p['director'])
    r=client.post(f'/service-tickets/{t.id}/kind',headers=auth,json=dict(kind='MANG_VE',version=t.version))
    assert r.status_code==409 and r.json()['error']=='BAD_STATUS'
    for kind in (None,'','OTHER'):
        r=client.post(f'/service-tickets/{t.id}/kind',headers=auth,json=dict(kind=kind,version=t.version))
        assert r.status_code==422
    assert snapshot(db,t)==before


@pytest.mark.parametrize('kind',[None,'MANG_VE'])
@pytest.mark.usefixtures('configured_finance')
def test_commission_uses_selected_kind_or_general_rule_for_legacy_null(db,unclassified,monkeypatch,kind):
    p,_,t=unclassified
    general=commissions.add_rule(db,p['director'],dict(target='THO',pct=0,fixed_amount=10000,effective_from='2026-01-01',reason='Quy tắc chung'))
    specific=commissions.add_rule(db,p['director'],dict(target='THO',service_kind='MANG_VE',pct=0,fixed_amount=30000,effective_from='2026-01-01',reason='Mang về'))
    svc.set_kind(db,p['technician'],t.id,ServiceKindSet(kind='MANG_VE',version=t.version))
    technician_work.complete(db,p['technician'],t.id,t.version,'108000','FULL','',[],fault='Kẹt giấy',fix='Đã vệ sinh máy')
    # Confirmed receipts determine commission, exactly as before KS1.
    from app.models import SalesReceipt
    receipt=db.scalar(select(SalesReceipt).where(SalesReceipt.ticket_id==t.id))
    sales_receipts.confirm(db,p['finance'],receipt.id,receipt.version)
    if kind is None:
        # Defensive legacy data: a NULL kind must still match the general rule.
        t.kind=None;db.commit()
    month=NOW.date().replace(day=1)
    from app.config import get_settings
    monkeypatch.setattr(get_settings(),'commission_start',month)
    monkeypatch.setattr(commissions,'_now',lambda:NOW+timedelta(days=40))
    rows=[r for r in commissions.draft(db,month) if r['ticket_id']==t.id]
    assert len(rows)==1
    assert (rows[0]['rule_id'],rows[0]['amount'])==((specific.id,30000) if kind else (general.id,10000))


def test_migration_nullable_warranty_guard_and_downgrade_fallback(db,engine,people,monkeypatch):
    t=svc.create(db,people['dispatcher'],ServiceTicketIn(customer_code='KTD-000001',symptom='Máy không in'))
    parent=svc.create(db,people['dispatcher'],ServiceTicketIn(customer_code='KTD-000001',symptom='Máy phiếu trước',kind='SUA_TAI_CHO'))
    spec=importlib.util.spec_from_file_location('ks1_migration',Path(__file__).parents[1]/'migrations/versions/0057_ks1_optional_service_kind.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    db.rollback()
    with engine.begin() as conn:
        monkeypatch.setattr(module,'op',Operations(MigrationContext.configure(conn)))
        for fields in (f"warranty_of_id={parent.id}", "warranty_basis='HET_HAN'"):
            with pytest.raises(IntegrityError) as e:
                with conn.begin_nested():conn.execute(text('update service_tickets set '+fields+' where id=:id'),dict(id=t.id))
            assert e.value.orig.diag.constraint_name=='ck_service_tickets_warranty'
        module.downgrade()
        assert conn.scalar(text('select kind from service_tickets where id=:id'),dict(id=t.id))=='SUA_TAI_CHO'
        with pytest.raises(IntegrityError):
            with conn.begin_nested():conn.execute(text('update service_tickets set kind=null where id=:id'),dict(id=t.id))
        module.upgrade()
        conn.execute(text('update service_tickets set kind=null where id=:id'),dict(id=t.id))
    db.expire_all()
    assert db.get(ServiceTicket,t.id).kind is None
