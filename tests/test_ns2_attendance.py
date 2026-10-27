"""NS2: real PostgreSQL, server time, immutable punches and atomic month locking."""
import csv
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import select,func,text,event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import attendance as svc,employees
from app.errors import BusinessError
from app.models import (AttendancePunch as Punch,AttendanceRequest as Request,AttendanceHoliday as Holiday,
    AttendanceMonthLock as Lock,Employee,User,AuditLog,ServiceVisit,StockDoc,CashDoc,FundLedger)
from test_tk_tai_khoan import web_login

DAY=date(2026,11,2)


def stamp(value):return datetime.fromisoformat(value).replace(tzinfo=svc.TZ)


@pytest.fixture
def clock(monkeypatch):
    current={'at':stamp('2026-11-02T19:00')}
    monkeypatch.setattr(svc,'_now',lambda:current['at'])
    return current


@pytest.fixture
def p(db,make_user,clock):
    people={r:make_user('ns2.'+r,r if r!='other' else 'technician') for r in ('director','admin','finance','technician','other','dispatcher')}
    for key in ('technician','other'):
        employees.create(db,people['director'],people[key].id,dict(position='Kỹ thuật',start_date='2026-01-01'))
    return people


def fail(code,fn,*args,**kwargs):
    with pytest.raises(BusinessError) as exc:fn(*args,**kwargs)
    assert getattr(exc.value,'code',None)==code,str(exc.value)
    return exc.value


def tap(db,p,clock,kind,at,user='technician'):
    clock['at']=stamp(at);return svc.punch(db,p[user],kind)


def req(db,p,kind,**kwargs):return svc.request(db,p['technician'],kind,reason='Quên bấm / có việc gia đình',**kwargs)


def leave(db,p,kind='NGHI_PHEP',start=DAY,end=DAY,part='CA_NGAY'):
    row=req(db,p,kind,from_date=start,to_date=end,part=part);svc.approve(db,p['director'],row.id,row.version);return row


def test_profile_active_start_and_end_dates_are_required(db,p,clock):
    fail('NO_EMPLOYEE',svc.punch,db,p['finance'],'IN')
    employee=db.scalar(select(Employee).where(Employee.user_id==p['technician'].id));employee.end_date=date(2026,11,1);db.commit()
    assert fail('DA_NGHI_VIEC',svc.punch,db,p['technician'],'IN').status==403
    employee.end_date=None;employee.start_date=date(2026,11,3);db.commit()
    fail('CHUA_VAO_LAM',svc.punch,db,p['technician'],'IN')
    employee.start_date=date(2026,1,1);p['technician'].active=False;db.commit()
    fail('DA_NGHI_VIEC',svc.punch,db,p['technician'],'IN')
    assert db.scalar(select(func.count()).select_from(Punch))==0


def test_duplicate_60_second_rule_and_state_errors(db,p,clock):
    fail('CHUA_VAO_CA',svc.punch,db,p['technician'],'OUT')
    first=tap(db,p,clock,'IN','2026-11-02T08:00')
    assert tap(db,p,clock,'IN','2026-11-02T08:00:30').id==first.id
    fail('DA_VAO_CA',tap,db,p,clock,'IN','2026-11-02T08:01')
    out=tap(db,p,clock,'OUT','2026-11-02T12:00')
    assert tap(db,p,clock,'OUT','2026-11-02T12:00:30').id==out.id
    fail('CHUA_VAO_CA',tap,db,p,clock,'OUT','2026-11-02T12:01')
    tap(db,p,clock,'IN','2026-11-02T13:30');tap(db,p,clock,'OUT','2026-11-02T17:30')
    day=svc.day_summary(db,p['technician'].id,DAY)
    assert day['hours']==Decimal('7.5') and day['cong']==1 and len(day['punches'])==4  # 08:00–12:00 trừ 30 phút trùng giờ trưa + 13:30–17:30
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action=='attendance.punch'))==4
    # Chỉ bấm vào sáng và ra chiều: giờ nghỉ trưa 11:30–13:30 không tính vào số giờ, công vẫn 1, không thiếu giờ ra.
    tap(db,p,clock,'IN','2026-11-03T07:30');tap(db,p,clock,'OUT','2026-11-03T17:30')
    day=svc.day_summary(db,p['technician'].id,DAY+timedelta(days=1))
    assert day['hours']==8 and day['cong']==1 and not day['missing_out'] and day['late_minutes']==0 and day['early_minutes']==0


