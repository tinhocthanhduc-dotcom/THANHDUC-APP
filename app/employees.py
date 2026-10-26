"""NS1: common profiles and director-only pay; no money/stock posting."""
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from . import audit
from .errors import BusinessError
from .models import AuditLog, Employee, EmployeePay, EmployeeSalaryVersion as Salary, User
from .security import has_role
from .stock_docs import _transaction
from .text import fold
from . import search as tim

POSITIONS = ('Kỹ thuật','Kinh doanh','Kho','Kế toán','Điều phối','Lễ tân','Quản lý','Khác')
MONEY_FIELDS = ('base_salary','meal_allowance','fuel_allowance','phone_allowance','other_allowance')
MONEY_LABELS = ('Lương cơ bản','Phụ cấp ăn','Phụ cấp xăng','Phụ cấp điện thoại','Phụ cấp khác')
COMMON = ('id','code','user_id','position','start_date','end_date','birth_date','id_number','id_issued_on',
          'address','emergency_name','emergency_phone','note','version','created_at','updated_at','created_by')


def today():
    return datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date()


def manager(actor):
    if not actor.active or not has_role(actor,'admin','director'):
        raise BusinessError('FORBIDDEN','Chỉ quản trị hoặc giám đốc được quản lý hồ sơ nhân viên.',status=403)


def director(actor):
    if not actor.active or not has_role(actor,'director'):
        raise BusinessError('FORBIDDEN','Chỉ giám đốc được xem và sửa lương, ngân hàng.',status=403)


def _error(code,message,field):
    raise BusinessError(code,message,{'fields':{field:message}})


def _date(value,field,required=False):
    if value in (None,''):
        if required:_error('DATE_REQUIRED','Hãy chọn ngày.',field)
        return None
    if type(value) is date:return value
    try:return date.fromisoformat(str(value))
    except ValueError:_error('BAD_DATE','Ngày chưa hợp lệ.',field)


def _text(value,field,limit=200):
    value=str(value or '').strip()
    if len(value)>limit:_error('TEXT_TOO_LONG',f'Nội dung tối đa {limit} ký tự.',field)
    return value


def _common(data):
    values={k:_text(data.get(k,''),k,4000 if k=='note' else 2000 if k=='address' else 200)
            for k in ('position','id_number','address','emergency_name','emergency_phone','note')}
    if values['position'] not in POSITIONS:_error('BAD_POSITION','Hãy chọn vị trí làm việc.', 'position')
    for k in ('start_date','end_date','birth_date','id_issued_on'):values[k]=_date(data.get(k),k,k=='start_date')
    if values['end_date'] and values['end_date']<values['start_date']:_error('BAD_END_DATE','Ngày nghỉ việc không được trước ngày vào làm.','end_date')
    if values['id_number'] and not re.fullmatch(r'(?:[0-9]{9}|[0-9]{12})',values['id_number']):
        _error('BAD_ID_NUMBER','CCCD gồm 9 hoặc 12 chữ số.','id_number')
    return values


def _json(row,fields):
    result={k:getattr(row,k) for k in fields}
    return {k:v.isoformat() if isinstance(v,(date,datetime)) else str(v) if isinstance(v,Decimal) else v for k,v in result.items()}


def _snapshot(row):return _json(row,COMMON)


def _pay(row):return _json(row,('employee_id','bank_name','bank_account','bank_holder','version','updated_at'))


def _salary(row):return _json(row,('id','employee_id','effective_from',*MONEY_FIELDS,'standard_days','reason','created_by','created_at','replaces_id','replaced_at'))


def _locked(session,ident):
    row=session.scalar(select(Employee).where(Employee.id==ident).with_for_update().execution_options(populate_existing=True))
    if row is None:raise BusinessError('NOT_FOUND','Không tìm thấy hồ sơ nhân viên.',status=404)
    return row


def _version(actual,version):
    if isinstance(version,bool) or str(version)!=str(actual):
        raise BusinessError('VERSION_CONFLICT','Hồ sơ vừa được thay đổi, hãy tải lại trước khi lưu.',status=409)


def _integrity(exc):
    name=getattr(getattr(exc.orig,'diag',None),'constraint_name',None)
    if name=='employees_user_id_key':raise BusinessError('EMPLOYEE_EXISTS','Tài khoản đã có hồ sơ nhân sự.',status=409) from exc
    if name=='uq_employees_id_number':raise BusinessError('ID_NUMBER_EXISTS','CCCD đã có trong hồ sơ khác.',{'fields':{'id_number':'CCCD đã có trong hồ sơ khác.'}},status=409) from exc
    if name=='uq_employee_salary_effective':raise BusinessError('SALARY_EXISTS','Đã có bản lương còn hiệu lực cùng ngày. Dùng “Thay bản này” nếu nhập sai.',status=409) from exc
    raise exc


