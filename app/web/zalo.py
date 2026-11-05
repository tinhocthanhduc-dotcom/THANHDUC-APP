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
         user: User = Depends(web_user), session: Session = Depends(get_session)):
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
    note = unquote(request.cookies.get(NOTE_COOKIE, '')) if webhook else ''
    resp = render(request, 'zalo_tin_nhan.jinja', q=q, chats=chats, p=p, current=current, messages=messages, pm=pm, when=_when,
                  send_states=STATUS, kind_labels=svc.KIND_LABELS, max_text=svc.MAX_TEXT, gui=gui, webhook=webhook, note=note,
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
