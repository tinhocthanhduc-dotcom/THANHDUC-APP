"""Màn Trợ lý AI của giám đốc (bản mẫu TUẤN duyệt 01/11/2026) và bảng duyệt nhanh cạnh chuông."""
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import ai_agent as svc
from ..db import get_session
from ..models import User
from .. import form_keys
from . import checked_form, render, web_user, _safe_next
from .paging import build

router = APIRouter()


def _back(form, tab='cho-duyet'):
    nxt = _safe_next(str(form.get('next') or ''))
    return nxt if nxt and nxt != '/' else f'/tro-ly-ai?tab={tab}'


@router.get('/tro-ly-ai')
def page(request: Request, tab: str = 'cho-duyet', trang: str = '', so: str = '', khoa_moi: str = '',
         user: User = Depends(web_user), session: Session = Depends(get_session)):
    svc._director(user)
    if tab not in (*svc.TABS, 'nhat-ky'):
        tab = 'cho-duyet'
    counts = svc.counts(session)
    p = build(trang, so, counts[tab], {'tab': tab})
    rows = svc.listing(session, tab, p['offset'], p['size'])
    tabs = [dict(label=label, n=counts[key], href=f'/tro-ly-ai?tab={key}', on=key == tab) for key, (label, _) in svc.TABS.items()]
    tabs.append(dict(label='Nhật ký', n=counts['nhat-ky'], href='/tro-ly-ai?tab=nhat-ky', on=tab == 'nhat-ky'))
    # Khóa mới chỉ hiện một lần ngay sau khi tạo (giữ trong phiên, không đưa lên địa chỉ).
    new_key = request.cookies.get('td_ai_key_once', '') if khoa_moi else ''
    resp = render(request, 'tro_ly_ai.jinja', tab=tab, tabs=tabs, rows=rows, p=p, keys=svc.active_keys(session), agents={k: v for k, v in svc.AGENTS.items() if v['ready']},
                  new_key=new_key, status_labels=svc.STATUS_LABELS)
    if khoa_moi:
        resp.delete_cookie('td_ai_key_once', path='/tro-ly-ai')
    return resp


@router.get('/tro-ly-ai/cho-duyet.json')
def pending(user: User = Depends(web_user), session: Session = Depends(get_session)):
    if 'director' not in user.roles:
        return JSONResponse(dict(count=0, items=[]), headers={'Cache-Control': 'no-store'})
    total, items = svc.pending_brief(session)
    for item in items:
        item['form_key'] = form_keys.form_key()
    return JSONResponse(dict(count=total, items=items), headers={'Cache-Control': 'no-store'})


@router.post('/tro-ly-ai/{action_id}/duyet')
def approve(action_id: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    svc.approve(session, user, action_id)
    return RedirectResponse(_back(form), status_code=303)


@router.post('/tro-ly-ai/{action_id}/tu-choi')
def reject(action_id: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    svc.reject(session, user, action_id, str(form.get('ly_do', '')))
    return RedirectResponse(_back(form), status_code=303)


@router.post('/tro-ly-ai/khoa/tao')
def create_key(form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    raw = svc.create_key(session, user, str(form.get('tro_ly') or 'dieu-phoi'))
    resp = RedirectResponse('/tro-ly-ai?khoa_moi=1', status_code=303)
    resp.set_cookie('td_ai_key_once', raw, max_age=300, path='/tro-ly-ai', httponly=True, secure=bool(_secure()), samesite='strict')
    return resp


def _secure():
    from ..config import get_settings
    return get_settings().cookie_secure


@router.post('/tro-ly-ai/khoa/thu-hoi')
def revoke_key(form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    svc.revoke_key(session, user, str(form.get('tro_ly') or 'dieu-phoi'))
    return RedirectResponse('/tro-ly-ai', status_code=303)


@router.post('/tro-ly-ai/tam-dung')
def pause(form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    svc.set_paused(session, user, form.get('dung') == '1', str(form.get('tro_ly') or 'dieu-phoi'))
    return RedirectResponse('/tro-ly-ai', status_code=303)
