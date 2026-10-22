"""KT6: bảo hành liên kết, hạn và quyền thay đổi cách thu."""
import csv
import io
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select,text
from sqlalchemy.exc import DBAPIError

from app import attachments,equipment,service_charges,service_tickets as tickets,service_warranty as svc,stock_posting
from app.config import Settings,get_settings
from app.errors import BusinessError
from app.models import AuditLog,Customer,ServiceStep,ServiceTicket,StockDoc,StockLedger
from app.schemas import EquipmentIn,ServiceCharges,ServiceTicketIn,ServiceWarrantyLink
from test_my_work import people,purchase_people,NOW,ticket
from test_technician_stock import held,transfer
from test_web import d5_image,service_login


def fail(code,fn,*args,**kw):
    with pytest.raises(BusinessError) as exc:fn(*args,**kw)
    assert exc.value.code==code


def previous(db,p,days=30,**kw):
    stamp=NOW-timedelta(days=days)
    n=db.query(ServiceTicket).count()+1
    data=dict(number=f'DV-26-P{n:04}',customer_code='KTD-000001',kind='SUA_TAI_CHO',symptom='Đã sửa',
        status='HOAN_THANH',assignee_id=p['technician'].id,created_by=p['dispatcher'].id,billing='MIEN_PHI',free_reason='Bảo hành',priced_at=stamp,subtotal=0,vat_total=0,total=0,receivable=0)
    data.update(kw);row=ServiceTicket(**data);db.add(row);db.flush()
    if row.status in ('HOAN_THANH','DA_DONG'):
        db.add(ServiceStep(ticket_id=row.id,action='transition',from_status='DANG_LAM',to_status='HOAN_THANH',
            actor_id=p['technician'].id,assignee_id=p['technician'].id,at=stamp,note='Đã sửa máy'))
    db.commit();return row


def warranty(db,p,parent=None,**kw):
    return tickets.create(db,p['dispatcher'],ServiceTicketIn(customer_code='KTD-000001',kind='BAO_HANH',
        symptom='Khách báo lỗi lại',warranty_of_id=parent.id if parent else None,**kw))


def machine(db,p,serial='KT6-001',until=None,customer='KTD-000001'):
    return equipment.create(db,p['dispatcher'],EquipmentIn(serial=serial,product_code='TD-0001',customer_code=customer,warranty_until=until))


@pytest.mark.parametrize('days,basis,free',[(30,'PHIEU_TRUOC',True),(90,'PHIEU_TRUOC',True),(100,'HET_HAN',False)])
def test_previous_period_and_default_billing(db,people,days,basis,free):
    parent=previous(db,people,days);t=warranty(db,people,parent)
    assert t.warranty_of_id==parent.id and t.warranty_basis==basis
    assert t.billing==('MIEN_PHI' if free else None)
    assert t.free_reason==('Bảo hành '+parent.number if free else '')


@pytest.mark.parametrize('days,basis',[(0,'MAY_CON_HAN'),(1,'MAY_CON_HAN'),(-1,'HET_HAN')])
def test_machine_warranty_inclusive_today(db,people,days,basis):
    eq=machine(db,people,until=NOW.date()+timedelta(days=days));t=warranty(db,people,serial=eq.serial)
    assert t.warranty_basis==basis
    assert t.billing==('MIEN_PHI' if days>=0 else None)
    assert t.free_reason==('Bảo hành máy đến '+eq.warranty_until.strftime('%d/%m/%Y') if days>=0 else '')


def test_no_machine_or_parent_expired_and_configurable_days(db,people,monkeypatch):
    assert warranty(db,people).warranty_basis=='HET_HAN'
    parent=previous(db,people,30)
    monkeypatch.setattr(get_settings(),'service_warranty_days',20)
    assert warranty(db,people,parent).warranty_basis=='HET_HAN'
    assert Settings(_env_file=None).service_warranty_days==90
    with pytest.raises(ValidationError):Settings(_env_file=None,service_warranty_days=-1)


def test_wrong_customer_serial_and_unfinished_parent(db,people):
    p=people
    db.add(Customer(code='KTD-000002',name='Khách khác'));db.commit()
    a=machine(db,p);b=machine(db,p,serial='KT6-002')
    parent=previous(db,p,serial=a.serial)
    fail('WARRANTY_MISMATCH',warranty,db,p,parent,serial=b.serial)
    other=previous(db,p,customer_code='KTD-000002')
    fail('WARRANTY_MISMATCH',warranty,db,p,other)
    pending=previous(db,p,status='DA_NHAN')
    fail('WARRANTY_NOT_COMPLETED',warranty,db,p,pending)
    assert warranty(db,p,parent).warranty_basis=='PHIEU_TRUOC' # Chỉ một bên có serial vẫn hợp lệ.


