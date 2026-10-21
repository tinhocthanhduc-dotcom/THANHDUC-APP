"""NS3: monthly KPI projections. Sources/aggregates are shared by board and drill-down.

Only targets write data. Business documents, stock and financial ledgers are never changed.
All timestamps and date-only receipts use Vietnam calendar boundaries.
"""
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql

from . import audit, purchase_reports
from .attendance import month_day, month_end
from .config import get_settings
from .errors import BusinessError
from .models import Employee, KpiTarget, User
from .security import has_role
from .stock_docs import _transaction

TZ = ZoneInfo('Asia/Ho_Chi_Minh')
GROUPS = {'technician':'Thợ','sales':'Kinh doanh','dispatcher':'Điều phối','warehouse':'Kho','finance':'Kế toán'}


def _metric(key, group, label, unit, higher, explanation, *, mode='sum', shared=False):
    return dict(key=key, group=group, label=label, unit=unit, higher=higher, shared=shared,
                explanation=explanation, mode=mode, calculator=_aggregate)


def _aggregate(source):
    # SQL aggregation, never per-person queries or Python sums over documents.
    return ("select key, owner, case when key = any(:medians) then "
            "cast(percentile_cont(0.5) within group (order by value) as numeric) "
            "when key = any(:ratios) then 100 * sum(value) / nullif(sum(denominator),0) "
            "else sum(value) end as actual from sources group by key, owner")


METRICS = (
    _metric('tho_hoan_thanh','technician','Phiếu hoàn thành','lần',True,'Đếm phiếu khác nhau có bước Hoàn thành do bạn thực hiện trong tháng.'),
    _metric('tho_dung_hen','technician','Đến đúng hẹn','%',True,'Phiếu hoàn thành có hẹn và lượt đến: lượt đầu không trễ quá 30 phút.',mode='ratio'),
    _metric('tho_nhan_viec','technician','Thời gian nhận việc','phút',False,'Trung vị từ mỗi lần giao trong tháng đến bước nhận kế tiếp của chính thợ, trước lần giao mới.',mode='median'),
    _metric('tho_bao_hanh_lai','technician','Bảo hành lại','lần',False,'Phiếu bảo hành tạo trong tháng; quy về người hoàn thành gần nhất của phiếu gốc trước lúc tạo bảo hành.'),
    _metric('tho_tien_thu','technician','Tiền đã thu','đồng',True,'Báo thu đã xác nhận theo ngày thu, gồm VAT; quy về người hoàn thành gần nhất tính đến cuối kỳ.'),
    _metric('tho_bao_hu','technician','Hàng báo hư','lần',False,'Số lượng thực nhận là Hư của yêu cầu trả kho do bạn lập, được kho nhận trong tháng.'),
    _metric('kd_doanh_thu','sales','Doanh thu chưa VAT','đồng',True,'Các lần giao còn hiệu lực trong tháng của đơn bạn tạo, trừ hàng trả ghi nhận trong tháng.'),
    _metric('kd_lai_gop','sales','Lãi gộp','đồng',True,'Doanh thu trừ giá vốn đúng các lần giao và hàng trả, dùng nguồn Báo cáo lãi gộp. Chỉ giám đốc xem.'),
    _metric('kd_bao_gia_thanh_don','sales','Báo giá thành đơn','%',True,'Báo giá gửi lần đầu trong tháng, còn hiệu lực: đã chấp nhận hoặc có đơn không hủy.',mode='ratio'),
    _metric('kd_tien_thu','sales','Tiền đã thu','đồng',True,'Báo thu đã xác nhận của đơn bạn tạo, theo ngày thu, gồm VAT.'),
    _metric('dp_giao_viec','dispatcher','Thời gian giao việc','phút',False,'Trung vị từ tạo phiếu trong tháng đến bước giao đầu tiên do bạn thực hiện.',mode='median'),
    _metric('dp_tre_hen','dispatcher','Phiếu trễ hẹn','lần',False,'Chung nhóm: phiếu đến hạn trong kỳ, hoàn thành trễ hoặc chưa hoàn thành tại cuối kỳ.',shared=True),
    _metric('dp_ton_dong','dispatcher','Phiếu tồn đọng','lần',False,'Chung nhóm: phiếu còn mở tại cuối kỳ, đã tạo quá 3 ngày.',shared=True),
    _metric('kho_cap_linh_kien','warehouse','Thời gian cấp linh kiện','giờ',False,'Trung vị từ tạo yêu cầu đến cấp xong trong tháng, theo người cấp.',mode='median'),
    _metric('kho_chenh_kiem_ke','warehouse','Chênh lệch kiểm kê','đồng',False,'Chung nhóm: tổng tuyệt đối giá trị các dòng sổ kho của phiếu KK đã ghi trong tháng.',shared=True),
    _metric('kho_cho_xu_ly_qua_han','warehouse','Hồ sơ chờ xử lý quá hạn','lần',False,'Chung nhóm: hồ sơ còn chờ tại cuối kỳ, tuổi từ ngày nhập vượt ngưỡng Chờ xử lý đang cấu hình.',shared=True),
    _metric('kt_xac_nhan_trong_ngay','finance','Xử lý báo thu trong ngày','%',True,'Báo thu bạn xác nhận hoặc từ chối trong tháng, cùng ngày Việt Nam với ngày báo.',mode='ratio'),
    _metric('kt_chot_so_dung_ngay','finance','Chốt sổ đúng ngày','%',True,'Chung nhóm: ngày làm việc theo lịch NS2 đã chốt trước 10:00 hôm sau; không tính ngày lễ.',mode='ratio',shared=True),
)
BY_KEY = {m['key']:m for m in METRICS}


