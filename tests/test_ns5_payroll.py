"""NS5: real PostgreSQL, reused payroll sources and atomic cash posting."""
import csv
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import event,select,func,text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import (payroll as svc,employees,attendance,commissions,kpi,finance_catalogs,finance_mappings,cash_docs,cash_posting)
from app.models import (User,Employee,EmployeeSalaryVersion as Salary,PayrollInput as Input,PayrollMonth as Month,
    PayrollLine as Line,SalaryAdvance as Advance,AttendanceMonthLock,CommissionMonth,CommissionLine,AttendancePunch,AttendanceRequest,
    CashDoc,Fund,FundBalance,FundLedger,CashCategory,AuditLog)
from app.errors import BusinessError
from app.schemas import FundIn,CashCategoryIn,CashDocIn
from test_ns3_kpi import stamp,err
from test_tk_tai_khoan import web_login

JAN=date(2027,1,1);FEB=date(2027,2,1)


@pytest.fixture
def p(db,make_user,monkeypatch):
    p={r:make_user('ns5.'+r,r) for r in ('director','finance','admin','technician','sales','warehouse')}
    clock={'at':stamp('2027-03-02T12:00')}
    for module in (svc,attendance,commissions,kpi):monkeypatch.setattr(module,'_now',lambda:clock['at'])
    p['clock']=clock
    for role in ('technician','sales','warehouse'):
        e=employees.create(db,p['director'],p[role].id,dict(position='Khác',start_date='2026-01-01'))
        employees.add_salary(db,p['director'],e.id,dict(effective_from='2026-01-01',base_salary=6000000,meal_allowance=520000,
            fuel_allowance=260000,phone_allowance=200000,other_allowance=0,standard_days=26,reason='Lương gốc'))
    return p


def employee(db,p,role='technician'):return db.scalar(select(Employee).where(Employee.user_id==p[role].id))


def ready(db,p,month=JAN):
    db.add(AttendanceMonthLock(month=month,locked_by=p['director'].id,locked_at=svc._now()))
    db.add(CommissionMonth(month=month,locked_by=p['director'].id,locked_at=svc._now(),total=0));db.commit()


def summary(monkeypatch,cong='24.5',missing=0):
    def get(session,ids,month):
        value=dict(cong=Decimal(cong),missing_out_days=missing)
        return {ident:dict(value) for ident in ids} if isinstance(ids,(list,set,tuple)) else dict(value)
    monkeypatch.setattr(attendance,'month_summary',get)


@pytest.fixture
def money(db,p):
    finance_catalogs.create(db,p['director'],Fund,FundIn(code='TM',name='Quỹ lương',kind='TIEN_MAT'))
    finance_catalogs.create(db,p['director'],CashCategory,CashCategoryIn(code='LUONG',name='Chi lương',direction='CHI'))
    finance_mappings.set_mapping(db,p['director'],'DANH_MUC_LUONG',category_code='LUONG',version=1)
    doc=cash_docs.insert(db,p['director'],CashDocIn(kind='PT',doc_date=date(2027,1,1),fund_code='TM',is_opening=True,amount=100000000))
    cash_posting.post_in_tx(db,p['director'],doc.id,doc.version);db.commit()
    return 'TM'


def paid_advance(db,p,fund='TM',amount=300000,day=date(2027,1,15),role='technician'):
    row=svc.request_advance(db,p[role],amount,'Tạm ứng sinh hoạt')
    svc.decide_advance(db,p['director'],row.id,row.version)
    return svc.pay_advance(db,p['finance'],row.id,fund,day,row.version)


