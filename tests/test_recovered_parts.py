from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import select,text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import alerts,attachments,service_recovered_parts as svc,service_tickets,service_charges,stock_posting,technician_stock,inventory_catalogs
from app.models import AuditLog,FundLedger,SalesReceipt,ServiceRecoveredPart,StockBalance,StockDoc,StockLedger,User,Warehouse
from app.schemas import RecoveredPartIn,RecoveredPartReceive,ServiceCharges,WarehouseIn
from test_my_work import people,purchase_people,ticket,NOW
from test_technician_stock import held,transfer
from test_service_warranty import fail
from test_web import service_login,d5_image


def recover(db,p,t,**kw):
    return svc.create(db,p['technician'],t.id,RecoveredPartIn(version=t.version,product_code='TD-0001',qty=kw.get('qty',1),note='Drum trầy, hỏng lớp phủ'))


def defect(db,p):
    return inventory_catalogs.create(db,p['warehouse'],Warehouse,WarehouseIn(code='K900',name='Kho linh kiện hỏng',kind='LOI',recovered_only=True))


def balances(db):return db.execute(text('select * from stock_balances order by warehouse_code,product_code')).all()


def test_recover_no_stock_or_funds_and_integer_positive_qty(db,held):
    p,_,_=held;transfer(db,p);t=ticket(db,p,'DANG_LAM');before=balances(db);docs=db.query(StockDoc).count()
    row=recover(db,p,t,qty=2)
    assert (row.status,row.holder_id,row.qty,row.stock_doc_id)==('CHO_NOP',p['technician'].id,2,None)
    assert balances(db)==before and db.query(StockDoc).count()==docs
    assert db.query(FundLedger).count()==db.query(SalesReceipt).count()==0
    for qty in (0,-1,1.5,True):
        with pytest.raises(ValidationError):RecoveredPartIn(product_code='TD-0001',qty=qty,note='Hỏng')


def test_receive_only_defect_zero_cost_good_and_technician_average_unchanged(db,held):
    p,wh,_=held;transfer(db,p);t=ticket(db,p,'DANG_LAM');row=recover(db,p,t,qty=2)
    before=balances(db);defect(db,p)
    svc.receive(db,p['warehouse'],row.id,RecoveredPartReceive(version=row.version))
    doc=db.get(StockDoc,row.stock_doc_id);ledger=db.scalar(select(StockLedger).where(StockLedger.doc_id==doc.id))
    assert (row.status,row.warehouse_code,doc.kind,doc.status)==('DA_NHAN','K900','PN','DA_GHI')
    assert doc.lines[0].unit_cost==0 and ledger.amount==ledger.unit_cost==0 and ledger.qty_in==2
    assert [r for r in balances(db) if r.warehouse_code!='K900']==before
    assert db.get(StockBalance,('K900','TD-0001')).qty==2
    assert db.query(FundLedger).count()==db.query(SalesReceipt).count()==0


@pytest.mark.parametrize('code',[None,'K58','K501'])
def test_no_defect_warehouse_or_bad_target(db,held,code):
    p,_,_=held;t=ticket(db,p,'DANG_LAM');row=recover(db,p,t);before=balances(db);docs=db.query(StockDoc).count()
    fail('NO_DEFECT_WAREHOUSE',svc.receive,db,p['warehouse'],row.id,RecoveredPartReceive(version=row.version,warehouse_code=code))
    assert row.status=='CHO_NOP' and balances(db)==before and db.query(StockDoc).count()==docs


def test_receive_permissions_and_cancel_requirements(db,people):
    p=people;t=ticket(db,p,'DANG_LAM');row=recover(db,p,t);defect(db,p)
    fail('FORBIDDEN',svc.receive,db,p['technician'],row.id,RecoveredPartReceive(version=row.version))
    fail('FORBIDDEN',svc.receive,db,p['finance'],row.id,RecoveredPartReceive(version=row.version))
    fail('FORBIDDEN',svc.cancel,db,p['finance'],row.id,row.version,'Lý do')
    fail('REASON_REQUIRED',svc.cancel,db,p['technician'],row.id,row.version,' ')
    svc.cancel(db,p['technician'],row.id,row.version,'Khách giữ lại linh kiện')
    assert row.status=='DA_HUY' and row.void_reason=='Khách giữ lại linh kiện'
    row=recover(db,p,t);svc.receive(db,p['warehouse'],row.id,RecoveredPartReceive(version=row.version))
    fail('BAD_STATUS',svc.cancel,db,p['technician'],row.id,row.version,'Hủy hàng đã nộp')


