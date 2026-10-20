"""KT5: transaction, concurrency, lịch sử bất biến, migration và báo cáo PostgreSQL thật."""
import csv
import importlib.util
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app import attachments, service_charges, service_tickets as svc, service_visits
from app.errors import BusinessError
from app.models import EquipmentCustody, ServiceStep, ServiceVisit, User
from app.schemas import ServiceCharges
from app.web import dispatch
from test_my_work import people, purchase_people, ticket, NOW
from test_technician_stock import held
from test_web import d5_image, service_login

FUTURE = NOW + timedelta(days=1)


def visits(db, t):
    db.expire_all()
    return service_visits.list_for(db, t.id)


def fail(code, fn, *args, **kwargs):
    with pytest.raises(BusinessError) as exc:
        fn(*args, **kwargs)
    assert exc.value.code == code


def prepare_completion(db, p, t):
    attachments.add(db, p['technician'], 'service_ticket', str(t.id), 'SAU', 'sau.png', d5_image())
    service_charges.set_charges(db, p['technician'], t.id,
        ServiceCharges(version=t.version, billing='MIEN_PHI', free_reason='Bảo hành'))


def unchanged_ledgers(db):
    return {name: db.execute(text(f'SELECT * FROM {name} ORDER BY 1')).all() for name in (
        'service_lines', 'stock_docs', 'stock_ledger', 'stock_balances', 'stock_reservations',
        'sales_receipts', 'cash_docs', 'fund_ledger', 'fund_balances', 'equipment_custody')}


def test_arrival_opens_assigned_visit_and_duplicate_blocked(db, people):
    p = people; t = ticket(db, p, 'DA_HEN')
    svc.transition(db, p['dispatcher'], t.id, 'DANG_LAM', t.version)
    row, = visits(db, t)
    assert (row.technician_id, row.started_at, row.ended_at, row.outcome, row.version) == (p['technician'].id, NOW, None, None, 1)
    fail('BAD_TRANSITION', svc.transition, db, p['technician'], t.id, 'DANG_LAM', None)
    assert len(visits(db, t)) == 1
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(ServiceVisit(ticket_id=t.id, technician_id=p['technician'].id, started_at=NOW)); db.flush()


def test_concurrent_arrival_only_one_request_opens_visit(db, people, engine):
    t = ticket(db, people, 'DA_HEN'); tid, version, uid = t.id, t.version, people['technician'].id
    db.rollback(); barrier = Barrier(2)
    def arrive():
        with Session(engine) as s:
            u = s.get(User, uid); barrier.wait(timeout=10)
            try:
                svc.transition(s, u, tid, 'DANG_LAM', version)
                return 'OK'
            except BusinessError as exc:
                return exc.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: arrive(), range(2)))
    assert sorted(results) == ['OK', 'VERSION_CONFLICT']
    assert len(visits(db, t)) == 1


def test_partial_index_blocks_concurrent_raw_inserts(db, people, engine):
    t = ticket(db, people, 'DA_HEN'); tid, uid = t.id, people['technician'].id
    db.rollback(); barrier = Barrier(2)
    def insert():
        with Session(engine) as s:
            barrier.wait(timeout=10)
            try:
                s.add(ServiceVisit(ticket_id=tid, technician_id=uid, started_at=NOW)); s.commit()
                return 'OK'
            except IntegrityError as exc:
                s.rollback()
                assert exc.orig.diag.constraint_name == 'uq_service_visits_open_ticket'
                return 'UNIQUE'
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: insert(), range(2))) == ['OK', 'UNIQUE']
    assert len(visits(db, t)) == 1


@pytest.mark.parametrize('target,outcome', [('HOAN_THANH','XONG'), ('CHO_LINH_KIEN','CHO_LINH_KIEN'),
    ('TAM_DUNG','TAM_DUNG'), ('KHONG_LAM_DUOC','KHONG_LAM_DUOC'), ('DA_HUY','HUY'), ('assign','CHUYEN_THO')])
def test_every_exit_closes_once_with_note(db, people, monkeypatch, target, outcome):
    p = people; t = ticket(db, p, 'DANG_LAM'); start = visits(db,t)[0].started_at
    monkeypatch.setattr(svc, '_now', lambda: NOW + timedelta(hours=2))
    if target == 'HOAN_THANH': prepare_completion(db, p, t)
    if target == 'assign':
        svc.assign(db, p['dispatcher'], t.id, p['other_tech'].id, t.version, 'Cần quay lại sau')
    else:
        svc.transition(db, p['dispatcher'] if target == 'DA_HUY' else p['technician'], t.id,
            target, t.version, 'Cần quay lại sau', hold_reason='KHAC' if target == 'TAM_DUNG' else '')
    row, = visits(db,t)
    assert (row.outcome, row.note, row.version, row.technician_id) == (outcome, 'Cần quay lại sau', 2, p['technician'].id)
    assert row.ended_at - start == timedelta(hours=2)