def test_formula_rounding_advance_commission_and_no_proration(db,p,money,monkeypatch):
    summary(monkeypatch);ready(db,p)
    e=employee(db,p);advance=paid_advance(db,p)
    svc.save_inputs(db,p['director'],JAN,[dict(employee_id=e.id,version=0,bonus=200000,social_insurance=100000,income_tax=50000,other_deduction=20000,note='Thưởng và khấu trừ')])
    row=next(r for r in svc.draft(db,p['director'],JAN) if r['employee_id']==e.id)
    assert row['base_pay']==5653846 and row['allowances']==935000
    assert row['gross']==6788846 and row['net']==6318846 and row['advance']==300000
    approved=svc.approve(db,p['director'],JAN)
    assert approved.total_net==sum(r['net'] for r in svc.draft(db,p['director'],JAN))
    assert db.get(Advance,advance.id,populate_existing=True).payroll_line_id is not None
    assert next(r for r in svc.draft(db,p['director'],FEB) if r['employee_id']==e.id)['advance']==0


def test_approval_conditions_missing_salary_negative_and_future(db,p,monkeypatch):
    err('CHAM_CONG_CHUA_KHOA',svc.approve,db,p['director'],JAN)
    db.add(AttendanceMonthLock(month=JAN,locked_by=p['director'].id,locked_at=svc._now()));db.commit()
    err('HOA_HONG_CHUA_KHOA',svc.approve,db,p['director'],JAN)
    db.add(CommissionMonth(month=JAN,locked_by=p['director'].id,locked_at=svc._now(),total=0));db.commit()
    e=employee(db,p)
    svc.save_inputs(db,p['director'],JAN,[dict(employee_id=e.id,bonus=0,other_deduction=1000000)])
    err('THUC_NHAN_AM',svc.approve,db,p['director'],JAN)
    row=db.get(Input,(JAN,e.id));svc.save_inputs(db,p['director'],JAN,[dict(employee_id=e.id,version=row.version)])
    new=employees.create(db,p['director'],p['finance'].id,dict(position='Kế toán',start_date='2026-01-01'))
    assert any('Chưa có lương' in r['warnings'] for r in svc.draft(db,p['director'],JAN))
    err('CHUA_CO_LUONG',svc.approve,db,p['director'],JAN)
    err('THANG_CHUA_QUA',svc.approve,db,p['director'],date(2027,3,1))


def test_three_payments_exact_cash_and_duplicate_guard(db,p,money,monkeypatch):
    summary(monkeypatch);ready(db,p);svc.approve(db,p['director'],JAN)
    rows=svc.approved(db,p['finance'],JAN);total=sum(r['net'] for r in rows)
    svc.pay(db,p['finance'],[r['id'] for r in rows],money,date(2027,2,1))
    assert db.get(FundBalance,money,populate_existing=True).balance==100000000-total
    docs=list(db.scalars(select(CashDoc).where(CashDoc.source_type=='PAYROLL_LINE')))
    assert len(docs)==3 and all(d.status=='DA_GHI' and d.kind=='PC' and d.category_code=='LUONG' for d in docs)
    assert {str(r['id']) for r in rows}=={d.source_id for d in docs}
    assert all(r['cash_doc_id'] and r['paid_at'] for r in svc.approved(db,p['finance'],JAN))
    assert err('DA_CHI',svc.pay,db,p['finance'],[rows[0]['id']],money,date(2027,2,1)).status==409
    err('SOURCE_LOCKED',cash_posting.void,db,p['finance'],docs[0].id,'Chi sai',docs[0].version)
    err('SOURCE_LOCKED',cash_posting.void_in_tx,db,p['director'],docs[0].id,'Chi sai',docs[0].version)


