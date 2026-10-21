"""NS5: salary snapshots, consumed advances and atomic cash posting.

Salary, attendance, commission and cash calculations delegate to their existing cores.
"""
from datetime import UTC,date,datetime
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP

from sqlalchemy import func,select,text,or_

from . import attendance,cash_docs,cash_posting,commissions,doc_numbers,employees,finance_mappings
from .config import get_settings
from .errors import BusinessError
from .models import (AttendanceMonthLock,CommissionMonth,Employee,PayrollInput as Input,PayrollMonth as Month,
    PayrollLine as Line,SalaryAdvance as Advance,CashDoc,User)
from .schemas import CashDocIn
from .security import has_role
from .stock_docs import _transaction

TZ=attendance.TZ
FIELDS=('bonus','social_insurance','income_tax','other_deduction')
LABELS={'cong':'Công','base_pay':'Lương công','allowances':'Phụ cấp','commission':'Hoa hồng','bonus':'Thưởng',
        'advance':'Tạm ứng','social_insurance':'BHXH','income_tax':'Thuế TNCN','other_deduction':'Khấu trừ khác','gross':'Thu nhập','net':'Thực nhận'}
STATUSES={'CHO_DUYET':'Chờ duyệt','DA_DUYET':'Đã duyệt · chờ chi','TU_CHOI':'Từ chối','DA_HUY':'Đã hủy','DA_CHI':'Đã chi'}


def _now():return datetime.now(UTC)
def _month(month):return attendance.month_day(attendance._date(month))
def _round(value):return int(Decimal(value).quantize(Decimal(1),rounding=ROUND_HALF_UP))


def _access(user):
    if not user.active or 'admin' in user.roles and 'director' not in user.roles:
        raise BusinessError('FORBIDDEN','Admin không có quyền với lương.',status=403)


def director(user):
    _access(user)
    if not has_role(user,'director'):raise BusinessError('FORBIDDEN','Chỉ giám đốc được lập và duyệt bảng lương.',status=403)


def reader(user):
    _access(user)
    if not has_role(user,'finance'):raise BusinessError('FORBIDDEN','Chỉ kế toán và giám đốc được chi lương.',status=403)


def _guard(session,shared=False):
    session.execute(text('select pg_advisory_xact_lock'+('_shared' if shared else '')+'(836005,0)'))


def _open(session,month):
    if session.get(Month,month,populate_existing=True):raise BusinessError('DA_DUYET','Bảng lương đã duyệt, hãy điều chỉnh ở tháng sau.',status=409)


def _snapshot(row):
    return {c.key:v.isoformat() if isinstance(v,(date,datetime)) else str(v) if isinstance(v,Decimal) else v
            for c in row.__table__.columns for v in (getattr(row,c.key),)}


def _record(session,user,action,row,before=None):
    session.flush();ident=str(getattr(row,'id','')) or str(row.month)+':'+str(getattr(row,'employee_id',''))
    audit_data=_snapshot(row)
    from . import audit
    audit.record(session,user,'payroll.'+action,'payroll_'+('advance' if action.startswith('advance_') else 'month' if action=='approve' else 'input' if action=='input' else 'line'),ident,before,audit_data,getattr(row,'note',getattr(row,'reason','')))


def _money(value,zero=True):
    try:
        number=Decimal(str(value))
        if not number.is_finite() or number!=number.to_integral_value() or number<0 or number>10**12 or not zero and number==0:raise ValueError()
        return int(number)
    except (ValueError,TypeError,InvalidOperation):raise BusinessError('BAD_AMOUNT','Số tiền phải là số đồng không âm, tối đa 1.000 tỷ đồng.')


def _version(row,version):
    expected=row.version if row else 0
    if str(expected)!=str(version if version not in (None,'') else 0):raise BusinessError('VERSION_CONFLICT','Dữ liệu vừa thay đổi, hãy tải lại.',status=409)


