"""NS2: server-time punches, atomic approvals/month locks, reusable payroll summaries."""
from calendar import monthrange
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text

from . import audit, doc_numbers
from .config import get_settings
from .errors import BusinessError
from .models import (AttendanceHoliday as Holiday, AttendanceMonthLock as MonthLock,
                     AttendancePunch as Punch, AttendanceRequest as Request, Employee, ServiceVisit, User)
from .security import has_role
from .stock_docs import _transaction

TZ=ZoneInfo('Asia/Ho_Chi_Minh')
KINDS={'BO_SUNG_VAO':'Bổ sung giờ vào','BO_SUNG_RA':'Bổ sung giờ ra','NGHI_PHEP':'Nghỉ phép','NGHI_KHONG_LUONG':'Nghỉ không lương'}
STATUSES={'DI_LAM':'Đi làm','THIEU_GIO_RA':'Thiếu giờ ra','NGHI_PHEP':'Nghỉ phép','NGHI_KHONG_LUONG':'Nghỉ không lương',
          'NGHI_KHONG_PHEP':'Nghỉ không phép','NGAY_LE':'Ngày lễ','NGAY_NGHI':'Ngày nghỉ','CHUA_VAO_LAM':'Chưa vào làm','DA_NGHI_VIEC':'Đã nghỉ việc'}
TOTAL_FIELDS=('cong','work_days','holiday_days','paid_leave_days','unpaid_leave_days','absent_days',
              'late_count','late_minutes','early_count','early_minutes','missing_out_days','off_hours_count')


def _now():return datetime.now(UTC)
def today():return _now().astimezone(TZ).date()
def month_day(day):return day.replace(day=1)
def month_end(day):return day.replace(day=monthrange(day.year,day.month)[1])
def _days(start,end):
    for n in range((end-start).days+1):yield start+timedelta(days=n)
def _bounds(day):return datetime.combine(day,time.min,TZ),datetime.combine(day+timedelta(days=1),time.min,TZ)


def _date(value):
    if type(value) is date:return value
    try:return date.fromisoformat(str(value))
    except (TypeError,ValueError):raise BusinessError('BAD_DATE','Ngày không hợp lệ.')


def _stamp(value):
    try:stamp=value if isinstance(value,datetime) else datetime.fromisoformat(str(value))
    except (TypeError,ValueError):raise BusinessError('BAD_PUNCH_TIME','Hãy chọn ngày giờ cần bổ sung.')
    return stamp.replace(tzinfo=TZ) if stamp.tzinfo is None else stamp


def director(user):
    if not user.active or not has_role(user,'director'):raise BusinessError('FORBIDDEN','Chỉ giám đốc được duyệt và khóa chấm công.',status=403)


def reader(user):
    if not user.active or not has_role(user,'finance'):raise BusinessError('FORBIDDEN','Chỉ kế toán và giám đốc được xem bảng công.',status=403)


def is_locked(row):return bool(row and (row.unlocked_at is None or row.locked_at>row.unlocked_at))


def _months(start,end):
    cursor=month_day(start)
    while cursor<=end:
        yield cursor
        if cursor.month==12:cursor=date(cursor.year+1,1,1)
        else:cursor=cursor.replace(month=cursor.month+1)


def _month_locks(session,start,end,check=True):
    # All writes acquire these locks BEFORE per-user locks, also when no lock row exists yet.
    for month in _months(start,end):
        session.execute(text('select pg_advisory_xact_lock(836001,:month)'),dict(month=month.year*12+month.month))
        row=session.get(MonthLock,month,populate_existing=True)
        if check and is_locked(row):raise BusinessError('THANG_DA_KHOA','Tháng đã khóa bảng công.',status=409)


def _user_lock(session,ident):
    session.execute(text('select pg_advisory_xact_lock(836002,:id)'),dict(id=ident))


def _eligible(session,ident,start,end=None):
    account=session.get(User,ident,populate_existing=True)
    employee=session.scalar(select(Employee).where(Employee.user_id==ident))
    if employee is None:raise BusinessError('NO_EMPLOYEE','Chưa có hồ sơ nhân sự, liên hệ quản lý')
    if not account or not account.active or employee.end_date and employee.end_date<(end or start):
        raise BusinessError('DA_NGHI_VIEC','Tài khoản đã khóa hoặc nhân viên đã nghỉ việc.',status=403)
    if employee.start_date>start:raise BusinessError('CHUA_VAO_LAM','Chưa đến ngày vào làm.')
    return employee