def _now(): return datetime.now(UTC)


def period(month):
    try:
        month = date.fromisoformat(month) if isinstance(month,str) else month
        if type(month) is not date: raise ValueError()
        month = month_day(month)
        end = month_end(month)
        lo = datetime.combine(month,time.min,TZ)
        hi = min(datetime.combine(end+timedelta(days=1),time.min,TZ),_now())
        return month, end, lo, hi
    except (ValueError,TypeError,OverflowError):
        raise BusinessError('BAD_MONTH','Tháng không hợp lệ.')


def previous(month): return (month_day(month)-timedelta(days=1)).replace(day=1)


def director(user):
    if not user.active or not has_role(user,'director'):
        raise BusinessError('FORBIDDEN','Chỉ giám đốc được xem KPI tổng và đặt mục tiêu.',status=403)


def people(session,month,user_ids=None):
    month,end,_,_ = period(month)
    q = select(User.id, User.full_name, User.roles, Employee.code).join(Employee,Employee.user_id==User.id).where(
        Employee.start_date<=end, (Employee.end_date.is_(None)) | (Employee.end_date>=month))
    if user_ids is not None: q=q.where(User.id.in_(user_ids))
    return [dict(r) for r in session.execute(q.order_by(Employee.code)).mappings()
            if 'director' not in r.roles and set(r.roles)&GROUPS.keys()]


