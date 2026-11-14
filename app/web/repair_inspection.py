"""Thợ kiểm tra máy (mẫu M3) + điều phối áp báo giá chính thức (mẫu M4), TUẤN duyệt 14/11/2026, đợt 3."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from .. import price_book as pb
from .. import repair_inspection as ri
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, Product, QuickQuote, RepairPrice, User
from ..security import has_role
from . import checked_form, render, web_user

router = APIRouter(include_in_schema=False)
LEVELS = {1: 'Cấp 1 · Nhẹ', 2: 'Cấp 2 · Trung bình', 3: 'Cấp 3 · Nặng'}


def _services(session, model_name, chosen_ids):
    """Dịch vụ gói: có giá cho nhóm/model của máy lên trước, dịch vụ khác gập lại."""
    model = pb.find_model(session, model_name) if model_name else None
    rows = pb.services(session, 'GOI', active_only=True)
    if model is None:
        return rows, []
    ids = set(session.scalars(select(RepairPrice.service_id).where(or_(RepairPrice.group_id == model.group_id,
                                                                     RepairPrice.model_id == model.id))))
    main = [s for s in rows if s.id in ids or s.id in chosen_ids]
    return main, [s for s in rows if s not in main]


def _form_view(session, t, row, form=None):
    """Giá trị hiển thị cho form: lấy form vừa gửi (lỗi) hoặc kết quả đã lưu."""
    items = {i.service_id: i for i in row.items} if row else {}
    if form is not None:
        model = str(form.get('model', '') or '')
        chosen = [int(v) for v in form.getlist('dv') if str(v).isdigit()]
        get = lambda sid: dict(opts=[str(v) for v in form.getlist(f'opt_{sid}')], cap=str(form.get(f'cap_{sid}', '') or ''),
                               reason=str(form.get(f'ly_do_{sid}', '') or ''), genuine=str(form.get(f'ch_{sid}', '')) == '1',
                               parts=[dict(code=c, qty=q) for c, q in zip(form.getlist(f'lk_{sid}'), form.getlist(f'sl_{sid}')) if str(c).strip()])
        note = str(form.get('note', '') or '')
    else:
        guess = ri.guess_model(session, ri.machine_name(session, t))
        model = row.model_name if row else (guess.name if guess else ri.machine_name(session, t))
        chosen = list(items)

        def get(sid):
            i = items.get(sid)
            if i is None:
                return dict(opts=[], cap='', reason='', genuine=False, parts=[])
            names = set(i.options)
            return dict(opts=[str(o.id) for o in pb.options(session, sid) if o.name in names], cap=str(i.level), reason=i.level_reason,
                        genuine=i.genuine, parts=i.parts)
        note = row.note if row else ''
    return dict(model=model, chosen=chosen, item=get, note=note)


def _inspect_page(request, session, user, ticket_id, form=None, error='', error_code='', status=200):
    t = ri.load_ticket(session, user, ticket_id)
    row = ri.inspection(session, t.id)
    v = _form_view(session, t, row, form)
    main, others = _services(session, v['model'], v['chosen'])
    opts = {s.id: pb.options(session, s.id) for s in main + others}
    hq = session.scalars(select(QuickQuote).where(QuickQuote.ticket_id == t.id).order_by(QuickQuote.id.desc()).limit(1)).first()
    parts = list(session.execute(select(Product.code, Product.name).where(Product.active.is_(True)).order_by(Product.name)).all())
    return render(request, 'kiem_tra_may.jinja', t=t, row=row, v=v, main=main, others=others, opts=opts, levels=LEVELS,
                  customer=session.get(Customer, t.customer_code), machine=ri.machine_name(session, t), hq=hq,
                  model_names=[m.name for m in pb.models(session) if m.active], part_rows=parts, waiting=ri.is_waiting(t),
                  users={u.id: u.full_name for u in session.scalars(select(User))}, error=error, error_code=error_code, status=status)


@router.get('/kiem-tra-may')
def listing(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    ri.require_inspect(user)
    rows = ri.waiting_rows(session)
    customers = {c.code: c for c in session.scalars(select(Customer).where(Customer.code.in_({r['ticket'].customer_code for r in rows})))} if rows else {}
    return render(request, 'kiem_tra_may_ds.jinja', rows=rows, customers=customers, todo=sum(1 for r in rows if r['todo']))


@router.get('/kiem-tra-may/{ticket_id}')
def inspect_page(request: Request, ticket_id: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _inspect_page(request, session, user, ticket_id)


@router.post('/kiem-tra-may/{ticket_id}')
def inspect_save(request: Request, ticket_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    try:
        ri.save(session, user, ticket_id, form)
    except BusinessError as exc:
        session.rollback()
        if exc.status in (403, 404):
            raise
        return _inspect_page(request, session, user, ticket_id, form, exc.message, exc.code, exc.status)
    back = '/kiem-tra-may' if not has_role(user, 'dispatcher') else f'/dich-vu/{ticket_id}'
    return RedirectResponse(f'{back}?da_kiem_tra={ticket_id}', status_code=303)


def panel_context(session, user, t, picks=None) -> dict:
    """Khung "Báo giá sửa chữa" trong phiếu (điều phối, máy chờ sửa ở xưởng)."""
    row = ri.inspection(session, t.id)
    if row is None or not row.items:
        return dict(rq=None, rq_warranty=None)
    finance = has_role(user, 'finance')
    q = ri.quote(session, t, row, picks, with_cost=finance)
    from .. import repair_quotes as rqs
    from ..tin_gui_khach import linked_chat
    vers = rqs.versions(session, t.id)
    open_v = next((v for v in vers if v.status == 'CHO'), None)
    send = None
    if open_v is not None:
        text, source = rqs.suggested_text(session, open_v)
        send = dict(v=open_v, link=rqs.link(open_v), text=text, source=source, ai=rqs.ai_text(session, open_v),
                    late=rqs.overdue(open_v), days=rqs.waiting_days(open_v),
                    stale=bool(row.applied_at and open_v.created_at < row.applied_at))
    return dict(rq=dict(row=row, q=q, finance=finance, levels=LEVELS, director='director' in user.roles,
                        current=bool(row.applied_at), versions=vers, send=send, next_no=(vers[0].no + 1) if vers else 1,
                        can_send=bool(row.applied_at) and (open_v is None or send['stale']),
                        linked=bool(linked_chat(session, t.customer_code)), via=rqs.VIA, reasons=rqs.REASONS,
                        statuses=rqs.STATUSES, sent_via=rqs.SENT_VIA, valid_days=rqs.VALID_DAYS),
                rq_warranty=row.warranty_months if row.applied_at else None)


@router.post('/kiem-tra-may/{ticket_id}/ap-gia')
def apply_quote(request: Request, ticket_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    row = ri.inspection(session, ticket_id)
    choices = {i.service_id: str(form.get(f'pa_{i.service_id}', '1')) == '2' for i in (row.items if row else [])}
    notes = {i.service_id: str(form.get(f'mo_ta_{i.service_id}', '') or '') for i in (row.items if row else [])}
    version = str(form.get('version', '') or '')
    try:
        ri.apply(session, user, ticket_id, int(version) if version.isdigit() else None, choices, notes)
    except BusinessError as exc:
        session.rollback()
        if exc.status in (403, 404):
            raise
        from .services import _detail
        return _detail(request, session, user, ticket_id, failed_action='rq-apply', error=exc.message, error_code=exc.code, status=exc.status)
    return RedirectResponse(f'/dich-vu/{ticket_id}?da_ap_gia=1', status_code=303)


# ---------- Đợt 4: gửi khách, khách trả lời (mẫu M5 14/11/2026) ----------

def _ticket_error(request, session, user, ticket_id, exc):
    if exc.status in (403, 404):
        raise exc  # trang lỗi chung; không rollback trước để tài khoản còn nạp được khi vẽ menu
    session.rollback()
    from .services import _detail
    return _detail(request, session, user, ticket_id, failed_action='rq-send', error=exc.message, error_code=exc.code, status=exc.status)


@router.post('/kiem-tra-may/{ticket_id}/gui')
def quote_create(request: Request, ticket_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    from .. import repair_quotes as rqs
    user = request.state.user
    version = str(form.get('version', '') or '')
    try:
        v = rqs.create(session, user, ticket_id, int(version) if version.isdigit() else None)
    except BusinessError as exc:
        return _ticket_error(request, session, user, ticket_id, exc)
    return RedirectResponse(f'/dich-vu/{ticket_id}?bao_gia_lan={v.no}#gui-khach', status_code=303)


QUOTE_ACTIONS = ('gui-zalo', 'da-gui', 'thu-hoi', 'dong-y', 'chua-dong-y')


@router.post('/kiem-tra-may/bao-gia/{vid}/{action}')
def quote_action(request: Request, vid: int, action: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    from datetime import date
    from .. import repair_quotes as rqs
    from ..models import RepairQuoteVersion
    user = request.state.user
    if action not in QUOTE_ACTIONS:
        raise BusinessError('NOT_FOUND', 'Không có thao tác này.', status=404)
    v = session.get(RepairQuoteVersion, vid)
    if v is None:
        raise BusinessError('NOT_FOUND', 'Không có lần báo giá này.', status=404)
    ticket_id = v.ticket_id
    text = form.get('text')
    try:
        if action == 'gui-zalo':
            rqs.send_bot(session, user, vid, text)
        elif action == 'da-gui':
            rqs.mark_sent(session, user, vid, 'COPY', text)
        elif action == 'thu-hoi':
            rqs.revoke(session, user, vid)
        elif action == 'dong-y':
            raw = str(form.get('promised_return_on', '') or '')
            try:
                day = date.fromisoformat(raw)
            except ValueError:
                day = None
            version = str(form.get('version', '') or '')
            rqs.agree(session, user, vid, int(version) if version.isdigit() else None, str(form.get('pa', '')),
                      str(form.get('via', '')), str(form.get('person', '')), day, str(form.get('note', '') or ''))
        elif action == 'chua-dong-y':
            from . import _digits
            wanted = _digits(form.get('wanted_price')) or None
            rqs.not_yet(session, user, vid, str(form.get('reason', '')), wanted, str(form.get('via', '')), str(form.get('note', '') or ''))
    except BusinessError as exc:
        return _ticket_error(request, session, user, ticket_id, exc)
    return RedirectResponse(f'/dich-vu/{ticket_id}?bao_gia_xong={action}#gui-khach', status_code=303)


@router.get('/kiem-tra-may/bao-gia/{vid}/in')
def quote_print(request: Request, vid: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    from .. import repair_quotes as rqs
    from ..models import RepairQuoteVersion, ServiceTicket
    from ..money_words import money_words
    rqs.require(user)
    v = session.get(RepairQuoteVersion, vid)
    if v is None:
        raise BusinessError('NOT_FOUND', 'Không có lần báo giá này.', status=404)
    t = session.get(ServiceTicket, v.ticket_id)
    return render(request, 'bao_gia_sua_in.jinja', v=v, s=v.snapshot, t=t, customer=session.get(Customer, t.customer_code),
                  words=money_words(v.snapshot['chosen']['total']), company=get_settings(), maker=session.get(User, v.created_by), back=f'/dich-vu/{t.id}#gui-khach')


public_router = APIRouter(include_in_schema=False)


@public_router.get('/bg/{token}')
def quote_public(request: Request, token: str, session: Session = Depends(get_session)):
    """Khách mở link báo giá: không cần đăng nhập, chỉ đọc bản chụp phần cho khách (mẫu M6)."""
    from .. import repair_quotes as rqs
    v, state = rqs.public(session, token)
    resp = render(request, 'bao_gia_sua_khach.jinja', v=v if state == 'ok' else None, s=v.snapshot if v is not None and state == 'ok' else None,
                  state=state, co=rqs.link_settings(), status=200 if state == 'ok' else 404 if state == 'khong_co' else 410)
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['X-Robots-Tag'] = 'noindex, nofollow'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    return resp
