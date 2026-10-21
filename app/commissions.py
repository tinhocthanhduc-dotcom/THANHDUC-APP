"""NS4: cumulative commission projections and immutable monthly snapshots.

No money/stock posting. One aggregate source query handles all recipients;
only rules, adjustments and lock snapshots write data.
"""
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from . import audit, kpi, sales_receipts
from .config import get_settings
from .errors import BusinessError
from .models import (CommissionLine as Line, CommissionMonth as Month, CommissionRule as Rule,
                     SalesOrder, ServiceTicket, User, SERVICE_KINDS)
from .security import has_role
from .stock_docs import _transaction

TZ=kpi.TZ
TARGETS={'KINH_DOANH':'Kinh doanh','THO':'Thợ'}


def _now():return datetime.now(UTC)


def _period(month):
    month=kpi.period(month)[0]
    end=kpi.month_end(month)
    hi=min(datetime.combine(end+timedelta(days=1),time.min,TZ),_now())
    return month,end,hi


def _director(user):
    if not user.active or not has_role(user,'director'):raise BusinessError('FORBIDDEN','Chỉ giám đốc được thay đổi hoa hồng.',status=403)


def _write_lock(session):session.execute(text('select pg_advisory_xact_lock(836004,0)'))


def _open(session,month):
    if session.get(Month,month,populate_existing=True):raise BusinessError('DA_KHOA','Tháng hoa hồng đã khóa, hãy điều chỉnh ở tháng sau.',status=409)


def _snapshot(row):
    return {c.key:v.isoformat() if isinstance(v,(date,datetime)) else str(v) if isinstance(v,Decimal) else v
            for c in row.__table__.columns for v in (getattr(row,c.key),)}


def _record(session,user,action,row,before=None):
    session.flush();audit.record(session,user,'commission.'+action,'commission_'+('rule' if action=='rule' else 'month' if action=='lock' else 'line'),
        str(getattr(row,'id',getattr(row,'month',''))),before,_snapshot(row),getattr(row,'reason',getattr(row,'note','')))


def _rule_values(session,data):
    target=data.get('target');kind=data.get('service_kind') or None;uid=data.get('user_id') or None
    if target not in TARGETS or kind is not None and kind not in SERVICE_KINDS:raise BusinessError('BAD_RULE','Vai trò hoặc loại phiếu không hợp lệ.')
    try:
        uid=int(uid) if uid is not None else None
        pct=Decimal(str(data.get('pct',0)));fixed=Decimal(str(data.get('fixed_amount',0)))
        if not pct.is_finite() or not 0<=pct<=50 or pct!=pct.quantize(Decimal('.01')):raise ValueError()
        if not fixed.is_finite() or fixed<0 or fixed!=fixed.to_integral_value() or fixed>10**12:raise ValueError()
        effective=date.fromisoformat(str(data.get('effective_from')))
    except (ValueError,TypeError,InvalidOperation):raise BusinessError('BAD_RULE','% từ 0 đến 50 (tối đa 2 số lẻ), cố định là số đồng không âm; ngày hiệu lực hợp lệ.')
    if target=='KINH_DOANH' and (fixed or kind):raise BusinessError('BAD_RULE','Kinh doanh chỉ có % lãi gộp, không có cố định hoặc loại phiếu.')
    if uid is not None:
        account=session.get(User,uid)
        if account is None or not has_role(account,'sales' if target=='KINH_DOANH' else 'technician'):
            raise BusinessError('BAD_USER','Người được chọn không có vai trò tương ứng.')
    reason=str(data.get('reason') or '').strip()
    if not reason or len(reason)>4000:raise BusinessError('REASON_REQUIRED','Lý do bắt buộc, tối đa 4.000 ký tự.')
    return dict(target=target,user_id=uid,service_kind=kind,pct=pct,fixed_amount=int(fixed),effective_from=effective,reason=reason)