def test_midmonth_salary_end_date_and_real_attendance_clipping(db,p):
    e=employee(db,p);e.start_date=date(2027,1,10);e.end_date=date(2027,1,20);db.commit()
    # Seed actual immutable punches, including dates outside the employment window.
    for day in (5,10,15,20,25):
        for kind,hour in (('IN',8),('OUT',17)):
            db.add(AttendancePunch(user_id=e.user_id,kind=kind,at=stamp(f'2027-01-{day:02d}T{hour:02d}:00'),source='APP',off_hours=False))
    db.commit()
    newer=employees.add_salary(db,p['director'],e.id,dict(effective_from='2027-01-15',base_salary=8000000,standard_days=26,reason='Tăng giữa tháng'))
    row=next(r for r in svc.draft(db,p['director'],JAN) if r['employee_id']==e.id)
    assert row['salary_version_id']==newer.id and row['base_salary']==8000000 and row['cong']==3
    assert e.id not in {r['employee_id'] for r in svc.draft(db,p['director'],FEB)}
    scalar=attendance.month_summary(db,e.user_id,JAN);batch=attendance.month_summary(db,[e.user_id],JAN)
    assert scalar==batch[e.user_id] and scalar['cong']==3
    assert employees.salary_on(db,[e.id],date(2027,1,31))[e.id].id==employees.salary_on(db,e.id,date(2027,1,31)).id


def test_before_commission_start_no_lock_and_missing_out_warning(db,p,monkeypatch):
    month=date(2026,12,1);summary(monkeypatch,missing=2)
    db.add(AttendanceMonthLock(month=month,locked_by=p['director'].id,locked_at=svc._now()));db.commit()
    rows=svc.draft(db,p['director'],month)
    assert all(r['commission']==0 and 'Thiếu giờ ra 2 ngày' in r['warnings'] for r in rows)
    svc.approve(db,p['director'],month)
    assert db.get(Month,month).total_net==sum(r['net'] for r in rows)


def test_no_cap_on_cong_ratio_and_commission_batch(db,p,monkeypatch):
    summary(monkeypatch,'30');ready(db,p)
    e=employee(db,p);rule=commissions.add_rule(db,p['director'],dict(target='THO',pct=0,fixed_amount=0,effective_from='2027-01-01',reason='Mẫu'))
    # Immutable adjustment before locking a fresh February commission month.
    commissions.add_adjustment(db,p['director'],FEB,p['technician'].id,123456,'Hoa hồng để tính lương')
    commissions.lock(db,p['director'],FEB)
    scalar=commissions.month_total(db,p['technician'].id,FEB)
    assert scalar==123456 and commissions.month_total(db,[p['technician'].id],FEB)[p['technician'].id]==scalar
    row=next(r for r in svc.draft(db,p['director'],FEB) if r['employee_id']==e.id)
    assert row['cong']==30 and row['base_pay']==6923077 and row['commission']==123456


def test_approved_snapshot_input_versions_and_immutable_numbers(db,p,monkeypatch):
    summary(monkeypatch);ready(db,p);e=employee(db,p)
    svc.save_inputs(db,p['director'],JAN,[dict(employee_id=e.id,bonus=100000,note='Đợt 1')])
    current=db.get(Input,(JAN,e.id));version=current.version
    svc.save_inputs(db,p['director'],JAN,[dict(employee_id=e.id,version=version,bonus=200000)])
    assert db.get(Input,(JAN,e.id)).note=='Đợt 1'
    err('VERSION_CONFLICT',svc.save_inputs,db,p['director'],JAN,[dict(employee_id=e.id,version=version,bonus=1)])
    svc.approve(db,p['director'],JAN);before=svc.draft(db,p['director'],JAN)
    assert err('DA_DUYET',svc.approve,db,p['director'],JAN).status==409
    err('DA_DUYET',svc.save_inputs,db,p['director'],JAN,[])
    employees.add_salary(db,p['director'],e.id,dict(effective_from='2027-01-15',base_salary=9000000,standard_days=26,reason='Thay sau duyệt'))
    summary(monkeypatch,'1')
    assert svc.draft(db,p['director'],JAN)==before


