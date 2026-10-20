import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC,datetime,timedelta
from threading import Barrier

import jwt
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.orm import Session

from app import users,attachments,equipment,equipment_custody,service_tickets,sales_receipts
from app.config import get_settings
from app.errors import BusinessError
from app.main import app
from app.models import User,Warehouse,AuditLog,StockBalance
from app.schemas import CustodyIntake,EquipmentIn,SalesReceiptReport
from app.security import verify_password,issue_token
from app.web import COOKIE,csrf_token
from conftest import PASSWORD
from test_my_work import people,purchase_people,ticket
from test_technician_stock import held,transfer,balance
from test_web import d5_image
from web_forms import FormClient


@pytest.fixture
def staff(make_user):return {r:make_user('tk.'+r,r) for r in ('admin','director','sales','technician')}


def web_login(client,user,password=PASSWORD):
    r=client.post('/dang-nhap',data=dict(username=user.username,password=password),follow_redirects=False)
    assert r.status_code==303,r.text
    return csrf_token(client.cookies.get(COOKIE))


def api_login(client,user,password=PASSWORD):
    r=client.post('/auth/login',data=dict(username=user.username,password=password));assert r.status_code==200
    return {'Authorization':'Bearer '+r.json()['access_token']}


def fail(code,func,*args,**kwargs):
    with pytest.raises(BusinessError) as exc:func(*args,**kwargs)
    assert exc.value.code==code
    return exc.value


@pytest.mark.parametrize('role',['director','sales','technician'])
def test_non_admin_denied_web_and_api(client,db,staff,role):
    user=staff[role];csrf=web_login(client,user)
    for path in ['/tai-khoan','/tai-khoan/moi',f'/tai-khoan/{staff["admin"].id}/sua']:
        assert client.get(path).status_code==403
        if path != '/tai-khoan':assert client.post(path,data=dict(csrf=csrf)).status_code==403
    h=api_login(client,user)
    for path in ['/users','/auth/users']:
        assert client.get(path,headers=h).status_code==403
        assert client.post(path,json=dict(username='new.user',full_name='Nhân viên',password=PASSWORD,roles=['sales']),headers=h).status_code==403
    assert 'href="/tai-khoan"' not in client.get('/doi-mat-khau').text
    fail('FORBIDDEN',users.create,db,user,'new.user','Nhân viên','',['sales'])


def test_create_technician_warehouse_unique_and_secret_audit(db,staff):
    user,temp=users.create(db,staff['admin'],'Tho.Moi','Nguyễn Đức','0901234567',['technician'])
    assert user.username=='tho.moi' and len(temp)==12 and not set(temp)&set('0O1lI') and verify_password(temp,user.password_hash)
    assert user.must_change_password and user.token_version==1 and user.phone=='0901234567'
    warehouse=db.scalar(select(Warehouse).where(Warehouse.holder_id==user.id));assert warehouse.kind=='THO' and warehouse.active
    fail('USERNAME_TAKEN',users.create,db,staff['admin'],'THO.MOI','Khác','',['sales'])
    assert db.query(Warehouse).count()==1
    logs=list(db.scalars(select(AuditLog)));encoded=json.dumps([dict(before=l.before,after=l.after,reason=l.reason) for l in logs]);assert temp not in encoded and user.password_hash not in encoded and 'password_hash' not in encoded


@pytest.mark.parametrize('name,roles,code',[('ab',['sales'],'BAD_USERNAME'),('éduc',['sales'],'BAD_USERNAME'),('a'*33,['sales'],'BAD_USERNAME'),('valid.user',[],'ROLES_REQUIRED'),('valid.user',['god'],'UNKNOWN_ROLE')])
def test_invalid_create_has_no_user_or_warehouse(db,staff,name,roles,code):
    count=db.query(User).count();fail(code,users.create,db,staff['admin'],name,'Nhân viên','',roles)
    assert db.query(User).count()==count and db.query(Warehouse).count()==0


