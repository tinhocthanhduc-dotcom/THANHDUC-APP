"""NS1: profile authorization, sensitive data isolation and immutable salary history."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, UTC
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import select, text, event, func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import employees as svc
from app.errors import BusinessError
from app.models import Employee, EmployeePay, EmployeeSalaryVersion as Salary, AuditLog, User, CashDoc, StockDoc, FundLedger
from app.web import employees as web
from conftest import PASSWORD
from test_tk_tai_khoan import web_login


@pytest.fixture
def p(make_user):
    return {r:make_user('ns1.'+r,r) for r in ('admin','director','technician','sales','warehouse','finance')}


def body(**kwargs):return dict(position='Kỹ thuật',start_date='2026-01-01',**kwargs)


def salary(**kwargs):
    data=dict(effective_from='2026-01-01',base_salary=5178913,meal_allowance=500000,fuel_allowance=300000,
        phone_allowance=200000,other_allowance=100000,standard_days='26',reason='Bắt đầu hợp đồng')
    data.update(kwargs);return data


@pytest.fixture
def employee(db,p):return svc.create(db,p['admin'],p['technician'].id,body())


@pytest.fixture
def private(db,p,employee):
    svc.set_pay(db,p['director'],employee.id,dict(bank_name='BANKSECRET',bank_account='0000123456789012',bank_holder='TENCHUTAIKHOAN'),0)
    s=svc.add_salary(db,p['director'],employee.id,salary(reason='SALARYSECRET'))
    return employee,s


def fail(code,fn,*args,**kwargs):
    with pytest.raises(BusinessError) as exc:fn(*args,**kwargs)
    assert exc.value.code==code
    return exc.value


@pytest.mark.parametrize('role,expected',[('admin',200),('director',200),('sales',403),('technician',403),('warehouse',403),('finance',403),('dispatcher',403),('reception',403)])
def test_every_hr_route_permission(client,db,make_user,role,expected):
    actor=make_user('actor',role);director=actor if role=='director' else make_user('director','director')
    target=make_user('target','technician');employee=svc.create(db,director,target.id,body())
    csrf=web_login(client,actor)
    for path in ('/nhan-su','/nhan-su/moi','/nhan-su/tim-tai-khoan?q=target',f'/nhan-su/{employee.id}'):
        assert client.get(path).status_code==expected,path
    if expected==403:
        for path in ('/nhan-su/moi',f'/nhan-su/{employee.id}',f'/nhan-su/{employee.id}/ngan-hang',f'/nhan-su/{employee.id}/luong'):
            assert client.post(path,data=dict(csrf=csrf)).status_code==403,path
        fail('FORBIDDEN',svc.create,db,actor,target.id,body())
    else:
        r=client.post(f'/nhan-su/{employee.id}',data=dict(csrf=csrf,version=employee.version,**body()),follow_redirects=False)
        assert r.status_code==303
    html=client.get('/ho-so-cua-toi').text
    assert 'Hồ sơ của tôi' in html
    if expected==403:assert 'href="/nhan-su"' not in html


def test_admin_html_core_context_audit_and_queries_never_include_pay(client,db,p,private,monkeypatch):
    employee,s=private;data=svc.get(db,p['admin'],employee.id)
    assert 'pay' not in data and 'salaries' not in data and 'current_salary_id' not in data
    contexts=[];original=web._render
    def capture(request,template,**context):
        contexts.append(context);return original(request,template,**context)
    monkeypatch.setattr(web,'_render',capture)
    csrf=web_login(client,p['admin']);queries=[]
    def query(conn,cursor,statement,*args):queries.append(statement)
    event.listen(db.bind,'before_cursor_execute',query)
    try:
        detail=client.get(f'/nhan-su/{employee.id}');listing=client.get('/nhan-su')
    finally:event.remove(db.bind,'before_cursor_execute',query)
    for document in (detail.text,listing.text,json.dumps(contexts,default=str)):
        for secret in ('BANKSECRET','0000123456789012','TENCHUTAIKHOAN','SALARYSECRET','5178913','5.178.913','bank_account','base_salary','employee.salary','employee.pay'):
            assert secret not in document
    assert not any('FROM employee_pay' in q or 'FROM employee_salary_versions' in q for q in queries)
    for suffix in ('ngan-hang','luong'):
        assert client.post(f'/nhan-su/{employee.id}/{suffix}',data=dict(csrf=csrf)).status_code==403
    fail('FORBIDDEN',svc.set_pay,db,p['admin'],employee.id,{},1)
    fail('FORBIDDEN',svc.add_salary,db,p['admin'],employee.id,salary())
    assert {r.entity for r in svc.history(db,p['admin'],employee.id)}=={'employee'}
    assert {r.entity for r in svc.history(db,p['director'],employee.id)}=={'employee','employee_pay','employee_salary'}


@pytest.mark.parametrize('role',['technician','director'])
def test_own_profile_is_common_only_even_for_director(client,db,p,private,role,monkeypatch):
    employee,s=private
    if role=='director':employee=svc.create(db,p['admin'],p['director'].id,body(note='DIRECTOROWN'))
    svc.set_pay(db,p['director'],employee.id,dict(bank_name='BANKSECRET',bank_account='0000123456789012',bank_holder='TEST'),0 if role=='director' else 1)
    if role=='director':svc.add_salary(db,p['director'],employee.id,salary())
    csrf=web_login(client,p[role]);response=client.get('/ho-so-cua-toi')
    assert response.status_code==200 and p[role].full_name in response.text and employee.code in response.text
    assert all(word not in response.text for word in ('BANKSECRET','0000123456789012','5.178.913','base_salary','bank_account','Lương và phụ cấp'))
    assert 'pay' not in svc.own(db,p[role]) and 'salaries' not in svc.own(db,p[role])
    assert client.post('/ho-so-cua-toi',data=dict(csrf=csrf)).status_code==405
    if role=='technician':fail('FORBIDDEN',svc.get,db,p['sales'],employee.id)


def test_no_own_profile_and_account_link_create_then_detail(client,db,p,employee):
    web_login(client,p['sales']);assert 'Chưa có hồ sơ, liên hệ quản lý' in client.get('/ho-so-cua-toi').text
    web_login(client,p['admin'])
    page=client.get(f'/tai-khoan/{p["sales"].id}');assert f'/nhan-su/moi?user_id={p["sales"].id}' in page.text
    page=client.get(f'/tai-khoan/{p["technician"].id}');assert f'/nhan-su/{employee.id}' in page.text and 'Hồ sơ nhân sự' in page.text
    assert 'href="/ca-nhan"' in page.text and 'href="/ho-so-cua-toi"' in client.get('/ca-nhan').text


def test_user_unique_id_number_validation_and_empty_ids(db,p,employee):
    fail('EMPLOYEE_EXISTS',svc.create,db,p['director'],p['technician'].id,body())
    for bad in ('123','1234567890','1234567890123','12345678a','１２３４５６７８９'):
        fail('BAD_ID_NUMBER',svc.create,db,p['admin'],p['sales'].id,body(id_number=bad))
    svc.update(db,p['admin'],employee.id,{'id_number':'001234567890'},employee.version)
    fail('ID_NUMBER_EXISTS',svc.create,db,p['admin'],p['sales'].id,body(id_number='001234567890'))
    second=svc.create(db,p['admin'],p['sales'].id,body(id_number='123456789'))
    assert employee.code.startswith('NV-') and second.code!=employee.code and len(employee.code)>=7
    assert db.scalar(select(Employee.id).where(Employee.user_id==p['finance'].id)) is None
    svc.create(db,p['admin'],p['finance'].id,body())
    svc.create(db,p['admin'],p['warehouse'].id,body())


def test_common_stale_version_dates_and_active_account_warning(db,p,employee,client):
    version=employee.version
    svc.update(db,p['admin'],employee.id,{'end_date':'2026-06-01','note':'Nghỉ việc'},version)
    assert svc.get(db,p['admin'],employee.id)['warning']=='Nên khóa tài khoản' and p['technician'].active
    assert fail('VERSION_CONFLICT',svc.update,db,p['admin'],employee.id,{'note':'Cũ'},version).status==409
    assert employee.note=='Nghỉ việc'
    fail('BAD_END_DATE',svc.update,db,p['admin'],employee.id,{'end_date':'2025-01-01'},employee.version)
    fail('BAD_DATE',svc.update,db,p['admin'],employee.id,{'birth_date':'invalid'},employee.version)
    csrf=web_login(client,p['admin'])
    r=client.post(f'/nhan-su/{employee.id}',data=dict(csrf=csrf,version=version,**body()))
    assert r.status_code==409 and 'VERSION_CONFLICT' in r.text


def test_salary_on_effective_boundaries_replacements_and_dates(db,p,employee):
    jan=svc.add_salary(db,p['director'],employee.id,salary())
    june=svc.add_salary(db,p['director'],employee.id,salary(effective_from='2026-06-01',base_salary=6000000))
    assert svc.salary_on(db,employee.id,date(2025,12,31)) is None
    assert svc.salary_on(db,employee.id,date(2026,5,31)).id==jan.id
    assert svc.salary_on(db,employee.id,date(2026,6,1)).id==june.id
    fixed=svc.add_salary(db,p['director'],employee.id,salary(effective_from='2026-06-01',base_salary=7000000,replaces_id=june.id,reason='Nhập sai mức lương'))
    assert fixed.replaces_id==june.id and june.replaced_at is not None and june.base_salary==6000000
    assert svc.salary_on(db,employee.id,date(2026,6,1)).id==fixed.id
    moved=svc.add_salary(db,p['director'],employee.id,salary(effective_from='2026-07-01',replaces_id=fixed.id))
    assert svc.salary_on(db,employee.id,date(2026,6,30)).id==jan.id and svc.salary_on(db,employee.id,date(2026,7,1)).id==moved.id
    fail('SALARY_REPLACED',svc.add_salary,db,p['director'],employee.id,salary(replaces_id=june.id))
    fail('SALARY_BEFORE_START',svc.add_salary,db,p['director'],employee.id,salary(effective_from='2025-12-31'))
    fail('SALARY_BEFORE_START',svc.update,db,p['admin'],employee.id,{'start_date':'2026-02-01'},employee.version)


def test_duplicate_salary_atomic_replacement_failure(db,p,employee,monkeypatch):
    row=svc.add_salary(db,p['director'],employee.id,salary());old_amount=row.base_salary
    fail('SALARY_EXISTS',svc.add_salary,db,p['director'],employee.id,salary())
    other=svc.add_salary(db,p['director'],employee.id,salary(effective_from='2026-06-01'))
    fail('SALARY_EXISTS',svc.add_salary,db,p['director'],employee.id,salary(replaces_id=row.id,effective_from='2026-06-01'))
    assert row.replaced_at is None and row.base_salary==old_amount
    original=svc.audit.record
    def broken(*args,**kwargs):raise RuntimeError('After marking old and inserting replacement')
    monkeypatch.setattr(svc.audit,'record',broken)
    with pytest.raises(RuntimeError):svc.add_salary(db,p['director'],employee.id,salary(replaces_id=row.id,base_salary=10000000))
    assert row.replaced_at is None and svc.salary_on(db,employee.id,date(2026,1,1)).id==row.id
    assert db.scalar(select(func.count()).select_from(Salary))==2



@pytest.mark.parametrize('field,value',[('base_salary',-1),('meal_allowance',10**12+1),('fuel_allowance',True),
    ('phone_allowance','1.5'),('standard_days','NaN'),('standard_days','0'),('standard_days','31.1'),('standard_days','26.01'),('reason',' ')])
def test_salary_invalid_values_do_not_write(db,p,employee,field,value):
    with pytest.raises(BusinessError):svc.add_salary(db,p['director'],employee.id,salary(**{field:value}))
    assert db.scalar(select(func.count()).select_from(Salary))==0


def test_web_director_money_fields_pay_versions_and_admin_audit_isolation(client,db,p,employee):
    csrf=web_login(client,p['director'])
    data=dict(csrf=csrf,**salary(base_salary='5.000.000'))
    response=client.post(f'/nhan-su/{employee.id}/luong',data=data)
    assert response.status_code==200 and '5.000.000' in response.text
    row=svc.salary_on(db,employee.id,date(2026,1,1));assert row.base_salary==5000000
    for bad,code in [('-1','MONEY_NEGATIVE'),('1.000.000.000.001','MONEY_TOO_LARGE')]:
        response=client.post(f'/nhan-su/{employee.id}/luong',data={**data,'effective_from':'2026-07-01','base_salary':bad})
        assert response.status_code==400 and code in response.text
    response=client.post(f'/nhan-su/{employee.id}/ngan-hang',data=dict(csrf=csrf,version=0,bank_name='BANKSECRET',bank_account='0000123456789012',bank_holder='CHU'))
    assert response.status_code==200
    assert svc.get(db,p['director'],employee.id)['pay']['bank_account']=='0000123456789012'
    fail('VERSION_CONFLICT',svc.set_pay,db,p['director'],employee.id,{},0)
    fail('BAD_BANK_ACCOUNT',svc.set_pay,db,p['director'],employee.id,dict(bank_account='123a'),1)
    svc.update(db,p['admin'],employee.id,dict(note='Hồ sơ đủ'),employee.version)
    events=list(db.scalars(select(AuditLog).where(AuditLog.entity.in_(('employee','employee_pay','employee_salary')))))
    assert {e.action for e in events}=={'employee.create','employee.update','employee.pay','employee.salary'}
    assert all('bank_account' not in json.dumps(e.after) and 'base_salary' not in json.dumps(e.after) for e in events if e.entity=='employee')
    web_login(client,p['admin']);assert all(e.entity=='employee' for e in svc.history(db,p['admin'],employee.id))


def test_salary_db_guard_and_unique_partial_index(db,p,employee):
    row=svc.add_salary(db,p['director'],employee.id,salary());ident=row.id
    for assignment in ('base_salary=1',"reason='sua'","effective_from='2026-02-01'",'standard_days=20','replaces_id=id'):
        with pytest.raises(DBAPIError):db.execute(text(f'update employee_salary_versions set {assignment} where id=:id'),dict(id=ident));db.commit()
        db.rollback()
    with pytest.raises(DBAPIError):db.execute(text('delete from employee_salary_versions where id=:id'),dict(id=ident));db.commit()
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text('insert into employee_salary_versions(employee_id,effective_from,base_salary,reason,created_by) select employee_id,effective_from,base_salary,reason,created_by from employee_salary_versions where id=:id'),dict(id=ident));db.commit()
    db.rollback()
    db.execute(text('update employee_salary_versions set replaced_at=now() where id=:id'),dict(id=ident));db.commit()
    with pytest.raises(DBAPIError):db.execute(text('update employee_salary_versions set replaced_at=null where id=:id'),dict(id=ident));db.commit()
    db.rollback()


def test_concurrent_salary_same_date_and_profiles_are_unique(db,engine,p,employee):
    barrier=Barrier(2);director_id=p['director'].id;ident=employee.id
    def add():
        with Session(engine) as session:
            actor=session.get(User,director_id);barrier.wait()
            try:svc.add_salary(session,actor,ident,salary());return 'OK'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:add(),range(2)))
    assert sorted(results)==['OK','SALARY_EXISTS']
    assert db.scalar(select(func.count()).select_from(Salary))==1
    salary_id=db.scalar(select(Salary.id));barrier=Barrier(2)
    def replace():
        with Session(engine) as session:
            actor=session.get(User,director_id);old=session.get(Salary,salary_id);barrier.wait()
            try:svc.add_salary(session,actor,ident,salary(replaces_id=old.id,base_salary=8000000));return 'OK'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:replace(),range(2)))
    assert sorted(results)==['OK','SALARY_REPLACED']
    assert db.scalar(select(func.count()).select_from(Salary).where(Salary.replaced_at.is_(None)))==1
    barrier=Barrier(2);target_id=p['finance'].id
    def create():
        with Session(engine) as session:
            actor=session.get(User,director_id);barrier.wait()
            try:svc.create(session,actor,target_id,body());return 'OK'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:create(),range(2)))
    assert sorted(results)==['EMPLOYEE_EXISTS','OK']


def test_search_picker_current_salary_and_no_posting(client,db,p,private,monkeypatch):
    employee,s=private;monkeypatch.setattr(svc,'today',lambda:date(2026,3,1))
    target=p['technician'];target.full_name='Nguyễn Đức';db.commit()
    before=tuple(db.scalar(select(func.count()).select_from(m)) for m in (StockDoc,CashDoc,FundLedger))
    admin=svc.search(db,p['admin'],q='nguyen duc',position='Kỹ thuật',status='working')
    assert len(admin)==1 and admin[0]['id']==employee.id and 'current_salary' not in admin[0]
    director=svc.search(db,p['director']);assert director[0]['current_salary']==6278913
    assert all(a['id']!=target.id for a in svc.available_users(db,p['admin']))
    csrf=web_login(client,p['admin']);page=client.get('/nhan-su/moi')
    assert page.status_code==200 and 'data-employee-picker' in page.text and 'data-employee-user' in page.text
    response=client.post('/nhan-su/moi',data=dict(csrf=csrf,user_id=p['sales'].id,**body()))
    assert response.status_code==200
    assert tuple(db.scalar(select(func.count()).select_from(m)) for m in (StockDoc,CashDoc,FundLedger))==before


def test_migration_down_up_retains_accounts_and_restores_guards(db,engine,p,employee,monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path=Path(__file__).parents[1]/'migrations/versions/0041_ns1_ho_so_nhan_vien.py'
    spec=importlib.util.spec_from_file_location('ns1_migration',path)
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    assert migration.revision=='0041' and migration.down_revision=='0040'
    # NS5 snapshots reference salary versions: remove/reapply the dependent revision first.
    payroll_path=Path(__file__).parents[1]/'migrations/versions/0045_ns5_bang_luong.py'
    payroll_spec=importlib.util.spec_from_file_location('ns5_dependency',payroll_path)
    payroll_migration=importlib.util.module_from_spec(payroll_spec);payroll_spec.loader.exec_module(payroll_migration)
    account_id=p['technician'].id;db.rollback();db.expunge(employee)
    with engine.begin() as conn:
        monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(conn)))
        monkeypatch.setattr(payroll_migration,'op',Operations(MigrationContext.configure(conn)))
        payroll_migration.downgrade()
        migration.downgrade()
        assert conn.scalar(text("select to_regclass('employees')")) is None
        assert conn.scalar(text('select id from users where id=:id'),dict(id=account_id))==account_id
        migration.upgrade()
        assert conn.scalar(text("select to_regclass('employee_salary_versions')"))=='employee_salary_versions'
        assert conn.scalar(text("select count(*) from pg_trigger where tgname='employee_salary_immutable'"))==1
        payroll_migration.upgrade()
    db.expire_all();fresh=svc.create(db,p['admin'],account_id,body())
    assert fresh.code=='NV-0001'


def test_ca_nhan_lists_only_allowed_personal_pages(client,db,p,employee):
    web_login(client,p['admin']);html=client.get('/ca-nhan').text
    assert 'href="/ho-so-cua-toi"' in html and 'href="/doi-mat-khau"' in html and 'action="/dang-xuat"' in html
    assert 'href="/phieu-luong"' not in html and 'href="/kpi"' not in html
    web_login(client,p['technician']);html=client.get('/ca-nhan').text
    assert 'href="/cham-cong"' in html and 'href="/phieu-luong"' in html and 'href="/kpi"' in html
