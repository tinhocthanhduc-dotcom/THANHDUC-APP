"""Màn Tin gửi khách (Trợ lý Zalo đợt 4, TUẤN duyệt mẫu 06/11/2026): danh sách, soạn, sửa, gửi qua Zalo công ty hoặc sao chép."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import tin_gui_khach as tg
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, User
from . import checked_form, nav, render, web_user
from .paging import build

router = APIRouter()
ACTIONS = ('luu', 'gui-zalo', 'da-gui', 'bo', 'viet-lai', 'dung-ban-moi', 'giu-ban-cu')


def _int(raw):
    raw = str(raw or '').strip()
    return int(raw) if raw.isdecimal() and len(raw) < 12 else None


@router.get('/tin-gui-khach')
def listing(request: Request, view: str = 'cho', loai: str = '', trang: str = '', so: str = '', bo: str = '',
            user: User = Depends(web_user), session: Session = Depends(get_session)):
    tg.check_any(user)
    view = view if view in tg.VIEWS else 'cho'
    loai = loai if loai in tg.KINDS else ''
    counts = tg.counts(session, user)
    p = build(trang, so, tg.total(session, user, view, loai), {'view': view, 'loai': loai})
    rows = tg.rows(session, user, view, loai, p['offset'], p['size'])
    linked = {r.customer_code for r in rows if r.status == 'CHO' and tg.linked_chat(session, r.customer_code)}
    return render(request, 'tin_gui_khach_ds.jinja', view=view, views=tg.VIEWS, loai=loai, kinds=tg.KINDS, short=tg.SHORT, counts=counts,
                  kind_counts=tg.kind_counts(session, user, view), rows=rows, p=p, bo=bo, via=tg.VIA, linked=linked,
                  everyone=tg.is_director(user), **tg.names(session, rows))


@router.get('/tin-gui-khach/moi')
def new(loai: str = '', khach: str = '', bao_gia: str = '', phieu: str = '',
        user: User = Depends(web_user), session: Session = Depends(get_session)):
    """Nút "Soạn tin" trên báo giá, phiếu dịch vụ, công nợ khách và link trong tin nhắc nợ thứ Hai.
    Đã có tin chờ gửi cùng loại cho cùng khách / chứng từ thì mở lại tin đó, không soạn trùng."""
    row = tg.create(session, user, loai, customer_code=khach, ref_id=_int(bao_gia) or _int(phieu))
    return RedirectResponse(f'/tin-gui-khach/{row.id}', status_code=303)


def _page(request, session, user, row, error='', status=200, text=None):
    customer = session.get(Customer, row.customer_code)
    chat = tg.linked_chat(session, row.customer_code) if row.status == 'CHO' else None
    link = tg.ref_link(row)
    if link and row.ref_type == 'service_ticket':
        href = nav.ticket_url(user, row.ref_id)
        link = (href, link[1]) if href else None
    phones = []
    if customer is not None:
        from sqlalchemy import select
        from ..models import CustomerPhone
        phones = list(session.scalars(select(CustomerPhone.phone).where(CustomerPhone.customer_code == customer.code)
                                      .order_by(CustomerPhone.is_contact, CustomerPhone.phone).limit(2)))
    return render(request, 'tin_gui_khach_xem.jinja', row=row, customer=customer, chat=chat, link=link, phones=phones,
                  kinds=tg.KINDS, via=tg.VIA, ai=tg.rewrite_state(session, row), replies=tg.replies(session, row),
                  text=row.text if text is None else text, error=error, status=status, max_text=tg.MAX_TEXT,
                  can_edit=row.status == 'CHO' and tg.can_write(user, row.kind), doc_kinds=tg.DOC_KINDS, **tg.names(session, [row]))


@router.get('/tin-gui-khach/{msg_id}')
def view(request: Request, msg_id: int, gui: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    row = tg.load(session, user, msg_id)
    tg.mark_seen(session, user, row)
    return _page(request, session, user, row)


@router.get('/tin-gui-khach/{msg_id}/ai')
def ai_state(msg_id: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    """Trang soạn tin hỏi mỗi 5 giây khi đang nhờ AI viết lại."""
    row = tg.load(session, user, msg_id)
    state = tg.rewrite_state(session, row)
    return JSONResponse({'status': state['status'] if state else ''})


@router.post('/tin-gui-khach/{msg_id}/{action}')
def act(request: Request, msg_id: int, action: str, form=Depends(checked_form), user: User = Depends(web_user),
        session: Session = Depends(get_session)):
    if action not in ACTIONS:
        raise BusinessError('NOT_FOUND', 'Không có thao tác này.', status=404)
    version, text = form.get('version'), form.get('noi_dung')
    try:
        if action == 'luu':
            tg.save(session, user, msg_id, version, text)
        elif action == 'gui-zalo':
            tg.send_bot(session, user, msg_id, version, text)
        elif action == 'da-gui':
            tg.mark_personal(session, user, msg_id, version, text)
        elif action == 'bo':
            tg.drop(session, user, msg_id, version, str(form.get('ghi_chu') or ''))
            return RedirectResponse('/tin-gui-khach?bo=1', status_code=303)
        elif action == 'viet-lai':
            tg.request_rewrite(session, user, msg_id, version, text)
        else:
            tg.finish_rewrite(session, user, msg_id, version, action == 'dung-ban-moi')
    except BusinessError as exc:
        session.rollback()
        if exc.status in (403, 404):
            raise
        row = tg.load(session, user, msg_id)
        return _page(request, session, user, row, error=exc.message, status=exc.status,
                     text=str(text) if text is not None and row.status == 'CHO' else None)
    done = {'luu': 'luu', 'gui-zalo': 'gui', 'da-gui': 'gui'}.get(action, '')
    return RedirectResponse(f'/tin-gui-khach/{msg_id}' + (f'?gui={done}' if done else ''), status_code=303)
