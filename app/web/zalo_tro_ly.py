"""Màn Trợ lý Zalo (TUẤN duyệt 05/11/2026): gắn Zalo, bật tắt, nhật ký (giám đốc); Đơn nháp từ Zalo (kinh doanh)."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import zalo_agent as agent
from .. import zalo_bot
from .. import zalo_phieu_nhap as pn
from ..db import get_session
from ..models import Customer, User, ZaloChat
from . import checked_form, render, web_user
from .paging import build

router = APIRouter()
TABS = {'gan': 'Đã gắn', 'nhat-ky': 'Nhật ký'}


def _int(raw):
    raw = str(raw or '').strip()
    return int(raw) if raw.isdecimal() and len(raw) < 10 else None


@router.get('/zalo/tro-ly')
def page(request: Request, tab: str = 'gan', ngay: str = '', trang: str = '', so: str = '', luu: str = '',
         user: User = Depends(web_user), session: Session = Depends(get_session)):
    agent._director(user)
    tab = tab if tab in TABS else 'gan'
    try:
        day = date.fromisoformat(ngay) if ngay else agent._now().date()
    except ValueError:
        day = agent._now().date()
    cfg = agent.config(session)
    chats = agent.linked_chats(session)
    staff = {u.id: u for u in session.scalars(select(User).where(User.id.in_([c.user_id for c in chats if c.user_id])))}
    custs = {c.code: c for c in session.scalars(select(Customer).where(Customer.code.in_([c.customer_code for c in chats if c.customer_code])))}
    total, stats, _ = agent.logs(session, day, 0, 1)
    p = build(trang, so, total, {'tab': tab, 'ngay': ngay})
    rows = agent.logs(session, day, p['offset'], p['size'])[2] if tab == 'nhat-ky' else []
    used = {c.id: agent._used_today(session, c.id) for c in chats}
    return render(request, 'zalo_tro_ly.jinja', tab=tab, tabs=TABS, cfg=cfg, chats=chats, staff=staff, custs=custs, used=used,
                  label=agent.can_do_label, total=total, stats=stats, rows=rows, p=p, day=day, ngay=ngay,
                  names=agent.sender_names(session, rows), results=agent.RESULTS, askers=agent.ASKERS,
                  draft_users=agent.draft_users(session), luu=luu, has_key=bool(agent.get_settings().openai_api_key.strip()),
                  model=agent.get_settings().zalo_ai_model, per_person=agent.get_settings().zalo_ai_per_person,
                  per_day=agent.get_settings().zalo_ai_per_day, engines=agent.ENGINES, engine_short=agent.ENGINE_SHORT,
                  z2=agent.z2_status(session), ticket_waiting=pn.waiting(session))


@router.post('/zalo/tro-ly/cau-hinh')
def save(form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    agent.save_config(session, user, str(form.get('bat') or '') == '1', _int(form.get('nguoi_nhan')),
                      str(form.get('may') or '').strip() or None,
                      (str(form.get('phieu_nhap')) == '1') if form.get('phieu_nhap') is not None else None)
    return RedirectResponse('/zalo/tro-ly?luu=1', status_code=303)


@router.post('/zalo/tin-nhan/{chat_pk}/gan')
def link(chat_pk: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    agent.link(session, user, chat_pk, str(form.get('loai') or ''), _int(form.get('nhan_vien')), str(form.get('khach') or ''))
    back = '/zalo/tro-ly' if str(form.get('ve') or '') == 'tro-ly' else f'/zalo/tin-nhan?chat={chat_pk}&gan=1'
    return RedirectResponse(back, status_code=303)


@router.get('/don-nhap-zalo')
def drafts(request: Request, view: str = 'cho', trang: str = '', so: str = '', bo: str = '',
           user: User = Depends(web_user), session: Session = Depends(get_session)):
    from ..sales_orders import check_writer
    check_writer(user)
    view = view if view in agent.DRAFT_VIEWS else 'cho'
    counts = agent.draft_counts(session)
    p = build(trang, so, counts[view], {'view': view})
    orders, log_rows = agent.drafts(session, user, view, p['offset'], p['size'])
    custs = {c.code: c for c in session.scalars(select(Customer).where(Customer.code.in_({o.customer_code for o in orders})))}
    return render(request, 'don_nhap_zalo.jinja', view=view, views=agent.DRAFT_VIEWS, counts=counts, orders=orders, logs=log_rows,
                  custs=custs, p=p, bo=bo, askers=agent.ASKERS,
                  view_total=agent.draft_total(session, view))


@router.post('/don-nhap-zalo/{order_id}/bo')
def drop(order_id: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    agent.drop_draft(session, user, order_id, _int(form.get('version')) or 0)
    return RedirectResponse('/don-nhap-zalo?bo=1', status_code=303)


# ---- Đợt 2 (TUẤN duyệt mẫu 06/11/2026): phiếu dịch vụ / mực nháp từ Zalo, điều phối duyệt ----

@router.get('/phieu-nhap-zalo')
def ticket_drafts(request: Request, view: str = 'cho', trang: str = '', so: str = '', bo: str = '',
                  user: User = Depends(web_user), session: Session = Depends(get_session)):
    pn.check_reader(user)
    view = view if view in pn.VIEWS else 'cho'
    counts = pn.counts(session)
    p = build(trang, so, counts[view], {'view': view})
    rows = pn.rows(session, view, p['offset'], p['size'])
    return render(request, 'phieu_nhap_zalo.jinja', view=view, views=pn.VIEWS, counts=counts, rows=rows, p=p, bo=bo,
                  **_draft_names(session, rows), kinds=pn.KINDS, want=pn.want_label, reasons=pn.DROP_REASONS)


def _draft_names(session, rows):
    from ..models import ServiceTicket
    custs = dict(session.execute(select(Customer.code, Customer.name).where(Customer.code.in_({r.customer_code for r in rows}))).all()) if rows else {}
    ids = {r.sender_id for r in rows if r.sender_id} | {r.decided_by for r in rows if r.decided_by}
    users = dict(session.execute(select(User.id, User.full_name).where(User.id.in_(ids))).all()) if ids else {}
    tids = {r.ticket_id for r in rows if r.ticket_id}
    tickets = dict(session.execute(select(ServiceTicket.id, ServiceTicket.number).where(ServiceTicket.id.in_(tids))).all()) if tids else {}
    return dict(custs=custs, users=users, tickets=tickets)


@router.get('/phieu-nhap-zalo/{draft_id}')
def ticket_draft(request: Request, draft_id: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    row = pn.load(session, user, draft_id)
    customer = session.get(Customer, row.customer_code)
    return render(request, 'phieu_nhap_zalo_xem.jinja', row=row, customer=customer, **_draft_names(session, [row]),
                  kinds=pn.KINDS, priorities=pn.PRIORITIES, sources=pn.SOURCES, want=pn.want_label, reasons=pn.DROP_REASONS)


@router.post('/phieu-nhap-zalo/{draft_id}/bo')
def ticket_draft_drop(draft_id: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    pn.drop(session, user, draft_id, _int(form.get('version')) or 0, str(form.get('ly_do') or ''), str(form.get('ghi_chu') or ''))
    return RedirectResponse('/phieu-nhap-zalo?bo=1', status_code=303)
