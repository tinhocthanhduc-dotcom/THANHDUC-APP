"""KT3 trên PostgreSQL thật; tồn chỉ được dựng và thay đổi qua lõi kho."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import (attachments, inventory_catalogs, my_work, part_requests as svc,
                 service_charges, service_tickets, stock_docs, stock_posting)
from app.errors import BusinessError
from app.models import AuditLog, FundLedger, PartRequest, ServiceStep, StockDoc, Warehouse
from app.schemas import PartRequestIn, ServiceCharges, WarehousePatch
from test_my_work import people, purchase_people, ticket, DAY
from test_technician_stock import held, balance, charges, error
from test_web import d5_image, service_login


def request(db, p, t, who='technician', qty=2):
    return svc.create(db, p[who], t.id, PartRequestIn(version=t.version, product_code='TD-0001', qty=qty, reason='Thiếu drum thay máy'))


def test_create_number_step_audit_and_dispatcher_without_own_warehouse(db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM')
    r=request(db,p,t)
    assert r.number.startswith('YC-26-') and r.number.endswith('00001')
    assert r.requester_id==p['technician'].id and t.status=='CHO_LINH_KIEN'
    step=db.scalars(select(ServiceStep).where(ServiceStep.ticket_id==t.id).order_by(ServiceStep.id.desc())).first()
    assert step.from_status=='DANG_LAM' and step.to_status=='CHO_LINH_KIEN' and r.number in step.note
    second=request(db,p,t,who='dispatcher')
    assert second.number.endswith('00002') and second.requester_id==p['dispatcher'].id
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action=='part_request.create'))==2
    assert r.from_warehouse_code is None and r.to_warehouse_code is None and r.stock_doc_id is None


@pytest.mark.parametrize('who,status,code',[
    ('other_tech','DANG_LAM','FORBIDDEN'),('sales','DANG_LAM','FORBIDDEN'),
    ('technician','DA_GIAO','BAD_STATUS'),('technician','HOAN_THANH','BAD_STATUS')])
def test_create_forbidden_and_bad_status_atomic(db, held, who, status, code):
    p,a,b=held;t=ticket(db,p,status)
    count=db.query(StockDoc).count();steps=len(t.steps)
    error(code,request,db,p,t,who=who)
    assert db.query(PartRequest).count()==0 and db.query(StockDoc).count()==count
    assert len(service_tickets.load(db,p['dispatcher'],t.id).steps)==steps


def test_no_tech_warehouse_reason_product_and_version(db, people):
    p=people;t=ticket(db,p,'DANG_LAM')
    error('NO_TECH_WAREHOUSE',request,db,p,t,who='dispatcher')
    assert db.query(PartRequest).count()==0


def test_creation_validation_and_version(db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM')
    for code,changes in [('REASON_REQUIRED',dict(reason='  ')),('BAD_PRODUCT',dict(product_code='BAD')),
                         ('VERSION_CONFLICT',dict(version=t.version+1))]:
        body=PartRequestIn(**{**dict(version=t.version, product_code='TD-0001',qty=1,reason='Cần drum'),**changes})
        error(code,svc.create,db,p['technician'],t.id,body)
        assert t.status=='DANG_LAM' and db.query(PartRequest).count()==0
    assert request(db,p,t).number.endswith('00001')


def test_supply_transfer_version_and_roles(db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM');r=request(db,p,t);old=r.version
    assert error('FORBIDDEN',svc.supply,db,p['technician'],r.id,'K58',r.version).status==403
    svc.supply(db,p['warehouse'],r.id,'K58',r.version)
    assert r.status=='DA_CAP' and r.from_warehouse_code=='K58' and r.to_warehouse_code==a.code
    doc=db.get(StockDoc,r.stock_doc_id)
    assert doc.kind=='CK' and doc.status=='DA_GHI' and doc.to_warehouse_code==a.code
    assert r.number in doc.reason and t.number in doc.reason
    assert balance(db,'K58')==8 and balance(db,a.code)==2
    n=db.query(StockDoc).count()
    error('VERSION_CONFLICT',svc.supply,db,p['warehouse'],r.id,'K58',old)
    assert db.query(StockDoc).count()==n
    assert db.scalar(select(AuditLog).where(AuditLog.action=='part_request.supply')).after['to_warehouse_code']==a.code
    assert db.query(FundLedger).count()==0


def test_supply_insufficient_and_bad_source_no_ck_or_state_change(db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM');r=request(db,p,t,qty=11)
    n=db.query(StockDoc).count();old=r.version
    for code,source in [('NEGATIVE_STOCK','K58'),('BAD_WAREHOUSE',a.code),('BAD_WAREHOUSE','NONE')]:
        error(code,svc.supply,db,p['warehouse'],r.id,source,old)
        assert r.status=='CHO_CAP' and r.version==old and r.stock_doc_id is None
        assert db.query(StockDoc).count()==n and balance(db,'K58')==10 and balance(db,a.code)==0


def test_dispatcher_creator_destination_at_supply_and_dispatcher_cancel(db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM');r=request(db,p,t,who='dispatcher')
    assert r.requester_id==p['dispatcher'].id
    service_tickets.assign(db,p['dispatcher'],t.id,p['other_tech'].id,t.version)
    svc.supply(db,p['warehouse'],r.id,'K58',r.version)
    assert r.to_warehouse_code==b.code and balance(db,b.code)==2 and balance(db,a.code)==0
    t2=ticket(db,p,'DANG_LAM');r2=request(db,p,t2)
    svc.cancel(db,p['dispatcher'],r2.id,'Khách đổi phương án',r2.version)
    assert r2.status=='DA_HUY'


def test_cancel_and_out_of_stock_requires_reason_and_creator(db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM');r=request(db,p,t)
    error('REASON_REQUIRED',svc.out_of_stock,db,p['warehouse'],r.id,' ',r.version)
    error('FORBIDDEN',svc.cancel,db,p['other_tech'],r.id,'Không cần nữa',r.version)
    error('REASON_REQUIRED',svc.cancel,db,p['technician'],r.id,' ',r.version)
    svc.cancel(db,p['technician'],r.id,'Đã có hàng khác',r.version)
    assert r.status=='DA_HUY' and r.decision_note=='Đã có hàng khác'
    error('BAD_STATUS',svc.cancel,db,p['technician'],r.id,'Hủy lại',r.version)
    r2=request(db,p,t);svc.out_of_stock(db,p['warehouse'],r2.id,'Chưa nhập về',r2.version)
    assert r2.status=='HET_HANG' and r2.decision_note=='Chưa nhập về'
    r3=request(db,p,t);svc.supply(db,p['warehouse'],r3.id,'K58',r3.version)
    error('BAD_STATUS',svc.cancel,db,p['technician'],r3.id,'Không cần',r3.version)


def test_ready_resume_use_supplied_parts_and_complete(db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM');r=request(db,p,t);r2=request(db,p,t,qty=1)
    svc.supply(db,p['warehouse'],r.id,'K58',r.version)
    def ready():return next(g for g in my_work.collect(db,p['technician']) if g['key']=='service_parts_ready')
    assert ready()['count']==0
    svc.supply(db,p['warehouse'],r2.id,'K58',r2.version)
    assert [row['id'] for row in ready()['rows']]==[t.id]
    service_tickets.transition(db,p['technician'],t.id,'DANG_LAM',t.version)
    charges(db,p,t)
    attachments.add(db,p['technician'],'service_ticket',str(t.id),'SAU','sau.png',d5_image())
    service_tickets.transition(db,p['technician'],t.id,'HOAN_THANH',t.version)
    doc=db.get(StockDoc,t.lines[0].stock_doc_id)
    assert doc.kind=='PX' and doc.warehouse_code==a.code and doc.status=='DA_GHI'
    assert balance(db,a.code)==2 and balance(db,'K58')==7
    assert ready()['count']==0 and db.query(FundLedger).count()==0


def test_pause_required_resume_cancel_failure_and_api(db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM')
    error('HOLD_REASON_REQUIRED',service_tickets.transition,db,p['technician'],t.id,'TAM_DUNG',t.version)
    error('REASON_REQUIRED',service_tickets.transition,db,p['technician'],t.id,'TAM_DUNG',t.version,hold_reason='KHAC')
    service_tickets.transition(db,p['technician'],t.id,'TAM_DUNG',t.version,'Chờ khách đồng ý',hold_reason='CHO_KHACH_DUYET')
    assert t.hold_reason=='CHO_KHACH_DUYET' and t.status in service_tickets.OPEN
    assert service_tickets.search(db,p['dispatcher'],status='TAM_DUNG')==[t]
    service_tickets.transition(db,p['technician'],t.id,'DANG_LAM',t.version)
    assert t.hold_reason==''
    service_tickets.transition(db,p['technician'],t.id,'TAM_DUNG',t.version,'Thiếu dụng cụ',hold_reason='KHAC')
    service_tickets.transition(db,p['technician'],t.id,'KHONG_LAM_DUOC',t.version,'Không có dụng cụ')
    assert t.status=='KHONG_LAM_DUOC'
    second=ticket(db,p,'DANG_LAM')
    service_tickets.transition(db,p['technician'],second.id,'TAM_DUNG',second.version,hold_reason='CHO_DOI_TAC')
    service_tickets.transition(db,p['dispatcher'],second.id,'DA_HUY',second.version,'Khách hủy lịch')
    assert second.status=='DA_HUY'


def test_unchanged_dispatcher_material_can_keep_but_not_change_or_duplicate(db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM');charges(db,p,t,warehouse='K58',who='dispatcher')
    line=dict(kind='VAT_TU',product_code='TD-0001',warehouse_code='K58',qty=1,unit_price=2000,vat_rate=0)
    fee=dict(kind='PHI',description='Công thay drum',qty=1,unit_price=50000,vat_rate=0)
    service_charges.set_charges(db,p['technician'],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',lines=[line,fee]))
    assert len(t.lines)==2 and t.lines[0].warehouse_code=='K58'
    for lines in [[{**line,'qty':2},fee],[{**line,'product_code':'TD-0002'},fee],[line,line,fee]]:
        error('TECH_WAREHOUSE_ONLY',service_charges.set_charges,db,p['technician'],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',lines=lines))
    assert len(t.lines)==2 and t.lines[0].qty==1


def test_web_request_supply_history_ready_and_csrf(client, db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM');csrf=service_login(client,p['technician'])
    assert client.get(f'/dich-vu/{t.id}').status_code==200
    url=f'/dich-vu/{t.id}/yeu-cau-linh-kien'
    data=dict(version=t.version,product_code='TD-0001',qty='2',reason='Cần thay drum')
    assert client.post(url,data=data).status_code==403
    response=client.post(url,data={**data,'csrf':csrf})
    assert response.status_code==200 and 'Chờ cấp' in response.text
    r=db.scalar(select(PartRequest));db.refresh(t)
    picker=client.get(f'/dich-vu/{t.id}/tim-linh-kien?q=may in')
    assert picker.status_code==200 and 'TD-0001' in picker.text and 'K58: 10' in picker.text
    assert client.post(f'/yeu-cau-linh-kien/{r.id}/cap',data=dict(csrf=csrf,version=r.version,warehouse_code='K58')).status_code==403
    csrf=service_login(client,p['warehouse'])
    response=client.get('/yeu-cau-linh-kien');assert response.status_code==200 and r.number in response.text
    assert client.post(f'/yeu-cau-linh-kien/{r.id}/cap',data=dict(version=r.version,warehouse_code='K58')).status_code==403
    response=client.post(f'/yeu-cau-linh-kien/{r.id}/cap',data=dict(csrf=csrf,version=r.version,warehouse_code='K58'))
    assert response.status_code==200
    csrf=service_login(client,p['technician'])
    response=client.get('/viec-cua-toi');assert 'Đã có linh kiện' in response.text and t.number in response.text
    response=client.get(f'/dich-vu/{t.id}');assert 'Tiếp tục làm' in response.text and 'CK-26-' in response.text
    response=client.post(f'/dich-vu/{t.id}/transition',data=dict(csrf=csrf,version=t.version,to_status='DANG_LAM'))
    assert response.status_code==200 and 'Đang làm' in response.text


def test_web_out_of_stock_purchase_prefill_cancel_and_validation_retention(client, db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM');csrf=service_login(client,p['technician'])
    response=client.post(f'/dich-vu/{t.id}/yeu-cau-linh-kien',data=dict(csrf=csrf,version=t.version,product_code='TD-0001',qty='2',reason=''))
    assert response.status_code==400 and 'value="TD-0001"' in response.text and 'value="2"' in response.text
    r=request(db,p,t);csrf=service_login(client,p['warehouse'])
    response=client.post(f'/yeu-cau-linh-kien/{r.id}/het-hang',data=dict(csrf=csrf,version=r.version,note=''))
    assert response.status_code==400 and 'Phải ghi chú' in response.text
    response=client.post(f'/yeu-cau-linh-kien/{r.id}/het-hang',data=dict(csrf=csrf,version=r.version,note='Đang chờ nhập'))
    assert response.status_code==200 and 'Lập đơn mua' in response.text and 'product_code=TD-0001' in response.text
    response=client.get('/don-mua/moi?product_code=TD-0001')
    assert response.status_code==200 and 'value="TD-0001"' in response.text
    assert client.get('/viec-cua-toi').status_code==200


def test_web_pause_dispatch_filter_and_report(client, db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM');other=ticket(db,p,'DANG_LAM')
    csrf=service_login(client,p['technician']);url=f'/dich-vu/{t.id}/transition'
    response=client.post(url,data=dict(csrf=csrf,version=t.version,to_status='TAM_DUNG',note='Khách vắng nhà'))
    assert response.status_code==400 and 'Phải chọn lý do' in response.text and 'Khách vắng nhà' in response.text
    response=client.post(url,data=dict(csrf=csrf,version=t.version,to_status='TAM_DUNG',hold_reason='HEN_NGAY_KHAC',note='Khách vắng nhà'))
    assert response.status_code==200 and 'Hẹn ngày khác' in response.text
    service_login(client,p['dispatcher'])
    response=client.get('/dieu-phoi?view=paused')
    assert response.status_code==200 and t.number in response.text and other.number not in response.text and 'Hẹn ngày khác' in response.text
    # ServiceStep timestamp do PostgreSQL tạo, dùng đúng ngày giờ VN thực tế cho kỳ báo cáo.
    day=datetime.now(service_tickets.service_sla.TZ).date().isoformat()
    response=client.get(f'/dieu-phoi/bao-cao?from={day}&to={day}&status=TAM_DUNG')
    assert response.status_code==200 and t.number in response.text and 'Hẹn ngày khác' in response.text
    csv=client.get(f'/dieu-phoi/bao-cao.csv?from={day}&to={day}&status=TAM_DUNG')
    assert csv.status_code==200 and t.number in csv.text and 'Lý do tạm dừng' in csv.text


def test_concurrent_supply_only_one_ck(engine, db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM');r=request(db,p,t)
    ident,version,uid=r.id,r.version,p['warehouse'].id
    db.rollback()
    gate=Barrier(2)
    def supply():
        from app.models import User
        with Session(engine) as session:
            user=session.get(User,uid);gate.wait(timeout=10)
            try:svc.supply(session,user,ident,'K58',version);return 'OK'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:supply(),range(2)))
    assert sorted(results)==['OK','VERSION_CONFLICT']
    assert db.scalar(select(func.count()).select_from(StockDoc).where(StockDoc.kind=='CK'))==1
    assert balance(db,a.code)==2 and balance(db,'K58')==8


def test_supply_looks_up_current_tech_warehouse_and_rejects_closed_ticket(db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM');r=request(db,p,t)
    inventory_catalogs.update(db,p['warehouse'],Warehouse,a.code,WarehousePatch(version=a.version,active=False))
    error('NO_TECH_WAREHOUSE',svc.supply,db,p['warehouse'],r.id,'K58',r.version)
    assert r.status=='CHO_CAP' and r.stock_doc_id is None
    service_tickets.transition(db,p['dispatcher'],t.id,'DA_HUY',t.version,'Khách ngừng sửa')
    error('BAD_STATUS',svc.supply,db,p['warehouse'],r.id,'K58',r.version)


def test_charge_version_changes_even_when_only_lines_change(db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM');charges(db,p,t,warehouse='K58',who='dispatcher')
    old=t.version
    body=ServiceCharges(version=old,billing='THU_TIEN',lines=[dict(kind='VAT_TU',product_code='TD-0001',warehouse_code='K58',qty=1,unit_price=2000,vat_rate=0),dict(kind='PHI',description='Công sửa',qty=1,unit_price=10000,vat_rate=0)])
    service_charges.set_charges(db,p['technician'],t.id,body)
    assert t.version>old
    error('VERSION_CONFLICT',service_charges.set_charges,db,p['technician'],t.id,body)


def test_web_preserves_dispatcher_warehouse_line_when_tech_adds_fee(client, db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM');charges(db,p,t,warehouse='K58',who='dispatcher')
    csrf=service_login(client,p['technician'])
    response=client.get(f'/dich-vu/{t.id}')
    assert 'name="warehouse_0" value="K58" data-charge-warehouse checked' in response.text
    data=dict(csrf=csrf,version=t.version,billing='THU_TIEN',line_kind=['VAT_TU','PHI'],line_product=['TD-0001',''],
        line_description=['Drum','Công sửa'],line_qty=['1','1'],line_price=['2.000','50.000'],warehouse_0='K58',warehouse_1='',vat_0='0',vat_1='0')
    response=client.post(f'/dich-vu/{t.id}/tien/luu',data=data)
    assert response.status_code==200
    db.refresh(t);assert len(t.lines)==2 and t.lines[1].unit_price==50000
    data.update(version=t.version,line_qty=['2','1'])
    response=client.post(f'/dich-vu/{t.id}/tien/luu',data=data)
    assert response.status_code==409 and 'TECH_WAREHOUSE_ONLY' in response.text and 'value="2"' in response.text


def test_api_create_supply_pause_and_permissions(client, db, held):
    from conftest import PASSWORD
    p,a,b=held;t=ticket(db,p,'DANG_LAM')
    def auth(role):
        token=client.post('/auth/login',data=dict(username=p[role].username,password=PASSWORD)).json()['access_token']
        return {'Authorization':f'Bearer {token}'}
    response=client.post(f'/service-tickets/{t.id}/part-requests',json=dict(version=t.version,product_code='TD-0001',qty='2',reason='Cần drum'),headers=auth('technician'))
    assert response.status_code==201,response.text
    r=response.json();assert r['requester_id']==p['technician'].id and r['to_warehouse_code'] is None
    assert client.get('/part-requests',headers=auth('finance')).status_code==403
    response=client.post(f"/part-requests/{r['id']}/supply",json=dict(version=r['version'],warehouse_code='K58'),headers=auth('warehouse'))
    assert response.status_code==200 and response.json()['to_warehouse_code']==a.code
    db.refresh(t)
    response=client.post(f'/service-tickets/{t.id}/transition',json=dict(to_status='DANG_LAM',version=t.version),headers=auth('technician'))
    assert response.status_code==200
    response=client.post(f'/service-tickets/{t.id}/transition',json=dict(to_status='TAM_DUNG',version=response.json()['version'],hold_reason='CHO_DOI_TAC'),headers=auth('technician'))
    assert response.status_code==200 and response.json()['hold_reason']=='CHO_DOI_TAC'


def test_web_missing_ticket_returns_404_and_foreign_technician_403(client, db, held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM')
    csrf=service_login(client,p['other_tech'])
    data=dict(csrf=csrf,version=t.version,product_code='TD-0001',qty='1',reason='Cần drum')
    response=client.post(f'/dich-vu/{t.id}/yeu-cau-linh-kien',data=data)
    assert response.status_code==403 and db.query(PartRequest).count()==0
    response=client.post('/dich-vu/999999/yeu-cau-linh-kien',data=data)
    assert response.status_code==404 and db.query(PartRequest).count()==0