@pytest.mark.parametrize('appointment,note,code', [(None,'Khách bận','APPOINTMENT_REQUIRED'),
    (NOW-timedelta(seconds=1),'Khách bận','BAD_APPOINTMENT'), (NOW,'Khách bận','BAD_APPOINTMENT'),
    (FUTURE,'  ','REASON_REQUIRED')])
def test_return_invalid_rolls_back_everything(db, people, appointment, note, code):
    t = ticket(db, people, 'DANG_LAM'); db.refresh(t); before = svc.snapshot(t); ledgers = unchanged_ledgers(db)
    steps = db.query(ServiceStep).count()
    fail(code, svc.transition, db, people['technician'], t.id, 'DA_HEN', t.version, note, appointment)
    assert svc.snapshot(t) == before and unchanged_ledgers(db) == ledgers
    assert db.query(ServiceStep).count() == steps
    assert visits(db,t)[0].ended_at is None


def test_return_preserves_charges_materials_and_ledger(db, people):
    t = ticket(db, people, 'DANG_LAM')
    service_charges.set_charges(db, people['technician'], t.id, ServiceCharges(version=t.version,
        billing='THU_TIEN', lines=[dict(kind='PHI', description='Công sửa', qty=1, unit_price=200000, vat_rate=0)]))
    before = unchanged_ledgers(db)
    charges = (t.billing,t.total,t.priced_at,t.receivable)
    svc.transition(db, people['technician'], t.id, 'DA_HEN', t.version, 'Khách bận họp', FUTURE)
    row, = visits(db,t)
    assert t.status == 'DA_HEN' and t.appointment_at == FUTURE
    assert row.outcome == 'HEN_LAI' and row.next_appointment_at == FUTURE
    assert unchanged_ledgers(db) == before and (t.billing,t.total,t.priced_at,t.receivable) == charges


def test_three_visit_sequence_same_ticket_and_reopening(db, people):
    p = people; t = ticket(db, p, 'DANG_LAM'); number = t.number
    svc.transition(db,p['technician'],t.id,'DA_HEN',t.version,'Khách hẹn ngày mai',FUTURE)
    svc.transition(db,p['technician'],t.id,'DANG_LAM',t.version)
    svc.transition(db,p['technician'],t.id,'CHO_LINH_KIEN',t.version,'Chờ linh kiện')
    svc.transition(db,p['technician'],t.id,'DANG_LAM',t.version)
    prepare_completion(db,p,t)
    svc.transition(db,p['technician'],t.id,'HOAN_THANH',t.version,'Đã xong')
    assert [v.outcome for v in visits(db,t)] == ['HEN_LAI','CHO_LINH_KIEN','XONG']
    assert t.number == number
    svc.transition(db,p['dispatcher'],t.id,'DANG_LAM',t.version,'Khách báo lỗi lại')
    assert [v.outcome for v in visits(db,t)] == ['HEN_LAI','CHO_LINH_KIEN','XONG',None]


@pytest.mark.parametrize('appointment,note,code', [(None,'Đã gọi cô Lan','APPOINTMENT_REQUIRED'),
    (FUTURE,'','REASON_REQUIRED'), (NOW,'Đã gọi cô Lan','BAD_APPOINTMENT')])
def test_no_show_validation(db, people, appointment, note, code):
    t = ticket(db,people,'DA_HEN'); db.refresh(t); before = svc.snapshot(t)
    fail(code,svc.no_show,db,people['technician'],t.id,t.version,note,appointment)
    assert visits(db,t) == [] and svc.snapshot(t) == before