def _snapshot(row):
    data={c.key:getattr(row,c.key) for c in row.__table__.columns}
    return {k:v.isoformat() if isinstance(v,(date,datetime)) else str(v) if isinstance(v,Decimal) else v for k,v in data.items()}


def _record(session,user,action,entity,row,before=None,reason=''):
    session.flush();audit.record(session,user,'attendance.'+action,entity,str(getattr(row,'id',getattr(row,'month',getattr(row,'day','')))),before,_snapshot(row),reason)


def _off_hours(session,stamp):
    local=stamp.astimezone(TZ);cfg=get_settings();clock=local.time()
    return clock<cfg.off_hours_start or clock>cfg.off_hours_end or local.weekday() not in cfg.work_days or session.get(Holiday,local.date()) is not None


def lunch_label():
    cfg=get_settings();return f"Nghỉ trưa từ {cfg.lunch_start.strftime('%H:%M')} đến {cfg.lunch_end.strftime('%H:%M')}"


def punch_hint(session,ident,now=None):
    """Gợi ý trên màn Hôm nay của thợ. 'LUNCH' trong giờ nghỉ trưa (không cần ra/vào ca, chỉ hiện dòng báo);
    'IN' từ trước ca sáng `punch_early_minutes` phút tới cuối ca sáng, hoặc từ đầu ca chiều tới cuối ca chiều, khi chưa vào ca;
    'OUT' từ cuối ca chiều khi còn đang trong ca; None ngoài khung và Chủ nhật/ngày lễ (trừ khi còn trong ca)."""
    local=(now or _now()).astimezone(TZ);cfg=get_settings();clock=local.time();day=local.date()
    rows=_punches(session,ident,day);opened=rows and rows[-1].kind=='IN'
    if opened and clock>=cfg.work_end:return 'OUT'
    if day.weekday() not in cfg.work_days or session.get(Holiday,day) is not None:return None
    if cfg.lunch_start<=clock<cfg.lunch_end:return 'LUNCH'
    if opened:return None
    before=(datetime.combine(day,cfg.work_start)-timedelta(minutes=cfg.punch_early_minutes)).time()
    if before<=clock<cfg.lunch_start or cfg.lunch_end<=clock<cfg.work_end:return 'IN'
    return None


def _punches(session,ident,day):
    start,end=_bounds(day)
    return list(session.scalars(select(Punch).where(Punch.user_id==ident,Punch.at>=start,Punch.at<end).order_by(Punch.at,Punch.id)))


def _location(lat,lng,accuracy):
    if lat in (None,'') or lng in (None,''):return None,None,None
    try:
        lat,lng=Decimal(str(lat)),Decimal(str(lng));acc=int(accuracy) if accuracy not in (None,'') else None
        if not lat.is_finite() or not lng.is_finite() or not -90<=lat<=90 or not -180<=lng<=180 or acc is not None and not 0<=acc<=2147483647:raise ValueError()
    except (ValueError,InvalidOperation,OverflowError):raise BusinessError('BAD_LOCATION','Tọa độ không hợp lệ. Có thể chấm công không kèm vị trí.')
    return lat,lng,acc


def punch(session,user,kind,lat=None,lng=None,accuracy=None):
    if kind not in ('IN','OUT'):raise BusinessError('BAD_PUNCH_KIND','Lần chấm công không hợp lệ.')
    with _transaction(session):
        stamp=_now();day=stamp.astimezone(TZ).date();_month_locks(session,day,day);_user_lock(session,user.id);_eligible(session,user.id,day)
        rows=_punches(session,user.id,day);last=rows[-1] if rows else None
        if last and last.kind==kind and 0<=(stamp-last.at).total_seconds()<60:return last
        if kind=='IN' and last and last.kind=='IN':raise BusinessError('DA_VAO_CA','Đã vào ca, hãy ra ca trước khi vào lại.')
        if kind=='OUT' and (not last or last.kind!='IN'):raise BusinessError('CHUA_VAO_CA','Chưa vào ca. Gửi bổ sung giờ vào.')
        lat,lng,acc=_location(lat,lng,accuracy)
        row=Punch(user_id=user.id,kind=kind,at=stamp,lat=lat,lng=lng,accuracy_m=acc,source='APP',off_hours=_off_hours(session,stamp))
        session.add(row);_record(session,user,'punch','attendance_punch',row)
    return row