def _sources(month, keys=None):
    month,end,lo,hi=period(month)
    params=dict(lo=lo,hi=hi,first=month,last=min(end,hi.astimezone(TZ).date()),
                overdue=timedelta(days=get_settings().pending_alert_days),workdays=list(get_settings().work_days))
    sources={}
    def add(key, owner, number, stamp, value, tail, denominator='0'):
        sources[key]=(f"select '{key}'::text as key, ({owner})::integer as owner, ({number})::text as number, "
                      f"({stamp})::timestamptz as day, ({value})::numeric as value, ({denominator})::numeric as denominator {tail}")
    # Distinct ticket/actor pairs avoid counting reopen/completion cycles twice.
    completed="""select distinct s.ticket_id,s.actor_id from service_steps s join service_tickets t on t.id=s.ticket_id
        where s.to_status='HOAN_THANH' and s.at>=:lo and s.at<:hi and t.status<>'DA_HUY'"""
    completion_from=f'from ({completed}) c join service_tickets t on t.id=c.ticket_id'
    add('tho_hoan_thanh','c.actor_id','t.number','t.created_at','1',completion_from)
    add('tho_dung_hen','c.actor_id','t.number','v.first_at',
        "case when v.first_at<=t.appointment_at+interval '30 minutes' then 1 else 0 end",
        completion_from+" join (select ticket_id,min(started_at) first_at from service_visits where started_at<:hi group by ticket_id) v on v.ticket_id=t.id where t.appointment_at is not null",'1')
    add('tho_nhan_viec','s.assignee_id','t.number','s.at','extract(epoch from (a.at-s.at))/60',"""from service_steps s join service_tickets t on t.id=s.ticket_id
        join lateral (select n.at from service_steps n where n.ticket_id=s.ticket_id and n.to_status='DA_NHAN'
          and n.actor_id=s.assignee_id and (n.at,n.id)>(s.at,s.id) and n.at<:hi
          and not exists (select 1 from service_steps x where x.ticket_id=s.ticket_id and x.to_status='DA_GIAO'
            and (x.at,x.id)>(s.at,s.id) and (x.at,x.id)<(n.at,n.id)) order by n.at,n.id limit 1) a on true
        where s.to_status='DA_GIAO' and s.at>=:lo and s.at<:hi and t.status<>'DA_HUY'""")
    last_owner="""join lateral (select s.actor_id from service_steps s where s.ticket_id=t.id and s.to_status='HOAN_THANH'
        and s.at < {before} order by s.at desc,s.id desc limit 1) c on true"""
    add('tho_bao_hanh_lai','c.actor_id','w.number','w.created_at','1',
        "from service_tickets w join service_tickets t on t.id=w.warranty_of_id "+last_owner.format(before='w.created_at')+
        " where w.created_at>=:lo and w.created_at<:hi and w.status<>'DA_HUY' and t.status<>'DA_HUY'")
    receipt_window="r.status='DA_XAC_NHAN' and r.received_at between :first and :last and :hi>:lo"
    add('tho_tien_thu','c.actor_id','r.number',"r.received_at::timestamp at time zone 'Asia/Ho_Chi_Minh'",'r.amount',
        "from sales_receipts r join service_tickets t on t.id=r.ticket_id "+last_owner.format(before=':hi')+
        " where "+receipt_window+" and t.status<>'DA_HUY'")
    add('tho_bao_hu','r.technician_id','r.number','r.received_at','sum(coalesce(l.received_qty,l.qty))',
        """from technician_returns r join technician_return_lines l on l.return_id=r.id
        where r.status='DA_NHAN' and coalesce(l.received_condition,l.condition)='HU_HONG'
        and r.received_at>=:lo and r.received_at<:hi group by r.id""")
    # Reuse the exact sales/returns and ledger-cost projection used by gross_margin.
    docs,cost=purchase_reports._margin_sources(month,min(end,params['last']))
    sql=lambda q:str(q.compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True}))
    documents=sql(select(docs)); costs=sql(select(cost.c.kind,cost.c.id,func.sum(cost.c.cost).label('cost')).group_by(cost.c.kind,cost.c.id))
    margin_from=f"""from ({documents}) d
        left join sales_deliveries g on d.kind='GH' and g.id=d.id
        left join sales_returns r on d.kind='TH' and r.id=d.id
        join sales_orders o on o.id=coalesce(g.order_id,r.order_id)
        where d.kind in ('GH','TH') and o.status<>'DA_HUY' and coalesce(g.delivered_at,r.returned_at)<:hi and :hi>:lo"""
    add('kd_doanh_thu','o.created_by','d.number',"d.day::timestamp at time zone 'Asia/Ho_Chi_Minh'",'d.revenue',margin_from)
    add('kd_lai_gop','o.created_by','d.number',"d.day::timestamp at time zone 'Asia/Ho_Chi_Minh'",'d.revenue-coalesce(c.cost,0)',
        margin_from.replace("where d.kind",f"left join ({costs}) c on c.kind=d.kind and c.id=d.id where d.kind"))
    add('kd_bao_gia_thanh_don','q.created_by','q.number','a.sent_at',
        "case when q.status='CHAP_NHAN' or exists(select 1 from sales_orders o where o.quote_id=q.id and o.status<>'DA_HUY') then 1 else 0 end",
        """from quotes q join (select entity_id,min(at) sent_at from audit_log where entity='quote' and action='quote.send' group by entity_id) a on a.entity_id=q.id::text
        where q.status not in ('NHAP','DA_HUY') and a.sent_at>=:lo and a.sent_at<:hi""",'1')
    add('kd_tien_thu','o.created_by','r.number',"r.received_at::timestamp at time zone 'Asia/Ho_Chi_Minh'",'r.amount',
        "from sales_receipts r join sales_orders o on o.id=r.order_id where "+receipt_window+" and o.status<>'DA_HUY'")
    add('dp_giao_viec','s.actor_id','t.number','s.at','extract(epoch from (s.at-t.created_at))/60',"""from service_tickets t
        join lateral (select actor_id,at from service_steps where ticket_id=t.id and to_status='DA_GIAO' and at<:hi order by at,id limit 1) s on true
        where t.created_at>=:lo and t.created_at<:hi and t.status<>'DA_HUY'""")
    state="""from service_tickets t left join lateral (select to_status from service_steps where ticket_id=t.id and at<:hi order by at desc,id desc limit 1) s on true
        left join lateral (select max(at) at from service_steps where ticket_id=t.id and to_status='HOAN_THANH' and at<:hi) c on true"""
    add('dp_tre_hen','null','t.number','t.due_at','1',state+" where t.status<>'DA_HUY' and t.due_at>=:lo and t.due_at<:hi and (coalesce(s.to_status,t.status) not in ('HOAN_THANH','DA_DONG') or c.at is null or c.at>t.due_at)")
    add('dp_ton_dong','null','t.number','t.created_at','1',state+" where t.status<>'DA_HUY' and t.created_at<:hi-interval '3 days' and coalesce(s.to_status,t.status) not in ('HOAN_THANH','DA_DONG','DA_HUY','KHONG_LAM_DUOC','KHACH_HEN_SAU')")
    add('kho_cap_linh_kien','p.decided_by','p.number','p.decided_at','extract(epoch from (p.decided_at-p.created_at))/3600',
        "from part_requests p where p.status='DA_CAP' and p.decided_at>=:lo and p.decided_at<:hi")
    add('kho_chenh_kiem_ke','null','d.number','d.posted_at','sum(abs(l.amount))',
        "from stock_docs d join stock_ledger l on l.doc_id=d.id where d.kind='KK' and d.status='DA_GHI' and not l.is_reversal and d.posted_at>=:lo and d.posted_at<:hi group by d.id")
    # Reconstruct at cutoff from immutable before/after audit snapshots. An initial
    # future create (before=NULL) is excluded; migrated dossiers without audit use
    # their baseline row. Split children retain entered_at but start at create audit.
    add('kho_cho_xu_ly_qua_han','null','p.number',"(h.state->>'entered_at')::timestamptz",'1',"""from pending_items p
        left join lateral (select a.after from audit_log a where a.entity='pending_item' and a.entity_id=p.id::text and a.at<:hi order by a.at desc,a.id desc limit 1) past on true
        left join lateral (select a.id,a.before from audit_log a where a.entity='pending_item' and a.entity_id=p.id::text and a.at>=:hi order by a.at,a.id limit 1) future on true
        cross join lateral (select coalesce(past.after,future.before,case when future.id is null then jsonb_build_object('status',p.status,'qty_left',p.qty_left,'entered_at',p.entered_at) end) state) h
        where h.state->>'status'='DANG_CHO' and (h.state->>'qty_left')::numeric>0 and (h.state->>'entered_at')::timestamptz<:hi-:overdue""")
    add('kt_xac_nhan_trong_ngay','r.decided_by','r.number','r.decided_at',
        "case when (r.decided_at at time zone 'Asia/Ho_Chi_Minh')::date=(r.reported_at at time zone 'Asia/Ho_Chi_Minh')::date then 1 else 0 end",
        "from sales_receipts r where r.status in ('DA_XAC_NHAN','TU_CHOI') and r.decided_at>=:lo and r.decided_at<:hi",'1')
    add('kt_chot_so_dung_ngay','null',"'Chốt sổ '||d.day::date", "d.day at time zone 'Asia/Ho_Chi_Minh'",
        "case when c.status='DA_CHOT' and c.closed_at<((d.day::date+1)+time '10:00') at time zone 'Asia/Ho_Chi_Minh' and c.closed_at<:hi then 1 else 0 end",
        """from generate_series(cast(:first as timestamp),cast(:last as timestamp),interval '1 day') d(day)
        left join day_closings c on c.day=d.day::date
        where (extract(isodow from d.day)::integer-1)=any(:workdays) and not exists(select 1 from attendance_holidays h where h.day=d.day::date) and :hi>:lo""",'1')
    keys=keys or BY_KEY.keys()
    return ' union all '.join('('+sources[k]+')' for k in keys),params


