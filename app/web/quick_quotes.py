"""Báo giá nhanh (TUẤN duyệt mẫu B1 14/11/2026): Dịch vụ › Hỏi giá sửa. Khách hỏi giá khi chưa gửi máy."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import price_book as pb
from .. import quick_quotes as qq
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, ServiceTicket, User
from ..security import has_role
from ..service_counter import ROLES as COUNTER_ROLES
from ..service_tickets import CREATORS
from . import checked_form, nav, paging, picked, render, web_user

router = APIRouter(include_in_schema=False)


def _params(src) -> dict:
    get = src.get
    return dict(model=str(get('model', '') or '')[:160], ht=[str(v)[:200] for v in src.getlist('ht')][:qq.MAX_SYMPTOMS],
                noi=str(get('noi', '') or 'CUA_HANG'), khach=str(get('khach', '') or '')[:16], lien_he=str(get('lien_he', '') or '')[:200],
                ghi_chu=str(get('ghi_chu', '') or '')[:2000], text_edited='1' if str(get('text_edited', '')) == '1' else '',
                text=str(get('text', '') or '')[:4000])


def _preview(session, d):
    result = qq.compute(session, d['model'], d['ht'], d['noi'])
    return dict(r=result, text=qq.zalo_text(result) if result['symptoms'] else '',
                symptom_chips=qq.symptom_names(session, result['model']), range_text=qq.range_text, levels_text=qq.levels_text, money=qq.money)


def _can_convert(user):
    return dict(nhan=has_role(user, *COUNTER_ROLES), hen=has_role(user, *CREATORS) and nav.allowed(user, '/dich-vu'))  # kinh doanh chỉ nhận Mực định kỳ


def _compose(request, session, user, d, error='', error_code='', status=200):
    customer = session.get(Customer, d['khach']) if d['khach'] else None
    ctx = _preview(session, d)
    if d['text_edited']:
        ctx['text'] = d['text']
    linked = bool(customer and qq.linked_chat(session, customer.code))
    return render(request, 'hoi_gia_moi.jinja', d=d, customer=customer, linked=linked, places=qq.PLACES,
                  model_names=[m.name for m in pb.models(session) if m.active], conv=_can_convert(user),
                  error=error, error_code=error_code, status=status, **ctx)


@router.get('/hoi-gia')
def listing(request: Request, view: str = 'cho', q: str = '', trang: str = '', so: str = '', user: User = Depends(web_user),
            session: Session = Depends(get_session)):
    qq.require(user)
    view = view if view in qq.VIEWS else 'cho'
    rows = qq.rows(session, user, view, q[:200])
    counts = qq.counts(session)
    query = dict(view=view, q=q[:200])
    page, shown = paging.slice_rows(rows, trang, so, query)
    link = paging.linker('/hoi-gia', query)
    tickets = {t.id: t for t in session.query(ServiceTicket).filter(ServiceTicket.id.in_([r.ticket_id for r in shown if r.ticket_id]))} \
        if any(r.ticket_id for r in shown) else {}
    return render(request, 'hoi_gia_ds.jinja', rows=shown, p=page, q=q[:200], view=view, names=qq.customer_names(session, shown),
                  statuses=qq.STATUSES, tickets=tickets, range_text=qq.range_text,
                  tabs=[dict(label=label, n=counts[k], on=k == view, href=link(view=k)) for k, (label, _) in qq.VIEWS.items()])


@router.get('/hoi-gia/moi')
def new(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    qq.require(user)
    return _compose(request, session, user, _params(request.query_params))


@router.get('/hoi-gia/xem-truoc')
def preview(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    qq.require(user)
    return render(request, 'hoi_gia_ket_qua.jinja', d=_params(request.query_params), **_preview(session, _params(request.query_params)))


@router.get('/hoi-gia/tim-khach')
def pick_customer(request: Request, q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    from .. import customers as customer_svc
    qq.require(user)
    rows = [c for c in customer_svc.search(session, q[:200]) if c.status == 'DANG_DUNG'] if q.strip() else []
    return render(request, 'dich_vu_chon_khach.jinja', rows=rows, customer_cards=picked.customer_cards(session, user, rows))


def _next(row, nxt):
    if nxt == 'nhan':
        return f'/quay-sua/nhan?hg={row.id}'
    if nxt == 'hen':
        return f'/dich-vu/moi?hg={row.id}'
    return f'/hoi-gia/{row.id}?da_luu=1' + ('&chep=1' if nxt == 'copy' else '')


@router.post('/hoi-gia/luu')
def create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    d = _params(form)
    nxt = str(form.get('next', ''))
    try:
        row = qq.create(session, user, model_name=d['model'], symptoms=d['ht'], place=d['noi'], customer_code=d['khach'],
                        contact=d['lien_he'], note=d['ghi_chu'], text=d['text'] if d['text_edited'] else None)
        if nxt == 'copy':
            row = qq.mark_sent(session, user, row.id)
        elif nxt == 'bot':
            try:
                row = qq.send_bot(session, user, row.id)
            except BusinessError as exc:
                session.rollback()
                return RedirectResponse(f'/hoi-gia/{row.id}?loi_gui={exc.code}', status_code=303)
    except BusinessError as exc:
        session.rollback()
        return _compose(request, session, user, d, exc.message, exc.code, exc.status)
    return RedirectResponse(_next(row, nxt), status_code=303)


def _detail(request, session, user, qid, error='', error_code='', status=200, text=None):
    row = qq.load(session, user, qid)
    customer = session.get(Customer, row.customer_code) if row.customer_code else None
    ticket = session.get(ServiceTicket, row.ticket_id) if row.ticket_id else None
    ticket_href = None
    if ticket:
        ticket_href = f'/quay-sua/{ticket.id}' if ticket.counter_intake and nav.allowed(user, '/quay-sua') else nav.ticket_url(user, ticket.id)
    err = request.query_params.get('loi_gui', '')
    if err and not error:
        error, error_code = ('Khách chưa chọn hoặc chưa gắn Zalo công ty, chưa gửi được. Hãy Sao chép rồi gửi bằng Zalo cá nhân.'
                             if err == 'NOT_LINKED' else 'Zalo chưa gửi được. Báo giá đã lưu, hãy thử lại hoặc Sao chép.'), err
    return render(request, 'hoi_gia_chi_tiet.jinja', row=row, customer=customer, ticket=ticket, ticket_href=ticket_href, linked=bool(qq.linked_chat(session, row.customer_code)),
                  statuses=qq.STATUSES, places=qq.PLACES, range_text=qq.range_text, levels_text=qq.levels_text, money=qq.money,
                  open=row.status in qq.OPEN, conv=_can_convert(user), text=row.text if text is None else text,
                  saved=request.query_params.get('da_luu') == '1', copy=request.query_params.get('chep') == '1',
                  error=error, error_code=error_code, status=status)


@router.get('/hoi-gia/{qid}')
def detail(request: Request, qid: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _detail(request, session, user, qid)


ACTIONS = ('luu-tin', 'da-gui', 'gui-zalo', 'khong-sua', 'mo-lai', 'nhan', 'hen')


@router.post('/hoi-gia/{qid}/{action}')
def act(request: Request, qid: int, action: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    if action not in ACTIONS:
        raise BusinessError('NOT_FOUND', 'Không có thao tác này.', status=404)
    text = form.get('text')
    try:
        if action == 'luu-tin':
            qq.save_text(session, user, qid, text)
            return RedirectResponse(f'/hoi-gia/{qid}?da_luu=1', status_code=303)
        if action == 'da-gui':
            qq.mark_sent(session, user, qid, text)
        elif action == 'gui-zalo':
            qq.send_bot(session, user, qid, text)
        elif action == 'khong-sua':
            qq.decline(session, user, qid, str(form.get('note', '')))
        elif action == 'mo-lai':
            qq.reopen(session, user, qid)
        elif action in ('nhan', 'hen'):
            if text is not None:
                qq.save_text(session, user, qid, text)
            return RedirectResponse(f'/quay-sua/nhan?hg={qid}' if action == 'nhan' else f'/dich-vu/moi?hg={qid}', status_code=303)
    except BusinessError as exc:
        session.rollback()
        if exc.status in (403, 404):
            raise
        return _detail(request, session, user, qid, exc.message, exc.code, exc.status, text=str(text) if text is not None else None)
    return RedirectResponse(f'/hoi-gia/{qid}', status_code=303)