def _people(session,month,lock=False):
    end=attendance.month_end(month)
    q=select(Employee,User.full_name).join(User,User.id==Employee.user_id).where(Employee.start_date<=end,
        (Employee.end_date.is_(None))|(Employee.end_date>=month)).order_by(Employee.id)
    if lock:q=q.with_for_update(of=Employee).execution_options(populate_existing=True)
    return list(session.execute(q))


def _advance_query(month):
    # The PC accounting date (chosen at payment) determines the deduction month.
    return select(Advance).join(CashDoc,CashDoc.id==Advance.cash_doc_id).where(Advance.status=='DA_CHI',
        Advance.payroll_line_id.is_(None),CashDoc.status=='DA_GHI',CashDoc.doc_date>=month,CashDoc.doc_date<=attendance.month_end(month))


def _stored(session,month=None,uid=None,ids=None):
    q=select(Line,Employee.code,User.full_name,CashDoc.number,CashDoc.doc_date).join(Month,Month.month==Line.month)\
        .join(Employee,Employee.id==Line.employee_id).join(User,User.id==Line.user_id).outerjoin(CashDoc,CashDoc.id==Line.cash_doc_id)
    if month is not None:q=q.where(Line.month==month)
    if uid is not None:q=q.where(Line.user_id==uid)
    if ids is not None:q=q.where(Line.id.in_(ids))
    return [dict({c.key:getattr(row,c.key) for c in row.__table__.columns},code=code,full_name=name,
                 cash_number=number,cash_day=day,warnings=[],input_version=None)
            for row,code,name,number,day in session.execute(q.order_by(Line.month.desc(),Employee.code))]


def _draft(session,month,people=None):
    people=_people(session,month) if people is None else people
    ids=[e.id for e,_ in people];uids=[e.user_id for e,_ in people]
    salaries=employees.salary_on(session,ids,attendance.month_end(month))
    summaries=attendance.month_summary(session,uids,month)
    commission=commissions.month_total(session,uids,month) if month>=attendance.month_day(get_settings().commission_start) else {uid:0 for uid in uids}
    inputs={r.employee_id:r for r in session.scalars(select(Input).where(Input.month==month))}
    amounts=select(Advance.employee_id,func.sum(Advance.amount)).where(Advance.id.in_(_advance_query(month).with_only_columns(Advance.id))).group_by(Advance.employee_id)
    advances=dict(session.execute(amounts).all());rows=[]
    for employee,name in people:
        salary=salaries.get(employee.id);summary=summaries[employee.user_id];entry=inputs.get(employee.id)
        values={k:getattr(entry,k) if entry else 0 for k in FIELDS};cong=summary['cong'];warnings=[]
        if not salary:warnings.append('Chưa có lương')
        missing=summary['missing_out_days']
        if missing:warnings.append(f'Thiếu giờ ra {missing} ngày')
        base_pay=_round(Decimal(salary.base_salary)*cong/salary.standard_days) if salary else 0
        allowances=_round(Decimal(salary.meal_allowance+salary.fuel_allowance)*cong/salary.standard_days)+salary.phone_allowance+salary.other_allowance if salary else 0
        comm=commission.get(employee.user_id,0);advance=int(advances.get(employee.id,0))
        gross=base_pay+allowances+comm+values['bonus'];net=_round(Decimal(gross)-advance-sum(values[k] for k in FIELDS if k!='bonus'))
        if net<0:warnings.append('Thực nhận âm')
        rows.append(dict(month=month,employee_id=employee.id,user_id=employee.user_id,full_name=name,code=employee.code,
            salary_version_id=salary.id if salary else None,base_salary=salary.base_salary if salary else 0,
            standard_days=salary.standard_days if salary else 0,cong=cong,base_pay=base_pay,allowances=allowances,
            commission=comm,advance=advance,gross=gross,net=net,**values,note=entry.note if entry else '',
            input_version=entry.version if entry else 0,warnings=warnings,paid_at=None,cash_doc_id=None))
    return rows


def draft(session,user,month):
    director(user);month=_month(month);_guard(session,True)
    return _stored(session,month) if session.get(Month,month,populate_existing=True) else _draft(session,month)