def _compute(session,month,user_ids=None,*,sensitive=True):
    staff=people(session,month,user_ids)
    result={p['id']:{m['key']:None if m['mode']!='sum' else Decimal(0)
                      for m in METRICS if m['group'] in p['roles'] and (sensitive or m['key']!='kd_lai_gop')} for p in staff}
    keys={k for row in result.values() for k in row}
    if not keys or period(month)[3]<=period(month)[2]:return result
    source,params=_sources(month,keys)
    params.update(medians=[k for k in keys if BY_KEY[k]['mode']=='median'],ratios=[k for k in keys if BY_KEY[k]['mode']=='ratio'])
    rows=session.execute(text('with sources as ('+source+') '+_aggregate(source)),params).mappings()
    shared={}
    for r in rows:
        value=Decimal(str(r.actual)) if r.actual is not None else None
        if BY_KEY[r.key]['shared']: shared[r.key]=value
        elif r.owner in result and r.key in result[r.owner]:result[r.owner][r.key]=value
    for row in result.values():
        for key in row:
            if key in shared:row[key]=shared[key]
    return result


def compute(session,month,user_ids=None):return _compute(session,month,user_ids)


def my_kpi(session,user,month):
    if not user.active:raise BusinessError('FORBIDDEN',status=403)
    # Cost-sensitive metric never enters the query or the returned context.
    return _compute(session,month,[user.id],sensitive=has_role(user,'director')).get(user.id,{})