def test_web_server_time_ignores_fake_browser_clock_and_no_gps_still_works(client,db,p,clock):
    clock['at']=stamp('2026-11-02T08:02');csrf=web_login(client,p['technician'])
    response=client.post('/cham-cong/bam',data=dict(csrf=csrf,kind='IN',at='1990-01-01T00:00',punch_at='2040-01-01T00:00'))
    assert response.status_code==200 and 'Đã vào ca 08:02' in response.text
    row=db.scalar(select(Punch));assert row.at==clock['at'] and row.lat is None and row.lng is None
    clock['at']=stamp('2026-11-02T17:30')
    response=client.post('/cham-cong/bam',data=dict(csrf=csrf,kind='OUT',lat='10.1234567',lng='106.1234567',accuracy='25'))
    assert response.status_code==200
    row=db.scalar(select(Punch).where(Punch.kind=='OUT'));assert row.lat==Decimal('10.1234567') and row.accuracy_m==25


def test_late_early_missing_out_then_approved_supplement(db,p,clock):
    tap(db,p,clock,'IN','2026-11-02T07:39');tap(db,p,clock,'OUT','2026-11-02T17:00')
    data=svc.day_summary(db,p['technician'].id,DAY)
    assert data['late_minutes']==0 and data['early_minutes']==30 and data['cong']==1
    tap(db,p,clock,'IN','2026-11-03T07:41');clock['at']=stamp('2026-11-03T19:00')
    data=svc.day_summary(db,p['technician'].id,DAY+timedelta(days=1))
    assert data['status']=='THIEU_GIO_RA' and data['late_minutes']==11 and data['cong']==0
    row=req(db,p,'BO_SUNG_RA',punch_at='2026-11-03T17:30');svc.approve(db,p['director'],row.id,row.version)
    data=svc.day_summary(db,p['technician'].id,DAY+timedelta(days=1))
    assert data['status']=='DI_LAM' and data['cong']==1 and data['punches'][-1].source=='BO_SUNG' and data['punches'][-1].request_id==row.id


def test_half_paid_leave_with_and_without_work_holiday_sunday_absence(db,p,clock):
    leave(db,p,part='SANG');assert svc.day_summary(db,p['technician'].id,DAY)['cong']==Decimal('0.5')
    tap(db,p,clock,'IN','2026-11-02T13:30');tap(db,p,clock,'OUT','2026-11-02T17:30')
    assert svc.day_summary(db,p['technician'].id,DAY)['cong']==1
    svc.set_holiday(db,p['director'],'2026-11-03','Ngày lễ công ty')
    assert svc.day_summary(db,p['technician'].id,date(2026,11,3))['cong']==1
    assert svc.day_summary(db,p['technician'].id,date(2026,11,1))['status']=='NGAY_NGHI'
    assert svc.day_summary(db,p['technician'].id,date(2026,11,4))['status']=='NGHI_KHONG_PHEP'
    clock['at']=stamp('2026-11-05T19:00');leave(db,p,'NGHI_KHONG_LUONG',date(2026,11,5),date(2026,11,5))
    assert svc.day_summary(db,p['technician'].id,date(2026,11,5))['cong']==0
    assert svc.day_summary(db,p['technician'].id,date(2025,12,31))['status']=='CHUA_VAO_LAM'


def test_leave_overlap_is_blocked_and_nonwork_dates_do_not_double_count(db,p,clock):
    leave(db,p,start=date(2026,11,1),end=date(2026,11,3));svc.set_holiday(db,p['director'],'2026-11-03','Lễ')
    assert svc.day_summary(db,p['technician'].id,date(2026,11,1))['cong']==0
    assert svc.day_summary(db,p['technician'].id,date(2026,11,3))['paid_leave']==0
    row=req(db,p,'NGHI_KHONG_LUONG',from_date=DAY,to_date=DAY)
    fail('LEAVE_OVERLAP',svc.approve,db,p['director'],row.id,row.version)
    assert row.status=='CHO_DUYET'


