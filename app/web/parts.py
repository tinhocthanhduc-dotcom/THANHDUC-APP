"""KT3: giao diện yêu cầu/cấp linh kiện; mọi ghi đi qua lõi."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import part_requests as svc, products, technician_stock
from ..db import get_session
from ..errors import BusinessError
from ..models import PartRequest, Product, ServiceTicket, StockDoc, User, Warehouse
from ..schemas import PartRequestIn, PartStockIn
from ..security import has_role
from . import checked_form, render, web_user

router = APIRouter()


def _stocks(session, codes, warehouses):
    """F6 (báo cáo đồng bộ 25/10/2026): số hiện và nút Cấp dùng khả dụng của lõi (tồn − đã giữ cho đơn), không dùng tồn vật lý.
    Lõi vẫn chặn RESERVED_STOCK lần hai khi cấp; đây chỉ là gợi ý đọc từ cùng một hàm."""
    from .. import stock_reservations
    found = stock_reservations.quantities(session, [(w, c) for w in warehouses for c in codes])
    return {key: dict(qty=v['qty'], reserved=v['reserved'], available=max(v['available'], 0)) for key, v in found.items()}


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
    codes = [str(v).strip() for v in form.getlist('product_code')]
    qtys = [str(v).strip() for v in form.getlist('qty')]
    # UX5b: nhiều dòng mã + số lượng; dòng trống (ô tìm khi không có JS) bị bỏ qua.
    pairs = [(c, q) for c, q in zip(codes, qtys) if c] if len(codes) == len(qtys) else []
    names = {p.code: p.name for p in session.scalars(select(Product).where(Product.code.in_([c for c, _ in pairs])))} if pairs else {}
    data['lines'] = [(c, q, names.get(c, '')) for c, q in pairs]
    try:
        if len(codes) != len(qtys):
            raise BusinessError('BAD_LINES', 'Danh sách linh kiện không hợp lệ, hãy tải lại trang.')
        version = _version(form)
        bodies = [PartRequestIn(version=version, product_code=c, qty=q.replace(',', '.'), reason=data['reason']) for c, q in pairs] or \
            [PartRequestIn(version=version, product_code=data['product_code'], qty=data['qty'].replace(',', '.'), reason=data['reason'])]
        svc.create_many(session, request.state.user, ticket_id, version, bodies)
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


TABS = {**svc.STATUSES, 'tat-ca': 'Tất cả'}
LOAI = {'phieu': 'Cho phiếu dịch vụ', 'mang-theo': 'Thợ xin mang theo'}
LATE_HOURS = 24


def _waited(created, now):
    minutes = max(0, int((now - created).total_seconds() // 60))
    if minutes < 60:
        return f'chờ {minutes} phút', False
    if minutes < 48 * 60:
        return f'chờ {minutes // 60} giờ', minutes >= LATE_HOURS * 60
    return f'chờ {minutes // 1440} ngày', True


def _queue(request, session, user, error=None, code='', status=200, failed_id=None, data=None):
    """Đợt 7: danh sách Yêu cầu linh kiện theo khuôn 3. Chỉ đọc; cấp và báo hết hàng vẫn qua lõi part_requests."""
    from datetime import UTC, datetime
    from ..models import Customer
    from ..text import fold
    from .. import service_sla
    from . import paging
    params = request.query_params
    selected = params.get('status', 'CHO_CAP')
    selected = selected if selected in TABS else 'CHO_CAP'
    q, loai, ton = params.get('q', '')[:200], params.get('loai', ''), params.get('ton', '')
    loai = loai if loai in LOAI else ''
    ton = ton if ton in ('du', 'thieu') and selected == 'CHO_CAP' else ''
    tho = params.get('tho', '')
    tho = int(tho) if tho.isdigit() else None
    found = svc.search(session, user, None)
    warehouses = list(session.scalars(select(Warehouse).where(Warehouse.kind == 'KHO', Warehouse.active.is_(True)).order_by(Warehouse.code)))
    tickets = {t.id: t for t in session.scalars(select(ServiceTicket).where(ServiceTicket.id.in_({r.ticket_id for r in found if r.ticket_id})))}
    names = dict(session.execute(select(Product.code, Product.name).where(Product.code.in_({r.product_code for r in found}))).all()) if found else {}
    users = dict(session.execute(select(User.id, User.full_name)).all())
    customers = dict(session.execute(select(Customer.code, Customer.name).where(Customer.code.in_({t.customer_code for t in tickets.values()}))).all()) if tickets else {}
    stocks = _stocks(session, list(names), [w.code for w in warehouses]) if names else {}
    tech = lambda r: tickets[r.ticket_id].assignee_id if r.ticket_id and r.ticket_id in tickets else r.requester_id
    enough = lambda r: any(stocks.get((w.code, r.product_code), {}).get('available', 0) >= r.qty for w in warehouses)

    def matches(r):
        if loai and (loai == 'phieu') != bool(r.ticket_id):
            return False
        if tho and tech(r) != tho:
            return False
        if q:
            t = tickets.get(r.ticket_id)
            text = ' '.join([r.number, r.product_code, names.get(r.product_code, ''), users.get(tech(r), ''), users.get(r.requester_id, ''),
                             t.number if t else '', customers.get(t.customer_code, '') if t else ''])
            return fold(q) in fold(text)
        return True
    # Tab đếm trên tập đã lọc (ô tìm, thợ, loại); ô số đếm trên toàn bộ, bấm là lọc.
    filtered = [r for r in found if matches(r)]
    counts = {k: sum(1 for r in filtered if r.status == k) for k in svc.STATUSES}
    rows = [r for r in filtered if (selected == 'tat-ca' or r.status == selected) and (not ton or enough(r) == (ton == 'du'))]
    if selected == 'tat-ca':
        rows.sort(key=lambda r: (r.status != 'CHO_CAP', -r.created_at.timestamp() if r.status != 'CHO_CAP' else r.created_at.timestamp()))
    elif selected != 'CHO_CAP':
        rows.sort(key=lambda r: (r.decided_at or r.created_at), reverse=True)
    # Giữ dòng vừa đổi trạng thái để lỗi version vẫn hiện cùng dữ liệu đã nhập.
    if failed_id and failed_id not in {r.id for r in rows}:
        row = session.get(PartRequest, failed_id)
        if row:
            rows.insert(0, row)
    query = dict(q=q, status='' if selected == 'CHO_CAP' else selected, tho=tho or '', loai=loai, ton=ton)
    page, shown = paging.slice_rows(rows, params.get('trang'), params.get('so'), query)
    link = paging.linker('/yeu-cau-linh-kien', query)
    now = datetime.now(UTC)
    today = datetime.now(service_sla.TZ).date()
    waiting = [r for r in found if r.status == 'CHO_CAP']
    oldest = min((r.created_at for r in waiting), default=None)
    ready = sum(1 for r in waiting if enough(r))
    done_today = [r for r in found if r.status == 'DA_CAP' and r.decided_at and r.decided_at.astimezone(service_sla.TZ).date() == today]
    tiles = [
        dict(label='Chờ cấp', n=len(waiting), sub=('Cũ nhất ' + _waited(oldest, now)[0].removeprefix('chờ ')) if oldest else 'Không có yêu cầu chờ',
             href='/yeu-cau-linh-kien', on=selected == 'CHO_CAP' and not ton, tone='cho' if waiting else ''),
        dict(label='Cấp được ngay', n=ready, sub='Có kho đủ tồn', href='/yeu-cau-linh-kien?ton=du', on=ton == 'du', tone='xong' if ready else ''),
        dict(label='Thiếu hàng', n=len(waiting) - ready, sub='Không kho nào đủ', href='/yeu-cau-linh-kien?ton=thieu', on=ton == 'thieu', tone='huy' if len(waiting) - ready else ''),
        dict(label='Hết hàng · chờ mua', n=sum(1 for r in found if r.status == 'HET_HANG'), sub='Cần lập đơn mua', href='/yeu-cau-linh-kien?status=HET_HANG', on=selected == 'HET_HANG'),
        dict(label='Đã cấp hôm nay', n=len(done_today), sub=f'{sum(r.qty for r in done_today):g} cái' if done_today else 'Chưa cấp', href='/yeu-cau-linh-kien?status=DA_CAP', on=selected == 'DA_CAP'),
    ]
    tabs = [dict(label=label, n=counts[k] if k in counts else len(filtered), href=link(status='' if k == 'CHO_CAP' else k, ton=''), on=selected == k) for k, label in TABS.items()]
    technicians = sorted({tech(r) for r in found}, key=lambda i: users.get(i, ''))
    chips = [dict(label=label, href=link(**{key: ''})) for key, label in (
        ('tho', 'Thợ: ' + users.get(tho, '') if tho else ''), ('loai', LOAI.get(loai, '')),
        ('ton', {'du': 'Cấp được ngay', 'thieu': 'Thiếu hàng'}.get(ton, ''))) if query.get(key)]
    docs = dict(session.execute(select(StockDoc.id, StockDoc.number).where(StockDoc.id.in_({r.stock_doc_id for r in shown if r.stock_doc_id}))).all()) if shown else {}
    done = None
    if params.get('da_luu') == '1' and params.get('yc', '').isdigit():
        done = session.get(PartRequest, int(params['yc']))
        if done is not None and done.stock_doc_id:
            docs.setdefault(done.stock_doc_id, session.get(StockDoc, done.stock_doc_id).number)
    return render(request, 'linh_kien_ds.jinja', rows=shown, page=page, tiles=tiles, tabs=tabs, chips=chips, link=link, q=q,
        tho=tho, loai=loai, ton=ton, loai_labels=LOAI, technicians=[(i, users.get(i, '')) for i in technicians],
        warehouses=warehouses, tickets=tickets, part_statuses=svc.STATUSES, selected_status=selected,
        names=names, users=users, customers=customers, stocks=stocks, docs=docs, tech=tech, waited=lambda r: _waited(r.created_at, now),
        qty_sum=sum(r.qty for r in rows), done=done,
        error=error, error_code=code, status=status, failed_id=failed_id, d=data or {}, saved=params.get('da_luu') == '1')


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
    return RedirectResponse(f'/yeu-cau-linh-kien?da_luu=1&yc={ident}' + ('&status=HET_HANG' if action == 'het-hang' else ''), status_code=303)


# ---------- Thợ xin hàng mang theo (không gắn phiếu) ----------


def _stock_form(request, session, user, data=None, error=None, code='', status=200):
    own = technician_stock.own_warehouse(session, user) if 'technician' in user.roles else None
    return render(request, 'xin_linh_kien.jinja', own=own, d=data or {'qty': '1'}, error=error, error_code=code, status=status)


@router.get('/hang-toi-giu/xin-linh-kien')
def stock_request_page(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    if 'technician' not in user.roles:
        raise BusinessError('FORBIDDEN', 'Chỉ thợ được xin hàng mang theo.', status=403)
    return _stock_form(request, session, user)


@router.get('/hang-toi-giu/tim-linh-kien')
def stock_pick(request: Request, q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    if 'technician' not in user.roles:
        raise BusinessError('FORBIDDEN', 'Chỉ thợ được xin hàng mang theo.', status=403)
    rows = products.search(session, q[:200], active_only=True, limit=20)
    own = technician_stock.own_warehouse(session, user)
    warehouses = [own.code] if own else []
    warehouses += [w.code for w in session.scalars(select(Warehouse).where(Warehouse.kind == 'KHO', Warehouse.active.is_(True)).order_by(Warehouse.code))]
    return render(request, 'linh_kien_chon.jinja', rows=rows, warehouses=warehouses,
                  stocks=_stocks(session, [r.code for r in rows], warehouses))


@router.post('/hang-toi-giu/xin-linh-kien')
def stock_request(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    from .services import _message
    user = request.state.user
    codes = [str(v).strip() for v in form.getlist('product_code')]
    qtys = [str(v).strip() for v in form.getlist('qty')]
    reason = str(form.get('reason', ''))
    # Thẻ A: nhiều dòng mã + số lượng; dòng trống (ô tìm khi không có JS) bị bỏ qua.
    pairs = [(c, q) for c, q in zip(codes, qtys) if c] if len(codes) == len(qtys) else []
    names = {p.code: p.name for p in session.scalars(select(Product).where(Product.code.in_([c for c, _ in pairs])))} if pairs else {}
    data = dict(lines=[(c, q, names.get(c, '')) for c, q in pairs], reason=reason, qty='1')
    try:
        if len(codes) != len(qtys):
            raise BusinessError('BAD_LINES', 'Danh sách linh kiện không hợp lệ, hãy tải lại trang.')
        bodies = [PartStockIn(product_code=c, qty=q.replace(',', '.'), reason=reason) for c, q in pairs] or \
            [PartStockIn(product_code='', qty=(qtys[0] if qtys else '1').replace(',', '.'), reason=reason)]
        svc.create_stock_many(session, user, bodies)
    except (BusinessError, ValidationError) as exc:
        message, code, status = _message(exc)
        if code == 'FORBIDDEN':
            return render(request, 'loi.jinja', error=exc, status=status)
        return _stock_form(request, session, user, data, message, code, status)
    return RedirectResponse('/hang-toi-giu?da_luu=1#xin-linh-kien', status_code=303)


@router.post('/hang-toi-giu/xin-linh-kien/{ident}/huy')
def stock_request_cancel(request: Request, ident: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    from .services import _version
    row = session.get(PartRequest, ident)
    if row is None or row.ticket_id is not None or row.requester_id != request.state.user.id:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy yêu cầu mang theo của bạn.', status=404)
    reason = str(form.get('reason', '')).strip() or 'Thợ hủy yêu cầu mang theo'
    svc.cancel(session, request.state.user, ident, reason, _version(form))
    return RedirectResponse('/hang-toi-giu?da_luu=1#xin-linh-kien', status_code=303)