def request(session,user,kind,*,punch_at=None,from_date=None,to_date=None,part='CA_NGAY',reason=''):
    if kind not in KINDS:raise BusinessError('BAD_REQUEST_KIND','Loại yêu cầu không hợp lệ.')
    reason=str(reason or '').strip()
    if not reason or len(reason)>4000:raise BusinessError('REASON_REQUIRED','Lý do bắt buộc, tối đa 4.000 ký tự.')
    if part not in ('CA_NGAY','SANG','CHIEU'):raise BusinessError('BAD_PART','Buổi nghỉ không hợp lệ.')
    stamp=_now()
    if kind.startswith('BO_SUNG'):
        at=_stamp(punch_at)
        if at>stamp:raise BusinessError('FUTURE_PUNCH','Không bổ sung giờ ở tương lai.')
        start=end=at.astimezone(TZ).date();first=last=None;part='CA_NGAY'
    else:
        first,last=_date(from_date),_date(to_date)
        if first>last or part!='CA_NGAY' and first!=last:raise BusinessError('BAD_LEAVE_DATES','Khoảng ngày nghỉ không hợp lệ; nghỉ nửa ngày chỉ chọn một ngày.')
        start,end=first,last;at=None
    with _transaction(session):
        _month_locks(session,start,end);_user_lock(session,user.id);_eligible(session,user.id,start,end)
        row=Request(number=doc_numbers.next_number(session,'CC',stamp.astimezone(TZ).date()),user_id=user.id,kind=kind,
            punch_at=at,from_date=first,to_date=last,part=part,reason=reason,created_at=stamp)
        session.add(row);_record(session,user,'request','attendance_request',row,reason=reason)
    return row


def request_days(row):
    if row.punch_at:return (row.punch_at.astimezone(TZ).date(),)*2
    return row.from_date,row.to_date


def _request_lock(session,ident,version):
    row=session.scalar(select(Request).where(Request.id==ident).with_for_update().execution_options(populate_existing=True))
    if row is None:raise BusinessError('NOT_FOUND','Không tìm thấy yêu cầu.',status=404)
    if str(row.version)!=str(version):raise BusinessError('VERSION_CONFLICT','Yêu cầu vừa được xử lý, hãy tải lại.',status=409)
    if row.status!='CHO_DUYET':raise BusinessError('REQUEST_PROCESSED','Yêu cầu đã được xử lý.',status=409)
    return row


def _load_request(session,ident):
    row=session.get(Request,ident)
    if row is None:raise BusinessError('NOT_FOUND','Không tìm thấy yêu cầu.',status=404)
    return row


def _approve_punch(session,row):
    day=row.punch_at.astimezone(TZ).date();kind='IN' if row.kind=='BO_SUNG_VAO' else 'OUT'
    rows=[(p.at,p.kind) for p in _punches(session,row.user_id,day)]+[(row.punch_at,kind)]
    previous='OUT'
    for stamp,current in sorted(rows,key=lambda p:p[0]):
        if current==previous:raise BusinessError('BAD_PUNCH_SEQUENCE','Giờ bổ sung trùng hoặc không khớp thứ tự vào/ra. Hãy kiểm tra chi tiết ngày.')
        previous=current
    punch=Punch(user_id=row.user_id,kind=kind,at=row.punch_at,source='BO_SUNG',request_id=row.id,off_hours=_off_hours(session,row.punch_at))
    session.add(punch);return punch


def _leave_overlap(session,row):
    others=session.scalars(select(Request).where(Request.user_id==row.user_id,Request.id!=row.id,Request.status=='DA_DUYET',
        Request.kind.in_(('NGHI_PHEP','NGHI_KHONG_LUONG')),Request.from_date<=row.to_date,Request.to_date>=row.from_date))
    for other in others:
        if row.part=='CA_NGAY' or other.part=='CA_NGAY' or row.part==other.part:
            raise BusinessError('LEAVE_OVERLAP','Đã có nghỉ được duyệt trùng ngày/buổi.',status=409)


