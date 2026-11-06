"""Màn Tin nhắn Zalo của giám đốc: danh sách hội thoại, xem tin, trả lời, cài webhook (TUẤN 05/11/2026)."""
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import zalo_bot as svc
from ..config import get_settings
from ..db import get_session
from ..models import User, ZaloChat
from . import checked_form, render, web_user
from .paging import build

router = APIRouter()
TZ = ZoneInfo('Asia/Ho_Chi_Minh')
NOTE_COOKIE = 'td_zalo_webhook_note'
STATUS = {'DANG_GUI': ('Đang gửi', 'cho'), 'DA_GUI': ('Đã gửi', 'xong'), 'LOI': ('Gửi lỗi', 'huy')}


def _when(value) -> str:
    return value.astimezone(TZ).strftime('%H:%M %d/%m/%Y') if value else ''


@router.get('/zalo/tin-nhan')
def page(request: Request, q: str = '', chat: str = '', trang: str = '', so: str = '', tin: str = '', gui: str = '', webhook: str = '',
         kq: str = '', gan: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    svc._director(user)
    q = q.strip()[:200]
    chat_pk = int(chat) if chat.isdecimal() and len(chat) < 10 else None
    current = session.get(ZaloChat, chat_pk) if chat_pk else None
    if current is not None:
        svc.mark_read(session, current.id)
    query = {'q': q, 'chat': current.id if current else ''}
    p = build(trang, so, svc.count_chats(session, q), query)
    chats = svc.list_chats(session, q, p['offset'], p['size'])
    messages, pm = [], None
    if current is not None:
        pm = build(tin, 50, svc.count_messages(session, current.id), {**query, 'trang': p['page'] if p['page'] > 1 else ''}, key='tin')
        messages = svc.list_messages(session, current.id, pm['offset'], pm['size'])
    cfg = get_settings()
    # Trợ lý Zalo: gắn hội thoại riêng với nhân viên hoặc khách. kq = ô tìm khách để gắn.
    staff_list, found, linked_user, linked_customer = [], [], None, None
    kq = kq.strip()[:100]
    if current is not None and current.chat_type == 'PRIVATE':
        from sqlalchemy import select
        from .. import customers
        from ..models import Customer
        staff_list = list(session.scalars(select(User).where(User.active.is_(True)).order_by(User.full_name)))
        linked_user = session.get(User, current.user_id) if current.user_id else None
        linked_customer = session.get(Customer, current.customer_code) if current.customer_code else None
        if kq:
            found = [c for c in customers.search(session, kq, include_test=False) if c.status == 'DANG_DUNG'][:8]
    note = unquote(request.cookies.get(NOTE_COOKIE, '')) if webhook else ''
    resp = render(request, 'zalo_tin_nhan.jinja', q=q, chats=chats, p=p, current=current, messages=messages, pm=pm, when=_when,
                  send_states=STATUS, kind_labels=svc.KIND_LABELS, max_text=svc.MAX_TEXT, gui=gui, webhook=webhook, note=note,
                  staff_list=staff_list, found=found, kq=kq, gan=gan, linked_user=linked_user, linked_customer=linked_customer,
                  has_token=bool(cfg.zalo_bot_token.strip()), has_secret=bool(cfg.zalo_webhook_secret), webhook_url=cfg.zalo_webhook_url)
    if webhook:
        resp.delete_cookie(NOTE_COOKIE, path='/zalo/tin-nhan')
    return resp


@router.post('/zalo/tin-nhan/cai-webhook')
def install(form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    ok, note = svc.install_webhook(session, user)
    resp = RedirectResponse(f"/zalo/tin-nhan?webhook={'ok' if ok else 'loi'}", status_code=303)
    resp.set_cookie(NOTE_COOKIE, quote(note), max_age=60, path='/zalo/tin-nhan', httponly=True,
                    secure=bool(get_settings().cookie_secure), samesite='strict')
    return resp


@router.post('/zalo/tin-nhan/{chat_pk}/tra-loi')
def answer(chat_pk: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    row = svc.reply(session, user, chat_pk, str(form.get('noi_dung') or ''))
    suffix = '&gui=loi' if row.status == 'LOI' else ''
    return RedirectResponse(f'/zalo/tin-nhan?chat={chat_pk}{suffix}', status_code=303)


# Báo cáo sáng (TUẤN duyệt 05/11/2026): xem trước nội dung tin 7:30 và gửi thử cho chính mình.
@router.get('/zalo/bao-cao-sang')
def morning(request: Request, gui: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    from .. import dispatch_alerts, morning_report
    svc._director(user)
    now = morning_report.dashboard._now()
    body = morning_report.render_text(morning_report.build(session, user, now.astimezone(TZ).date(), now), dispatch_alerts.base_url())
    cfg = get_settings()
    linked = any(u.id == user.id for u, _ in morning_report.recipients(session))
    return render(request, 'zalo_bao_cao_sang.jinja', body=body, gui=gui, linked=linked, enabled=cfg.morning_report,
                  has_token=bool(cfg.zalo_bot_token.strip()), at=cfg.morning_report_time.strftime('%H:%M'))


@router.post('/zalo/bao-cao-sang/gui-thu')
def morning_test(form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    from .. import morning_report
    svc._director(user)
    row = morning_report.send_test(session, user)
    result = 'chua-gan' if row is None else ('loi' if row.status == 'LOI' else 'ok')
    return RedirectResponse(f'/zalo/bao-cao-sang?gui={result}', status_code=303)


# Nhắc nợ quá hạn (TUẤN duyệt 06/11/2026): giám đốc xem trước tin thứ Hai gửi kế toán và gửi thử.
@router.get('/zalo/nhac-no')
def debt_reminder_page(request: Request, gui: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    from .. import debt_reminder
    svc._director(user)
    people = debt_reminder.recipients(session)
    body = debt_reminder.preview(session, people[0][0] if people else user)
    cfg = get_settings()
    return render(request, 'zalo_nhac_no.jinja', body=body, gui=gui, people=people, enabled=cfg.debt_reminder,
                  has_token=bool(cfg.zalo_bot_token.strip()), at=cfg.debt_reminder_time.strftime('%H:%M'))


@router.post('/zalo/nhac-no/gui-thu')
def debt_reminder_test(form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    from .. import debt_reminder
    svc._director(user)
    try:
        chat_pk = int(form.get('chat') or 0)
    except ValueError:
        chat_pk = 0
    row = debt_reminder.send_test(session, user, chat_pk)
    result = 'chua-gan' if row is None else ('loi' if row.status == 'LOI' else 'ok')
    return RedirectResponse(f'/zalo/nhac-no?gui={result}', status_code=303)