def board(session,user,month):
    director(user)
    return compute(session,month)


def detail(session,user,month,user_id,metric_key):
    if not user.active or (not has_role(user,'director') and user.id!=user_id) or (metric_key=='kd_lai_gop' and not has_role(user,'director')):
        raise BusinessError('FORBIDDEN','Bạn chỉ được xem chứng từ KPI của mình.',status=403)
    metric=BY_KEY.get(metric_key)
    staff=people(session,month,[user_id])
    if not metric or not staff or metric['group'] not in staff[0]['roles']:raise BusinessError('NOT_FOUND','Không có chỉ số này.',status=404)
    if period(month)[3]<=period(month)[2]:return []
    source,params=_sources(month,[metric_key]);params['owner']=user_id
    return list(session.execute(text('select number,day,value,denominator from ('+source+") s where owner=:owner or owner is null order by day,number"),params).mappings())


def targets(session,month):
    month=period(month)[0]
    return {(r.user_id,r.metric_key):r for r in session.scalars(select(KpiTarget).where(KpiTarget.month==month).execution_options(populate_existing=True))}


def target_for(rows,user_id,key):
    row=rows.get((user_id,key)) or rows.get((None,key))
    return row.target if row else None


def achievement(actual,target,key):
    if actual is None or target is None:return None
    actual,target=Decimal(str(actual)),Decimal(str(target))
    if BY_KEY[key]['higher']:
        if target==0:return Decimal(100 if actual==0 else 200 if actual>0 else 0)
        return min(Decimal(200),actual/target*100)
    return Decimal(100) if actual<=target else target/actual*100


def save_targets(session,user,month,changes,*,copy=False):
    director(user);month=period(month)[0]
    with _transaction(session):
        # Serialize absent-row inserts too; ordinary optimistic versions still reject stale forms.
        session.execute(text('select pg_advisory_xact_lock(836003,:month)'),dict(month=month.year*12+month.month))
        rows=targets(session,month)
        staff={p['id']:p for p in people(session,month)}
        if copy:
            changes=[dict(user_id=uid,metric_key=key,target=row.target,version=None)
                     for (uid,key),row in targets(session,previous(month)).items()
                     if (uid,key) not in rows and (uid is None or uid in staff and BY_KEY.get(key,{}).get('group') in staff[uid]['roles'])]
        seen=set()
        for change in changes:
            uid,key=change.get('user_id'),change.get('metric_key')
            if uid is not None:
                try:uid=int(uid)
                except (ValueError,TypeError):raise BusinessError('BAD_TARGET','Nhân viên không hợp lệ.')
            metric=BY_KEY.get(key)
            if not metric or uid is not None and (uid not in staff or metric['group'] not in staff[uid]['roles']):
                raise BusinessError('BAD_TARGET','Chỉ số hoặc vai trò nhân viên không hợp lệ.')
            if (uid,key) in seen:raise BusinessError('BAD_TARGET','Mục tiêu trùng lặp.')
            seen.add((uid,key))
            try:
                value=Decimal(str(change['target']))
                if not value.is_finite() or value<0 or value>=Decimal('1e16') or value.as_tuple().exponent < -4:raise ValueError()
            except (InvalidOperation,ValueError,TypeError,KeyError):raise BusinessError('BAD_TARGET','Mục tiêu phải là số không âm, tối đa 4 chữ số thập phân.')
            row=rows.get((uid,key));version=change.get('version')
            if (row and str(row.version)!=str(version)) or (not row and version not in (None,'')):
                raise BusinessError('VERSION_CONFLICT','Mục tiêu vừa thay đổi, hãy tải lại.',status=409)
            before={'target':str(row.target),'version':row.version} if row else None
            if row is None:
                row=KpiTarget(month=month,user_id=uid,metric_key=key);session.add(row)
            row.target=value;row.set_by=user.id;row.set_at=_now()
            # Identical saves still record the setter/time and advance the version.
            session.flush()
            audit.record(session,user,'kpi.target','kpi_target',str(row.id),before,
                dict(month=month.isoformat(),user_id=uid,metric_key=key,target=str(value),version=row.version))
    return targets(session,month)