def test_temporary_password_forces_change_web_api_and_current_session_survives(client,db,staff):
    user,temp=users.create(db,staff['admin'],'new.staff','Nhân viên','',['sales'])
    response=client.post('/dang-nhap',data=dict(username=user.username,password=temp),follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/doi-mat-khau'
    for path in ['/','/viec-cua-toi','/khach-hang','/tai-khoan','/dang-nhap']:
        r=client.get(path,follow_redirects=False);assert r.status_code==303 and r.headers['location']=='/doi-mat-khau'
    assert client.get('/static/app.css').status_code==200
    assert client.get('/doi-mat-khau').status_code==200
    headers=api_login(client,user,temp);r=client.get('/auth/me',headers=headers);assert r.status_code==403 and r.json()['error']=='PASSWORD_CHANGE_REQUIRED'
    csrf=csrf_token(client.cookies.get(COOKIE));r=client.post('/doi-mat-khau',data=dict(csrf=csrf,old_password=temp,new_password='Mat-khau-moi-123',confirm_password='Mat-khau-moi-123'),follow_redirects=False)
    assert r.status_code==303 and client.get('/viec-cua-toi').status_code==200
    db.expire_all();assert not user.must_change_password and user.token_version==2 and user.last_login_at
    assert client.get('/auth/me',headers=headers).status_code==401
    assert client.get('/auth/me',headers=api_login(client,user,'Mat-khau-moi-123')).status_code==200


def test_lock_unlock_revokes_old_cookie_and_token(client,db,staff):
    target=staff['sales'];web_login(client,target);cookie=client.cookies.get(COOKIE);headers=api_login(client,target)
    users.lock(db,staff['admin'],target.id,'Nghỉ việc, đã bàn giao')
    r=client.get('/viec-cua-toi',follow_redirects=False);assert r.status_code==303 and r.headers['location'].startswith('/dang-nhap')
    assert client.get('/auth/me',headers=headers).status_code==401
    assert client.post('/auth/login',data=dict(username=target.username,password=PASSWORD)).status_code==401
    users.unlock(db,staff['admin'],target.id);assert target.active
    assert client.get('/auth/me',headers=headers).status_code==401
    client.cookies.clear();client.cookies.set(COOKIE,cookie);assert client.get('/viec-cua-toi',follow_redirects=False).status_code==303
    client.cookies.clear();web_login(client,target);assert client.get('/viec-cua-toi').status_code==200


def test_reset_invalidates_password_all_sessions_and_sets_temporary(client,db,staff):
    target=staff['sales'];web_login(client,target);headers=api_login(client,target)
    temp=users.reset_password(db,staff['admin'],target.id)
    assert target.must_change_password and target.token_version==2 and verify_password(temp,target.password_hash)
    assert client.get('/auth/me',headers=headers).status_code==401
    assert client.get('/viec-cua-toi',follow_redirects=False).headers['location'].startswith('/dang-nhap')
    assert client.post('/auth/login',data=dict(username=target.username,password=PASSWORD)).status_code==401
    web_login(client,target,temp);assert client.get('/viec-cua-toi',follow_redirects=False).headers['location']=='/doi-mat-khau'


def test_change_own_password_preserves_only_current_cookie(client,db,staff):
    user=staff['sales'];csrf=web_login(client,user)
    with FormClient(app) as other:
        web_login(other,user);old=other.cookies.get(COOKIE)
        r=client.post('/doi-mat-khau',data=dict(csrf=csrf,old_password=PASSWORD,new_password='Khac-mat-khau-123',confirm_password='Khac-mat-khau-123'),follow_redirects=False)
        assert r.status_code==303 and client.cookies.get(COOKIE)!=old
        assert client.get('/viec-cua-toi').status_code==200
        r=other.get('/viec-cua-toi',follow_redirects=False);assert r.status_code==303 and r.headers['location'].startswith('/dang-nhap')


@pytest.mark.parametrize('old,new,code',[('wrong-old','Khac-mat-khau-123','BAD_PASSWORD'),(PASSWORD,PASSWORD,'PASSWORD_UNCHANGED'),(PASSWORD,'123','WEAK_PASSWORD')])
def test_change_password_invalid_no_write(db,staff,old,new,code):
    user=staff['sales'];before=user.password_hash;version=user.token_version
    fail(code,users.change_own_password,db,user,old,new)
    assert user.password_hash==before and user.token_version==version and not user.must_change_password
    assert db.query(AuditLog).count()==0


def test_self_lock_last_admin_and_missing_reason(db,staff,make_user):
    admin=staff['admin'];fail('SELF_LOCK',users.lock,db,admin,admin.id,'Tự khóa')
    fail('LAST_ADMIN',users.update_roles,db,admin,admin.id,['sales'],users.marker(admin))
    fail('REASON_REQUIRED',users.lock,db,admin,staff['sales'].id,' ')
    assert admin.active and admin.roles==['admin']
    second=make_user('tk.second','admin');users.update_roles(db,admin,admin.id,['sales'],users.marker(admin))
    assert admin.roles==['sales']
    fail('LAST_ADMIN',users.update_roles,db,second,second.id,['sales'],users.marker(second))


def test_concurrent_admin_demotions_leave_one(engine,db,staff,make_user):
    a=staff['admin'];b=make_user('tk.concurrent','admin');barrier=Barrier(2)
    def remove(ident):
        with Session(engine) as s:
            actor=s.get(User,ident);version=users.marker(actor);barrier.wait(timeout=10)
            try:users.update_roles(s,actor,ident,['sales'],version);return 'ok'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(remove,[a.id,b.id]))
    assert sorted(results)==['LAST_ADMIN','ok'];db.expire_all()
    assert db.scalar(select(func.count()).select_from(User).where(User.active.is_(True),User.roles.contains(['admin'])))==1


