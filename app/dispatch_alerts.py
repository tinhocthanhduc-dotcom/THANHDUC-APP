"""Báo điều phối qua Zalo (TUẤN duyệt 05/11/2026): phiếu cần giao thợ (kèm gợi ý), phiếu sắp trễ hẹn, thợ báo trễ.

Chỉ gửi cho người có vai Điều phối đã được giám đốc gắn Zalo (Tin nhắn Zalo > Gắn nhân viên). Tin chỉ báo và kèm link,
giao thợ hay dời hẹn vẫn bấm trong app. Mỗi mốc một tin cho mỗi người (bảng dispatch_zalo_sent).
Chỉ gửi trong giờ làm việc, ngày làm việc (không Chủ nhật, không ngày lễ). Bot Zalo chưa cấu hình thì không làm gì.
Chạy nền cùng cờ TD_PUSH_LOOP; 2 worker uvicorn chỉ một worker chạy nhờ khóa advisory.
"""
import logging
import threading
import time as clock
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import select, text

from . import service_appointments, service_suggest, service_tickets
from .config import get_settings
from .models import AttendanceHoliday, Customer, CustomerSite, DispatchZaloSent, ServiceStep, ServiceTicket, User, ZaloChat
from .service_sla import TZ

log = logging.getLogger(__name__)
LOCK_ID = 7_301_028
EVERY = 60
FRESH = timedelta(minutes=30)   # chỉ báo việc vừa tới mốc trong 30 phút, không dồn việc cũ khi mới bật
MAX_PER_ROUND = 10


def base_url() -> str:
    parts = urlsplit(get_settings().zalo_webhook_url or '')
    return f'{parts.scheme}://{parts.netloc}' if parts.scheme and parts.netloc else 'https://quanlythanhduc.com'


def working_now(session, now) -> bool:
    s = get_settings()
    local = now.astimezone(TZ)
    if local.weekday() not in s.work_days or not (s.work_start <= local.time() <= s.work_end):
        return False
    return session.get(AttendanceHoliday, local.date()) is None


def recipients(session) -> list[tuple[User, ZaloChat]]:
    out = []
    for user, chat in session.execute(select(User, ZaloChat).join(ZaloChat, ZaloChat.user_id == User.id)
                                      .where(User.active.is_(True), ZaloChat.chat_type == 'PRIVATE').order_by(User.id, ZaloChat.id)):
        if 'dispatcher' in user.roles and all(u.id != user.id for u, _ in out):
            out.append((user, chat))
    return out


def _place(session, t):
    site = session.get(CustomerSite, t.site_id) if t.site_id else None
    customer = session.get(Customer, t.customer_code)
    name = customer.name if customer else t.customer_code
    where = service_suggest.area((site.address if site else '') or (customer.address if customer else ''))
    if where:
        where = ('Quận ' + where) if where.isdigit() else where.title()
    return name, where


def _code(number: str) -> str:
    return number.rsplit('-', 1)[-1]


