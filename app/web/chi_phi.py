"""Chi phí phát sinh trên đơn (TUẤN duyệt 16/11/2026, mẫu 3jpHcVYJarVVHsxfm461sD): văn phòng ghi trên phiếu dịch vụ / đơn bán,
thợ giao ghi trên màn giao đơn. Thợ ghi trên phiếu dịch vụ vẫn qua /tho/phieu/{id}/chi-phi (technician.py)."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import attachments, service_expenses as svc
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import Attachment, ServiceExpense, User
from ..security import has_role
from . import checked_form, web_user

router = APIRouter()


def read_photo(form):
    upload = form.get('photo')
    if upload is None or not getattr(upload, 'filename', ''):
        return None
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = upload.file.read(limit + 1)
    if not data:
        return None
    if len(data) > limit:
        raise BusinessError('FILE_TOO_LARGE', 'Ảnh quá lớn.')
    if not attachments._prepare(data)[1].startswith('image/'):
        raise BusinessError('BAD_FILE_TYPE', 'Ảnh hóa đơn phải là ảnh JPEG, PNG hoặc WEBP.')
    return (upload.filename, data)


def fields(form):
    return dict(kind=str(form.get('kind', '')), description=str(form.get('description', '')), place=str(form.get('place', '')),
                amount=str(form.get('amount', '')), payer=str(form.get('payer', 'NV_UNG') or 'NV_UNG'), vat_rate=str(form.get('vat_rate', '0') or '0'),
                note=str(form.get('note', '')), delivery_id=str(form.get('delivery_id', '')))


def photos_for(session, rows):
    out = {}
    for a in session.scalars(select(Attachment).where(Attachment.entity == 'service_expense', Attachment.entity_id.in_([str(e.id) for e in rows]),
                                                      Attachment.hidden.is_(False)).order_by(Attachment.id)):
        out.setdefault(int(a.entity_id), a.id)
    return out


def context(session, user, *, ticket=None, order=None, values=None, error=None, error_code=''):
    """Khung "Chi phí phát sinh nội bộ" trên phiếu dịch vụ / đơn bán (văn phòng) và màn giao đơn của thợ."""
    if ticket is not None:
        rows = svc.for_ticket(session, ticket.id)
        done = svc.ticket_done_at(session, ticket)
        can_add = svc.ticket_can_add(session, user, ticket, done)
        action, profit, deliveries = f'/dich-vu/{ticket.id}/chi-phi/them', svc.ticket_profit(session, user, ticket), []
    else:
        rows = svc.for_order(session, order.id)
        done = svc.order_done_at(session, order)
        can_add = svc.order_can_add(session, user, order)
        action, profit, deliveries = f'/don-ban/{order.id}/chi-phi/them', svc.order_profit(session, user, order), svc.deliveries(session, order.id)
    names = dict(session.execute(select(User.id, User.full_name).where(User.id.in_({r.technician_id for r in rows}))).all()) if rows else {}
    numbers = {d.id: d.number for d in deliveries}
    return dict(rows=rows, photos=photos_for(session, rows), names=names, kinds=svc.KINDS, payers=svc.PAYERS, statuses=svc.STATUSES,
                vat_rates=svc.VAT_RATES, can_add=can_add, action=action, profit=profit, deliveries=deliveries, delivery_numbers=numbers,
                need_note=svc._late(done), form_values=values or {}, open=values is not None, error=error, error_code=error_code,
                pending=sum(r.cost for r in rows if r.status == 'CHO_DUYET'), counted=sum(r.cost for r in rows if r.status in svc.COUNTED),
                financial=has_role(user, 'finance'))


def _create(session, user, form, **target):
    f = fields(form)
    return svc.create(session, user, target.get('ticket_id'), f['kind'], f['description'], f['place'], f['amount'], read_photo(form),
                      payer=f['payer'], vat_rate=f['vat_rate'], note=f['note'], order_id=target.get('order_id'),
                      delivery_id=f['delivery_id'] if target.get('order_id') and not target.get('tech') else None)


@router.post('/dich-vu/{ticket_id}/chi-phi/them')
def ticket_add(request: Request, ticket_id: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    from .services import _detail, _message
    try:
        _create(session, user, form, ticket_id=ticket_id)
    except BusinessError as exc:
        session.rollback()
        message, code, status = _message(exc)
        if status in (403, 404):
            raise
        return _detail(request, session, user, ticket_id, form=form, failed_action='expense', error=message, error_code=code, status=status)
    return RedirectResponse(f'/dich-vu/{ticket_id}?da_luu=1#chi-phi-phat-sinh', status_code=303)


@router.post('/don-ban/{order_id}/chi-phi/them')
def order_add(request: Request, order_id: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    from .. import sales_orders
    from .orders import _form
    try:
        _create(session, user, form, order_id=order_id)
    except BusinessError as exc:
        session.rollback()
        if exc.status in (403, 404):
            raise
        return _form(request, session, order=sales_orders.load(session, user, order_id), error=exc.message, code=exc.code, status=exc.status,
                     action_form={k: v for k, v in form.items() if k != 'photo'}, action='expense')
    return RedirectResponse(f'/don-ban/{order_id}?da_luu=1#chi-phi-phat-sinh', status_code=303)


@router.post('/giao-don/{order_id}/chi-phi/them')
def delivery_add(request: Request, order_id: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    """Thợ giao đơn bán ghi chi phí (phí gửi hàng, xe chở...)."""
    from .. import technician_deliveries as tds
    from .technician_deliveries import page
    request.state.technician_return = True
    try:
        _create(session, user, form, order_id=order_id, tech=True)
    except BusinessError as exc:
        session.rollback()
        if exc.status in (403, 404):
            raise
        raw = {k: str(v) for k, v in form.items() if k != 'photo'}
        raw['failed_action'] = 'expense'
        return page(request, session, user, tds.load(session, user, order_id), raw, exc.message, exc.code, exc.status)
    return RedirectResponse(f'/giao-don/{order_id}?return_to=tho&da_luu=1#chi-phi-phat-sinh', status_code=303)


@router.post('/chi-phi/{expense_id}/xoa')
def delete(request: Request, expense_id: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    from .services import _version
    row = session.get(ServiceExpense, expense_id)
    if row is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy khoản chi phí.', status=404)
    svc.cancel(session, user, expense_id, _version(form))
    back = str(form.get('quay_ve', ''))
    if not (back.startswith('/') and '//' not in back and '\\' not in back and len(back) < 200):
        back = f'/dich-vu/{row.ticket_id}' if row.ticket_id else f'/don-ban/{row.order_id}'
    return RedirectResponse(back + ('&' if '?' in back else '?') + 'da_luu=1#chi-phi-phat-sinh', status_code=303)