@pytest.mark.parametrize('role', ['technician','dispatcher'])
def test_no_show_reschedules_without_work_or_ledgers(db,people,role):
    t=ticket(db,people,'DA_HEN'); ledgers=unchanged_ledgers(db)
    svc.no_show(db,people[role],t.id,t.version,'Cô Lan, đã gọi 2 lần chưa nghe',FUTURE)
    row,=visits(db,t)
    assert (t.status,t.appointment_at)==('DA_HEN',FUTURE)
    assert row.outcome=='KHONG_GAP_KHACH' and row.started_at==row.ended_at==NOW
    assert row.next_appointment_at==FUTURE and row.technician_id==people['technician'].id
    assert unchanged_ledgers(db)==ledgers
    assert not db.scalar(select(ServiceStep.id).where(ServiceStep.ticket_id==t.id,ServiceStep.to_status=='DANG_LAM'))
    assert svc.load(db,people['dispatcher'],t.id).steps[-1].action=='no_show'
    fail('VERSION_CONFLICT',svc.no_show,db,people[role],t.id,t.version-1,'Gọi lại',FUTURE)
    assert len(visits(db,t))==1


@pytest.mark.parametrize('operation', ['return','no_show'])
@pytest.mark.parametrize('holder', [False,True])
def test_other_technician_and_custodian_cannot_change_visits(db,people,operation,holder):
    t=ticket(db,people,'DANG_LAM' if operation=='return' else 'DA_HEN')
    if holder:
        # Lịch sử nhận giữ máy đủ kích hoạt luật KT4, không cấp quyền sửa phiếu.
        from app import equipment
        from app.schemas import EquipmentIn
        eq=equipment.create(db,people['dispatcher'],EquipmentIn(serial='VISIT-MACHINE',product_code='TD-0001',customer_code='KTD-000001'))
        db.add(EquipmentCustody(serial=eq.serial,ticket_id=t.id,location='THO_GIU',holder_id=people['other_tech'].id,
            partner_name='',accessories=[],accessories_note='',condition_note='Vỏ trầy',status='DA_NHAN',
            created_by=people['dispatcher'].id,confirmed_by=people['other_tech'].id,confirmed_at=NOW))
        db.commit()
    args=(db,people['other_tech'],t.id)
    fn=svc.no_show if operation=='no_show' else svc.transition
    tail=(t.version,'Đã gọi khách',FUTURE) if operation=='no_show' else ('DA_HEN',t.version,'Khách bận',FUTURE)
    fail('FORBIDDEN' if holder else 'NOT_FOUND',fn,*args,*tail)


def test_raw_sql_guards_open_and_closed_rows(db,people):
    t=ticket(db,people,'DANG_LAM'); vid=visits(db,t)[0].id
    for sql in ["UPDATE service_visits SET note='sửa' WHERE id=:id", "DELETE FROM service_visits WHERE id=:id",
                "UPDATE service_visits SET started_at=started_at-interval '1 hour',ended_at=now(),outcome='XONG',version=version+1 WHERE id=:id"]:
        with pytest.raises(DBAPIError), db.begin_nested(): db.execute(text(sql),{'id':vid})
    svc.transition(db,people['technician'],t.id,'CHO_LINH_KIEN',t.version,'Chờ drum')
    for sql in ["UPDATE service_visits SET note='sửa',version=version+1 WHERE id=:id",
                "UPDATE service_visits SET ended_at=null,outcome=null,version=version+1 WHERE id=:id", "DELETE FROM service_visits WHERE id=:id"]:
        with pytest.raises(DBAPIError), db.begin_nested(): db.execute(text(sql),{'id':vid})


def test_migration_backfills_latest_real_arrival_not_same_status_note(db,people):
    t=ticket(db,people,'DANG_LAM'); pending=ticket(db,people,'DA_HEN')
    svc.transition(db,people['technician'],t.id,'CHO_LINH_KIEN',t.version,'Chờ drum')
    svc.transition(db,people['technician'],t.id,'DANG_LAM',t.version)
    later=NOW+timedelta(hours=1)
    db.add(ServiceStep(ticket_id=t.id,action='transfer_request',from_status='DANG_LAM',to_status='DANG_LAM',
        actor_id=people['technician'].id,assignee_id=people['technician'].id,note='Xin chuyển',at=later))
    db.commit()
    # Bước gần nhất thật sự vào DANG_LAM do DB ghi giờ, không phải đồng hồ giả lõi.
    expected=db.scalar(select(ServiceStep.at).where(ServiceStep.ticket_id==t.id,ServiceStep.to_status=='DANG_LAM',
        ServiceStep.from_status!='DANG_LAM').order_by(ServiceStep.id.desc()).limit(1))
    spec=importlib.util.spec_from_file_location('kt5_migration',Path('migrations/versions/0032_kt5_luot_den_khach.py'))
    migration=importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    with db.begin_nested():
        with Operations.context(MigrationContext.configure(db.connection())):
            migration.downgrade(); migration.upgrade()
        rows=db.execute(text('select ticket_id,technician_id,started_at,ended_at,outcome from service_visits')).all()
        assert rows==[(t.id,people['technician'].id,expected,None,None)]
        assert pending.id!=rows[0].ticket_id