def test_technician_removal_blocked_with_stock_then_deactivates_and_reuses(db,held,make_user):
    p,warehouse,_=held;transfer(db,p);admin=make_user('tk.stock.admin','admin');target=p['technician']
    exc=fail('TECH_STOCK_NOT_EMPTY',users.update_roles,db,admin,target.id,['sales'],users.marker(target));assert 'TD-0001' in exc.details['products'] and warehouse.active and 'technician' in target.roles
    transfer(db,p,source=warehouse.code,dest='K58',qty=3)
    users.update_roles(db,admin,target.id,['sales'],users.marker(target));assert not warehouse.active and 'technician' not in target.roles
    users.update_roles(db,admin,target.id,['sales','technician'],users.marker(target));assert warehouse.active
    assert db.scalar(select(func.count()).select_from(Warehouse).where(Warehouse.holder_id==target.id))==1


def test_handover_counts_stock_cash_machines_and_open_tickets(db,held):
    p,warehouse,_=held;transfer(db,p);tech=p['technician'];job=ticket(db,p,'DANG_LAM')
    machine=equipment.create(db,p['dispatcher'],EquipmentIn(serial='TK-MAY',product_code='TD-0001',customer_code=job.customer_code))
    job.kind='MANG_VE';job.serial=machine.serial;db.commit();attachments.add(db,tech,'equipment_custody',str(job.id),'NHAN_MAY','may.png',d5_image())
    equipment_custody.receive(db,tech,job.id,CustodyIntake(version=machine.version,accessories=[],condition_note='Vỏ máy tốt'))
    another=ticket(db,p,'DA_HEN');done=ticket(db,p,'HOAN_THANH')
    sales_receipts.report_for_ticket(db,tech,done.id,SalesReceiptReport(version=done.version,amount=100000,method='TIEN_MAT'))
    sales_receipts.report_for_ticket(db,tech,done.id,SalesReceiptReport(version=done.version,amount=200000,method='CHUYEN_KHOAN'))
    summary=users.handover_summary(db,tech)
    assert (summary['stock_count'],summary['cash_total'],summary['machine_count'],summary['ticket_count'])==(1,100000,1,2)
    assert {row.id for row in summary['ticket_rows']}=={job.id,another.id}
    assert summary['stock_rows'][0]['product_code']=='TD-0001'


def test_stale_roles_version_rolls_back_profile_and_warehouse(db,staff):
    user,_=users.create(db,staff['admin'],'role.staff','Nhân viên','',['technician']);old=users.marker(user)
    users.update(db,staff['admin'],user.id,['technician'],old,'Tên đã sửa','123')
    fail('VERSION_CONFLICT',users.update_roles,db,staff['admin'],user.id,['sales'],old)
    assert user.full_name=='Tên đã sửa' and user.phone=='123' and user.roles==['technician']
    assert db.scalar(select(Warehouse).where(Warehouse.holder_id==user.id)).active


