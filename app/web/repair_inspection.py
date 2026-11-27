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
    # Mẫu K1 15/11/2026: đổi model là dịch vụ của nhóm máy lên trước ngay (không phải lưu), nên gửi kèm nhóm/model có giá của từng dịch vụ.
    groups = {g.id: g.name for g in pb.groups(session, active_only=False)}
    models = [m for m in pb.models(session) if m.active]
    priced = {}
    for sid, gid, mid in session.execute(select(RepairPrice.service_id, RepairPrice.group_id, RepairPrice.model_id)):
        priced.setdefault(sid, set()).add(f'g{gid}' if gid else f'm{mid}')
    model = pb.find_model(session, v['model']) if v['model'] else None
    from .. import ghi_chu_phieu
    from .. import workshop_board as wb
    stock = wb.part_stock(session)
    return render(request, 'kiem_tra_may.jinja', stock=stock, plan=wb.plan(session, row), today=wb._now().date(), t=t, ai_notes=ghi_chu_phieu.notes(session, user, ticket=t), row=row, v=v, main=main, others=others, opts=opts, levels=LEVELS,
                  customer=session.get(Customer, t.customer_code), machine=ri.machine_name(session, t), hq=hq,
                  models=models, groups=groups, priced={k: ' '.join(sorted(x)) for k, x in priced.items()},
                  model_group=groups.get(model.group_id) if model else '', part_rows=parts, waiting=ri.is_waiting(t),
                  users={u.id: u.full_name for u in session.scalars(select(User))}, error=error, error_code=error_code, status=status)


BOARD_ROLES = ('technician', 'dispatcher', 'sales')
BOARD_VIEWS = ('cua-toi', 'tat-ca', 'qua-han', 's1', 's2', 's3', 's4', 's6')