def add_rule(session,user,data,*,replaces_id=None):
    _director(user)
    try:
        with _transaction(session):
            _write_lock(session);values=_rule_values(session,data);old=None;before=None
            if replaces_id is not None:
                old=session.get(Rule,replaces_id,populate_existing=True)
                if not old:raise BusinessError('NOT_FOUND','Không tìm thấy quy tắc.',status=404)
                if old.replaced_at:raise BusinessError('RULE_REPLACED','Quy tắc đã được thay, hãy tải lại.',status=409)
                before=_snapshot(old);old.replaced_at=_now();session.flush()
            row=Rule(**values,created_by=user.id,created_at=_now(),replaces_id=old.id if old else None)
            session.add(row);_record(session,user,'rule',row,before)
        return row
    except IntegrityError as exc:
        raise BusinessError('RULE_EXISTS','Đã có quy tắc cùng phạm vi và ngày hiệu lực. Hãy dùng Thay bản này.',status=409) from exc


def replace_rule(session,user,rule_id,data):return add_rule(session,user,data,replaces_id=rule_id)


def rules(session,user):
    _director(user)
    return list(session.execute(select(Rule,User.full_name).outerjoin(User,User.id==Rule.user_id)
        .order_by(Rule.target,Rule.effective_from.desc(),Rule.id.desc())))


def add_adjustment(session,user,month,user_id,amount,note):
    _director(user);month=_period(month)[0];note=str(note or '').strip()
    if not note or len(note)>4000:raise BusinessError('REASON_REQUIRED','Lý do điều chỉnh bắt buộc, tối đa 4.000 ký tự.')
    try:
        uid=int(user_id);value=Decimal(str(amount))
        if not value.is_finite() or value!=value.to_integral_value() or not 0<abs(value)<=10**12:raise ValueError()
    except (ValueError,TypeError,InvalidOperation):raise BusinessError('BAD_AMOUNT','Số tiền điều chỉnh là số đồng khác 0, tối đa 1.000 tỷ đồng.')
    with _transaction(session):
        _write_lock(session);_open(session,month)
        if month<kpi.month_day(get_settings().commission_start):raise BusinessError('BEFORE_START','Tháng trước ngày bắt đầu hoa hồng.')
        account=session.get(User,uid)
        if not account or not has_role(account,'sales','technician'):raise BusinessError('BAD_USER','Chọn kinh doanh hoặc thợ được điều chỉnh.')
        row=Line(month=month,user_id=uid,source='DIEU_CHINH',base=0,pct=0,fixed_amount=0,paid_ratio=0,
                 entitled=int(value),previous=0,amount=int(value),note='Điều chỉnh: '+note,created_by=user.id)
        session.add(row);_record(session,user,'adjust',row)
    return row


def _sql(query):return str(query.compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True}))