def save_inputs(session,user,month,rows):
    director(user);month=_month(month)
    with _transaction(session):
        _guard(session);_open(session,month);valid={e.id for e,_ in _people(session,month)}
        existing={r.employee_id:r for r in session.scalars(select(Input).where(Input.month==month).execution_options(populate_existing=True))};seen=set()
        for data in rows:
            try:ident=int(data['employee_id'])
            except (ValueError,TypeError,KeyError):raise BusinessError('BAD_EMPLOYEE','Nhân viên không hợp lệ.')
            if ident not in valid or ident in seen:raise BusinessError('BAD_EMPLOYEE','Nhân viên không thuộc tháng này hoặc bị trùng.')
            seen.add(ident);row=existing.get(ident);_version(row,data.get('version'));before=_snapshot(row) if row else None
            values={k:_money(data.get(k,getattr(row,k) if row else 0)) for k in FIELDS};note=str(data.get('note',row.note if row else '') or '').strip()
            if len(note)>4000:raise BusinessError('BAD_NOTE','Ghi chú tối đa 4.000 ký tự.')
            if row is None:row=Input(month=month,employee_id=ident);session.add(row)
            else:row.version+=1
            for key,value in values.items():setattr(row,key,value)
            row.note=note;row.updated_by=user.id;row.updated_at=_now();_record(session,user,'input',row,before)


def approve(session,user,month):
    director(user);month=_month(month)
    with _transaction(session):
        _guard(session);_open(session,month)
        if month>=_now().astimezone(TZ).date().replace(day=1):raise BusinessError('THANG_CHUA_QUA','Chỉ duyệt tháng đã qua.')
        attendance._month_locks(session,month,month,False)
        if not attendance.is_locked(session.get(AttendanceMonthLock,month,populate_existing=True)):
            raise BusinessError('CHAM_CONG_CHUA_KHOA','Bảng công tháng chưa khóa.')
        if month>=attendance.month_day(get_settings().commission_start) and not session.get(CommissionMonth,month):
            raise BusinessError('HOA_HONG_CHUA_KHOA','Hoa hồng tháng chưa khóa.')
        people=_people(session,month,True)
        advances=list(session.scalars(_advance_query(month).order_by(Advance.id).with_for_update(of=Advance).execution_options(populate_existing=True)))
        rows=_draft(session,month,people)
        if any(r['salary_version_id'] is None for r in rows):raise BusinessError('CHUA_CO_LUONG','Có nhân viên chưa có lương.')
        if any(r['net']<0 for r in rows):raise BusinessError('THUC_NHAN_AM','Có nhân viên thực nhận âm, hãy kiểm tra khấu trừ/tạm ứng.')
        saved={}
        for data in rows:
            values={c.key:data[c.key] for c in Line.__table__.columns if c.key in data}
            row=Line(**values);session.add(row);saved[row.employee_id]=row
        session.flush()
        for advance in advances:
            if advance.employee_id in saved:advance.payroll_line_id=saved[advance.employee_id].id
        session.flush()
        row=Month(month=month,approved_by=user.id,approved_at=_now(),total_net=sum(r['net'] for r in rows));session.add(row);_record(session,user,'approve',row)
    return row


def _date(value):return attendance._date(value)


def _post(session,user,amount,fund,day,partner,description,source,ident,category):
    if not fund:raise BusinessError('BAD_FUND','Hãy chọn quỹ chi.')
    doc=cash_docs.insert(session,user,CashDocIn(kind='PC',doc_date=day,fund_code=fund,category_code=category,
        partner_name=partner,description=description[:2000],amount=amount,source_type=source,source_id=str(ident)))
    cash_posting.post_in_tx(session,user,doc.id,doc.version)
    return doc


