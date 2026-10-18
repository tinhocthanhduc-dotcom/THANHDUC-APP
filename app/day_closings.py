"""T4: chốt ngày và khóa advisory theo ngày, giữ tới khi transaction kết thúc."""
from datetime import UTC,date,datetime,timedelta

from sqlalchemy import case,func,select,text

from . import audit
from .errors import BusinessError
from .finance_catalogs import check_reader,check_writer,check_version
from .models import CashDoc,DayClosing,FundLedger
from .security import has_role

LOCK_NAMESPACE=0x54444341


def _today():
    from .cash_docs import today
    return today()


def snapshot(row):
    return {c.name:(v.isoformat() if isinstance(v:=getattr(row,c.name),(date,datetime)) else v)
            for c in DayClosing.__table__.columns}


def lock_days(session,days):
    for day in sorted(set(days)):
        session.execute(text('select pg_advisory_xact_lock(:namespace,:day)'),{'namespace':LOCK_NAMESPACE,'day':day.toordinal()})


def check_open(session,day):
    lock_days(session,[day])
    if session.scalar(select(DayClosing.status).where(DayClosing.day==day))=='DA_CHOT':
        raise BusinessError('DAY_CLOSED',f'Ngày {day.strftime("%d/%m/%Y")} đã chốt sổ.',{'day':day.isoformat()},status=409)


def check_days(session,days):
    for day in sorted(set(days)):
        check_open(session,day)


def is_closed(session,day):
    return session.scalar(select(DayClosing.status).where(DayClosing.day==day))=='DA_CHOT'


def _version(row,version):
    if not isinstance(version,int) or isinstance(version,bool) or version<0:
        raise BusinessError('VERSION_REQUIRED','Phải gửi phiên bản ngày, hãy tải lại.')
    current=row.version if row is not None else 0
    if version!=current:
        raise BusinessError('VERSION_CONFLICT','Ngày vừa được người khác cập nhật, hãy tải lại.',{'current':current},status=409)


def close(session,user,day,version):
    from .cash_docs import _transaction
    check_writer(user)
    with _transaction(session):
        if day>_today():
            raise BusinessError('FUTURE_DAY','Không được chốt ngày trong tương lai.')
        lock_days(session,[day])
        row=session.scalar(select(DayClosing).where(DayClosing.day==day).with_for_update().execution_options(populate_existing=True))
        _version(row,version)
        if row and row.status=='DA_CHOT':
            raise BusinessError('DAY_CLOSED','Ngày này đã chốt sổ.',{'day':day.isoformat()},status=409)
        drafts=list(session.scalars(select(CashDoc.number).where(CashDoc.doc_date==day,CashDoc.status=='NHAP').order_by(CashDoc.id)))
        if drafts:
            raise BusinessError('DRAFTS_OPEN','Còn phiếu nháp trong ngày: '+', '.join(drafts),{'numbers':drafts},status=409)
        before=snapshot(row) if row else None
        if row is None:
            row=DayClosing(day=day,status='DA_CHOT',closed_by=user.id,closed_at=datetime.now(UTC));session.add(row)
        else:
            row.status,row.closed_by,row.closed_at='DA_CHOT',user.id,datetime.now(UTC)
            row.version+=1
        session.flush()
        audit.record(session,user,'day_closing.close','day_closing',day.isoformat(),before,snapshot(row))
    return row


def unlock(session,user,day,reason,version):
    from .cash_docs import _transaction
    if not has_role(user,'director'):
        raise BusinessError('FORBIDDEN','Chỉ giám đốc được mở khóa ngày đã chốt.',status=403)
    with _transaction(session):
        reason=(reason or '').strip()
        if not reason or len(reason)>2000:
            raise BusinessError('REASON_REQUIRED','Mở khóa phải nhập lý do (tối đa 2000 ký tự).')
        lock_days(session,[day])
        row=session.scalar(select(DayClosing).where(DayClosing.day==day).with_for_update().execution_options(populate_existing=True))
        if row is None:
            raise BusinessError('NOT_FOUND','Ngày này chưa được chốt sổ.',status=404)
        _version(row,version)
        if row.status!='DA_CHOT':
            raise BusinessError('BAD_STATUS','Ngày này đã mở khóa.',status=409)
        before=snapshot(row)
        row.status,row.unlocked_by,row.unlocked_at,row.unlock_reason='DA_MO',user.id,datetime.now(UTC),reason
        row.version+=1;session.flush()
        audit.record(session,user,'day_closing.unlock','day_closing',day.isoformat(),before,snapshot(row),reason)
    return row


def recent(session,user):
    check_reader(user)
    end=_today();start=end-timedelta(days=29)
    closed={r.day:r for r in session.scalars(select(DayClosing).where(DayClosing.day.between(start,end)))}
    drafts=dict(session.execute(select(CashDoc.doc_date,func.count()).where(CashDoc.doc_date.between(start,end),
                    CashDoc.status=='NHAP').group_by(CashDoc.doc_date)).all())
    totals={r.day:r for r in session.execute(select(FundLedger.entry_date.label('day'),
        func.sum(case((CashDoc.kind=='PT',FundLedger.amount),else_=0)).label('income'),
        func.sum(case((CashDoc.kind=='PC',-FundLedger.amount),else_=0)).label('expense'))
        .join(CashDoc,CashDoc.id==FundLedger.doc_id).where(FundLedger.entry_date.between(start,end),CashDoc.is_opening.is_(False))
        .group_by(FundLedger.entry_date))}
    return [dict(day=day,status=closed[day].status if day in closed else 'CHUA_CHOT',version=closed[day].version if day in closed else 0,
                 income=int(totals[day].income) if day in totals else 0,expense=int(totals[day].expense) if day in totals else 0,
                 drafts=drafts.get(day,0)) for day in [end-timedelta(days=n) for n in range(30)]]