def test_approval_failure_rolls_back_snapshot_and_advance_claim(db,p,money,monkeypatch):
    summary(monkeypatch);ready(db,p);advance=paid_advance(db,p)
    original=svc._record
    def fail(session,user,action,row,before=None):
        if action=='approve':raise RuntimeError('Failure after lines and claims flushed')
        return original(session,user,action,row,before)
    monkeypatch.setattr(svc,'_record',fail)
    with pytest.raises(RuntimeError):svc.approve(db,p['director'],JAN)
    assert db.scalar(select(func.count()).select_from(Line))==0 and db.get(Month,JAN) is None
    assert db.get(Advance,advance.id,populate_existing=True).payroll_line_id is None


@pytest.mark.parametrize('kind',['mapping','balance'])
def test_batch_payment_failure_rolls_back_all_cash_and_paid_flags(db,p,money,monkeypatch,kind):
    summary(monkeypatch);ready(db,p);svc.approve(db,p['director'],JAN)
    rows=svc.approved(db,p['finance'],JAN);amount=rows[0]['net']
    if kind=='mapping':finance_mappings.set_mapping(db,p['director'],'DANH_MUC_LUONG',category_code=None,version=2)
    else:
        # Leave enough to pay one person, then fail on the next in the same batch.
        category='LUONG';remaining=amount+100
        doc=cash_docs.insert(db,p['finance'],CashDocIn(kind='PC',doc_date=date(2027,2,1),fund_code=money,category_code=category,amount=100000000-remaining))
        cash_posting.post_in_tx(db,p['finance'],doc.id,doc.version);db.commit()
    old_balance=db.get(FundBalance,money,populate_existing=True).balance
    with pytest.raises(BusinessError):svc.pay(db,p['finance'],[r['id'] for r in rows],money,date(2027,2,1))
    assert db.scalar(select(func.count()).select_from(CashDoc).where(CashDoc.source_type=='PAYROLL_LINE'))==0
    assert all(not r['paid_at'] and not r['cash_doc_id'] for r in svc.approved(db,p['finance'],JAN))
    assert db.get(FundBalance,money,populate_existing=True).balance==old_balance


def test_zero_net_marks_paid_without_mapping_or_document(db,p,monkeypatch):
    summary(monkeypatch,'0');ready(db,p)
    for e,_ in svc._people(db,JAN):
        employees.add_salary(db,p['director'],e.id,dict(effective_from='2027-01-01',base_salary=0,standard_days=26,reason='Không hưởng'))
    svc.approve(db,p['director'],JAN);rows=svc.approved(db,p['finance'],JAN)
    svc.pay(db,p['finance'],[r['id'] for r in rows],'',date(2027,2,1))
    assert all(r['paid_at'] and not r['cash_doc_id'] for r in svc.approved(db,p['finance'],JAN))
    assert db.scalar(select(func.count()).select_from(CashDoc))==0


def test_advance_cap_cancel_reject_roles_versions_and_failure(db,p,money):
    r=svc.request_advance(db,p['technician'],2000000,'Gia đình');assert r.number=='TU-27-00001'
    err('VUOT_HAN_MUC_TAM_UNG',svc.request_advance,db,p['technician'],1000001,'Vượt')
    err('FORBIDDEN',svc.decide_advance,db,p['finance'],r.id,r.version)
    err('FORBIDDEN',svc.cancel_advance,db,p['sales'],r.id,r.version)
    err('REASON_REQUIRED',svc.decide_advance,db,p['director'],r.id,r.version,False,'')
    svc.cancel_advance(db,p['technician'],r.id,r.version)
    r2=svc.request_advance(db,p['technician'],3000000,'Đủ nửa lương')
    err('VERSION_CONFLICT',svc.decide_advance,db,p['director'],r2.id,0)
    svc.decide_advance(db,p['director'],r2.id,r2.version,False,'Không duyệt')
    r3=svc.request_advance(db,p['technician'],3000000,'Xin lại');svc.decide_advance(db,p['director'],r3.id,r3.version)
    with pytest.raises(BusinessError):svc.pay_advance(db,p['finance'],r3.id,'NO_FUND',date(2027,1,15),r3.version)
    assert db.get(Advance,r3.id,populate_existing=True).status=='DA_DUYET'
    assert db.scalar(select(func.count()).select_from(CashDoc).where(CashDoc.source_type=='SALARY_ADVANCE'))==0
    svc.pay_advance(db,p['finance'],r3.id,money,date(2027,1,15),r3.version)
    assert db.get(FundBalance,money,populate_existing=True).balance==97000000
    err('VUOT_HAN_MUC_TAM_UNG',svc.request_advance,db,p['technician'],1,'Chưa trừ')
    pc=db.get(CashDoc,r3.cash_doc_id)
    err('SOURCE_LOCKED',cash_posting.void_in_tx,db,p['finance'],pc.id,'Hủy riêng',pc.version)


