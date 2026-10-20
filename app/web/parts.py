"""KT3: giao diện yêu cầu/cấp linh kiện; mọi ghi đi qua lõi."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import part_requests as svc, products, technician_stock
from ..db import get_session
from ..errors import BusinessError
from ..models import PartRequest, Product, ServiceTicket, StockBalance, StockDoc, User, Warehouse
from ..schemas import PartRequestIn
from ..security import has_role
from . import checked_form, render, web_user

router = APIRouter()


def _stocks(session, codes, warehouses):
    return {(r.warehouse_code, r.product_code): r.qty for r in session.scalars(select(StockBalance).where(
        StockBalance.product_code.in_(codes), StockBalance.warehouse_code.in_(warehouses)))}


def detail_context(session, user, ticket, shown=None):
    rows = svc.for_ticket(session, user, ticket.id)
    own = technician_stock.own_warehouse(session,user)
    doc_links = {r.stock_doc_id: f'/phieu-kho/{r.stock_doc_id}' if has_role(user,'warehouse') else
                 f'/hang-toi-giu/phieu/{r.stock_doc_id}' for r in rows if r.stock_doc_id and
                 (has_role(user,'warehouse') or (own and r.to_warehouse_code == own.code))}
    can_create = has_role(user, 'dispatcher') or ('technician' in user.roles and ticket.assignee_id == user.id)
    return dict(part_requests=rows, part_statuses=svc.STATUSES, part_doc_links=doc_links,
        part_names=dict(session.execute(select(Product.code, Product.name).where(Product.code.in_([r.product_code for r in rows]))).all()),
        part_docs=dict(session.execute(select(StockDoc.id, StockDoc.number).where(StockDoc.id.in_([r.stock_doc_id for r in rows if r.stock_doc_id]))).all()),
        part_can_purchase=has_role(user,'warehouse'), can_request_parts=can_create and ticket.status in ('DANG_LAM', 'CHO_LINH_KIEN'),
        part_form=shown or {}, part_can_cancel={r.id for r in rows if (r.requester_id == user.id or has_role(user, 'dispatcher')) and r.status == 'CHO_CAP'})


@router.get('/dich-vu/{ticket_id}/tim-linh-kien')
def pick(request: Request, ticket_id: int, q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    from .. import service_tickets
    t = service_tickets.load(session, user, ticket_id)
    svc.check_requester(user, t)
    rows = products.search(session, q[:200], active_only=True, limit=20)
    tech = session.get(User, t.assignee_id) if t.assignee_id else None
    own = technician_stock.own_warehouse(session, tech) if tech else None
    warehouses = [own.code] if own else []
    warehouses += [w.code for w in session.scalars(select(Warehouse).where(Warehouse.kind == 'KHO', Warehouse.active.is_(True)).order_by(Warehouse.code))]
    return render(request, 'linh_kien_chon.jinja', rows=rows, warehouses=warehouses,
                  stocks=_stocks(session, [r.code for r in rows], warehouses))


@router.post('/dich-vu/{ticket_id}/yeu-cau-linh-kien')
def create(request: Request, ticket_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    from .services import _detail, _message, _version
    data = {k: str(form.get(k, '')) for k in ('version', 'product_code', 'qty', 'reason')}
    try:
        svc.create(session, request.state.user, ticket_id, PartRequestIn(**{**data, 'version': _version(form), 'qty': data['qty'].replace(',', '.')}))
    except (BusinessError, ValidationError) as exc:
        message, code, status = _message(exc)
        if code in ('FORBIDDEN', 'NOT_FOUND'):
            return render(request, 'loi.jinja', error=exc, status=status)
        return _detail(request, session, request.state.user, ticket_id, error=message, error_code=code, status=status, part_form=data)
    from .technician import after_post
    return after_post(request,session,request.state.user,ticket_id,f'/dich-vu/{ticket_id}?da_luu=1#linh-kien')


@router.post('/dich-vu/{ticket_id}/linh-kien/{ident}/huy')
def cancel(request: Request, ticket_id: int, ident: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    from .. import service_tickets
    from .services import _detail, _message, _version
    service_tickets.load(session, request.state.user, ticket_id)
    row = session.get(PartRequest, ident)
    if row is None or row.ticket_id != ticket_id:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy yêu cầu trên phiếu này.', status=404)
    try:
        svc.cancel(session, request.state.user, ident, str(form.get('reason', '')), _version(form))
    except BusinessError as exc:
        message, code, status = _message(exc)
        return _detail(request, session, request.state.user, ticket_id, error=message, error_code=code, status=status,
                       part_form=dict(cancel_id=ident, reason=str(form.get('reason', '')), version=form.get('version', '')))
    from .technician import after_post
    return after_post(request,session,request.state.user,ticket_id,f'/dich-vu/{ticket_id}?da_luu=1#linh-kien')


def _queue(request, session, user, error=None, code='', status=200, failed_id=None, data=None):
    selected = request.query_params.get('status', 'CHO_CAP')
    rows = svc.search(session, user, selected)
    # Giữ dòng vừa đổi trạng thái để lỗi version vẫn hiện cùng dữ liệu đã nhập.
    if failed_id and failed_id not in {r.id for r in rows}:
        row = session.get(PartRequest, failed_id)
        if row:rows.append(row)
    warehouses = list(session.scalars(select(Warehouse).where(Warehouse.kind == 'KHO', Warehouse.active.is_(True)).order_by(Warehouse.code)))
    tickets = {t.id: t for t in session.scalars(select(ServiceTicket).where(ServiceTicket.id.in_([r.ticket_id for r in rows])))}
    return render(request, 'linh_kien_ds.jinja', rows=rows, warehouses=warehouses, tickets=tickets, part_statuses=svc.STATUSES, selected_status=selected,
        names=dict(session.execute(select(Product.code, Product.name).where(Product.code.in_([r.product_code for r in rows]))).all()),
        users=dict(session.execute(select(User.id, User.full_name)).all()),
        stocks=_stocks(session, [r.product_code for r in rows], [w.code for w in warehouses]),
        error=error, error_code=code, status=status, failed_id=failed_id, d=data or {}, saved=request.query_params.get('da_luu') == '1')


@router.get('/yeu-cau-linh-kien')
def queue(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    svc.check_supplier(user)
    return _queue(request, session, user)


@router.post('/yeu-cau-linh-kien/{ident}/{action}')
def decide(request: Request, ident: int, action: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    from .services import _message, _version
    user = request.state.user
    svc.check_supplier(user)
    data = {k: str(form.get(k, '')) for k in ('version', 'warehouse_code', 'note')}
    try:
        version = _version(form)
        if action == 'cap':svc.supply(session, user, ident, data['warehouse_code'], version)
        elif action == 'het-hang':svc.out_of_stock(session, user, ident, data['note'], version)
        else:raise BusinessError('NOT_FOUND', 'Không tìm thấy thao tác.', status=404)
    except BusinessError as exc:
        message, code, status = _message(exc)
        return _queue(request, session, user, message, code, status, ident, data)
    return RedirectResponse('/yeu-cau-linh-kien?da_luu=1' + ('&status=HET_HANG' if action == 'het-hang' else ''), status_code=303)
