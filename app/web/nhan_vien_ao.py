"""Trang Nhân viên ảo (TUẤN duyệt phương án 07/11/2026): công tắc, cài đặt, nhật ký. Chỉ giám đốc.
Hoàn tác lần giao của Điều phối ảo nằm trên phiếu dịch vụ (điều phối/giám đốc)."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import nhan_vien_ao as nva
from ..db import get_session
from ..errors import BusinessError
from ..models import User, VirtualAgentLog
from . import checked_form, render, web_user
from .paging import build

router = APIRouter()


def _day(raw):
    try:
        return date.fromisoformat(raw) if raw else nva._now().astimezone(nva.TZ).date()
    except ValueError:
        return nva._now().astimezone(nva.TZ).date()


def _page(request, session, user, ngay='', loai='', trang='', so='', luu='', error='', open_code='', status=200):
    nva._director(user)
    day = _day(ngay)
    loai = loai if loai in nva.AGENTS else ''
    rows = nva.rows(session)
    counts = nva.log_counts(session, day)
    total = counts.get(loai, 0) if loai else sum(counts.values())
    p = build(trang, so, total, {'ngay': ngay, 'loai': loai})
    entries = list(session.scalars(nva.log_query(day, loai).order_by(VirtualAgentLog.at.desc(), VirtualAgentLog.id.desc())
                                   .offset(p['offset']).limit(p['size'])))
    names = dict(session.execute(select(User.id, User.full_name)).all())
    q = f'&ngay={ngay}' if ngay else ''
    tabs = [dict(label='Tất cả', n=sum(counts.values()), href='/nhan-vien-ao' + ('?' + q[1:] if q else ''), on=not loai)]
    tabs += [dict(label=a['name'], n=counts.get(code, 0), href=f'/nhan-vien-ao?loai={code}{q}', on=loai == code) for code, a in nva.AGENTS.items()]
    return render(request, 'nhan_vien_ao.jinja', rows=rows, master=rows[nva.MASTER], agents=nva.AGENTS, cfgs={c: nva.cfg(rows[c]) for c in nva.AGENTS},
                  modes=nva.MODES, actions=nva.ACTIONS, kinds=nva.KIND_LABELS, dispatch_kinds=nva.DISPATCH_KINDS,
                  priorities=nva.PRIORITY_LABELS, stats=nva.today_stats(session), counts=counts, total=total, entries=entries,
                  trial=nva.trial_results(session, entries), names=names, p=p, tabs=tabs, day=day, ngay=ngay, loai=loai, luu=luu,
                  error=error, open_code=open_code, zalo=nva._zalo_ready(), status=status)


@router.get('/nhan-vien-ao')
def page(request: Request, ngay: str = '', loai: str = '', trang: str = '', so: str = '', luu: str = '',
         user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _page(request, session, user, ngay, loai, trang, so, luu)


@router.post('/nhan-vien-ao/{code}')
def save(request: Request, code: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    try:
        nva.save(session, user, code, form)
    except BusinessError as exc:
        if exc.status == 403:
            raise
        session.rollback()
        return _page(request, session, user, error=exc.message, open_code=code, status=exc.status)
    return RedirectResponse('/nhan-vien-ao?luu=1', status_code=303)


@router.post('/dich-vu/{ticket_id}/dieu-phoi-ao/hoan-tac')
def undo(request: Request, ticket_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    from .services import _detail
    user = request.state.user
    raw = str(form.get('log_id') or '')
    try:
        if not raw.isdecimal():
            raise BusinessError('VALIDATION', 'Thiếu lần giao cần hoàn tác.')
        entry = session.get(VirtualAgentLog, int(raw))
        if entry is None or entry.ticket_id != ticket_id:
            raise BusinessError('NOT_FOUND', 'Không tìm thấy lần giao của Điều phối ảo trên phiếu này.', status=404)
        nva.undo(session, user, entry.id)
    except BusinessError as exc:
        session.rollback()
        return _detail(request, session, user, ticket_id, failed_action='hoan-tac-ao', error=exc.message, error_code=exc.code,
                       status=exc.status)
    return RedirectResponse(f'/dich-vu/{ticket_id}?hoan_tac=1', status_code=303)