def events(session, now) -> list[tuple[str, str]]:
    """[(mốc, nội dung)] cần báo lúc này, chưa lọc theo người đã nhận."""
    out = []
    t = ServiceTicket
    fresh = list(session.scalars(select(t).where(t.status == 'MOI', t.assignee_id.is_(None), t.counter_intake.is_(False),
        ((t.self_claim_until.is_not(None) & (t.self_claim_until <= now) & (t.self_claim_until > now - FRESH))
         | (t.self_claim_until.is_(None) & (t.created_at > now - FRESH)))).order_by(t.id)))
    ranks = service_suggest.rank_many(session, fresh, now) if fresh else {}
    for row in fresh:
        name, where = _place(session, row)
        why = 'hết giờ tự nhận, chưa thợ nào nhận' if row.self_claim_until else ('phiếu Khẩn' if row.priority == 'KHAN' else 'cần giao thợ')
        top = service_suggest.best(ranks.get(row.id, []))
        tip = f"\nGợi ý: {top['name']}" + (' · ' + ' · '.join(top['why']) if top['why'] else '') if top else ''
        place = f' ({where})' if where else ''
        out.append((f'can-giao:{row.id}', f"{row.number} {name}{place}: {why}.{tip}\nMở app để giao: {base_url()}/dich-vu/{row.id}"))
    for item in service_suggest.at_risk(session, now, level='dispatch'):
        row = item['ticket']
        name, where = _place(session, row)
        tech = session.get(User, row.assignee_id)
        when = service_appointments.label(row.appointment_at, row.appointment_slot) if row.appointment_slot in service_appointments.LABELS \
            else row.appointment_at.astimezone(TZ).strftime('%H:%M')
        late = f" Thợ đã báo: {item['late']['note'].lower()}." if item['late'] else ''
        out.append((f"tre:{item['mark']}", f"{row.number} {name} hẹn {when}: {tech.full_name if tech else 'thợ'} chưa đi ({service_suggest.risk_label(item).lower()}).{late}"
                    f"\nMở app: {base_url()}/dich-vu/{row.id}"))
    for step, number, ticket_id in session.execute(select(ServiceStep, t.number, t.id).join(t, t.id == ServiceStep.ticket_id).where(
            ServiceStep.action == service_suggest.LATE, ServiceStep.at > now - FRESH).order_by(ServiceStep.id)):
        tech = session.get(User, step.actor_id)
        out.append((f'bao-tre:{step.id}', f"{tech.full_name if tech else 'Thợ'} báo {step.note.lower()} cho {number}. Nhờ anh gọi khách báo hoặc dời hẹn."
                    f"\nMở app: {base_url()}/dich-vu/{ticket_id}"))
    return out


def run_once(session, now=None, sender=None) -> int:
    from . import zalo_bot
    s = get_settings()
    if not s.dispatch_zalo or not s.zalo_bot_token.strip():
        return 0
    now = now or service_tickets._now()
    if not working_now(session, now):
        return 0
    people = recipients(session)
    if not people:
        return 0
    todo = events(session, now)
    if not todo:
        return 0
    sent_marks = set(session.execute(select(DispatchZaloSent.mark, DispatchZaloSent.user_id).where(
        DispatchZaloSent.mark.in_([m for m, _ in todo]))).all())
    send = sender or zalo_bot.send_text
    count = 0
    for mark, body in todo:
        for user, chat in people:
            if (mark, user.id) in sent_marks or count >= MAX_PER_ROUND:
                continue
            # Ghi mốc trước rồi mới gửi: gửi lỗi vẫn không gửi lại (tránh 2 tin), lỗi hiện ở Tin nhắn Zalo.
            row = DispatchZaloSent(mark=mark, user_id=user.id)
            session.add(row)
            session.commit()
            msg = send(session, chat, body, None, 'dieu_phoi')
            row.message_id = getattr(msg, 'id', None)
            session.commit()
            count += 1
    return count


def _loop():
    from sqlalchemy.orm import Session
    from .db import engine
    eng = engine()
    while True:
        try:
            with eng.connect() as conn:
                locked = conn.execute(text('select pg_try_advisory_lock(:k)'), dict(k=LOCK_ID)).scalar()
                conn.commit()
                if locked:
                    while True:
                        with Session(bind=eng, expire_on_commit=False) as session:
                            run_once(session)
                        from . import production_stale, weekly_zalo
                        for job in (production_stale.run_once, weekly_zalo.run_once):
                            try:
                                with Session(bind=eng, expire_on_commit=False) as session:
                                    job(session)
                            except Exception:  # noqa: BLE001 - lỗi một việc nhắc không chặn báo điều phối.
                                log.exception('zalo job %s', job.__module__)
                        clock.sleep(EVERY)
        except Exception:  # noqa: BLE001 - vòng nền không được làm sập app; thử lại sau.
            log.exception('dispatch zalo loop')
        clock.sleep(EVERY)


def start():
    threading.Thread(target=_loop, name='dispatch-zalo', daemon=True).start()
