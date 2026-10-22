"""D7: dashboard và báo cáo chỉ đọc; không thay đổi phiếu hay lịch sử."""
import csv
import io
from collections import defaultdict, deque
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from .. import service_tickets as svc, work_watch
from ..db import get_session
from ..errors import BusinessError
from ..models import AuditLog, ServiceStep, ServiceTicket, ServiceVisit, User
from ..security import has_role
from . import render, watch_response, web_user

router = APIRouter()
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _now():
    return datetime.now(UTC)


def dispatch_user(user: User = Depends(web_user)):
    if not has_role(user, "dispatcher"):
        raise BusinessError("FORBIDDEN", "Chỉ điều phối hoặc giám đốc được xem điều phối và báo cáo.", status=403)
    return user


def _delay(due_at, now):
    minutes = max(0, int((now - due_at).total_seconds()) // 60)
    hours, minutes = divmod(minutes, 60)
    return f"trễ {hours} giờ {minutes} phút"


@router.get("/dieu-phoi")
def dashboard(request: Request, view: str = "overdue", user: User = Depends(dispatch_user), session: Session = Depends(get_session)):
    now = _now()
    opened = ServiceTicket.status.in_(svc.OPEN)
    counts = session.execute(select(
        func.count().filter(opened),
        func.count().filter(opened, ServiceTicket.due_at < now),
        func.count().filter(ServiceTicket.status == "CHO_LINH_KIEN"),
        func.count().filter(ServiceTicket.status == "TAM_DUNG"),
    )).one()
    count_open, count_overdue, count_parts, count_paused = counts
    if view == "open":
        rows = list(session.scalars(select(ServiceTicket).where(opened)
                                   .order_by(ServiceTicket.due_at.asc().nulls_last(), ServiceTicket.id.desc())))
        title = "Đơn đang mở"
    elif view == 'deferred':
        rows = list(session.scalars(select(ServiceTicket).where(ServiceTicket.status=='KHACH_HEN_SAU').order_by(ServiceTicket.id.desc())))
        title = 'Khách hẹn lần sau'
    elif view == "paused":
        rows = svc.search(session, user, status="TAM_DUNG", limit=count_paused or 1)
        title = "Tạm dừng"
    elif view == "parts":
        rows = svc.search(session, user, status="CHO_LINH_KIEN", limit=count_parts or 1)
        title = "Chờ linh kiện"
    else:
        view, title = "overdue", "Đơn quá hạn"
        # Truyền limit theo tổng thực tế: không cắt danh sách ở giới hạn tìm kiếm mặc định.
        rows = svc.search(session, user, overdue=True, limit=count_overdue or 1)
    from .. import technician_deliveries
    from ..models import SalesOrder
    delivery_orders=list(session.scalars(select(SalesOrder).where(SalesOrder.fulfillment=='THO_GIAO',SalesOrder.status.in_(('DA_CHOT','GIAO_MOT_PHAN'))).order_by(SalesOrder.id)))
    delivery_labels={o.id:technician_deliveries.state(session,user,o)['label'] for o in delivery_orders}
    visit_counts = dict(session.execute(select(ServiceVisit.ticket_id, func.count()).where(
        ServiceVisit.ticket_id.in_([t.id for t in rows])).group_by(ServiceVisit.ticket_id)).all())
    return render(request, "dieu_phoi.jinja", visit_counts=visit_counts,delivery_orders=delivery_orders,delivery_labels=delivery_labels, rows=rows, view=view, title=title,
                  count_deferred=session.scalar(select(func.count()).select_from(ServiceTicket).where(ServiceTicket.status=='KHACH_HEN_SAU')),count_open=count_open, count_overdue=count_overdue, count_parts=count_parts, count_paused=count_paused,
                  users=dict(session.execute(select(User.id, User.full_name)).all()), open_counts=svc.open_counts(session),
                  now=now, delay=_delay, watch=dict(url='/dieu-phoi/moi', stamp=work_watch.dispatch(session)))


@router.get("/dieu-phoi/moi")
def dashboard_stamp(user: User = Depends(dispatch_user), session: Session = Depends(get_session)):
    return watch_response(work_watch.dispatch(session))


def _date_range(request):
    today = _now().astimezone(TZ).date()
    start_raw = request.query_params.get("from", (today - timedelta(days=6)).isoformat())
    end_raw = request.query_params.get("to", today.isoformat())
    try:
        start, end = date.fromisoformat(start_raw), date.fromisoformat(end_raw)
        if start > end:
            raise ValueError
        since = datetime.combine(start, time.min, TZ)
        until = datetime.combine(end + timedelta(days=1), time.min, TZ)
    except (ValueError, OverflowError):
        raise BusinessError("BAD_DATE_RANGE", "Khoảng ngày chưa hợp lệ; ngày bắt đầu phải trước hoặc bằng ngày kết thúc.")
    return start, end, since, until


def _history_lateness(session, ticket_ids, history):
    """Ghép bước với snapshot cùng transaction, không đoán lại hạn theo SLA hiện tại."""
    snapshots = defaultdict(deque)
    for log in session.scalars(select(AuditLog).where(
        AuditLog.entity == "service_ticket", AuditLog.entity_id.in_([str(i) for i in ticket_ids]),
        AuditLog.action.startswith("service_ticket."),
    ).order_by(AuditLog.id)):
        before, after = log.before or {}, log.after or {}
        key = (log.entity_id, log.at, log.user_id, log.action.removeprefix("service_ticket."),
               before.get("status"), after.get("status"), after.get("assignee_id"))
        snapshots[key].append(before)
    result = {}
    for ticket_id, steps in history.items():
        for step in steps:
            key = (str(ticket_id), step.at, step.actor_id, step.action, step.from_status, step.to_status, step.assignee_id)
            queue = snapshots.get(key)
            before = queue.popleft() if queue else None
            if step.from_status is None:
                result[step.id] = False  # Tạo mới chưa có hạn của bước trước.
            elif before is None or "due_at" not in before:
                result[step.id] = None
            elif before["due_at"] is None:
                result[step.id] = False
            else:
                try:
                    due = datetime.fromisoformat(before["due_at"])
                    result[step.id] = step.at > due if due.tzinfo is not None else None
                except (TypeError, ValueError):
                    result[step.id] = None
    return result


def _report(session, start, end, since, until):
    # D6 cho mở lại nghiệm thu: chỉ tính lần hoàn thành cuối của phiếu hiện vẫn hoàn thành/đóng.
    # Không nhân đôi một phiếu khi hoàn thành lại, không tính phiếu đã hủy hoặc đang mở lại.
    final_steps = select(func.max(ServiceStep.id)).where(
        ServiceStep.to_status.in_(("HOAN_THANH", "KHONG_LAM_DUOC", "KHACH_HEN_SAU")),
        ServiceStep.action == "transition",
    ).group_by(ServiceStep.ticket_id)
    endings = list(session.scalars(select(ServiceStep).join(ServiceTicket, ServiceTicket.id == ServiceStep.ticket_id).where(
        ServiceStep.id.in_(final_steps), ServiceStep.at >= since, ServiceStep.at < until,
        or_(and_(ServiceStep.to_status == "HOAN_THANH", ServiceTicket.status.in_(("HOAN_THANH", "DA_DONG"))),
            and_(ServiceStep.to_status == "KHONG_LAM_DUOC", ServiceTicket.status == "KHONG_LAM_DUOC"),
            and_(ServiceStep.to_status == "KHACH_HEN_SAU", ServiceTicket.status == "KHACH_HEN_SAU")),
    ).order_by(ServiceStep.id)))
    ticket_ids = {step.ticket_id for step in endings}
    history = defaultdict(list)
    for step in session.scalars(select(ServiceStep).where(ServiceStep.ticket_id.in_(ticket_ids)).order_by(ServiceStep.id)):
        history[step.ticket_id].append(step)
    lateness = _history_lateness(session, ticket_ids, history)
    names = dict(session.execute(select(User.id, User.full_name)).all())
    from .. import service_charges
    costs=service_charges.material_costs(session,ticket_ids)
    tickets={t.id:t for t in session.scalars(select(ServiceTicket).where(ServiceTicket.id.in_(ticket_ids)))}
    technicians = {}
    completed_by_day = defaultdict(int)
    for ending in endings:
        row = technicians.setdefault(ending.assignee_id, dict(user_id=ending.assignee_id,
            name=names.get(ending.assignee_id, "Chưa giao"), completed=0, failed=0, durations=[],
            late=0, classified=0, unknown=0))
        if ending.to_status == "HOAN_THANH":
            row['revenue']=row.get('revenue',0)+(tickets[ending.ticket_id].subtotal or 0 if tickets[ending.ticket_id].billing=='THU_TIEN' else 0)
            row['material_cost']=row.get('material_cost',0)+costs.get(ending.ticket_id,0)
            row['completed'] += 1
            completed_by_day[ending.at.astimezone(TZ).date()] += 1
            assignments = [s for s in history[ending.ticket_id] if s.id < ending.id
                           and s.action in ("assign", "reassign") and s.assignee_id == ending.assignee_id]
            if assignments:
                elapsed = (ending.at - assignments[-1].at).total_seconds() / 60
                if elapsed >= 0:
                    row['durations'].append(elapsed)
        elif ending.to_status == 'KHACH_HEN_SAU':
            row['deferred'] = row.get('deferred',0)+1
        else:
            row['failed'] += 1
        flags = [lateness[s.id] for s in history[ending.ticket_id] if s.id <= ending.id]
        if any(flag is True for flag in flags):
            row['late'] += 1
            row['classified'] += 1
        elif flags and all(flag is False for flag in flags):
            row['classified'] += 1
        else:
            row['unknown'] += 1
    now = _now()
    # Đếm lượt theo giờ đến trong kỳ; giờ ở khách là phần thời gian giao với kỳ báo cáo.
    visits = session.scalars(select(ServiceVisit).where(ServiceVisit.started_at < until,
        or_(ServiceVisit.started_at >= since, ServiceVisit.ended_at > since,
            and_(ServiceVisit.ended_at.is_(None), ServiceVisit.started_at < now, since < now))))
    for visit in visits:
        row = technicians.setdefault(visit.technician_id, dict(user_id=visit.technician_id,
            name=names.get(visit.technician_id, 'Nhân viên'), completed=0, failed=0, durations=[],
            late=0, classified=0, unknown=0))
        for key in ('visits', 'visits_done', 'visits_return', 'visits_no_show', 'visit_hours'):
            row.setdefault(key, 0)
        if since <= visit.started_at < until:
            row['visits'] += 1
            row['visits_done'] += visit.outcome == 'XONG'
            row['visits_return'] += visit.outcome == 'HEN_LAI'
            row['visits_no_show'] += visit.outcome == 'KHONG_GAP_KHACH'
        row['visit_hours'] += max(0, (min(visit.ended_at or now, until) - max(visit.started_at, since)).total_seconds()) / 3600
    warranty, previous = aliased(ServiceTicket), aliased(ServiceTicket)
    finishing_tech = select(ServiceStep.assignee_id).where(ServiceStep.ticket_id == previous.id,
        ServiceStep.to_status == 'HOAN_THANH', ServiceStep.from_status.is_distinct_from('HOAN_THANH'),
        ServiceStep.at <= warranty.created_at).order_by(ServiceStep.at.desc(), ServiceStep.id.desc()).limit(1).correlate(previous, warranty).scalar_subquery()
    technician = func.coalesce(finishing_tech, previous.assignee_id)
    for uid, count in session.execute(select(technician, func.count()).select_from(warranty).join(
            previous, previous.id == warranty.warranty_of_id).where(warranty.warranty_basis == 'PHIEU_TRUOC',
            warranty.created_at >= since, warranty.created_at < until).group_by(technician)):
        row = technicians.setdefault(uid, dict(user_id=uid, name=names.get(uid, 'Chưa giao'),
            completed=0, failed=0, durations=[], late=0, classified=0, unknown=0))
        row['warranty_calls'] = count
    for row in technicians.values():
        row.setdefault('revenue',0);row.setdefault('material_cost',0)
        row['margin']=row['revenue']-row['material_cost']
        row.setdefault('deferred',0)
        row.setdefault('warranty_calls', 0)
        for key in ('visits', 'visits_done', 'visits_return', 'visits_no_show', 'visit_hours'):
            row.setdefault(key, 0)
        row['avg_minutes'] = sum(row['durations']) / len(row['durations']) if row['durations'] else None
        row['late_rate'] = 100 * row['late'] / row['classified'] if row['classified'] else None
    local_day = func.date(func.timezone("Asia/Ho_Chi_Minh", ServiceTicket.created_at))
    received = dict(session.execute(select(
        local_day, func.count()
    ).where(ServiceTicket.created_at >= since, ServiceTicket.created_at < until)
      .group_by(local_day)).all())
    daily = []
    day = start
    while day <= end:
        daily.append(dict(day=day.isoformat(), received=received.get(day, 0), completed=completed_by_day[day]))
        day += timedelta(days=1)
    return sorted(technicians.values(), key=lambda row: (row['name'].casefold(), row['user_id'] or 0)), daily


def _paused(session, since, until):
    latest = select(ServiceStep.ticket_id, func.max(ServiceStep.at).label('at')).where(
        ServiceStep.to_status == 'TAM_DUNG').group_by(ServiceStep.ticket_id).subquery()
    return list(session.scalars(select(ServiceTicket).join(latest, latest.c.ticket_id == ServiceTicket.id).where(
        ServiceTicket.status == 'TAM_DUNG', latest.c.at >= since, latest.c.at < until).order_by(latest.c.at, ServiceTicket.id)))


def _report_context(request, session):
    try:
        start, end, since, until = _date_range(request)
    except BusinessError as exc:
        return dict(start=request.query_params.get("from", ""), end=request.query_params.get("to", ""),
                    technicians=[], daily=[], error=exc.message, status=exc.status)
    technicians, daily = _report(session, start, end, since, until)
    selected = request.query_params.get('status', '')
    if selected not in ('', 'TAM_DUNG'):
        raise BusinessError('BAD_STATUS', 'Bộ lọc trạng thái chưa hợp lệ.')
    return dict(start=start.isoformat(), end=end.isoformat(), technicians=technicians, daily=daily, error=None,
        selected_status=selected, paused=_paused(session,since,until) if selected else [],
        users=dict(session.execute(select(User.id,User.full_name)).all()))


@router.get("/dieu-phoi/bao-cao")
def report_page(request: Request, user: User = Depends(dispatch_user), session: Session = Depends(get_session)):
    return render(request, "dieu_phoi_bao_cao.jinja", **_report_context(request, session))


def _csv_text(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


@router.get("/dieu-phoi/bao-cao.csv")
def report_csv(request: Request, user: User = Depends(dispatch_user), session: Session = Depends(get_session)):
    start, end, since, until = _date_range(request)
    technicians, daily = _report(session, start, end, since, until)
    output = io.StringIO()
    writer = csv.writer(output)
    if request.query_params.get('status') == 'TAM_DUNG':
        names = dict(session.execute(select(User.id, User.full_name)).all())
        writer.writerow(['Số phiếu', 'Thợ', 'Trạng thái', 'Lý do tạm dừng'])
        for row in _paused(session,since,until):
            writer.writerow([row.number, _csv_text(names.get(row.assignee_id,'')), 'Tạm dừng', svc.HOLD_REASONS.get(row.hold_reason,'')])
        return Response("\ufeff" + output.getvalue(), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition':'attachment; filename="tam-dung.csv"', 'Cache-Control':'private, no-store'})
    writer.writerow(["Thợ", "Đơn hoàn thành", "Không làm được", "Thời gian trung bình (phút)", "Tỷ lệ trễ (%)", "Chưa xác định trễ", "Số lượt", "Lượt xong việc", "Lượt hẹn lại", "Lượt không gặp khách", "Tổng giờ ở khách", "Bị gọi bảo hành", "Khách hẹn lần sau", "Doanh thu chưa VAT", "Chi phí vật tư", "Chênh lệch"])
    for row in technicians:
        writer.writerow([_csv_text(row['name']), row['completed'], row['failed'],
                         f"{row['avg_minutes']:.1f}" if row['avg_minutes'] is not None else "",
                         f"{row['late_rate']:.1f}" if row['late_rate'] is not None else "", row['unknown'], row['visits'], row['visits_done'],
                         row['visits_return'], row['visits_no_show'], f"{row['visit_hours']:.2f}", row['warranty_calls'], row['deferred'],row['revenue'],row['material_cost'],row['margin']])
    writer.writerow([])
    writer.writerow(["Ngày", "Đơn nhận", "Đơn hoàn thành"])
    writer.writerows([row['day'], row['received'], row['completed']] for row in daily)
    return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="bao-cao-dich-vu-{start}-{end}.csv"',
                             "Cache-Control": "private, no-store"})