def approve(session,user,ident,version,note=''):
    director(user)
    with _transaction(session):
        original=_load_request(session,ident);start,end=request_days(original)
        _month_locks(session,start,end);_user_lock(session,original.user_id);row=_request_lock(session,ident,version)
        _eligible(session,row.user_id,start,end);before=_snapshot(row)
        if row.punch_at:
            if row.punch_at>_now():raise BusinessError('FUTURE_PUNCH','Không bổ sung giờ ở tương lai.')
            new=_approve_punch(session,row);_record(session,user,'punch','attendance_punch',new,reason=row.reason)
        else:_leave_overlap(session,row)
        row.status='DA_DUYET';row.decided_by=user.id;row.decided_at=_now();row.decision_note=str(note or '').strip();row.version+=1
        _record(session,user,'approve','attendance_request',row,before,row.reason)
    return row


def _finish(session,user,ident,version,status,note):
    with _transaction(session):
        original=_load_request(session,ident)
        if status=='DA_HUY' and original.user_id!=user.id:raise BusinessError('FORBIDDEN','Chỉ người gửi được hủy yêu cầu của mình.',status=403)
        start,end=request_days(original);_month_locks(session,start,end);_user_lock(session,original.user_id)
        row=_request_lock(session,ident,version);before=_snapshot(row)
        row.status=status;row.decided_by=user.id;row.decided_at=_now();row.decision_note=note;row.version+=1
        _record(session,user,'cancel' if status=='DA_HUY' else 'reject','attendance_request',row,before,note)
    return row


def reject(session,user,ident,version,note):
    director(user);note=str(note or '').strip()
    if not note:raise BusinessError('NOTE_REQUIRED','Từ chối phải có ghi chú.')
    return _finish(session,user,ident,version,'TU_CHOI',note)


def cancel(session,user,ident,version):return _finish(session,user,ident,version,'DA_HUY','Người gửi hủy yêu cầu')


def set_holiday(session,user,day,name):
    director(user);day=_date(day);name=str(name or '').strip()
    if not name or len(name)>200:raise BusinessError('HOLIDAY_NAME_REQUIRED','Tên ngày lễ bắt buộc, tối đa 200 ký tự.')
    with _transaction(session):
        _month_locks(session,day,day);row=session.get(Holiday,day);before=_snapshot(row) if row else None
        if row is None:row=Holiday(day=day,name=name);session.add(row)
        else:row.name=name
        _record(session,user,'holiday','attendance_holiday',row,before)
    return row


def remove_holiday(session,user,day):
    director(user);day=_date(day)
    with _transaction(session):
        _month_locks(session,day,day);row=session.get(Holiday,day)
        if row is None:raise BusinessError('NOT_FOUND','Không tìm thấy ngày lễ.',status=404)
        before=_snapshot(row);session.delete(row)
        audit.record(session,user,'attendance.holiday','attendance_holiday',day.isoformat(),before,None,'Bỏ ngày lễ')


def lock_month(session,user,month):
    director(user);month=month_day(_date(month))
    with _transaction(session):
        _month_locks(session,month,month,False);row=session.get(MonthLock,month);before=_snapshot(row) if row else None
        if is_locked(row):raise BusinessError('THANG_DA_KHOA','Tháng đã khóa.',status=409)
        stamp=max(_now(),row.unlocked_at+timedelta(microseconds=1)) if row and row.unlocked_at else _now()
        if row is None:row=MonthLock(month=month,locked_by=user.id,locked_at=stamp);session.add(row)
        else:row.locked_by=user.id;row.locked_at=stamp
        _record(session,user,'lock','attendance_month_lock',row,before)
    return row


def unlock_month(session,user,month,reason):
    director(user);month=month_day(_date(month));reason=str(reason or '').strip()
    if not reason:raise BusinessError('REASON_REQUIRED','Mở khóa phải có lý do.')
    with _transaction(session):
        _month_locks(session,month,month,False);row=session.get(MonthLock,month)
        if not is_locked(row):raise BusinessError('MONTH_NOT_LOCKED','Tháng chưa khóa.',status=409)
        before=_snapshot(row);row.unlocked_by=user.id;row.unlocked_at=max(_now(),row.locked_at+timedelta(microseconds=1));row.unlock_reason=reason
        _record(session,user,'unlock','attendance_month_lock',row,before,reason)
    return row


