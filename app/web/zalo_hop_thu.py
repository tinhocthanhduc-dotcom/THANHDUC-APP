"""Tất cả tin Zalo (TUẤN duyệt mẫu 19/11/2026): xem lại hội thoại theo từng khách, trả lời qua Zalo Bot."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import zalo_bot as bot
from .. import zalo_hop_thu as svc
from ..db import get_session
from ..models import User
from . import checked_form, hubs, nav, render, web_user
from .paging import build
from .zalo import STATUS, _when

router = APIRouter()
PATH = '/zalo/hop-thu'


@router.get(PATH)
def page(request: Request, q: str = '', loc: str = 'tat-ca', chat: str = '', trang: str = '', so: str = '', tin: str = '', gui: str = '', ai: str = '',
         user: User = Depends(web_user), session: Session = Depends(get_session)):
    nav.check_access(user, PATH)
    q = q.strip()[:200]
    tabs = svc.tabs(user)
    loc = loc if loc in dict(tabs) else 'tat-ca'
    current = svc.get_chat(session, user, int(chat)) if chat.isdecimal() and len(chat) < 10 else None
    if current is not None:
        bot.mark_read(session, current.id)
    query = {'q': q, 'loc': loc if loc != 'tat-ca' else '', 'chat': current.id if current else ''}
    n = svc.counts(session, user, q)
    p = build(trang, so, n[loc], query)
    chats = svc.list_chats(session, user, loc, q, p['offset'], p['size'])
    shown = chats + ([current] if current is not None and current not in chats else [])
    messages, pm, side, draft = [], None, None, None
    if current is not None:
        pm = build(tin, 50, bot.count_messages(session, current.id), {**query, 'trang': p['page'] if p['page'] > 1 else ''}, key='tin')
        messages = bot.list_messages(session, current.id, pm['offset'], pm['size'])
        side = svc.panel(session, user, current)
        draft = svc.draft_state(session, current) if current.chat_type == 'PRIVATE' and not current.user_id else None
    tab_rows = [dict(key=k, label=label, n=n[k], on=k == loc) for k, label in tabs]
    return render(request, 'zalo_hop_thu.jinja', hub=hubs.zalo(session, user, PATH), q=q, loc=loc, tabs=tab_rows,
                  chats=chats, p=p, current=current, messages=messages, pm=pm, side=side, when=_when, gui=gui,
                  waiting=svc.waiting(session, [c.id for c in shown]), names=svc.customers(session, shown),
                  send_states=STATUS, kind_labels=bot.KIND_LABELS, max_text=bot.MAX_TEXT, director=svc.is_director(user), draft=draft, ai=ai)


@router.post(PATH + '/{chat_pk}/tra-loi')
def answer(chat_pk: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    nav.check_access(user, PATH)
    draft = str(form.get('ban_soan') or '')
    row = svc.reply(session, user, chat_pk, str(form.get('noi_dung') or ''), int(draft) if draft.isdecimal() and len(draft) < 15 else None)
    suffix = _loc(form, user) + ('&gui=loi' if row.status == 'LOI' else '&gui=ok')
    return RedirectResponse(f'{PATH}?chat={chat_pk}{suffix}#cuoi', status_code=303)


def _loc(form, user) -> str:
    loc = str(form.get('loc') or '')
    return '&loc=' + loc if loc in dict(svc.tabs(user)) and loc != 'tat-ca' else ''


@router.post(PATH + '/{chat_pk}/ai-soan')
def ai_draft(chat_pk: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    """Đợt 2: nhờ AI trên Z2 soạn câu trả lời; Z2 không chạy thì báo để gõ tay (không lỗi trang)."""
    nav.check_access(user, PATH)
    from ..errors import BusinessError
    try:
        svc.request_draft(session, user, chat_pk)
        flag = ''
    except BusinessError as exc:
        if exc.code != 'NO_Z2':
            raise
        session.rollback()
        flag = '&ai=z2'
    return RedirectResponse(f'{PATH}?chat={chat_pk}{_loc(form, user)}{flag}#tra-loi', status_code=303)


@router.post(PATH + '/{chat_pk}/bo-soan/{draft_id}')
def ai_drop(chat_pk: int, draft_id: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    nav.check_access(user, PATH)
    svc.drop_draft(session, user, chat_pk, draft_id)
    return RedirectResponse(f'{PATH}?chat={chat_pk}{_loc(form, user)}#tra-loi', status_code=303)