@pytest.mark.parametrize('role',['finance','admin','dispatcher'])
def test_only_director_decides_and_locks(client,db,p,clock,role):
    row=req(db,p,'BO_SUNG_VAO',punch_at='2026-11-02T08:00');csrf=web_login(client,p[role])
    assert client.get('/cham-cong/duyet').status_code==403
    for action in ('duyet','tu-choi'):
        assert client.post(f'/cham-cong/duyet/{row.id}/{action}',data=dict(csrf=csrf,version=row.version,note='Không đồng ý')).status_code==403
    assert client.post('/cham-cong/bang/khoa?thang=2026-11',data=dict(csrf=csrf)).status_code==403
    assert client.get('/cham-cong/ngay-le').status_code==403
    fail('FORBIDDEN',svc.approve,db,p[role],row.id,row.version)
    fail('FORBIDDEN',svc.lock_month,db,p[role],DAY)
    fail('FORBIDDEN',svc.set_holiday,db,p[role],DAY,'Lễ')


def test_approval_version_and_failure_roll_back_created_punch_and_audit(db,p,clock,monkeypatch):
    row=req(db,p,'BO_SUNG_VAO',punch_at='2026-11-02T08:00');version=row.version
    original=svc.audit.record
    def broken(session,actor,action,*args,**kwargs):
        if action=='attendance.approve':raise RuntimeError('After punch insertion')
        return original(session,actor,action,*args,**kwargs)
    monkeypatch.setattr(svc.audit,'record',broken)
    with pytest.raises(RuntimeError):svc.approve(db,p['director'],row.id,version)
    assert row.status=='CHO_DUYET' and db.scalar(select(func.count()).select_from(Punch))==0
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action=='attendance.punch'))==0
    monkeypatch.setattr(svc.audit,'record',original);svc.approve(db,p['director'],row.id,version)
    assert fail('VERSION_CONFLICT',svc.approve,db,p['director'],row.id,version).status==409
    assert db.scalar(select(func.count()).select_from(Punch))==1


def test_requests_future_late_cancel_reject_and_ownership(db,p,clock):
    fail('FUTURE_PUNCH',req,db,p,'BO_SUNG_VAO',punch_at='2026-11-03T08:00')
    fail('BAD_LEAVE_DATES',req,db,p,'NGHI_PHEP',from_date=DAY,to_date=DAY+timedelta(days=1),part='SANG')
    row=req(db,p,'BO_SUNG_VAO',punch_at='2026-10-20T08:00')
    assert svc.requests(db,p['director'],True)[0]['late_submission']
    fail('FORBIDDEN',svc.cancel,db,p['other'],row.id,row.version)
    svc.cancel(db,p['technician'],row.id,row.version);assert row.status=='DA_HUY'
    fail('REQUEST_PROCESSED',svc.cancel,db,p['technician'],row.id,row.version)
    second=req(db,p,'NGHI_PHEP',from_date=DAY,to_date=DAY)
    fail('NOTE_REQUIRED',svc.reject,db,p['director'],second.id,second.version,' ')
    svc.reject(db,p['director'],second.id,second.version,'Thiếu người trực');assert second.status=='TU_CHOI' and second.decision_note=='Thiếu người trực'