def _dataset(session,ids,start,end):
    first,_=_bounds(start);_,last=_bounds(end)
    data=dict(employees={e.user_id:e for e in session.scalars(select(Employee).where(Employee.user_id.in_(ids)))},
        punches=defaultdict(list),leaves=defaultdict(list),visits={},holidays=dict(session.execute(select(Holiday.day,Holiday.name).where(Holiday.day.between(start,end))).all()))
    for row in session.scalars(select(Punch).where(Punch.user_id.in_(ids),Punch.at>=first,Punch.at<last).order_by(Punch.at,Punch.id)):
        data['punches'][(row.user_id,row.at.astimezone(TZ).date())].append(row)
    for row in session.scalars(select(Request).where(Request.user_id.in_(ids),Request.status=='DA_DUYET',Request.kind.in_(('NGHI_PHEP','NGHI_KHONG_LUONG')),Request.from_date<=end,Request.to_date>=start)):
        for day in _days(max(row.from_date,start),min(row.to_date,end)):data['leaves'][(row.user_id,day)].append(row)
    day=func.date(func.timezone('Asia/Ho_Chi_Minh',ServiceVisit.started_at))
    for ident,day_value,count,stamp in session.execute(select(ServiceVisit.technician_id,day,func.count(),func.min(ServiceVisit.started_at)).where(
            ServiceVisit.technician_id.in_(ids),ServiceVisit.started_at>=first,ServiceVisit.started_at<last).group_by(ServiceVisit.technician_id,day)):
        data['visits'][(ident,day_value)]=dict(count=count,first_at=stamp)
    return data