def test_concurrent_warehouse_receive_single_pn(db,people,engine,make_user):
    p=people;t=ticket(db,p,'DANG_LAM');row=recover(db,p,t);defect(db,p)
    other=make_user('kt6.warehouse2','warehouse');users=[p['warehouse'].id,other.id];ident,version=row.id,row.version
    db.rollback();barrier=Barrier(2)
    def receive(uid):
        with Session(engine) as s:
            user=s.get(User,uid);barrier.wait(timeout=10)
            try:svc.receive(s,user,ident,RecoveredPartReceive(version=version));return 'OK'
            except svc.BusinessError as exc:return exc.code
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(receive,users))==['OK','VERSION_CONFLICT']
    db.expire_all();assert db.query(StockDoc).count()==1 and row.status=='DA_NHAN'


def test_stock_pn_void_reopens_same_holder_and_receive_again(db,people):
    p=people;t=ticket(db,p,'DANG_LAM');row=recover(db,p,t);defect(db,p)
    svc.receive(db,p['warehouse'],row.id,RecoveredPartReceive(version=row.version))
    old_id=row.stock_doc_id;doc=db.get(StockDoc,old_id)
    stock_posting.void(db,p['warehouse'],doc.id,'Nhận nhầm số lượng',doc.version)
    assert row.status=='CHO_NOP' and row.stock_doc_id is None and row.warehouse_code is None
    assert row.received_at is None and row.received_by is None and row.holder_id==p['technician'].id
    assert db.get(StockBalance,('K900','TD-0001')).qty==0
    svc.receive(db,p['warehouse'],row.id,RecoveredPartReceive(version=row.version))
    assert row.stock_doc_id!=old_id and row.status=='DA_NHAN'
    assert db.scalar(select(AuditLog.reason).where(AuditLog.action=='recovered_part.receipt_void'))=='Nhận nhầm số lượng'
    from test_cxl1_pending_items import assert_pending_invariant
    assert_pending_invariant(db)


def test_complete_allowed_but_close_blocked_until_received_or_cancelled(db,people):
    p=people;t=ticket(db,p,'DANG_LAM');a=recover(db,p,t);b=recover(db,p,t)
    attachments.add(db,p['technician'],'service_ticket',str(t.id),'SAU','sau.png',d5_image())
    service_charges.set_charges(db,p['technician'],t.id,ServiceCharges(version=t.version,billing='MIEN_PHI',free_reason='Bảo hành'))
    service_tickets.transition(db,p['technician'],t.id,'HOAN_THANH',t.version)
    fail('RECOVERED_PENDING',service_tickets.transition,db,p['dispatcher'],t.id,'DA_DONG',t.version)
    defect(db,p);svc.receive(db,p['warehouse'],a.id,RecoveredPartReceive(version=a.version))
    svc.cancel(db,p['dispatcher'],b.id,b.version,'Khách giữ lại')
    assert t.status=='DA_DONG'


def test_reassignment_preserves_holder_and_old_holder_access(client,db,people):
    p=people;t=ticket(db,p,'DANG_LAM');row=recover(db,p,t)
    service_tickets.assign(db,p['dispatcher'],t.id,p['other_tech'].id,t.version)
    assert row.holder_id==p['technician'].id
    assert [r.id for r in svc.search(db,p['technician'])]==[row.id]
    assert [r.id for r in svc.search(db,p['other_tech'])]==[row.id]
    assert [r.id for r in technician_stock.held(db,p['technician'])['recovered_parts']]==[row.id]
    fail('FORBIDDEN',svc.cancel,db,p['other_tech'],row.id,row.version,'Không giữ thực tế')
    service_login(client,p['technician'])
    page=client.get('/hang-toi-giu');assert 'Linh kiện hỏng chưa nộp' in page.text and row.note in page.text
    assert row.note in client.get('/linh-kien-thu-hoi').text
    svc.cancel(db,p['technician'],row.id,row.version,'Khách giữ lại')


def test_finance_read_only_and_unrelated_technician_privacy(db,people,make_user):
    p=people;t=ticket(db,p,'DANG_LAM');row=recover(db,p,t)
    assert svc.search(db,p['other_tech'])==[]
    assert [r.id for r in svc.search(db,p['finance'])]==[row.id]
    fail('NOT_FOUND',svc.cancel,db,p['other_tech'],row.id,row.version,'Không được xem')
    fail('FORBIDDEN',svc.create,db,p['finance'],t.id,RecoveredPartIn(version=t.version,product_code='TD-0001',qty=1,note='Hỏng'))


def test_raw_delete_and_identity_change_blocked(db,people):
    t=ticket(db,people,'DANG_LAM');row=recover(db,people,t)
    for sql in ('delete from service_recovered_parts where id=:id', 'update service_recovered_parts set qty=2,version=version+1 where id=:id'):
        with pytest.raises(DBAPIError),db.begin_nested():db.execute(text(sql),dict(id=row.id))


def test_receive_failure_rolls_back_stock_and_part(db,people,monkeypatch):
    p=people;t=ticket(db,p,'DANG_LAM');row=recover(db,p,t);defect(db,p);before=balances(db)
    actual=stock_posting.post_in_tx
    def broken(*args,**kw):
        actual(*args,**kw)
        raise svc.BusinessError('TEST_FAILURE','Lỗi giả lập')
    monkeypatch.setattr(stock_posting,'post_in_tx',broken)
    fail('TEST_FAILURE',svc.receive,db,p['warehouse'],row.id,RecoveredPartReceive(version=row.version))
    assert row.status=='CHO_NOP' and balances(db)==before and db.query(StockDoc).count()==0