def _draft_query(month):
    month,end,hi=_period(month);start=get_settings().commission_start
    margins=_sql(kpi.sales_margin_sources(start,end,as_of=hi))
    orders_paid=_sql(select(sales_receipts.totals(as_of=hi)))
    tickets_paid=_sql(select(sales_receipts.totals(ServiceTicket,as_of=hi)))
    # First completion fixes the source's single beneficiary and 90-day clock.
    # Never use the current assignee, which can change on a warranty/reopen.
    query="""
    with margin as (select order_id,sum(profit) base,sum(receivable) receivable,
         bool_or(kind='TH' and day>=:month) returned from (__MARGINS__) d group by order_id),
    prior as (select l.source,l.order_id,l.ticket_id,sum(l.amount) previous from commission_lines l
         join commission_months m on m.month=l.month where l.month<:month and l.source<>'DIEU_CHINH'
         group by l.source,l.order_id,l.ticket_id),
    finished as (select distinct on (s.ticket_id) s.ticket_id,s.actor_id,s.at from service_steps s
         where s.to_status='HOAN_THANH' and s.at<:hi order by s.ticket_id,s.at,s.id),
    fees as (select ticket_id,sum(amount) base from service_lines where kind='PHI' group by ticket_id),
    sources as (
      select 'DON_BAN'::text source,'KINH_DOANH'::text target,o.created_by user_id,o.id order_id,null::bigint ticket_id,
        o.number,(o.created_at at time zone 'Asia/Ho_Chi_Minh')::date source_day,null::text service_kind,
        coalesce(m.base,0) base,coalesce(m.receivable,0) receivable,coalesce(paid.paid,0) paid,false free,
        (o.status='DA_HUY' or exists(select 1 from pending_dispositions x join sales_deliveries g on g.id=x.delivery_id
          where g.order_id=o.id and x.kind='THANH_LY' and x.decided_at<:hi)) excluded,
        case when m.returned then 'Hàng trả' when o.status='DA_HUY' then 'Đơn đã hủy' else '' end note,
        coalesce(p.previous,0) previous
      from sales_orders o left join margin m on m.order_id=o.id
      left join (__ORDER_PAID__) paid on paid.parent_id=o.id
      left join prior p on p.order_id=o.id and p.source='DON_BAN'
      where o.created_at>=:start_at and o.created_at<:hi and (m.order_id is not null or p.order_id is not null)
      union all
      select 'PHIEU_DV','THO',f.actor_id,null,t.id,t.number,(f.at at time zone 'Asia/Ho_Chi_Minh')::date,t.kind,
        coalesce(fee.base,0),coalesce(t.total,0),coalesce(paid.paid,0),(t.billing='MIEN_PHI' or (t.kind='BAO_HANH' and t.warranty_basis='PHIEU_TRUOC')),
        t.status='DA_HUY' or w.number is not null,
        case when w.number is not null then 'Bảo hành lại '||w.number when t.status='DA_HUY' then 'Phiếu đã hủy' else '' end,
        coalesce(p.previous,0)
      from service_tickets t join finished f on f.ticket_id=t.id
      left join fees fee on fee.ticket_id=t.id
      left join (__TICKET_PAID__) paid on paid.parent_id=t.id
      left join prior p on p.ticket_id=t.id and p.source='PHIEU_DV'
      left join lateral (select w.number from service_tickets w where w.warranty_of_id=t.id and w.kind='BAO_HANH'
        and w.warranty_basis='PHIEU_TRUOC' and w.status<>'DA_HUY' and w.created_at>=f.at
        and w.created_at<=f.at+interval '90 days' and w.created_at<:hi order by w.created_at,w.id limit 1) w on true
      where t.created_at>=:start_at and t.created_at<:hi
    ), selected as (
      select s.*,r.id rule_id,coalesce(r.pct,0) pct,coalesce(r.fixed_amount,0) fixed_amount,
        case when s.free or s.receivable=0 then 1::numeric else least(1::numeric,greatest(0::numeric,s.paid)/s.receivable) end paid_ratio
      from sources s left join lateral (select r.* from commission_rules r where r.target=s.target and r.replaced_at is null
        and (r.user_id is null or r.user_id=s.user_id) and (r.service_kind is null or r.service_kind=s.service_kind)
        and r.effective_from<=s.source_day
        order by (r.user_id is not null) desc,(r.service_kind is not null) desc,r.effective_from desc,r.id desc limit 1) r on true
    ), earned as (
      select s.*,case when excluded then 0 else round((fixed_amount+pct/100*
        case when source='DON_BAN' then greatest(0,base) when free then 0 else base end)*paid_ratio) end::bigint entitled
      from selected s
    )
    select cast(:month as date) as month,user_id,source,order_id,ticket_id,rule_id,base,pct,fixed_amount,round(paid_ratio,6) paid_ratio,
      entitled,previous,(entitled-previous)::bigint amount,
      case when rule_id is null and not excluded then 'Chưa có quy tắc' when note<>'' then note when excluded and source='DON_BAN' then 'Đơn có hàng thanh lý' when entitled<previous then 'Chênh lệch quy tắc / tiền đã thu' when free and source='PHIEU_DV' then 'Miễn phí: chỉ tiền cố định' else '' end note,
      (rule_id is null and not excluded) missing_rule,number,source_day
    from earned where entitled<>previous or (rule_id is null and not excluded)
    """
    query=query.replace('__MARGINS__',margins).replace('__ORDER_PAID__',orders_paid).replace('__TICKET_PAID__',tickets_paid)
    return query,dict(month=month,hi=hi,start_at=datetime.combine(start,time.min,TZ))