def test_month_lock_blocks_all_dates_and_unlock_keeps_history(db,p,clock):
    row=req(db,p,'BO_SUNG_VAO',punch_at='2026-11-02T08:00')
    svc.set_holiday(db,p['director'],DAY,'Lễ');locked=svc.lock_month(db,p['director'],DAY)
    assert locked.month==date(2026,11,1) and svc.is_locked(locked)
    for fn,args in [(svc.punch,(db,p['technician'],'IN')),(req,(db,p,'BO_SUNG_RA')),
                    (svc.approve,(db,p['director'],row.id,row.version)),(svc.set_holiday,(db,p['director'],DAY,'Lễ khác')),
                    (svc.remove_holiday,(db,p['director'],DAY))]:
        if fn==req:fail('THANG_DA_KHOA',fn,*args,punch_at='2026-11-02T17:30')
        else:fail('THANG_DA_KHOA',fn,*args)
    fail('REASON_REQUIRED',svc.unlock_month,db,p['director'],DAY,' ')
    svc.unlock_month(db,p['director'],DAY,'Bổ sung giờ bị quên')
    assert not svc.is_locked(locked) and locked.unlock_reason=='Bổ sung giờ bị quên' and locked.unlocked_by==p['director'].id
    svc.approve(db,p['director'],row.id,row.version);svc.remove_holiday(db,p['director'],DAY)
    svc.lock_month(db,p['director'],DAY)
    assert svc.is_locked(locked) and locked.unlocked_at is not None and locked.unlock_reason=='Bổ sung giờ bị quên'


def test_cross_month_leave_checks_every_month(db,p,clock):
    svc.lock_month(db,p['director'],date(2026,12,1))
    fail('THANG_DA_KHOA',req,db,p,'NGHI_PHEP',from_date='2026-11-30',to_date='2026-12-01')
    assert db.scalar(select(func.count()).select_from(Request))==0


def test_vietnam_day_boundary_off_hours_and_immutable_sql(db,p,clock):
    row=tap(db,p,clock,'IN','2026-10-31T23:50');assert row.off_hours
    data=svc.day_summary(db,p['technician'].id,date(2026,10,31));assert len(data['punches'])==1
    assert svc.day_summary(db,p['technician'].id,date(2026,11,1))['punches']==[]
    for statement in ('update attendance_punches set at=now() where id=:id','delete from attendance_punches where id=:id'):
        with pytest.raises(DBAPIError):db.execute(text(statement),dict(id=row.id));db.commit()
        db.rollback()
    tap(db,p,clock,'IN','2026-11-01T08:00',user='other');assert db.scalar(select(Punch).where(Punch.user_id==p['other'].id)).off_hours


def test_month_board_csv_same_totals_and_finance_read_only(client,db,p,clock):
    tap(db,p,clock,'IN','2026-11-02T07:41');tap(db,p,clock,'OUT','2026-11-02T17:00')
    leave(db,p,start=date(2026,11,3),end=date(2026,11,3));svc.set_holiday(db,p['director'],'2026-11-04','Lễ')
    clock['at']=stamp('2026-11-30T23:59');total=svc.month_summary(db,p['technician'].id,DAY)
    assert total['cong']==3 and total['work_days']==1 and total['holiday_days']==1 and total['paid_leave_days']==1
    assert total['late_count']==1 and total['late_minutes']==11 and total['early_minutes']==30 and total['absent_days']==22
    rows=svc.board(db,p['finance'],DAY);row=next(r for r in rows if r['id']==p['technician'].id);assert row['total']==total
    csrf=web_login(client,p['finance']);response=client.get('/cham-cong/bang?thang=2026-11')
    assert response.status_code==200 and 'Khóa bảng công' not in response.text and 'Mở khóa</button>' not in response.text
    assert 'data-total="cong">3<' in response.text
    csv_response=client.get('/cham-cong/bang.csv?thang=2026-11');assert csv_response.content.startswith(b'\xef\xbb\xbf')
    records=list(csv.reader(io.StringIO(csv_response.content.decode('utf-8-sig'))))
    record=next(r for r in records[1:] if r[0]==row['code']);assert record[2:32]==[d['symbol'] for d in row['days']]
    assert [Decimal(v) for v in record[32:]]==[total[k] for k in svc.TOTAL_FIELDS]
    assert client.post('/cham-cong/bang/khoa?thang=2026-11',data=dict(csrf=csrf)).status_code==403
    web_login(client,p['other']);assert client.get('/cham-cong/ngay?user_id='+str(p['technician'].id)+'&ngay=2026-11-02').status_code==403
    assert client.get('/cham-cong/bang.csv?thang=2026-11').status_code==403