def test_advance_selected_accounting_date_consumed_once_and_backdate_guard(db,p,money,monkeypatch):
    summary(monkeypatch);ready(db,p)
    r=paid_advance(db,p,day=date(2027,1,31));assert r.paid_at.astimezone(svc.TZ).date()==date(2027,3,2)
    assert next(x for x in svc.draft(db,p['director'],JAN) if x['user_id']==r.user_id)['advance']==300000
    svc.approve(db,p['director'],JAN)
    assert next(x for x in svc.draft(db,p['director'],FEB) if x['user_id']==r.user_id)['advance']==0
    new=svc.request_advance(db,p['technician'],300000,'Sau khi trừ');svc.decide_advance(db,p['director'],new.id,new.version)
    err('DA_DUYET',svc.pay_advance,db,p['finance'],new.id,money,date(2027,1,31),new.version)


def test_concurrent_salary_payments_one_pc(db,p,money,monkeypatch,engine):
    summary(monkeypatch);ready(db,p);svc.approve(db,p['director'],JAN)
    ident=svc.approved(db,p['finance'],JAN)[0]['id'];uid=p['finance'].id;db.commit();barrier=Barrier(2)
    def run():
        with Session(engine,expire_on_commit=False) as s:
            user=s.get(User,uid);barrier.wait()
            try:svc.pay(s,user,[ident],money,date(2027,2,1));return 'ok'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(lambda _:run(),range(2)))
    assert sorted(result)==['DA_CHI','ok']
    assert db.scalar(select(func.count()).select_from(CashDoc).where(CashDoc.source_type=='PAYROLL_LINE'))==1


@pytest.mark.parametrize('role',['technician','sales','warehouse','finance','admin'])
def test_director_routes_forbidden(client,db,p,role):
    csrf=web_login(client,p[role])
    for url in ('/luong','/luong.csv'):
        assert client.get(url+'?thang=2027-01').status_code==403
    for url in ('/luong/luu','/luong/duyet'):
        assert client.post(url+'?thang=2027-01',data={'csrf':csrf}).status_code==403
    err('FORBIDDEN',svc.draft,db,p[role],JAN)


def test_private_slips_finance_approved_only_admin_all_forbidden(client,db,p,money,monkeypatch):
    summary(monkeypatch);ready(db,p)
    web_login(client,p['technician']);assert 'Chưa có phiếu' in client.get('/phieu-luong?user_id='+str(p['sales'].id)).text
    svc.approve(db,p['director'],JAN);rows=svc.approved(db,p['finance'],JAN)
    own=next(r for r in rows if r['user_id']==p['technician'].id);other=next(r for r in rows if r['user_id']==p['sales'].id)
    page=client.get('/phieu-luong?user_id='+str(p['sales'].id));assert page.status_code==200 and p['sales'].full_name not in page.text
    assert client.get('/phieu-luong/'+str(other['id'])).status_code==404
    assert client.get('/phieu-luong/'+str(own['id'])).status_code==200
    web_login(client,p['finance']);assert client.get('/luong/chi?thang=2027-01').status_code==200
    assert 'ns5' not in client.get('/luong/chi?thang=2027-02').text.lower().split('<main>')[-1]  # no unapproved people
    csrf=web_login(client,p['admin'])
    for path in ('/luong','/luong.csv','/luong/chi','/luong/chi.csv','/phieu-luong','/phieu-luong/'+str(own['id']),'/tam-ung'):
        assert client.get(path).status_code==403,path
    assert client.post('/luong/chi',data={'csrf':csrf}).status_code==403