def test_alert_over_three_days_and_clears_after_cancel(db,people):
    p=people;t=ticket(db,p,'DANG_LAM');row=recover(db,p,t)
    boundary=row.created_at+timedelta(days=3)
    assert not any(a['kind']=='THU_HOI_CHUA_NOP' for a in alerts.collect(db,p['director'],now=boundary))
    found=[a for a in alerts.collect(db,p['finance'],now=boundary+timedelta(seconds=1)) if a['kind']=='THU_HOI_CHUA_NOP']
    assert len(found)==1 and p['technician'].full_name in found[0]['detail']
    svc.cancel(db,p['technician'],row.id,row.version,'Khách giữ lại')
    assert not any(a['kind']=='THU_HOI_CHUA_NOP' for a in alerts.collect(db,p['director'],now=boundary+timedelta(seconds=1)))




def test_web_create_receive_cancel_csrf_and_replay(client,db,people):
    from test_my_work import Page
    from web_forms import Forms
    p=people;t=ticket(db,p,'DANG_LAM');defect(db,p)
    service_login(client,p['technician'])
    page=client.get(f'/dich-vu/{t.id}');assert page.status_code==200
    form=Page(page.text).forms['recovered-create']
    data={**form['fields'],'product_code':'TD-0001','qty':'2','note':'Drum trầy, hỏng lớp phủ'}
    assert client.request('POST',form['action'],data={k:v for k,v in data.items() if k!='csrf'}).status_code==403
    response=client.post(form['action'],data=data,follow_redirects=False);assert response.status_code==303
    assert client.post(form['action'],data=data,follow_redirects=False).status_code==303
    assert db.query(ServiceRecoveredPart).count()==1
    row=db.scalar(select(ServiceRecoveredPart));ident=row.id
    service_login(client,p['warehouse'])
    page=client.get('/linh-kien-thu-hoi');assert page.status_code==200 and 'Nhận hàng lỗi' in page.text
    assert client.get('/linh-kien-thu-hoi?tho=&status=CHO_NOP').status_code==200
    forms=Forms(page.text,page.url)
    receive=next(f for f in forms.forms if f['path']==f'/linh-kien-thu-hoi/{ident}/nhan')
    data={**receive['fields'],'warehouse_code':'K900'}
    assert client.request('POST',receive['path'],data={k:v for k,v in data.items() if k!='csrf'}).status_code==403
    assert client.post(receive['path'],data=data,follow_redirects=False).status_code==303
    assert client.post(receive['path'],data=data,follow_redirects=False).status_code==303
    db.expire_all();assert row.status=='DA_NHAN' and db.query(StockDoc).count()==1
    service_login(client,p['finance'])
    page=client.get('/linh-kien-thu-hoi?status=');assert page.status_code==200
    assert '/nhan"' not in page.text and '/huy"' not in page.text


def test_recovered_api_integer_qty_roles_and_cancel_csrf(client,db,people):
    from conftest import PASSWORD
    from web_forms import Forms
    p=people;t=ticket(db,p,'DANG_LAM')
    def auth(role):
        r=client.post('/auth/login',data=dict(username=p[role].username,password=PASSWORD))
        return {'Authorization':'Bearer '+r.json()['access_token']}
    mine,other,finance=auth('technician'),auth('other_tech'),auth('finance')
    url=f'/service-tickets/{t.id}/recovered-parts'
    body=dict(version=t.version,product_code='TD-0001',qty=1,note='Hỏng drum')
    assert client.post(url,json={**body,'qty':0},headers=mine).status_code==422
    assert client.post(url,json=body,headers=other).status_code==404
    r=client.post(url,json=body,headers=mine);assert r.status_code==201
    row=r.json();assert row['status']=='CHO_NOP' and row['holder_id']==p['technician'].id
    assert client.get(url,headers=other).json()==[]
    assert len(client.get('/recovered-parts',headers=finance).json())==1
    assert client.post(f'/recovered-parts/{row["id"]}/receive',json=dict(version=row['version']),headers=mine).status_code==403
    assert client.post(f'/recovered-parts/{row["id"]}/cancel',json=dict(version=row['version'],reason='Khách giữ lại'),headers=finance).status_code==403
    service_login(client,p['technician'])
    page=client.get('/linh-kien-thu-hoi');form=next(f for f in Forms(page.text,page.url).forms if f['path']==f'/linh-kien-thu-hoi/{row["id"]}/huy')
    data={**form['fields'],'reason':'Khách giữ lại'}
    assert client.request('POST',form['path'],data={k:v for k,v in data.items() if k!='csrf'}).status_code==403
    assert client.post(form['path'],data=data,follow_redirects=False).status_code==303