def test_all_write_audits_no_stock_cash_and_director_pending_count(client,db,p,clock):
    tap(db,p,clock,'IN','2026-11-02T08:00');clock['at']=stamp('2026-11-02T19:00')
    row=req(db,p,'BO_SUNG_RA',punch_at='2026-11-02T17:30');assert svc.pending_count(db,p['director'])==1
    web_login(client,p['director']);assert 'Chấm công chờ duyệt · 1 yêu cầu' in client.get('/tong-quan').text
    assert 'Chấm công chờ duyệt · 1 yêu cầu' in client.get('/viec-cua-toi').text
    svc.approve(db,p['director'],row.id,row.version)
    row=req(db,p,'NGHI_PHEP',from_date=DAY,to_date=DAY);svc.reject(db,p['director'],row.id,row.version,'Không đồng ý')
    row=req(db,p,'NGHI_PHEP',from_date=DAY,to_date=DAY);svc.cancel(db,p['technician'],row.id,row.version)
    svc.set_holiday(db,p['director'],'2026-11-03','Ngày lễ');svc.remove_holiday(db,p['director'],'2026-11-03')
    svc.lock_month(db,p['director'],DAY);svc.unlock_month(db,p['director'],DAY,'Sửa giờ')
    actions=set(db.scalars(select(AuditLog.action).where(AuditLog.action.like('attendance.%'))))
    assert actions=={'attendance.punch','attendance.request','attendance.approve','attendance.reject','attendance.cancel','attendance.holiday','attendance.lock','attendance.unlock'}
    assert all(db.scalar(select(func.count()).select_from(m))==0 for m in (StockDoc,CashDoc,FundLedger))


def test_concurrent_punch_is_idempotent_and_lock_prevents_late_writes(db,engine,p,clock):
    barrier=Barrier(2);uid=p['technician'].id
    def hit():
        with Session(engine) as session:
            actor=session.get(User,uid);barrier.wait();return svc.punch(session,actor,'IN').id
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:hit(),range(2)))
    assert len(set(results))==1 and db.scalar(select(func.count()).select_from(Punch))==1
    svc.lock_month(db,p['director'],DAY)
    with Session(engine) as session:
        actor=session.get(User,uid);fail('THANG_DA_KHOA',svc.punch,session,actor,'OUT')
    assert db.scalar(select(func.count()).select_from(Punch))==1


def test_board_query_count_does_not_grow_per_day_or_employee(db,p,clock):
    statements=[]
    def capture(conn,cursor,sql,*args):statements.append(sql)
    event.listen(db.bind,'before_cursor_execute',capture)
    try:svc.board(db,p['finance'],DAY)
    finally:event.remove(db.bind,'before_cursor_execute',capture)
    assert len(statements)<=7 and all(sql.lstrip().upper().startswith('SELECT') for sql in statements)


def test_visits_crosscheck_in_summary_and_queue(db,p,clock):
    from app.models import Customer,ServiceTicket
    customer=Customer(code='KTD-000001',name='Khách',search_text='khach');db.add(customer);db.flush()
    ticket=ServiceTicket(number='DV-26-00001',customer_code=customer.code,kind='SUA_TAI_CHO',symptom='Kẹt giấy',created_by=p['dispatcher'].id)
    db.add(ticket);db.flush()
    db.add_all([ServiceVisit(ticket_id=ticket.id,technician_id=p['technician'].id,started_at=stamp('2026-11-02T08:40'),ended_at=stamp('2026-11-02T09:40'),outcome='XONG'),
        ServiceVisit(ticket_id=ticket.id,technician_id=p['technician'].id,started_at=stamp('2026-11-02T14:00'),ended_at=stamp('2026-11-02T15:00'),outcome='XONG')]);db.commit()
    data=svc.day_summary(db,p['technician'].id,DAY);assert data['visits']['count']==2 and data['visits']['first_at']==stamp('2026-11-02T08:40')
    row=req(db,p,'BO_SUNG_VAO',punch_at='2026-11-02T08:00');entry=svc.requests(db,p['director'],True)[0]
    assert entry['technician'] and entry['visits']['count']==2