def test_csv_total_mapping_visible_menu_and_advance_pending(client,db,p,money,monkeypatch):
    summary(monkeypatch);ready(db,p);r=svc.request_advance(db,p['technician'],100000,'Việc nhà')
    web_login(client,p['director']);assert 'Tạm ứng chờ duyệt · 1 yêu cầu' in client.get('/viec-cua-toi').text
    page=client.get('/ghep-tai-chinh');assert page.status_code==200 and 'Chi lương, tạm ứng (Chi)' in page.text
    svc.approve(db,p['director'],JAN)
    response=client.get('/luong.csv?thang=2027-01');data=list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert data[0][3:]==[*svc.LABELS.values(),'Ghi chú','Phiếu chi']
    assert int(data[-1][data[0].index('Thực nhận')])==db.get(Month,JAN).total_net
    assert f'{db.get(Month,JAN).total_net:,}'.replace(',','.') in client.get('/luong?thang=2027-01').text


def test_twenty_people_constant_queries(client,db,p,make_user):
    web_login(client,p['director'])
    def count():
        items=[]
        def capture(*args):items.append(args[2])
        event.listen(db.bind,'before_cursor_execute',capture)
        try:assert client.get('/luong?thang=2027-01').status_code==200
        finally:event.remove(db.bind,'before_cursor_execute',capture)
        return len(items)
    first=count()
    for i in range(20):
        user=make_user('salary.batch.'+str(i),'technician');e=employees.create(db,p['director'],user.id,dict(position='Kỹ thuật',start_date='2026-01-01'))
        employees.add_salary(db,p['director'],e.id,dict(effective_from='2026-01-01',base_salary=6000000,standard_days=26,reason='Bảng 20 người'))
    assert count()==first


@pytest.mark.parametrize('statement',[
    'update payroll_lines set net=1','delete from payroll_lines','update payroll_months set total_net=1','delete from payroll_months',
    "update payroll_inputs set bonus=1","delete from salary_advances","update salary_advances set amount=1",
])
def test_raw_sql_cannot_rewrite_payroll_or_advance(db,p,money,monkeypatch,statement):
    summary(monkeypatch);ready(db,p);r=paid_advance(db,p);e=employee(db,p)
    svc.save_inputs(db,p['director'],JAN,[dict(employee_id=e.id,bonus=1)])
    svc.approve(db,p['director'],JAN)
    with pytest.raises(DBAPIError):db.execute(text(statement));db.commit()
    db.rollback();assert db.get(Month,JAN) and db.get(Advance,r.id).amount==300000


def test_advance_insufficient_funds_rollback_and_no_orphan_pc(db,p,money):
    request=svc.request_advance(db,p['technician'],2000000,'Ứng chi phí')
    svc.decide_advance(db,p['director'],request.id,request.version)
    doc=cash_docs.insert(db,p['finance'],CashDocIn(kind='PC',doc_date=date(2027,1,15),fund_code=money,category_code='LUONG',amount=99999900))
    cash_posting.post_in_tx(db,p['finance'],doc.id,doc.version);db.commit()
    with pytest.raises(BusinessError):svc.pay_advance(db,p['finance'],request.id,money,date(2027,1,15),request.version)
    row=db.get(Advance,request.id,populate_existing=True)
    assert row.status=='DA_DUYET' and row.paid_at is None and row.cash_doc_id is None
    assert db.get(FundBalance,money,populate_existing=True).balance==100
    assert db.scalar(select(func.count()).select_from(CashDoc).where(CashDoc.source_type=='SALARY_ADVANCE'))==0


