"""Báo giá hợp đồng sửa chữa (TUẤN duyệt mẫu H1–H3 bản 3, 14/11/2026): Dịch vụ › Báo giá hợp đồng."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import contract_quotes as cq
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, User
from . import checked_form, paging, picked, render, web_user

router = APIRouter(include_in_schema=False)
public_router = APIRouter(include_in_schema=False)


def _form_page(request, session, user, d, q=None, error=None, status=200, preview=False):
    customer = session.get(Customer, d['khach']) if d['khach'] else None
    from .. import price_book as pb
    return render(request, 'bao_gia_hop_dong_moi.jinja', d=d, q=q, customer=customer, rows=cq.catalog(session, d),
                  levels=cq.LEVELS, level_labels=cq.LEVEL_LABELS, level_short=cq.LEVEL_SHORT, valid_choices=cq.VALID_CHOICES,
                  fees=cq.fee_services(session), director=cq.is_director(user), default_payment=cq.DEFAULT_PAYMENT,
                  model_names=[m.name for m in pb.models(session) if m.active], with_vat=pb.with_vat, discounted=cq.discounted,
                  max_models=cq.MAX_MODELS, preview=preview,
                  error=error.message if error else None, error_code=error.code if error else '', status=status)


@router.get('/bao-gia-hop-dong')
def listing(request: Request, view: str = 'cho', q: str = '', trang: str = '', so: str = '', user: User = Depends(web_user),
            session: Session = Depends(get_session)):
    cq.require(user)
    view = view if view in cq.VIEWS else 'cho'
    rows = cq.rows(session, view, q[:200])
    counts = cq.counts(session)
    query = dict(view=view, q=q[:200])
    page, shown = paging.slice_rows(rows, trang, so, query)
    link = paging.linker('/bao-gia-hop-dong', query)
    names = {c.code: c.name for c in session.query(Customer).filter(Customer.code.in_({r.customer_code for r in shown}))} if shown else {}
    return render(request, 'bao_gia_hop_dong_ds.jinja', rows=shown, p=page, q=q[:200], view=view, names=names, state=cq.state,
                  statuses=cq.STATUSES, tabs=[dict(label=label, n=counts[k], on=k == view, href=link(view=k)) for k, label in cq.VIEWS.items()])


@router.get('/bao-gia-hop-dong/tim-khach')
def pick_customer(request: Request, q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    from .. import customers as customer_svc
    cq.require(user)
    rows = [c for c in customer_svc.search(session, q[:200]) if c.status == 'DANG_DUNG'] if q.strip() else []
    return render(request, 'dich_vu_chon_khach.jinja', rows=rows, customer_cards=picked.customer_cards(session, user, rows))


@router.get('/bao-gia-hop-dong/moi')
def new(request: Request, khach: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    cq.require(user)
    d = cq.parse({'khach': khach})
    return _form_page(request, session, user, d)


@router.post('/bao-gia-hop-dong/moi')
def create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    cq.require(user)
    d = cq.parse(form)
    if str(form.get('do', '')) != 'luu':
        return _form_page(request, session, user, d, preview=True)
    try:
        q = cq.create(session, user, d)
    except BusinessError as exc:
        session.rollback()
        if exc.status == 403:
            raise
        return _form_page(request, session, user, d, error=exc, status=exc.status)
    return RedirectResponse(f'/bao-gia-hop-dong/{q.id}?da_luu=1', status_code=303)


def _detail(request, session, user, qid, error=None, status=200, text=None):
    from .. import tin_gui_khach as tg
    q = cq.load(session, user, qid)
    customer = session.get(Customer, q.customer_code)
    flag = request.query_params
    return render(request, 'bao_gia_hop_dong_chi_tiet.jinja', q=q, customer=customer, st=cq.state(q), statuses=cq.STATUSES,
                  link=cq.link(q), text=q.text if text is None else text, linked=bool(tg.linked_chat(session, q.customer_code)),
                  levels=cq.LEVELS, level_short=cq.LEVEL_SHORT, level_hint=cq.LEVEL_HINT, director=cq.is_director(user), sent_via=cq.SENT_VIA,
                  users={u.id: u.full_name for u in session.query(User).filter(User.id.in_([x for x in (q.created_by, q.signed_by, q.sent_by, q.stopped_by) if x]))},
                  today=cq._today().isoformat(), done=flag.get('xong', ''), saved=flag.get('da_luu') == '1',
                  error=error.message if error else None, error_code=error.code if error else '', status=status)


@router.get('/bao-gia-hop-dong/{qid}')
def detail(request: Request, qid: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _detail(request, session, user, qid)


@router.get('/bao-gia-hop-dong/{qid}/sua')
def edit(request: Request, qid: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    q = cq.load(session, user, qid)
    if q.status != 'CHO':
        return RedirectResponse(f'/bao-gia-hop-dong/{qid}', status_code=303)
    return _form_page(request, session, user, cq.from_quote(q), q=q)


@router.post('/bao-gia-hop-dong/{qid}/sua')
def save_edit(request: Request, qid: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    q = cq.load(session, user, qid)
    d = cq.parse(form)
    d['khach'] = q.customer_code
    if str(form.get('do', '')) != 'luu':
        return _form_page(request, session, user, d, q=q, preview=True)
    version = str(form.get('version', '') or '')
    try:
        cq.update(session, user, qid, int(version) if version.isdigit() else None, d)
    except BusinessError as exc:
        session.rollback()
        if exc.status in (403, 404):
            raise
        return _form_page(request, session, user, d, q=session.get(type(q), qid), error=exc, status=exc.status)
    return RedirectResponse(f'/bao-gia-hop-dong/{qid}?da_luu=1', status_code=303)


ACTIONS = ('gia', 'da-gui', 'gui-zalo', 'da-ky', 'ngung')


@router.post('/bao-gia-hop-dong/{qid}/{action}')
def act(request: Request, qid: int, action: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    if action not in ACTIONS:
        raise BusinessError('NOT_FOUND', 'Không có thao tác này.', status=404)
    version = str(form.get('version', '') or '')
    version = int(version) if version.isdigit() else None
    text = form.get('text')
    try:
        if action == 'gia':
            cq.set_cells(session, user, qid, version, form)
        elif action == 'da-gui':
            cq.mark_sent(session, user, qid, 'COPY', text)
        elif action == 'gui-zalo':
            cq.send_bot(session, user, qid, text)
        elif action == 'da-ky':
            try:
                day = date.fromisoformat(str(form.get('signed_on', '') or ''))
            except ValueError:
                day = None
            cq.sign(session, user, qid, version, day, str(form.get('note', '') or ''))
        elif action == 'ngung':
            cq.stop(session, user, qid, version, str(form.get('reason', '') or ''))
    except BusinessError as exc:
        if exc.status in (403, 404):
            raise
        session.rollback()
        return _detail(request, session, user, qid, error=exc, status=exc.status, text=str(text) if text is not None else None)
    return RedirectResponse(f'/bao-gia-hop-dong/{qid}?xong={action}', status_code=303)


@router.get('/bao-gia-hop-dong/{qid}/in')
def print_page(request: Request, qid: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    q = cq.load(session, user, qid)
    return render(request, 'bao_gia_hop_dong_in.jinja', **_print_ctx(session, q), back=f'/bao-gia-hop-dong/{q.id}')


def _print_ctx(session, q):
    return dict(q=q, s=q.snapshot, customer=session.get(Customer, q.customer_code), company=get_settings(), levels=q.levels,
                level_short=cq.LEVEL_SHORT, level_hint=cq.LEVEL_HINT, maker=session.get(User, q.created_by), st=cq.state(q))


@public_router.get('/bgh/{token}')
def quote_public(request: Request, token: str, session: Session = Depends(get_session)):
    """Khách mở link báo giá hợp đồng: không đăng nhập, chỉ đọc bản chụp phần cho khách."""
    q, state = cq.public(session, token)
    show = state in ('ok', 'da_ky')
    ctx = _print_ctx(session, q) if show else {}
    resp = render(request, 'bao_gia_hop_dong_khach.jinja', state=state, co=get_settings(), **ctx,
                  status=200 if show else 404 if state == 'khong_co' else 410)
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['X-Robots-Tag'] = 'noindex, nofollow'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    return resp