def test_d7_visit_counts_csv_hours_and_repeated_badge(client,db,people,monkeypatch):
    p=people; t=ticket(db,p,'DA_HEN'); base=NOW
    svc.no_show(db,p['technician'],t.id,t.version,'Đã gọi cô Lan',FUTURE)
    svc.transition(db,p['technician'],t.id,'DANG_LAM',t.version)
    monkeypatch.setattr(svc,'_now',lambda:base+timedelta(hours=1))
    svc.transition(db,p['technician'],t.id,'DA_HEN',t.version,'Khách bận',FUTURE)
    svc.transition(db,p['technician'],t.id,'DANG_LAM',t.version)
    service_login(client,p['dispatcher'])
    html=client.get('/dieu-phoi?view=open').text
    assert 'Đi nhiều lần' in html
    monkeypatch.setattr(svc,'_now',lambda:base+timedelta(hours=3))
    prepare_completion(db,p,t)
    svc.transition(db,p['technician'],t.id,'HOAN_THANH',t.version,'Xong việc')
    monkeypatch.setattr(dispatch,'_now',lambda:base+timedelta(hours=4))
    rows=list(csv.reader(io.StringIO(client.get('/dieu-phoi/bao-cao.csv?from=2026-10-18&to=2026-10-18').content.decode('utf-8-sig'))))
    assert rows[0][-5:]==['Số lượt','Lượt xong việc','Lượt hẹn lại','Lượt không gặp khách','Tổng giờ ở khách']
    assert rows[1][-5:]==['3','1','1','1','3.00']
    assert 'Đi nhiều lần' not in client.get('/dieu-phoi?view=open').text
    page=client.get(f'/dich-vu/{t.id}'); assert page.status_code==200
    assert 'Các lượt đến khách' in page.text and 'Đã gọi cô Lan' in page.text


def test_report_includes_open_visit_and_clips_hours_at_vietnam_midnight(db,people,monkeypatch):
    start=NOW.replace(hour=23)-timedelta(days=1)
    monkeypatch.setattr(svc,'_now',lambda:start)
    t=ticket(db,people,'DANG_LAM')
    monkeypatch.setattr(dispatch,'_now',lambda:NOW.replace(hour=2))
    since=NOW.replace(hour=0); until=since+timedelta(days=1)
    rows,_=dispatch._report(db,since.date(),since.date(),since,until)
    assert rows[0]['visits']==0 and rows[0]['visit_hours']==2


def test_mobile_forms_errors_and_no_show_replay(client,db,people):
    from test_my_work import Page
    p=people; t=ticket(db,p,'DA_HEN'); service_login(client,p['technician'])
    path=f'/dich-vu/{t.id}'
    page=client.get(path); assert 'Đã đến khách' in page.text and 'Không gặp khách' in page.text
    f=Page(page.text).forms['service-no-show']; fields=f['fields']
    invalid=client.post(f['action'],data={**fields,'note':'Đã gọi cô Lan','appointment_at':''})
    assert invalid.status_code==400 and 'Đã gọi cô Lan' in invalid.text
    f=Page(invalid.text).forms['service-no-show']
    data={**f['fields'],'note':'Đã gọi cô Lan','appointment_at':FUTURE.strftime('%Y-%m-%dT%H:%M')}
    assert client.post(f['action'],data=data,follow_redirects=False).status_code==303
    assert client.post(f['action'],data=data,follow_redirects=False).status_code==303
    assert len(visits(db,t))==1
    svc.transition(db,p['technician'],t.id,'DANG_LAM',t.version)
    page=client.get(path); assert 'Hẹn quay lại' in page.text and 'Đang ở khách từ 10:00' in page.text
    form=Page(page.text).forms['service-DA_HEN']
    response=client.post(form['action'],data={**form['fields'],'note':'Khách bận','appointment_at':FUTURE.strftime('%Y-%m-%dT%H:%M')},follow_redirects=False)
    assert response.status_code==303 and visits(db,t)[-1].outcome=='HEN_LAI'


