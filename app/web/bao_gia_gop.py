"""Báo giá gộp nhiều máy một khách (TUẤN duyệt mẫu 15/11/2026): chọn máy, gửi một link / tin / A4, ghi khách trả lời từng máy."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import repair_quote_groups as rg
from .. import repair_quotes as rqs
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, User
from . import checked_form, hubs, paging, render, web_user

router = APIRouter(include_in_schema=False)
public_router = APIRouter(include_in_schema=False)
DONE = {'gui-zalo': 'Đã gửi Zalo công ty.', 'da-gui': 'Đã ghi đã gửi khách.', 'thu-hoi': 'Đã thu hồi link, khách mở sẽ không thấy giá.',
        'tra-loi': 'Đã ghi khách trả lời vào từng phiếu.'}


def _int(v):
    v = str(v or '').strip()
    return int(v) if v.isdigit() and len(v) < 12 else None


def _pick_page(request, session, user, code, form=None, error='', error_code='', status=200):
    rqs.require(user)
    customer = session.get(Customer, code)
    if customer is None:
        raise BusinessError('NOT_FOUND', 'Không có khách này.', status=404)
    rows = rg.candidates(session, code)
    chosen = {int(x) for x in form.getlist('phieu') if str(x).isdigit()} if form is not None else {r['ticket'].id for r in rows if r['ready']}
    from ..tin_gui_khach import linked_chat
    old, _ = rg.listing(session, 'cho', customer_code=code)
    done, _ = rg.listing(session, 'xong', customer_code=code)
    return render(request, 'bao_gia_gop_chon.jinja', customer=customer, rows=rows, chosen=chosen, linked=bool(linked_chat(session, code)),
                  existing=(old + done)[:5], labels=rg.STATE_LABELS,
                  valid_days=rqs.VALID_DAYS, error=error, error_code=error_code, status=status)


@router.get('/bao-gia-gop')
def listing(request: Request, view: str = 'cho', q: str = '', trang: str = '', so: str = '', user: User = Depends(web_user),
            session: Session = Depends(get_session)):
    """Danh sách báo giá gộp (TUẤN 16/11/2026: xem lại báo giá cũ, không phải tạo mới)."""
    rqs.require(user)
    view = view if view in rg.VIEWS else 'cho'
    rows, counts = rg.listing(session, view, q[:200])
    query = dict(view=view, q=q[:200])
    page, shown = paging.slice_rows(rows, trang, so, query)
    link = paging.linker('/bao-gia-gop', query)
    return render(request, 'bao_gia_gop_ds.jinja', rows=shown, p=page, q=q[:200], view=view, labels=rg.STATE_LABELS,
                  hub=hubs.quotes(session, user, '/bao-gia-gop'),
                  tabs=[dict(label=label, n=counts[k], on=k == view, href=link(view=k)) for k, label in rg.VIEWS.items()])


@router.get('/bao-gia-sua')
def single_listing(request: Request, view: str = 'cho', q: str = '', trang: str = '', so: str = '', user: User = Depends(web_user),
                   session: Session = Depends(get_session)):
    """Báo giá sửa một máy đã lập (TUẤN 16/11/2026: "chưa có báo giá loại không gộp?"). Mở lại ở phiếu dịch vụ."""
    rqs.require(user)
    view = view if view in rqs.LIST_VIEWS else 'cho'
    rows, counts = rqs.listing(session, view, q[:200])
    query = dict(view=view, q=q[:200])
    page, shown = paging.slice_rows(rows, trang, so, query)
    link = paging.linker('/bao-gia-sua', query)
    return render(request, 'bao_gia_sua_ds.jinja', rows=shown, p=page, q=q[:200], view=view, statuses=rqs.STATUSES,
                  hub=hubs.quotes(session, user, '/bao-gia-sua'),
                  tabs=[dict(label=label, n=counts[k], on=k == view, href=link(view=k)) for k, label in rqs.LIST_VIEWS.items()])


@router.get('/bao-gia-gop/moi')
def pick(request: Request, khach: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _pick_page(request, session, user, khach[:16])


@router.post('/bao-gia-gop/moi')
def create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    code = str(form.get('khach', '') or '')[:16]
    picks = {}
    for x in form.getlist('phieu'):
        tid = _int(x)
        if tid is not None:
            picks[tid] = _int(form.get(f'v_{tid}'))
    try:
        g = rg.create(session, user, code, picks)
    except BusinessError as exc:
        session.rollback()
        if exc.status in (403, 404):
            raise
        return _pick_page(request, session, user, code, form, exc.message, exc.code, exc.status)
    return RedirectResponse(f'/bao-gia-gop/{g.id}?da_luu=1', status_code=303)


def _detail(request, session, user, gid, form=None, error='', error_code='', status=200):
    g = rg.load(session, user, gid)
    from ..tin_gui_khach import linked_chat
    from .services import _return_days
    from .. import service_tickets
    return render(request, 'bao_gia_gop.jinja', g=g, d=rg.view(session, g), link=rg.link(g), customer=session.get(Customer, g.customer_code),
                  linked=bool(linked_chat(session, g.customer_code)), via=rqs.VIA, reasons=rqs.REASONS, statuses=rqs.STATUSES,
                  sent_via=rqs.SENT_VIA, done_msg=DONE, return_days=_return_days(service_tickets._now()), expired=g.valid_until < rqs._today(),
                  users={u.id: u.full_name for u in session.scalars(select(User))}, form=form or {}, error=error, error_code=error_code,
                  status=status)


@router.get('/bao-gia-gop/{gid}')
def detail(request: Request, gid: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _detail(request, session, user, gid)


ACTIONS = ('gui-zalo', 'da-gui', 'thu-hoi', 'tra-loi')


@router.post('/bao-gia-gop/{gid}/{action}')
def action(request: Request, gid: int, action: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    if action not in ACTIONS:
        raise BusinessError('NOT_FOUND', 'Không có thao tác này.', status=404)
    g = rg.load(session, user, gid)
    try:
        if action == 'gui-zalo':
            rg.send_bot(session, user, gid, form.get('text'))
        elif action == 'da-gui':
            rg.mark_sent(session, user, gid, form.get('text'))
        elif action == 'thu-hoi':
            rg.revoke(session, user, gid)
        else:
            answers, versions, reasons = {}, {}, {}
            for v in rg.members(session, g):
                tid = v.ticket_id
                answers[tid] = str(form.get(f'tl_{tid}', '') or '')
                versions[tid] = _int(form.get(f'v_{tid}'))
                reasons[tid] = str(form.get(f'ly_do_{tid}', '') or '')
            try:
                day = date.fromisoformat(str(form.get('promised_return_on', '') or ''))
            except ValueError:
                day = None
            rg.reply(session, user, gid, answers, versions, str(form.get('via', '')), str(form.get('person', '')), day, reasons,
                     str(form.get('note', '') or ''))
    except BusinessError as exc:
        if exc.status in (403, 404):
            raise
        session.rollback()
        return _detail(request, session, user, gid, form, exc.message, exc.code, exc.status)
    return RedirectResponse(f'/bao-gia-gop/{gid}?xong={action}', status_code=303)


@router.get('/bao-gia-gop/{gid}/in')
def print_page(request: Request, gid: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    from ..money_words import money_words
    g = rg.load(session, user, gid)
    d = rg.view(session, g)
    live = [v for v in d['members'] if v.status in ('CHO', 'DONG_Y')] or d['members']
    tot = rg._totals([v.snapshot for v in live])
    return render(request, 'bao_gia_gop_in.jinja', g=g, rows=live, tot=tot, customer=session.get(Customer, g.customer_code),
                  words=money_words(tot['chosen']['total']), company=get_settings(), maker=session.get(User, g.created_by), back=f'/bao-gia-gop/{g.id}')


@public_router.get('/bgg/{token}')
def public(request: Request, token: str, session: Session = Depends(get_session)):
    """Khách mở link báo giá gộp: không đăng nhập, chỉ đọc bản chụp phần cho khách của từng máy."""
    g, state, rows = rg.public(session, token)
    ok = state == 'ok'
    shown = [v for v in rows if v.status in ('CHO', 'DONG_Y')] if ok else []
    resp = render(request, 'bao_gia_gop_khach.jinja', g=g if ok else None, rows=shown, tot=rg._totals([v.snapshot for v in shown]) if shown else None,
                  others=len(rows) - len(shown) if ok else 0, customer=session.get(Customer, g.customer_code) if ok else None,
                  state=state, co=rqs.link_settings(), status=200 if ok else 404 if state == 'khong_co' else 410)
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['X-Robots-Tag'] = 'noindex, nofollow'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    return resp