def test_non_warranty_api_schema_and_database_constraint(db,people):
    parent=previous(db,people)
    with pytest.raises(ValidationError):ServiceTicketIn(customer_code='KTD-000001',kind='SUA_TAI_CHO',symptom='Khác',warranty_of_id=parent.id)
    normal=ticket(db,people)
    with pytest.raises(DBAPIError),db.begin_nested():
        db.execute(text("update service_tickets set warranty_of_id=:parent,warranty_basis='PHIEU_TRUOC' where id=:id"),dict(id=normal.id,parent=parent.id))
    with pytest.raises(DBAPIError),db.begin_nested():
        db.execute(text("update service_tickets set warranty_basis='HET_HAN' where id=:id"),dict(id=normal.id))


def test_dispatcher_charge_override_reason_audit_and_technician_denied(db,people):
    p=people;parent=previous(db,p);t=warranty(db,p,parent)
    tickets.assign(db,p['dispatcher'],t.id,p['technician'].id,t.version)
    body=ServiceCharges(version=t.version,billing='THU_TIEN',lines=[dict(kind='PHI',description='Khách làm hỏng thêm',qty=1,unit_price=50000)])
    fail('FORBIDDEN',service_charges.set_charges,db,p['technician'],t.id,body)
    fail('REASON_REQUIRED',service_charges.set_charges,db,p['dispatcher'],t.id,body)
    body=body.model_copy(update={'billing_reason':'Khách làm hỏng thêm khay giấy'})
    service_charges.set_charges(db,p['dispatcher'],t.id,body)
    assert t.billing=='THU_TIEN' and t.free_reason==''
    log=db.scalar(select(AuditLog).where(AuditLog.action=='service_ticket.charges').order_by(AuditLog.id.desc()))
    assert log.reason==body.billing_reason and log.before['billing']=='MIEN_PHI' and log.after['billing']=='THU_TIEN'


def test_relink_only_dispatcher_open_rechecks_expired_and_audits(db,people):
    p=people;parent=previous(db,p);old=previous(db,p,100);t=warranty(db,p,parent)
    tickets.assign(db,p['dispatcher'],t.id,p['technician'].id,t.version)
    body=ServiceWarrantyLink(version=t.version,warranty_of_id=old.id,note='Chọn đúng phiếu gốc')
    fail('FORBIDDEN',svc.relink,db,p['technician'],t.id,body)
    svc.relink(db,p['dispatcher'],t.id,body)
    assert t.warranty_basis=='HET_HAN' and t.billing is None
    assert db.scalar(select(AuditLog.reason).where(AuditLog.action=='service_ticket.warranty'))=='Chọn đúng phiếu gốc'
    completed=ticket(db,p,'HOAN_THANH',free=True)
    completed.kind='BAO_HANH';db.commit()
    fail('PRICE_LOCKED',svc.relink,db,p['dispatcher'],completed.id,ServiceWarrantyLink(version=completed.version,warranty_of_id=parent.id))


def test_free_warranty_materials_still_issue_technician_px(db,held):
    p,wh,_=held;transfer(db,p);parent=previous(db,p);t=warranty(db,p,parent)
    tickets.assign(db,p['dispatcher'],t.id,p['technician'].id,t.version)
    for status in ('DA_NHAN','DA_HEN','DANG_LAM'):
        tickets.transition(db,p['technician'],t.id,status,t.version,appointment_at=tickets._now()+timedelta(days=1) if status=='DA_HEN' else None)
    service_charges.set_charges(db,p['technician'],t.id,ServiceCharges(version=t.version,billing='MIEN_PHI',free_reason=t.free_reason,
        lines=[dict(kind='VAT_TU',product_code='TD-0001',warehouse_code=wh.code,qty=1,unit_price=2000)]))
    attachments.add(db,p['technician'],'service_ticket',str(t.id),'SAU','sau.png',d5_image())
    tickets.transition(db,p['technician'],t.id,'HOAN_THANH',t.version)
    doc=db.get(StockDoc,t.lines[0].stock_doc_id)
    assert doc.kind=='PX' and doc.warehouse_code==wh.code and doc.status=='DA_GHI' and t.receivable==0
    assert db.scalar(select(StockLedger.amount).where(StockLedger.doc_id==doc.id))==-1000