def _day(data,ident,day):
    employee=data['employees'].get(ident);rows=data['punches'][(ident,day)];leaves=data['leaves'][(ident,day)];cfg=get_settings()
    ins=[r.at for r in rows if r.kind=='IN'];outs=[r.at for r in rows if r.kind=='OUT'];first=min(ins) if ins else None;last=max(outs) if outs else None
    opened=None;seconds=0
    # Giờ nghỉ trưa không tính vào số giờ làm: một cặp vào sáng/ra chiều trừ phần trùng với khung trưa.
    lunch=(datetime.combine(day,cfg.lunch_start,TZ),datetime.combine(day,cfg.lunch_end,TZ))
    for row in rows:
        if row.kind=='IN':opened=row.at
        elif opened:
            overlap=max(timedelta(0),min(row.at,lunch[1])-max(opened,lunch[0]))
            seconds+=(row.at-opened-overlap).total_seconds();opened=None
    paid=sum(Decimal(1) if r.part=='CA_NGAY' else Decimal('0.5') for r in leaves if r.kind=='NGHI_PHEP')
    unpaid=sum(Decimal(1) if r.part=='CA_NGAY' else Decimal('0.5') for r in leaves if r.kind=='NGHI_KHONG_LUONG')
    start=datetime.combine(day,cfg.work_start,TZ);end=datetime.combine(day,cfg.work_end,TZ)
    late=int((first-start).total_seconds()//60) if first and first>start+timedelta(minutes=cfg.late_grace_minutes) else 0
    early=max(0,int((end-last).total_seconds()//60)) if last else 0
    status='DI_LAM' if ins and outs and opened is None else 'THIEU_GIO_RA' if opened else 'NGHI_PHEP' if paid else 'NGHI_KHONG_LUONG' if unpaid else 'NGHI_KHONG_PHEP'
    cong=min(Decimal(1),paid+(max(Decimal(0),1-paid-unpaid) if ins and outs and opened is None else Decimal(0)))
    if opened:cong=Decimal(0)
    if day.weekday() not in cfg.work_days and not rows:status='NGAY_NGHI';cong=Decimal(0);paid=unpaid=Decimal(0)
    if day in data['holidays']:status='NGAY_LE';cong=Decimal(1);paid=unpaid=Decimal(0)
    if not employee or day<employee.start_date:status='CHUA_VAO_LAM';cong=Decimal(0)
    elif employee.end_date and day>employee.end_date:status='DA_NGHI_VIEC';cong=Decimal(0)
    future=day>today()
    symbol={'DI_LAM':'T' if late else 'X','THIEU_GIO_RA':'R','NGHI_PHEP':'P/2' if paid==Decimal('0.5') else 'P',
        'NGHI_KHONG_LUONG':'KL','NGHI_KHONG_PHEP':'K','NGAY_LE':'L'}.get(status,'–')
    if future:symbol='–'
    return dict(day=day,first_in=first,last_out=last,hours=Decimal(str(seconds/3600)).quantize(Decimal('0.01')),late_minutes=late,early_minutes=early,
        status=status,cong=cong,paid_leave=paid,unpaid_leave=unpaid,missing_out=opened is not None,off_hours_count=sum(r.off_hours for r in rows),
        visits=data['visits'].get((ident,day),dict(count=0,first_at=None)),punches=rows,symbol=symbol,future=future,holiday=data['holidays'].get(day))


def _total(days):
    total={k:0 for k in TOTAL_FIELDS};total['cong']=Decimal(0)
    for r in days:
        if r['future'] or r['status'] in ('CHUA_VAO_LAM','DA_NGHI_VIEC'):continue
        total['cong']+=r['cong'];total['work_days']+=r['status']=='DI_LAM';total['holiday_days']+=r['status']=='NGAY_LE'
        total['paid_leave_days']+=r['paid_leave'];total['unpaid_leave_days']+=r['unpaid_leave'];total['absent_days']+=r['status']=='NGHI_KHONG_PHEP'
        total['late_count']+=r['late_minutes']>0;total['late_minutes']+=r['late_minutes'];total['early_count']+=r['early_minutes']>0
        total['early_minutes']+=r['early_minutes'];total['missing_out_days']+=r['missing_out'];total['off_hours_count']+=r['off_hours_count']
    return total


def day_summary(session,user_id,day):
    day=_date(day)
    with session.no_autoflush:return _day(_dataset(session,[user_id],day,day),user_id,day)


def month_days(session,user_id,month):
    start=month_day(_date(month));end=month_end(start)
    with session.no_autoflush:
        data=_dataset(session,[user_id],start,end)
        return [_day(data,user_id,day) for day in _days(start,end)]


def month_summary(session,user_id,month):
    """Same day/total calculation; collections load one shared dataset for payroll."""
    if isinstance(user_id,(list,tuple,set)):
        start=month_day(_date(month));end=month_end(start)
        with session.no_autoflush:
            data=_dataset(session,user_id,start,end)
            return {ident:_total([_day(data,ident,day) for day in _days(start,end)]) for ident in user_id}
    return _total(month_days(session,user_id,month))


def board(session,user,month):
    reader(user);start=month_day(_date(month));end=month_end(start)
    people=list(session.execute(select(User.id,User.full_name,Employee.code).join(Employee,Employee.user_id==User.id).where(
        Employee.start_date<=end,(Employee.end_date.is_(None))|(Employee.end_date>=start)).order_by(Employee.code)).mappings())
    with session.no_autoflush:
        data=_dataset(session,[p['id'] for p in people],start,end);rows=[]
        for person in people:
            days=[_day(data,person['id'],day) for day in _days(start,end)]
            rows.append(dict(**person,days=days,total=_total(days)))
    return rows


def requests(session,user,pending=False):
    if pending:director(user)
    stmt=select(Request,User.full_name,User.roles).join(User,User.id==Request.user_id)
    stmt=stmt.where(Request.status=='CHO_DUYET') if pending else stmt.where(Request.user_id==user.id)
    rows=[]
    for row,name,roles in session.execute(stmt.order_by(Request.created_at.desc(),Request.id.desc())):
        day=row.punch_at.astimezone(TZ).date() if row.punch_at else row.from_date
        rows.append(dict(item=row,name=name,late_submission=bool(row.punch_at and row.created_at-row.punch_at>timedelta(days=7)),visit_day=day,technician='technician' in roles))
    # One grouped visit query for the entire queue, not one query per request.
    if rows:
        ids={r['item'].user_id for r in rows};start=min(r['visit_day'] for r in rows);end=max(r['visit_day'] for r in rows)
        first,_=_bounds(start);_,last=_bounds(end);day=func.date(func.timezone('Asia/Ho_Chi_Minh',ServiceVisit.started_at))
        visits={(ident,d):dict(count=count,first_at=stamp) for ident,d,count,stamp in session.execute(select(ServiceVisit.technician_id,day,func.count(),func.min(ServiceVisit.started_at)).where(
            ServiceVisit.technician_id.in_(ids),ServiceVisit.started_at>=first,ServiceVisit.started_at<last).group_by(ServiceVisit.technician_id,day))}
        for row in rows:row['visits']=visits.get((row['item'].user_id,row['visit_day']),dict(count=0,first_at=None))
    return rows


def pending_count(session,user):
    director(user);return int(session.scalar(select(func.count()).select_from(Request).where(Request.status=='CHO_DUYET')))