def test_six_audit_events_without_passwords_or_hashes(db,staff):
    admin=staff['admin'];user,temp=users.create(db,admin,'audit.staff','Nhân viên','',['sales'])
    hashes=[user.password_hash];users.update_roles(db,admin,user.id,['finance'],users.marker(user));users.lock(db,admin,user.id,'Đã bàn giao');users.unlock(db,admin,user.id)
    second=users.reset_password(db,admin,user.id);hashes.append(user.password_hash);users.change_own_password(db,user,second,'Mat-khau-rieng-123');hashes.append(user.password_hash)
    logs=list(db.scalars(select(AuditLog).where(AuditLog.entity=='user',AuditLog.entity_id==str(user.id))));assert {log.action for log in logs}=={'user.create','user.roles','user.lock','user.unlock','user.reset_password','user.change_password'}
    serialized=json.dumps([dict(before=l.before,after=l.after,reason=l.reason) for l in logs]);assert all(secret not in serialized for secret in [temp,second,'Mat-khau-rieng-123',*hashes]) and 'password_hash' not in serialized


def test_missing_and_stale_tv_rejected(client,db,staff):
    user=staff['sales'];now=datetime.now(UTC)
    for tv in [None,0]:
        payload=dict(sub=str(user.id),iat=now,exp=now+timedelta(minutes=5))
        if tv is not None:payload['tv']=tv
        token=jwt.encode(payload,get_settings().secret_key,algorithm='HS256')
        assert client.get('/auth/me',headers={'Authorization':'Bearer '+token}).status_code==401
        client.cookies.clear();client.cookies.set(COOKIE,token);r=client.get('/viec-cua-toi',follow_redirects=False);assert r.status_code==303 and r.headers['location'].startswith('/dang-nhap')


def test_web_create_one_shot_search_profile_and_lock_ack(client,db,staff):
    csrf=web_login(client,staff['admin']);r=client.post('/tai-khoan/moi',data=dict(csrf=csrf,username='web.staff',full_name='Nguyễn Đức',phone='0901234567',roles=['sales']))
    assert r.status_code==200 and 'data-temp-password' in r.text and r.headers['cache-control']=='no-store'
    target=db.scalar(select(User).where(User.username=='web.staff'));assert target.must_change_password
    assert 'Chỉ hiện một lần. Gửi cho nhân viên qua Zalo.' in r.text
    assert 'Nguyễn Đức' in client.get('/tai-khoan?q=nguyen+duc').text
    detail=client.get(f'/tai-khoan/{target.id}');assert 'data-temp-password' not in detail.text
    r=client.post(f'/tai-khoan/{target.id}/khoa',data=dict(csrf=csrf,reason='Đã bàn giao'));assert r.status_code==400 and 'HANDOVER_ACK_REQUIRED' in r.text and target.active
    r=client.post(f'/tai-khoan/{target.id}/khoa',data=dict(csrf=csrf,reason='Đã bàn giao',ack='1'),follow_redirects=False);assert r.status_code==303;db.expire_all();assert not target.active
    assert 'Nguyễn Đức' in client.get('/tai-khoan?status=locked').text
    assert 'Nguyễn Đức' not in client.get('/tai-khoan?status=active').text


def test_temp_result_replay_never_redisplays_or_resets_again(client,db,staff):
    web_login(client,staff['admin']);html=client.get(f'/tai-khoan/{staff["sales"].id}').text
    # Forms is the same browser helper used by the old form-key tests.
    from test_my_work import Page
    fields=Page(html).forms['account-reset']['fields'];path=f'/tai-khoan/{staff["sales"].id}/dat-lai-mat-khau'
    first=client.post(path,data=fields);assert first.status_code==200 and 'data-temp-password' in first.text
    db.expire_all();version=staff['sales'].token_version
    second=client.post(path,data=fields,follow_redirects=False);assert second.status_code==303 and 'data-temp-password' not in second.text
    db.expire_all();assert staff['sales'].token_version==version


def test_no_username_changes_and_stale_web_version(client,db,staff):
    admin=staff['admin'];target=staff['sales'];csrf=web_login(client,admin);version=users.marker(target)
    r=client.post(f'/tai-khoan/{target.id}/sua',data=dict(csrf=csrf,username='renamed',full_name='Tên mới',phone='',roles=['sales'],version=version));assert r.status_code==400 and 'USERNAME_IMMUTABLE' in r.text and target.username=='tk.sales'
    users.update_roles(db,admin,target.id,['finance'],version)
    r=client.post(f'/tai-khoan/{target.id}/sua',data=dict(csrf=csrf,username=target.username,full_name='Tên cũ',phone='',roles=['sales'],version=version));assert r.status_code==409 and 'VERSION_CONFLICT' in r.text