def test_migration_down_up_restores_schema_and_guard(db,engine,p,monkeypatch):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path=Path(__file__).parents[1]/'migrations/versions/0042_ns2_cham_cong.py';spec=importlib.util.spec_from_file_location('ns2_migration',path)
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    assert migration.down_revision=='0041' and migration.revision=='0042';db.rollback()
    with engine.begin() as conn:
        monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(conn)));migration.downgrade()
        assert conn.scalar(text("select to_regclass('attendance_punches')")) is None
        migration.upgrade();assert conn.scalar(text("select count(*) from pg_trigger where tgname='attendance_punch_immutable'"))==1
        # 0042 tạo lại bảng với ràng buộc cũ; chạy lại 0065 để schema về đúng head (nguồn TU_DONG) cho các test sau.
        path65=Path(__file__).parents[1]/'migrations/versions/0065_auto_clock_out.py';spec65=importlib.util.spec_from_file_location('auto_out_migration',path65)
        m65=importlib.util.module_from_spec(spec65);spec65.loader.exec_module(m65);monkeypatch.setattr(m65,'op',Operations(MigrationContext.configure(conn)));m65.upgrade()
    assert db.scalar(select(func.count()).select_from(Employee))==2


def test_auto_clock_out_at_19_when_forgot(db,p,clock):
    # Vào ca 07:30, quên ra ca: trước 19:00 chưa đóng; đúng 19:00 app ghi Ra ca nguồn TU_DONG, công 1, không thiếu giờ ra.
    tap(db,p,clock,'IN','2026-11-02T07:30')
    clock['at']=stamp('2026-11-02T18:59');assert svc.auto_clock_out(db)==[]
    assert svc.day_summary(db,p['technician'].id,DAY)['status']=='THIEU_GIO_RA'
    clock['at']=stamp('2026-11-02T19:00');rows=svc.auto_clock_out(db)
    assert len(rows)==1 and rows[0].kind=='OUT' and rows[0].source=='TU_DONG' and rows[0].at==stamp('2026-11-02T19:00')
    day=svc.day_summary(db,p['technician'].id,DAY)
    assert day['status']=='DI_LAM' and day['cong']==1 and not day['missing_out'] and day['hours']==Decimal('9.5') and day['early_minutes']==0
    # Chạy lại không tạo thêm; người đã ra ca không bị đụng.
    clock['at']=stamp('2026-11-02T19:05');assert svc.auto_clock_out(db)==[]
    assert len(svc.day_summary(db,p['technician'].id,DAY)['punches'])==2
    assert db.scalar(select(AuditLog).where(AuditLog.action=='attendance.punch',AuditLog.user_id.is_(None))) is not None


def test_auto_clock_out_closes_previous_day_and_skips_locked_month(db,p,clock):
    # Máy chủ tắt lúc 19:00 hôm trước: sáng hôm sau đóng ca cũ lúc 19:00 của ngày đó.
    tap(db,p,clock,'IN','2026-11-02T07:30');clock['at']=stamp('2026-11-03T08:00')
    rows=svc.auto_clock_out(db);assert len(rows)==1 and rows[0].at==stamp('2026-11-02T19:00')
    # Vào ca sau 19:00 (ngoài giờ) thì hôm đó không đóng, hôm sau đóng ngay tại giờ vào (0 giờ).
    tap(db,p,clock,'IN','2026-11-03T20:00');clock['at']=stamp('2026-11-03T23:00');assert svc.auto_clock_out(db)==[]
    clock['at']=stamp('2026-11-04T07:00');rows=svc.auto_clock_out(db);assert len(rows)==1 and rows[0].at==stamp('2026-11-03T20:00')
    # Tháng đã khóa: bỏ qua, không lỗi.
    tap(db,p,clock,'IN','2026-11-04T07:30');svc.lock_month(db,p['director'],DAY)
    clock['at']=stamp('2026-11-04T19:30');assert svc.auto_clock_out(db)==[]