def pay(session,user,line_ids,fund_code,pay_date):
    reader(user);day=_date(pay_date)
    try:ids=sorted({int(i) for i in line_ids})
    except (TypeError,ValueError):raise BusinessError('BAD_LINES','Hãy chọn dòng cần chi.')
    if not ids or len(ids)!=len(line_ids):raise BusinessError('BAD_LINES','Hãy chọn các dòng khác nhau.')
    with _transaction(session):
        _guard(session)
        # Same counter/day order for every payroll payment, before per-person rows.
        from . import day_closings
        day_closings.lock_days(session,[day]);doc_numbers.lock_counter(session,'PC',day)
        rows=list(session.scalars(select(Line).join(Month,Month.month==Line.month).where(Line.id.in_(ids))
            .order_by(Line.id).with_for_update(of=Line).execution_options(populate_existing=True)))
        if len(rows)!=len(ids):raise BusinessError('NOT_FOUND','Không tìm thấy dòng lương đã duyệt.',status=404)
        if any(r.paid_at for r in rows):raise BusinessError('DA_CHI','Có dòng lương đã chi.',status=409)
        names={e.id:(e.code,name) for e,name in session.execute(select(Employee,User.full_name).join(User,User.id==Employee.user_id).where(Employee.id.in_([r.employee_id for r in rows])))}
        category=finance_mappings.require(session,'DANH_MUC_LUONG') if any(r.net>0 for r in rows) else None
        for row in rows:
            before=_snapshot(row);code,name=names[row.employee_id]
            if row.net:
                doc=_post(session,user,row.net,fund_code,day,name,f'Lương {row.month:%m/%Y} – {code}','PAYROLL_LINE',row.id,category);row.cash_doc_id=doc.id
            row.paid_at=_now();row.paid_by=user.id;_record(session,user,'pay',row,before)
    return rows


def _person(session,user,*,current=False,lock=False):
    _access(user);q=select(Employee).where(Employee.user_id==user.id)
    if lock:q=q.with_for_update().execution_options(populate_existing=True)
    employee=session.scalar(q)
    if not employee:raise BusinessError('NO_EMPLOYEE','Chưa có hồ sơ nhân sự.',status=404)
    if current:
        day=_now().astimezone(TZ).date()
        if employee.start_date>day or employee.end_date and employee.end_date<day:raise BusinessError('NOT_WORKING','Nhân viên không còn làm việc trong ngày gửi.',status=403)
    return employee


def request_advance(session,user,amount,reason):
    _access(user);amount=_money(amount,False);reason=str(reason or '').strip()
    if not reason or len(reason)>4000:raise BusinessError('REASON_REQUIRED','Lý do bắt buộc, tối đa 4.000 ký tự.')
    with _transaction(session):
        _guard(session);employee=_person(session,user,current=True,lock=True);day=_now().astimezone(TZ).date()
        salary=employees.salary_on(session,employee.id,day)
        if not salary:raise BusinessError('CHUA_CO_LUONG','Chưa có bản lương hiện tại.',status=409)
        outstanding=session.scalar(select(func.coalesce(func.sum(Advance.amount),0)).where(Advance.user_id==user.id,
            Advance.status.in_(('CHO_DUYET','DA_DUYET','DA_CHI')),Advance.payroll_line_id.is_(None)))
        if outstanding+amount>Decimal(salary.base_salary)*get_settings().advance_max_pct/100:
            raise BusinessError('VUOT_HAN_MUC_TAM_UNG','Tổng tạm ứng chưa trừ vượt hạn mức '+str(get_settings().advance_max_pct)+'% lương cơ bản.')
        row=Advance(number=doc_numbers.next_number(session,'TU',day),user_id=user.id,employee_id=employee.id,
            amount=amount,reason=reason,status='CHO_DUYET',requested_at=_now());session.add(row);_record(session,user,'advance_request',row)
    return row


def _advance(session,ident,version):
    row=session.scalar(select(Advance).where(Advance.id==ident).with_for_update().execution_options(populate_existing=True))
    if not row:raise BusinessError('NOT_FOUND','Không tìm thấy yêu cầu.',status=404)
    _version(row,version);return row