def create(session,actor,user_id,data):
    manager(actor)
    try:
        with _transaction(session):
            account=session.scalar(select(User).where(User.id==user_id).with_for_update())
            if account is None:raise BusinessError('BAD_USER','Không tìm thấy tài khoản.',status=404)
            if session.scalar(select(Employee.id).where(Employee.user_id==user_id)):
                raise BusinessError('EMPLOYEE_EXISTS','Tài khoản đã có hồ sơ nhân sự.',status=409)
            values=_common(data)
            number=session.scalar(select(func.nextval('employee_code_seq')))
            row=Employee(code=f'NV-{number:04d}',user_id=user_id,created_by=actor.id,**values)
            session.add(row);session.flush()
            audit.record(session,actor,'employee.create','employee',str(row.id),None,_snapshot(row))
        return row
    except IntegrityError as exc:_integrity(exc)


def update(session,actor,employee_id,data,version):
    manager(actor)
    try:
        with _transaction(session):
            row=_locked(session,employee_id);_version(row.version,version);before=_snapshot(row)
            values=_common({**{k:getattr(row,k) for k in COMMON},**data})
            if values['start_date']!=row.start_date and session.scalar(select(Salary.id).where(
                    Salary.employee_id==row.id,Salary.replaced_at.is_(None),Salary.effective_from<values['start_date']).limit(1)):
                _error('SALARY_BEFORE_START','Ngày vào làm không được sau ngày hiệu lực lương đã có.','start_date')
            for k,v in values.items():setattr(row,k,v)
            row.updated_at=datetime.now(UTC);row.version+=1;session.flush()
            audit.record(session,actor,'employee.update','employee',str(row.id),before,_snapshot(row))
        return row
    except IntegrityError as exc:_integrity(exc)


def set_pay(session,actor,employee_id,data,version):
    director(actor)
    with _transaction(session):
        _locked(session,employee_id)
        row=session.get(EmployeePay,employee_id,populate_existing=True)
        _version(row.version if row else 0,version)
        values={k:_text(data.get(k,''),k) for k in ('bank_name','bank_account','bank_holder')}
        if values['bank_account'] and not re.fullmatch(r'[0-9]{6,20}',values['bank_account']):
            _error('BAD_BANK_ACCOUNT','Số tài khoản gồm 6–20 chữ số.','bank_account')
        before=_pay(row) if row else None
        if row is None:row=EmployeePay(employee_id=employee_id);session.add(row)
        else:row.version+=1
        for k,v in values.items():setattr(row,k,v)
        row.updated_at=datetime.now(UTC);session.flush()
        audit.record(session,actor,'employee.pay','employee_pay',str(employee_id),before,_pay(row))
    return row


def add_salary(session,actor,employee_id,data):
    director(actor)
    try:
        with _transaction(session):
            employee=_locked(session,employee_id)
            effective=_date(data.get('effective_from'),'effective_from',True)
            if effective<employee.start_date:_error('SALARY_BEFORE_START','Ngày hiệu lực không được trước ngày vào làm.','effective_from')
            values={}
            for field in MONEY_FIELDS:
                value=data.get(field,0)
                if isinstance(value,bool) or not re.fullmatch(r'[0-9]+',str(value)) or int(value)>10**12:
                    _error('BAD_SALARY_MONEY','Số tiền phải từ 0 đến 1.000.000.000.000 đồng.',field)
                values[field]=int(value)
            try:days=Decimal(str(data.get('standard_days',26)))
            except InvalidOperation:days=Decimal('NaN')
            if not days.is_finite() or not 1<=days<=31 or days!=days.quantize(Decimal('0.1')):
                _error('BAD_STANDARD_DAYS','Công chuẩn từ 1 đến 31, tối đa một chữ số thập phân.','standard_days')
            reason=_text(data.get('reason'),'reason',4000)
            if not reason:_error('REASON_REQUIRED','Hãy ghi lý do thay đổi lương.','reason')
            replaces=data.get('replaces_id');old=None;before=None
            if replaces not in ('',None):
                try:replaces=int(replaces)
                except (ValueError,TypeError):raise BusinessError('BAD_SALARY','Bản lương cần thay không hợp lệ.')
                old=session.scalar(select(Salary).where(Salary.id==replaces,Salary.employee_id==employee_id).with_for_update().execution_options(populate_existing=True))
                if old is None:raise BusinessError('NOT_FOUND','Không tìm thấy bản lương cần thay.',status=404)
                if old.replaced_at is not None:raise BusinessError('SALARY_REPLACED','Bản lương đã được thay, hãy tải lại.',status=409)
                before=_salary(old);old.replaced_at=datetime.now(UTC);session.flush()
            row=Salary(employee_id=employee_id,effective_from=effective,standard_days=days,reason=reason,
                       created_by=actor.id,replaces_id=old.id if old else None,**values)
            session.add(row);session.flush()
            audit.record(session,actor,'employee.salary','employee_salary',str(employee_id),before,_salary(row),reason)
        return row
    except IntegrityError as exc:_integrity(exc)