def test_concurrent_advances_respect_shared_cap(db,p,engine):
    uid=p['technician'].id;db.commit();barrier=Barrier(2)
    def run():
        with Session(engine,expire_on_commit=False) as s:
            user=s.get(User,uid);barrier.wait()
            try:svc.request_advance(s,user,2000000,'Gửi đồng thời');return 'ok'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(lambda _:run(),range(2)))
    assert sorted(result)==['VUOT_HAN_MUC_TAM_UNG','ok']
    assert db.scalar(select(func.count()).select_from(Advance))==1


def test_concurrent_approvals_only_one_snapshot(db,p,engine,monkeypatch):
    summary(monkeypatch);ready(db,p);uid=p['director'].id;db.commit();barrier=Barrier(2)
    def run():
        with Session(engine,expire_on_commit=False) as s:
            user=s.get(User,uid);barrier.wait()
            try:svc.approve(s,user,JAN);return 'ok'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(lambda _:run(),range(2)))
    assert sorted(result)==['DA_DUYET','ok'] and db.scalar(select(func.count()).select_from(Line))==3


def test_web_inputs_advance_and_finance_visibility(client,db,p,money,monkeypatch):
    summary(monkeypatch);ready(db,p);csrf=web_login(client,p['technician'])
    response=client.post('/tam-ung',data={'csrf':csrf,'amount':'300.000','reason':'Tạm ứng trên web'})
    assert response.status_code==200 and '300.000' in response.text
    r=db.scalar(select(Advance));assert r.status=='CHO_DUYET'
    other=svc.request_advance(db,p['sales'],100000,'Đề nghị riêng của kinh doanh')
    csrf=web_login(client,p['finance']);page=client.get('/tam-ung')
    assert 'Đề nghị riêng của kinh doanh' not in page.text and 'Tạm ứng trên web' not in page.text
    assert client.post(f'/tam-ung/{r.id}/duyet',data={'csrf':csrf,'version':r.version}).status_code==403
    csrf=web_login(client,p['director']);assert client.post(f'/tam-ung/{r.id}/duyet',data={'csrf':csrf,'version':r.version}).status_code==200
    e=employee(db,p);response=client.post('/luong/luu?thang=2027-01',data={'csrf':csrf,'employee_id':str(e.id),'version:'+str(e.id):'0',
        'bonus:'+str(e.id):'100.000','social_insurance:'+str(e.id):'0','income_tax:'+str(e.id):'0','other_deduction:'+str(e.id):'0','note:'+str(e.id):'Thưởng web'})
    assert response.status_code==200 and db.get(Input,(JAN,e.id)).bonus==100000
    assert client.post('/luong/duyet?thang=2027-01',data={'csrf':csrf}).status_code==200
    csrf=web_login(client,p['finance']);line=svc.approved(db,p['finance'],JAN)[0];db.commit()
    response=client.post('/luong/chi?thang=2027-01',data={'csrf':csrf,'line_ids':str(line['id']),'fund_code':money,'pay_date':'2027-02-01'})
    assert response.status_code==200 and db.get(Line,line['id'],populate_existing=True).paid_at
    assert client.get('/luong/chi.csv?thang=2027-01').status_code==200
    for path in ('/luong','/luong/luu','/luong/duyet'):assert client.get(path).status_code in (403,405)


def test_no_forged_manual_payroll_cash_source(db,p,money):
    for source in ('PAYROLL_LINE','SALARY_ADVANCE'):
        err('SOURCE_LOCKED',cash_docs.create,db,p['finance'],CashDocIn(kind='PC',doc_date=date(2027,1,15),fund_code=money,category_code='LUONG',amount=1,source_type=source,source_id='1'))