def cancel_advance(session,user,ident,version):
    _access(user)
    with _transaction(session):
        _guard(session);row=_advance(session,ident,version)
        if row.user_id!=user.id:raise BusinessError('FORBIDDEN','Chỉ người gửi được hủy.',status=403)
        if row.status!='CHO_DUYET':raise BusinessError('BAD_STATUS','Chỉ hủy yêu cầu còn chờ duyệt.',status=409)
        before=_snapshot(row);row.status='DA_HUY';row.decided_by=user.id;row.decided_at=_now();row.decision_note='Người gửi hủy'
        _record(session,user,'advance_cancel',row,before)
    return row


def decide_advance(session,user,ident,version,approve=True,note=''):
    director(user);note=str(note or '').strip()
    if not approve and not note:raise BusinessError('REASON_REQUIRED','Từ chối phải có ghi chú.')
    if len(note)>4000:raise BusinessError('BAD_NOTE','Ghi chú tối đa 4.000 ký tự.')
    with _transaction(session):
        _guard(session);row=_advance(session,ident,version)
        if row.status!='CHO_DUYET':raise BusinessError('BAD_STATUS','Yêu cầu không còn chờ duyệt.',status=409)
        before=_snapshot(row);row.status='DA_DUYET' if approve else 'TU_CHOI';row.decided_by=user.id;row.decided_at=_now();row.decision_note=note
        _record(session,user,'advance_decide',row,before)
    return row


def pay_advance(session,user,ident,fund_code,pay_date,version):
    reader(user);day=_date(pay_date)
    with _transaction(session):
        _guard(session)
        if session.get(Month,day.replace(day=1)):raise BusinessError('DA_DUYET','Bảng lương của ngày chi đã duyệt; chọn ngày chi trong tháng chưa duyệt.',status=409)
        from . import day_closings
        day_closings.lock_days(session,[day]);doc_numbers.lock_counter(session,'PC',day)
        row=_advance(session,ident,version)
        if row.status!='DA_DUYET':raise BusinessError('BAD_STATUS','Chỉ chi yêu cầu đã duyệt, chưa chi.',status=409)
        employee=session.get(Employee,row.employee_id)
        if employee.start_date>attendance.month_end(day) or employee.end_date and employee.end_date<day.replace(day=1):raise BusinessError('BAD_EMPLOYEE','Nhân viên không thuộc bảng lương của tháng chi.')
        before=_snapshot(row);name=session.scalar(select(User.full_name).where(User.id==row.user_id))
        category=finance_mappings.require(session,'DANH_MUC_LUONG')
        doc=_post(session,user,row.amount,fund_code,day,name,f'Tạm ứng {row.number}: {row.reason}','SALARY_ADVANCE',row.id,category)
        row.status='DA_CHI';row.cash_doc_id=doc.id;row.paid_by=user.id;row.paid_at=_now();_record(session,user,'advance_pay',row,before)
    return row


def approved(session,user,month=None):
    reader(user);_guard(session,True)
    return _stored(session,_month(month) if month else None)


def my_payslips(session,user):
    _person(session,user);return _stored(session,uid=user.id)


def payslip(session,user,line_id):
    _access(user)
    rows=_stored(session,uid=None if has_role(user,'finance') else user.id,ids=[line_id])
    if not rows:raise BusinessError('NOT_FOUND','Không tìm thấy phiếu lương.',status=404)
    return rows[0]


def advances(session,user):
    _access(user);q=select(Advance,User.full_name,CashDoc.number,CashDoc.doc_date).join(User,User.id==Advance.user_id).outerjoin(CashDoc,CashDoc.id==Advance.cash_doc_id)
    if not has_role(user,'finance'): _person(session,user)
    if not has_role(user,'director'):
        q=q.where(or_(Advance.user_id==user.id,Advance.status.in_(('DA_DUYET','DA_CHI')))) if has_role(user,'finance') else q.where(Advance.user_id==user.id)
    return [dict(item=r,name=name,cash_number=number,cash_day=day) for r,name,number,day in session.execute(q.order_by(Advance.requested_at.desc(),Advance.id.desc()))]


def pending_count(session,user):
    if 'admin' in user.roles and 'director' not in user.roles or not has_role(user,'director'):return 0
    return session.scalar(select(func.count()).select_from(Advance).where(Advance.status=='CHO_DUYET'))
