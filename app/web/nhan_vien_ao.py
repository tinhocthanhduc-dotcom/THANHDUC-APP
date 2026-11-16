"""Trang Nhân viên AI (TUẤN duyệt phương án 07/11/2026): công tắc, cài đặt, nhật ký. Chỉ giám đốc.
Hoàn tác lần giao của Điều phối AI nằm trên phiếu dịch vụ (điều phối/giám đốc)."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import nhan_vien_ao as nva
from . import hubs
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
    from ..ghi_chu_phieu import RULES as gc_rules
    return render(request, 'nhan_vien_ao.jinja', hub=hubs.ai(session, user, '/nhan-vien-ao'), rows=rows, gc_rules=gc_rules, master=rows[nva.MASTER], agents=nva.AGENTS, cfgs={c: nva.cfg(rows[c]) for c in nva.AGENTS},
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
            raise BusinessError('NOT_FOUND', 'Không tìm thấy lần giao của Điều phối AI trên phiếu này.', status=404)
        nva.undo(session, user, entry.id)
    except BusinessError as exc:
        session.rollback()
        return _detail(request, session, user, ticket_id, failed_action='hoan-tac-ao', error=exc.message, error_code=exc.code,
                       status=exc.status)
    return RedirectResponse(f'/dich-vu/{ticket_id}?hoan_tac=1', status_code=303)


@router.post('/viec-cua-toi/giam-doc/{task_id}/xong')
def gd_task_done(request: Request, task_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    """Đội AI đợt 3: nhân viên bấm Xong việc giám đốc nhắn nhắc qua Trưởng nhóm AI."""
    from .. import truong_nhom_chat
    truong_nhom_chat.finish(session, request.state.user, task_id)
    return RedirectResponse('/viec-cua-toi?xong=done', status_code=303)


# ---------- Đội AI (TUẤN duyệt 15/11/2026, mẫu 9rRchbgXNR4sY5jurdjQMq): chỉ giám đốc ----------

DOI_TABS = (('', 'Cả đội'), ('nguoi', 'Người'), ('ai', 'AI'), ('qua-han', 'Quá hạn'))


def _doi(request, session, user, tab='', error='', done='', status=200):
    from .. import truong_nhom as tn
    nva._director(user)
    tab = tab if tab in dict(DOI_TABS) else ''
    rows = nva.rows(session)
    b = tn.board(session)
    counts = {'': len(b['lanes']) + len(nva.AGENTS), 'nguoi': len(b['lanes']), 'ai': len(nva.AGENTS), 'qua-han': b['tiles']['late']}
    tabs = [dict(label=label, n=counts[code], href='/doi-ai' + (f'?xem={code}' if code else ''), on=tab == code) for code, label in DOI_TABS]
    tiles = [dict(label='Việc đang mở', n=b['tiles']['open'], sub=f"{b['tiles']['new_today']} việc mới hôm nay"),
             dict(label='AI đã tự làm hôm nay', n=b['tiles']['ai'], sub='Giao, nhắc, báo, đổi người', tone='area-sv'),
             dict(label='AI soạn sẵn · chờ người bấm', n=b['tiles']['waiting'], sub='Phiếu nháp Zalo, tin gửi khách', tone='area-sa'),
             dict(label='Quá hạn', n=b['tiles']['late'], sub='Đã nhắc người làm', href='/doi-ai?xem=qua-han', on=tab == 'qua-han',
                  tone='hot' if b['tiles']['late'] else ''),
             dict(label='Báo giám đốc', n=b['tiles']['boss'], sub='Treo lâu sau khi nhắc', tone='hot' if b['tiles']['boss'] else '')]
    return render(request, 'doi_ai.jinja', hub=hubs.ai(session, user, '/doi-ai'), b=b, rows=rows, master=rows[nva.MASTER], agents=nva.AGENTS, cfgs={c: nva.cfg(rows[c]) for c in nva.AGENTS},
                  actions=nva.ACTIONS, tabs=tabs, tiles=tiles, tab=tab, error=error, done=done, status=status)


@router.get('/doi-ai')
def doi_ai(request: Request, xem: str = '', xong: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _doi(request, session, user, xem, done=xong)


@router.post('/doi-ai/hoan-tac/{log_id}')
def doi_ai_undo(request: Request, log_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    from .. import truong_nhom as tn
    user = request.state.user
    try:
        tn.undo(session, user, log_id)
    except BusinessError as exc:
        if exc.status == 403:
            raise
        session.rollback()
        return _doi(request, session, user, error=exc.message, status=exc.status)
    return RedirectResponse('/doi-ai?xong=1', status_code=303)