def test_suggestions_order_serial_and_newest_with_status_filter(db,people):
    p=people;a=machine(db,p);b=machine(db,p,serial='KT6-002')
    old=previous(db,p,60,serial=a.serial);new=previous(db,p,10,serial=a.serial);other=previous(db,p,1,serial=b.serial)
    previous(db,p,0,status='DA_NHAN')
    assert [r.id for r in svc.suggestions(db,'KTD-000001',a.serial)]==[new.id,old.id,other.id]
    assert [r.id for r in svc.suggestions(db,'KTD-000001',q=new.number)]==[new.id]
    assert len(svc.suggestions(db,'KTD-000001',q='Minh Phat'))==3


def test_d7_counts_original_completing_technician_csv_and_links(client,db,people,monkeypatch):
    from app.web import dispatch
    p=people;parent=previous(db,p);t=warranty(db,p,parent)
    tickets.assign(db,p['dispatcher'],t.id,p['other_tech'].id,t.version)
    # Dữ liệu nhập lịch sử có ngày tạo trong kỳ; không đổi người hoàn thành của bước gốc.
    t.created_at=NOW;parent.assignee_id=p['other_tech'].id;db.commit()
    monkeypatch.setattr(dispatch,'_now',lambda:NOW)
    service_login(client,p['dispatcher'])
    data=client.get('/dieu-phoi/bao-cao.csv?from=2026-10-18&to=2026-10-18')
    rows=list(csv.reader(io.StringIO(data.content.decode('utf-8-sig'))))
    warranty_col=rows[0].index('Bị gọi bảo hành')
    assert rows[1][0]==p['technician'].full_name and rows[1][warranty_col]=='1'
    assert 'Bảo hành cho' in client.get(f'/dich-vu/{t.id}').text
    assert 'Đã bị gọi bảo hành' in client.get(f'/dich-vu/{parent.id}').text
    page=client.get('/dieu-phoi/bao-cao?from=2026-10-18&to=2026-10-18')
    assert page.status_code==200 and 'Bị gọi bảo hành' in page.text


def test_new_warranty_web_form_and_search_expiry(client,db,people):
    from test_my_work import Page
    p=people;parent=previous(db,p);expired=previous(db,p,100)
    service_login(client,p['dispatcher'])
    page=client.get('/dich-vu/moi');form=Page(page.text).forms['service-create']
    response=client.get('/dich-vu/tim-bao-hanh',params=dict(customer_code='KTD-000001',q=parent.number))
    assert parent.number in response.text and 'Còn hạn' in response.text
    response=client.post('/dich-vu/moi',data={**form['fields'],'customer_code':'KTD-000001','kind':'BAO_HANH','priority':'THUONG',
        'symptom':'Bảo hành lại lỗi kẹt giấy','warranty_of_id':str(expired.id),'appointment_choice':'CHIEU:'+(tickets._now()+timedelta(days=1)).date().isoformat()},follow_redirects=False)
    assert response.status_code==303
    page=client.get(response.headers['location']);assert 'Hết hạn bảo hành' in page.text


def test_warranty_api_fields_relink_roles_and_csrf(client,db,people):
    from conftest import PASSWORD
    from test_my_work import Page
    p=people;parent=previous(db,p);old=previous(db,p,100)
    def auth(role):
        r=client.post('/auth/login',data=dict(username=p[role].username,password=PASSWORD))
        return {'Authorization':'Bearer '+r.json()['access_token']}
    dp=auth('dispatcher');tech=auth('technician')
    body=dict(customer_code='KTD-000001',kind='BAO_HANH',symptom='Báo lỗi bảo hành',warranty_of_id=parent.id)
    r=client.post('/service-tickets',json=body,headers=dp);assert r.status_code==201
    data=r.json();assert data['warranty_basis']=='PHIEU_TRUOC' and data['billing']=='MIEN_PHI'
    assert client.post('/service-tickets',json={**body,'kind':'SUA_TAI_CHO'},headers=dp).status_code==422
    change=dict(version=data['version'],warranty_of_id=old.id,note='Đổi phiếu trước')
    url=f'/service-tickets/{data["id"]}/warranty'
    assert client.post(url,json=change,headers=tech).status_code==403
    r=client.post(url,json=change,headers=dp);assert r.status_code==200 and r.json()['warranty_basis']=='HET_HAN'
    service_login(client,p['dispatcher'])
    page=client.get(f'/dich-vu/{data["id"]}')
    form=Page(page.text).forms['warranty-link']
    fields={**form['fields'],'warranty_of_id':str(parent.id),'note':'Chọn lại phiếu gốc'}
    assert client.request('POST',form['action'],data={k:v for k,v in fields.items() if k!='csrf'}).status_code==403
    assert client.post(form['action'],data=fields,follow_redirects=False).status_code==303