@router.get('/kiem-tra-may')
def listing(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    """Bảng xưởng (TUẤN duyệt mẫu 17/11/2026): thay danh sách "Máy chờ kiểm tra"; mỗi máy một hàng, việc cần làm + ai làm."""
    if not has_role(user, *BOARD_ROLES):
        raise BusinessError('FORBIDDEN', 'Chỉ kho, kỹ thuật, kinh doanh hoặc giám đốc xem Bảng xưởng.', status=403)
    from .. import workshop_board as wb
    from .. import repair_quote_groups as rg
    from ..service_tickets import DISPATCHERS
    view = request.query_params.get('view', '')
    q = (request.query_params.get('q', '') or '').strip()[:80]
    board = wb.build(session, user)
    counts = board['counts']
    if view not in BOARD_VIEWS:
        view = 'cua-toi' if counts['mine'] else 'tat-ca'
    board['rows'] = wb.pick(board['rows'], view, q)
    tabs = [dict(label=label, n=counts[key], href=f'/kiem-tra-may?view={v}', on=view == v)
            for v, key, label in (('cua-toi', 'mine', 'Việc của tôi'), ('tat-ca', 'all', 'Tất cả'), ('qua-han', 'late', 'Quá hạn'))]
    # Báo giá gộp (mẫu duyệt 15/11/2026): khách có từ 2 máy đã áp giá, chưa đồng ý → gợi ý Gộp báo giá.
    gather = []
    if has_role(user, *DISPATCHERS):
        rows = ri.waiting_rows(session)
        for code, g in rg.by_customer(rows).items():
            ready = sum(1 for r in g if r['inspection'] is not None and r['inspection'].applied_at and not r['ticket'].quote_approved_at)
            opened = rg.open_for_customer(session, code) if len(g) > 1 else None
            if ready > 1 or opened:
                c = session.get(Customer, code)
                gather.append(dict(code=code, name=c.name if c else code, ready=ready, opened=opened))
    # Trả máy cả chuyến (TUẤN duyệt mẫu 23/11/2026): khách có từ 2 máy sửa xong chờ giao thợ trả.
    return_groups = []
    if has_role(user, *DISPATCHERS):
        from .. import service_return_trip
        for code, ts in service_return_trip.ready_by_customer(session).items():
            c = session.get(Customer, code)
            return_groups.append(dict(code=code, name=c.name if c else code, n=len(ts), due=sum(t.receivable or 0 for t in ts)))
    return render(request, 'kiem_tra_may_ds.jinja', board=board, tabs=tabs, view=view, q=q, gather=gather, return_groups=return_groups, giao_chuyen=request.query_params.get('giao_chuyen', ''),
                  can_group=has_role(user, *DISPATCHERS))


@router.get('/kiem-tra-may/{ticket_id}')
def inspect_page(request: Request, ticket_id: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _inspect_page(request, session, user, ticket_id)


@router.post('/kiem-tra-may/{ticket_id}')
def inspect_save(request: Request, ticket_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    """TUẤN 17/11/2026: kho chỉ kiểm tra, chọn gói + linh kiện rồi Lưu; báo giá do kinh doanh / giám đốc duyệt.
    Lưu cũng ghi kho kỹ thuật đã nhận máy (nếu máy còn chờ kho nhận và người lưu được nhận), khỏi bấm riêng."""
    user = request.state.user
    try:
        ri.save(session, user, ticket_id, form)
    except BusinessError as exc:
        session.rollback()
        if exc.status in (403, 404):
            session.refresh(user)  # trang lỗi còn đọc quyền người dùng sau khi phiên đóng
            raise
        return _inspect_page(request, session, user, ticket_id, form, exc.message, exc.code, exc.status)
    _confirm_custody(session, user, ticket_id)
    return RedirectResponse(f'/kiem-tra-may?da_kiem_tra={ticket_id}', status_code=303)


def _confirm_custody(session, user, ticket_id):
    from .. import equipment_custody as custody
    from ..models import ServiceTicket
    t = session.get(ServiceTicket, ticket_id)
    row = custody.pending(session, t.serial) if t is not None and t.serial else None
    if row is None or row.ticket_id != t.id or row.location != 'KHO_KY_THUAT' or not custody.can_confirm(user, row):
        return
    try:
        custody.confirm(session, user, row.id, row.version)
    except BusinessError:
        session.rollback()  # kết quả kiểm tra đã lưu; kho nhận máy bấm riêng như cũ


def _repairers(session):
    from .. import service_workshop
    return [dict(id=u.id, name=u.full_name, load=n) for u, n in service_workshop.repairers(session)]


def _stale(v, row, pv) -> bool:
    """Lần đang chờ không còn đúng bản khách hiện tại: duyệt lại giá sau lần đó, hoặc tổng tiền phiếu đã đổi."""
    if row.applied_at and v.created_at < row.applied_at:
        return True
    return pv['ready'] and (v.snapshot.get('chosen') or {}).get('total') != pv['total']


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
    pv = rqs.preview(session, t, row, q if not picks else None)
    send = None
    if open_v is not None:
        text, source = rqs.suggested_text(session, open_v)
        send = dict(v=open_v, link=rqs.link(open_v), text=text, source=source, ai=rqs.ai_text(session, open_v),
                    late=rqs.overdue(open_v), days=rqs.waiting_days(open_v),
                    stale=_stale(open_v, row, pv))
    from .. import repair_quote_groups as rg
    group = rg.for_ticket(session, t.id)
    siblings = sum(1 for r in rg.candidates(session, t.customer_code) if r['ready']) if has_role(user, 'dispatcher') else 0
    from .. import service_tickets as _st
    return dict(rq=dict(row=row, q=q, finance=finance, levels=LEVELS, director='director' in user.roles, quoter=_st.can_quote(user), group=group, siblings=siblings,
                        current=bool(row.applied_at), versions=vers, send=send, next_no=(vers[0].no + 1) if vers else 1, lead_days=rqs.LEAD_DAYS,
                        can_send=_st.can_quote(user) and pv['ready'] and (open_v is None or send['stale']), pv=pv,
                        linked=bool(linked_chat(session, t.customer_code)), via=rqs.VIA, reasons=rqs.REASONS,
                        statuses=rqs.STATUSES, sent_via=rqs.SENT_VIA, valid_days=rqs.VALID_DAYS,
                        repairers=_repairers(session) if _st.can_quote(user) else [], assignee=t.assignee_id,
                        previous=rqs.agreed_before(session, t.id) if send else None),
                rq_warranty=row.warranty_months if row.applied_at else None)


@router.post('/kiem-tra-may/{ticket_id}/ap-gia')
def apply_quote(request: Request, ticket_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    row = ri.inspection(session, ticket_id)
    choices = {i.service_id: str(form.get(f'pa_{i.service_id}', '1')) == '2' for i in (row.items if row else [])}
    notes = {i.service_id: str(form.get(f'mo_ta_{i.service_id}', '') or '') for i in (row.items if row else [])}
    prices = {i.service_id: str(form.get(f'gia_{i.service_id}', '') or '') for i in (row.items if row else []) if f'gia_{i.service_id}' in form}
    reasons = {i.service_id: str(form.get(f'ly_do_gia_{i.service_id}', '') or '') for i in (row.items if row else [])}
    version = str(form.get('version', '') or '')
    try:
        # Bảo hành riêng + giá chốt gõ gồm VAT (TUẤN duyệt 27/11/2026); form cũ không có ô thì giữ nguyên.
        warranty = str(form.get('bao_hanh', '') or '') if 'bao_hanh' in form else None
        gross = str(form.get('gia_gom_vat', '') or '') == '1'
        ri.apply(session, user, ticket_id, int(version) if version.isdigit() else None, choices, notes, prices, reasons,
                 warranty, gross)
    except BusinessError as exc:
        session.rollback()
        if exc.status in (403, 404):
            session.refresh(user)  # trang lỗi còn đọc quyền người dùng sau khi phiên đóng
            raise
        from .services import _detail
        return _detail(request, session, user, ticket_id, form={k: str(v) for k, v in form.items() if k not in ('csrf',)}, failed_action='rq-apply', error=exc.message, error_code=exc.code, status=exc.status)
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
    lead = str(form.get('du_kien', '') or '')
    days = int(lead) if lead.isdigit() and int(lead) in rqs.LEAD_DAYS else None
    try:
        v = rqs.create(session, user, ticket_id, int(version) if version.isdigit() else None, days=days)
    except BusinessError as exc:
        return _ticket_error(request, session, user, ticket_id, exc)
    return RedirectResponse(f'/dich-vu/{ticket_id}?bao_gia_lan={v.no}#gui-khach', status_code=303)


QUOTE_ACTIONS = ('gui-zalo', 'da-gui', 'thu-hoi', 'dong-y', 'chua-dong-y', 'giu-lan-truoc')


@router.post('/kiem-tra-may/{ticket_id}/in-bao-gia')
def quote_print_now(request: Request, ticket_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    """Nút In báo giá trên đầu phiếu (TUẤN duyệt 20/11/2026): lần đang chờ còn đúng thì in lần đó, không thì lưu lần mới rồi in."""
    from .. import repair_quotes as rqs
    from ..models import ServiceTicket
    user = request.state.user
    try:
        rqs.require(user)
        t = session.get(ServiceTicket, ticket_id)
        row = ri.inspection(session, ticket_id) if t is not None else None
        v = rqs.open_version(session, ticket_id) if row is not None else None
        if v is not None and v.group_id:  # máy trong báo giá gộp: in bản gộp của cả nhóm
            return RedirectResponse(f'/bao-gia-gop/{v.group_id}/in', status_code=303)
        if v is None or _stale(v, row, rqs.preview(session, t, row)):
            version = str(form.get('version', '') or '')
            v = rqs.create(session, user, ticket_id, int(version) if version.isdigit() else None)
    except BusinessError as exc:
        return _ticket_error(request, session, user, ticket_id, exc)
    return RedirectResponse(f'/kiem-tra-may/bao-gia/{v.id}/in', status_code=303)


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
                      str(form.get('via', '')), str(form.get('person', '')), day, str(form.get('note', '') or ''),
                      int(form.get('repairer')) if str(form.get('repairer', '')).isdigit() else None)
        elif action == 'giu-lan-truoc':
            rqs.keep_previous(session, user, vid, str(form.get('note', '') or ''))
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
                  words=money_words(v.snapshot['chosen']['total']), company=get_settings(), default_terms=rqs.DEFAULT_TERMS, bh_text=rqs.warranty_text(v.snapshot), maker=session.get(User, v.created_by), back=f'/dich-vu/{t.id}#gui-khach')


@router.get('/kiem-tra-may/{ticket_id:int}/in')
def inspection_print(request: Request, ticket_id: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    """TUẤN 16/11/2026: in kết quả kiểm tra NỘI BỘ (A4 cho xưởng/thợ): gói, cấp, lý do đổi cấp, việc cần làm, linh kiện. Không in giá."""
    t = ri.load_ticket(session, user, ticket_id)
    row = ri.inspection(session, t.id)
    if row is None or not row.items:
        raise BusinessError('NO_INSPECTION', 'Thợ chưa ghi kết quả kiểm tra máy.', status=404)
    q = ri.quote(session, t, row)
    names = {u.id: u.full_name for u in session.scalars(select(User).where(User.id.in_({row.inspected_by, row.applied_by} - {None})))}
    return render(request, 'kiem_tra_may_in.jinja', t=t, row=row, q=q, customer=session.get(Customer, t.customer_code),
                  machine=ri.machine_name(session, t), inspector=names.get(row.inspected_by, ''), applier=names.get(row.applied_by, ''),
                  levels=LEVELS, company=get_settings(), back=f'/dich-vu/{t.id}')


public_router = APIRouter(include_in_schema=False)


def _quote_words(v, state) -> str:
    from ..money_words import money_words
    if v is None or state != 'ok':
        return ''
    return money_words(int((v.snapshot.get('chosen') or {}).get('total') or 0))


@public_router.get('/bg/{token}')
def quote_public(request: Request, token: str, session: Session = Depends(get_session)):
    """Khách mở link báo giá: không cần đăng nhập, chỉ đọc bản chụp phần cho khách (mẫu M6)."""
    from .. import repair_quotes as rqs
    v, state = rqs.public(session, token)
    resp = render(request, 'bao_gia_sua_khach.jinja', v=v if state == 'ok' else None, s=v.snapshot if v is not None and state == 'ok' else None,
                  state=state, co=rqs.link_settings(), words=_quote_words(v, state), default_terms=rqs.DEFAULT_TERMS, bh_text=rqs.warranty_text(v.snapshot) if v is not None and state == 'ok' else '', status=200 if state == 'ok' else 404 if state == 'khong_co' else 410)
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['X-Robots-Tag'] = 'noindex, nofollow'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    return resp