def salary_on(session,employee_id,day):
    """Internal payroll lookup (NS5). Never expose through a public route/context."""
    if isinstance(employee_id,(list,tuple,set)):
        with session.no_autoflush:
            return {r.employee_id:r for r in session.scalars(select(Salary).where(Salary.employee_id.in_(employee_id),
                Salary.replaced_at.is_(None),Salary.effective_from<=day).distinct(Salary.employee_id)
                .order_by(Salary.employee_id,Salary.effective_from.desc(),Salary.id.desc()))}
    with session.no_autoflush:
        return session.scalar(select(Salary).where(Salary.employee_id==employee_id,Salary.replaced_at.is_(None),Salary.effective_from<=day)
                              .order_by(Salary.effective_from.desc(),Salary.id.desc()).limit(1))


def _public(row,account):
    # No relationship to pay/salaries, and no User object (password hash) in output.
    return {**{k:getattr(row,k) for k in COMMON},'full_name':account.full_name,'username':account.username,
            'phone':account.phone,'active':account.active,
            'warning':'Nên khóa tài khoản' if row.end_date and account.active else ''}


def get(session,actor,employee_id):
    with session.no_autoflush:
        result=session.execute(select(Employee,User).join(User,User.id==Employee.user_id).where(Employee.id==employee_id)).first()
        if result is None:raise BusinessError('NOT_FOUND','Không tìm thấy hồ sơ nhân viên.',status=404)
        row,account=result
        if not actor.active or not (has_role(actor,'admin','director') or actor.id==row.user_id):
            raise BusinessError('FORBIDDEN','Không được xem hồ sơ nhân viên khác.',status=403)
        data=_public(row,account)
        if has_role(actor,'director'):
            pay=session.get(EmployeePay,row.id)
            data['pay']=_pay(pay) if pay else None
            data['salaries']=[_salary(s) for s in session.scalars(select(Salary).where(Salary.employee_id==row.id).order_by(Salary.effective_from.desc(),Salary.id.desc()))]
            current=salary_on(session,row.id,today());data['current_salary_id']=current.id if current else None
        return data


def own(session,actor):
    if not actor.active:raise BusinessError('FORBIDDEN','Tài khoản đã khóa.',status=403)
    with session.no_autoflush:
        row=session.scalar(select(Employee).where(Employee.user_id==actor.id))
        return _public(row,actor) if row else None


def history(session,actor,employee_id):
    manager(actor)
    entities=('employee','employee_pay','employee_salary') if has_role(actor,'director') else ('employee',)
    return list(session.scalars(select(AuditLog).where(AuditLog.entity.in_(entities),AuditLog.entity_id==str(employee_id)).order_by(AuditLog.id.desc())))


def search(session,actor,q='',position='',status=''):
    manager(actor)
    if position and position not in POSITIONS:raise BusinessError('BAD_POSITION','Vị trí không hợp lệ.')
    if status not in ('','working','left'):raise BusinessError('BAD_STATUS','Trạng thái không hợp lệ.')
    stmt=select(Employee,User).join(User,User.id==Employee.user_id)
    if position:stmt=stmt.where(Employee.position==position)
    if status:stmt=stmt.where(Employee.end_date.is_(None) if status=='working' else Employee.end_date.is_not(None))
    is_director=has_role(actor,'director')
    if is_director:
        effective=(select(Salary.employee_id,(Salary.base_salary+Salary.meal_allowance+Salary.fuel_allowance+Salary.phone_allowance+Salary.other_allowance).label('salary'),
            func.row_number().over(partition_by=Salary.employee_id,order_by=Salary.effective_from.desc()).label('rn'))
            .where(Salary.replaced_at.is_(None),Salary.effective_from<=today()).subquery())
        stmt=stmt.add_columns(effective.c.salary).outerjoin(effective,(effective.c.employee_id==Employee.id)&(effective.c.rn==1))
    needle=fold(q);rows=[];day=today()
    with session.no_autoflush:
        for result in session.execute(stmt.order_by(Employee.code)):
            employee,account=result[:2];data=_public(employee,account)
            if needle and not tim.text_matches(q,' '.join((employee.code,account.full_name,account.username,account.phone))):continue
            last=min(employee.end_date or day,day)
            months=max(0,(last.year-employee.start_date.year)*12+last.month-employee.start_date.month-(last.day<employee.start_date.day))
            data['tenure']=f'{months//12} năm {months%12} tháng'
            if is_director:data['current_salary']=result[2]
            rows.append(data)
    return rows


def available_users(session,actor,q=''):
    manager(actor);needle=fold(q)
    rows=session.execute(select(User.id,User.username,User.full_name,User.phone).where(~select(Employee.id).where(Employee.user_id==User.id).exists()).order_by(User.full_name,User.id)).mappings()
    return [dict(r) for r in rows if not needle or tim.text_matches(q,' '.join((r['full_name'],r['username'],r['phone'])))][:20]