def _stored(session,month,user_ids=None,*,adjustments=False):
    q=select(Line,User.full_name,func.coalesce(SalesOrder.number,ServiceTicket.number,'Điều chỉnh').label('number')).join(User,User.id==Line.user_id)\
        .outerjoin(SalesOrder,SalesOrder.id==Line.order_id).outerjoin(ServiceTicket,ServiceTicket.id==Line.ticket_id).where(Line.month==month)
    if adjustments:q=q.where(Line.source=='DIEU_CHINH')
    if user_ids is not None:q=q.where(Line.user_id.in_(user_ids))
    return [dict({c.key:getattr(r,c.key) for c in r.__table__.columns},number=number,full_name=name,missing_rule=False) for r,name,number in session.execute(q.order_by(Line.user_id,Line.id))]


def draft(session,month,user_ids=None):
    # Keep header, projected rows and adjustments consistent with concurrent locks/rules.
    # Shared readers can run together; commission writes acquire the exclusive counterpart.
    session.execute(text('select pg_advisory_xact_lock_shared(836004,0)'))
    month=_period(month)[0]
    if session.get(Month,month,populate_existing=True):return _stored(session,month,user_ids)
    query,params=_draft_query(month)
    query='select d.*,u.full_name from ('+query+') d join users u on u.id=d.user_id'
    if user_ids is not None:
        if not user_ids:return []
        query+=' where d.user_id=any(:users)';params['users']=list(user_ids)
    rows=[dict(r) for r in session.execute(text(query.replace(':month::date','cast(:month as date)')),params).mappings()]
    return sorted(rows+_stored(session,month,user_ids,adjustments=True),key=lambda r:(r['user_id'],r['source'],r['number']))


def lock(session,user,month):
    _director(user);month=_period(month)[0]
    with _transaction(session):
        _write_lock(session);_open(session,month)
        if month>=_now().astimezone(TZ).date().replace(day=1):raise BusinessError('THANG_CHUA_QUA','Chỉ khóa tháng đã qua.')
        start=kpi.month_day(get_settings().commission_start)
        if month<start:raise BusinessError('BEFORE_START','Tháng trước ngày bắt đầu hoa hồng.')
        missing=session.scalar(text("select d::date from generate_series(cast(:start as timestamp),cast(:last as timestamp),interval '1 month') d where not exists(select 1 from commission_months m where m.month=d::date) order by d limit 1"),dict(start=start,last=kpi.previous(month)))
        if missing:raise BusinessError('THANG_TRUOC_CHUA_KHOA','Chưa khóa tháng '+missing.strftime('%m/%Y')+'.',dict(month=missing.isoformat()))
        rows=draft(session,month)
        for data in rows:
            if data['source']=='DIEU_CHINH' or not data['amount']:continue
            keys=('user_id','source','order_id','ticket_id','rule_id','base','pct','fixed_amount','paid_ratio','entitled','previous','amount','note')
            session.add(Line(month=month,created_by=user.id,created_at=_now(),**{k:data[k] for k in keys}))
        session.flush()
        total=session.scalar(select(func.coalesce(func.sum(Line.amount),0)).where(Line.month==month))
        row=Month(month=month,locked_by=user.id,locked_at=_now(),total=int(total));session.add(row);_record(session,user,'lock',row)
    return row


def month_total(session,user_id,month):
    month=_period(month)[0]
    return int(session.scalar(select(func.coalesce(func.sum(Line.amount),0)).join(Month,Month.month==Line.month)
                              .where(Line.user_id==user_id,Line.month==month)))


def _public(row):
    allowed=('source','order_id','ticket_id','number','paid_ratio','amount','note','user_id','full_name','missing_rule')
    if row['source']=='PHIEU_DV':allowed+=('base','fixed_amount','pct')
    return {k:row[k] for k in allowed if k in row}


def mine(session,user,month):
    if not user.active or not has_role(user,'sales','technician'):raise BusinessError('FORBIDDEN',status=403)
    return [_public(row) for row in draft(session,month,[user.id])]


def month_view(session,user,month,user_id=None):
    if not user.active or not has_role(user,'finance'):raise BusinessError('FORBIDDEN',status=403)
    month=_period(month)[0]
    if not has_role(user,'director') and session.get(Month,month) is None:
        raise BusinessError('THANG_CHUA_KHOA','Kế toán chỉ xem tháng đã khóa.',status=403)
    rows=draft(session,month,[user_id] if user_id is not None else None)
    return rows if has_role(user,'director') else [_public(row) for row in rows]
