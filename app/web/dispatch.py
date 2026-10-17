"""D7: dashboard và báo cáo chỉ đọc; không thay đổi phiếu hay lịch sử."""
import csv
import io
from collections import defaultdict, deque
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import service_tickets as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import AuditLog, ServiceStep, ServiceTicket, User
from ..security import has_role
from . import render, web_user

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
    )).one()
    count_open, count_overdue, count_parts = counts
    if view == "open":
        rows = list(session.scalars(select(ServiceTicket).where(opened)
                                   .order_by(ServiceTicket.due_at.asc().nulls_last(), ServiceTicket.id.desc())))
        title = "Đơn đang mở"
    elif view == "parts":
        rows = svc.search(session, user, status="CHO_LINH_KIEN", limit=count_parts or 1)
        title = "Chờ linh kiện"
    else:
        view, title = "overdue", "Đơn quá hạn"
        # Truyền limit theo tổng thực tế: không cắt danh sách ở giới hạn tìm kiếm mặc định.
        rows = svc.search(session, user, overdue=True, limit=count_overdue or 1)
    return render(request, "dieu_phoi.jinja", rows=rows, view=view, title=title,
                  count_open=count_open, count_overdue=count_overdue, count_parts=count_parts,
                  users=dict(session.execute(select(User.id, User.full_name)).all()), open_counts=svc.open_counts(session),
                  now=now, delay=_delay)


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
    endings = list(session.scalars(select(ServiceStep).where(
        ServiceStep.to_status.in_(("HOAN_THANH", "KHONG_LAM_DUOC")),
        ServiceStep.action == "transition", ServiceStep.at >= since, ServiceStep.at < until,
    ).order_by(ServiceStep.id)))
    ticket_ids = {step.ticket_id for step in endings}
    history = defaultdict(list)
    for step in session.scalars(select(ServiceStep).where(ServiceStep.ticket_id.in_(ticket_ids)).order_by(ServiceStep.id)):
        history[step.ticket_id].append(step)
    lateness = _history_lateness(session, ticket_ids, history)
    names = dict(session.execute(select(User.id, User.full_name)).all())
    technicians = {}
    completed_by_day = defaultdict(int)
    for ending in endings:
        row = technicians.setdefault(ending.assignee_id, dict(user_id=ending.assignee_id,
            name=names.get(ending.assignee_id, "Chưa giao"), completed=0, failed=0, durations=[],
            late=0, classified=0, unknown=0))
        if ending.to_status == "HOAN_THANH":
            row['completed'] += 1
            completed_by_day[ending.at.astimezone(TZ).date()] += 1
            assignments = [s for s in history[ending.ticket_id] if s.id < ending.id
                           and s.action in ("assign", "reassign") and s.assignee_id == ending.assignee_id]
            if assignments:
                elapsed = (ending.at - assignments[-1].at).total_seconds() / 60
                if elapsed >= 0:
                    row['durations'].append(elapsed)
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
    for row in technicians.values():
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


def _report_context(request, session):
    try:
        start, end, since, until = _date_range(request)
    except BusinessError as exc:
        return dict(start=request.query_params.get("from", ""), end=request.query_params.get("to", ""),
                    technicians=[], daily=[], error=exc.message, status=exc.status)
    technicians, daily = _report(session, start, end, since, until)
    return dict(start=start.isoformat(), end=end.isoformat(), technicians=technicians, daily=daily, error=None)


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
    writer.writerow(["Thợ", "Đơn hoàn thành", "Không làm được", "Thời gian trung bình (phút)", "Tỷ lệ trễ (%)", "Chưa xác định trễ"])
    for row in technicians:
        writer.writerow([_csv_text(row['name']), row['completed'], row['failed'],
                         f"{row['avg_minutes']:.1f}" if row['avg_minutes'] is not None else "",
                         f"{row['late_rate']:.1f}" if row['late_rate'] is not None else "", row['unknown']])
    writer.writerow([])
    writer.writerow(["Ngày", "Đơn nhận", "Đơn hoàn thành"])
    writer.writerows([row['day'], row['received'], row['completed']] for row in daily)
    return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="bao-cao-dich-vu-{start}-{end}.csv"',
                             "Cache-Control": "private, no-store"})