def test_materials_and_reserved_stock_survive_return(db,held):
    from test_technician_stock import transfer,charges
    p,a,_=held; transfer(db,p); t=ticket(db,p,'DANG_LAM'); charges(db,p,t)
    before=unchanged_ledgers(db)
    svc.transition(db,p['technician'],t.id,'DA_HEN',t.version,'Khách cần duyệt trước',FUTURE)
    assert unchanged_ledgers(db)==before
    assert t.lines[0].product_code=='TD-0001' and t.lines[0].warehouse_code==a.code
    assert t.lines[0].stock_doc_id is None


@pytest.mark.parametrize('target', ['DA_HEN','HOAN_THANH','no_show'])
def test_visit_failure_rolls_back_ticket_steps_and_completion(db,people,monkeypatch,target):
    p=people; t=ticket(db,p,'DA_HEN' if target=='no_show' else 'DANG_LAM')
    if target=='HOAN_THANH': prepare_completion(db,p,t)
    db.refresh(t); before=svc.snapshot(t); ledger=unchanged_ledgers(db); rows=len(visits(db,t)); steps=db.query(ServiceStep).count()
    actual=service_visits.close_visit
    def close_then_fail(*args,**kwargs):
        actual(*args,**kwargs)
        raise BusinessError('TEST_VISIT_FAILURE','Lỗi giả lập sau chốt lượt')
    monkeypatch.setattr(service_visits,'close_visit',close_then_fail)
    if target=='no_show':
        fail('TEST_VISIT_FAILURE',svc.no_show,db,p['technician'],t.id,t.version,'Đã gọi cô Lan',FUTURE)
    else:
        fail('TEST_VISIT_FAILURE',svc.transition,db,p['technician'],t.id,target,t.version,'Khách hẹn lại',FUTURE if target=='DA_HEN' else None)
    assert svc.snapshot(t)==before and unchanged_ledgers(db)==ledger
    assert len(visits(db,t))==rows and db.query(ServiceStep).count()==steps
    assert all(v.ended_at is None for v in visits(db,t))


def test_api_no_show_visit_list_ownership_and_aware_datetime(client,db,people):
    from conftest import PASSWORD
    p=people; t=ticket(db,p,'DA_HEN')
    def auth(role):
        response=client.post('/auth/login',data={'username':p[role].username,'password':PASSWORD})
        assert response.status_code==200
        return {'Authorization':'Bearer '+response.json()['access_token']}
    mine=auth('technician'); other=auth('other_tech')
    url=f'/service-tickets/{t.id}'
    body=dict(version=t.version,note='Cô Lan, đã gọi 2 lần',appointment_at=FUTURE.isoformat())
    assert client.post(url+'/no-show',json=body,headers=other).status_code==404
    assert client.get(url+'/visits',headers=other).status_code==404
    assert client.post(url+'/no-show',json={**body,'appointment_at':FUTURE.replace(tzinfo=None).isoformat()},headers=mine).status_code==422
    response=client.post(url+'/no-show',json=body,headers=mine)
    assert response.status_code==200 and response.json()['status']=='DA_HEN'
    rows=client.get(url+'/visits',headers=mine).json()
    assert len(rows)==1 and rows[0]['outcome']=='KHONG_GAP_KHACH' and rows[0]['version']==2
    assert rows[0]['technician_id']==p['technician'].id and rows[0]['ended_at'] is not None
    body['version']=response.json()['version']
    response=client.post(url+'/transition',json=dict(to_status='DANG_LAM',version=body['version']),headers=mine)
    assert response.status_code==200
    body['version']=response.json()['version']
    assert client.post(url+'/transition',json=dict(to_status='DA_HEN',**body),headers=other).status_code==404
    assert client.post(url+'/no-show',json=body,headers=mine).status_code==409



def test_concurrent_no_show_same_appointment_still_changes_version(db,people,engine):
    t=ticket(db,people,'DA_HEN',appointment=FUTURE)
    tid,uid,version=t.id,people['technician'].id,t.version
    db.rollback(); barrier=Barrier(2)
    def record():
        with Session(engine) as s:
            user=s.get(User,uid); barrier.wait(timeout=10)
            try:
                row=svc.no_show(s,user,tid,version,'Cô Lan, đã gọi nhưng chưa gặp',FUTURE)
                assert row.version==version+1
                return 'OK'
            except BusinessError as exc:
                return exc.code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _:record(),range(2)))==['OK','VERSION_CONFLICT']
    assert len(visits(db,t))==1 and visits(db,t)[0].outcome=='KHONG_GAP_KHACH'
