"""SX1 screens: list (khuôn 3), create form, two-column document, technician card. Rules live in production.py."""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import production as svc, sales_warranty
from ..db import get_session
from ..errors import BusinessError
from ..models import AuditLog, Product, StockBalance, StockDoc, StockLedger, User, Warehouse
from ..schemas import ProductionAssign, ProductionComplete, ProductionIn, ProductionMaterials, ProductionReceive, ProductionVersion, ProductionVoid
from ..security import has_role
from ..text import fold
from . import checked_form, money, paging, render, web_user
from .lists import _today
from .services import _message

router = APIRouter()
TONES = {'MOI': 'nhap', 'DANG_LAM': 'lam', 'HOAN_THANH': 'cho', 'DA_NHAP': 'xong', 'DA_HUY': 'huy'}
STEP_ORDER = ('MOI', 'DANG_LAM', 'HOAN_THANH', 'DA_NHAP')
STEP_LABELS = {'MOI': 'Mới', 'DANG_LAM': 'Đang làm', 'HOAN_THANH': 'Hoàn thành', 'DA_NHAP': 'Đã nhập kho'}
ACTIONS = {'production.create': 'Lập phiếu', 'production.assign': 'Giao thợ', 'production.start': 'Bắt đầu làm',
           'production.complete': 'Hoàn thành', 'production.materials': 'Bổ sung vật tư', 'production.receive': 'Nhận vào kho', 'production.void': 'Hủy phiếu'}


def _cost_viewer(user):
    return has_role(user, 'warehouse', 'finance')


def _people(session):
    return {u.id: u.full_name for u in session.scalars(select(User))}


def _technicians(session):
    return list(session.scalars(select(User).where(User.active.is_(True), User.roles.contains(['technician'])).order_by(User.full_name)))


def _products(session):
    return list(session.scalars(select(Product).where(Product.active.is_(True)).order_by(Product.name)))


def _warehouses(session):
    return list(session.scalars(select(Warehouse).where(Warehouse.active.is_(True), Warehouse.kind == 'KHO').order_by(Warehouse.code)))


def _next(row, people):
    tech = people.get(row.technician_id, '')
    if row.status == 'MOI':
        return ('Bắt đầu làm', tech) if row.technician_id else ('Giao thợ', 'điều phối')
    if row.status == 'DANG_LAM':
        return 'Hoàn thành', tech
    if row.status == 'HOAN_THANH':
        return 'Nhận vào kho', 'thủ kho ' + row.to_warehouse_code
    if row.status == 'DA_NHAP':
        return 'Xong', row.received_at.astimezone(svc.TZ).strftime('%d/%m') if row.received_at else ''
    return 'Đã hủy', ''


def _month_of(value, today):
    return bool(value) and value.astimezone(svc.TZ).date().replace(day=1) == today.replace(day=1)


def product_label(row, names):
    """'12A đổ lại +1' cho phiếu nhiều thành phẩm."""
    first = names.get(row.product_code, row.product_code)
    return first + (f' +{len(row.outputs) - 1}' if len(row.outputs) > 1 else '')


@router.get('/san-xuat')
def listing(request: Request, q: str = '', status: str = '', loai: str = '', tho: str = '', vattu: str = '', trang: str = '', so: str = '',
            user: User = Depends(web_user), session: Session = Depends(get_session)):
    people, today, viewer = _people(session), _today(), _cost_viewer(user)
    names = {p.code: p.name for p in session.scalars(select(Product))}
    rows = list(session.scalars(svc.search(session, user)))
    tech_id = int(tho) if tho.isdigit() else None
    scoped = [r for r in rows if (not loai or r.kind == loai) and (tech_id is None or r.technician_id == tech_id) and (vattu != 'khong' or svc.no_material(r))
              and (not q or all(w in fold(f"{r.number} {' '.join(names.get(o.product_code, '') + ' ' + o.product_code for o in r.outputs)} {people.get(r.technician_id, '')}") for w in fold(q).split()))]
    counts = {k: sum(1 for r in scoped if r.status == k) for k in svc.STATUSES}
    shown_rows = [r for r in scoped if not status or r.status == status]
    query = dict(q=q, status=status, loai=loai, tho=tho, vattu=vattu)
    page, shown = paging.slice_rows(shown_rows, trang, so, query)
    link = paging.linker('/san-xuat', query)
    done_month = [r for r in scoped if r.status == 'DA_NHAP' and _month_of(r.received_at, today)]
    scrap_month = sum(r.qty_scrap for r in scoped if _month_of(r.finished_at, today))
    tiles = [dict(label='Đang làm', n=counts['DANG_LAM'] + counts['MOI'], sub=f"{counts['MOI']} chưa bắt đầu" if counts['MOI'] else 'đã bắt đầu', href=link(status='DANG_LAM'), on=status == 'DANG_LAM'),
             dict(label='Chờ nhập kho', n=counts['HOAN_THANH'], sub='thủ kho nhận', href=link(status='HOAN_THANH'), on=status == 'HOAN_THANH', tone='cho' if counts['HOAN_THANH'] else ''),
             dict(label='Xong tháng này', n=sum(r.qty_good for r in done_month), sub=f"thành phẩm · {len(done_month)} phiếu", href=link(status='DA_NHAP'), on=status == 'DA_NHAP', tone='xong'),
             dict(label='Hỏng tháng này', n=scrap_month, sub='chi phí không dùng tiền', href=link(status=''), on=False, tone='huy' if scrap_month else '')]
    no_mat = [r for r in rows if svc.no_material(r) and (not loai or r.kind == loai) and (tech_id is None or r.technician_id == tech_id)]
    if viewer or no_mat:
        tiles.append(dict(label='Chưa xuất vật tư', n=len(no_mat), sub='giá vốn 0 · xem lại', href=link(vattu='khong', status=''), on=vattu == 'khong', tone='cho' if no_mat else ''))
    tabs = [dict(label='Tất cả', n=len(scoped), href=link(status=''), on=not status)] + \
           [dict(label=label, n=counts[k], href=link(status=k), on=status == k) for k, label in svc.STATUSES.items()]
    chips = ([dict(label=svc.KINDS[loai], href=link(loai=''))] if loai in svc.KINDS else []) + \
            ([dict(label='Thợ ' + people.get(tech_id, ''), href=link(tho=''))] if tech_id in people else []) + \
            ([dict(label='Chưa xuất vật tư', href=link(vattu=''))] if vattu == 'khong' else [])

    def cost_text(r):
        if not viewer or r.status not in ('HOAN_THANH', 'DA_NHAP'): return None
        costs = sorted({o.unit_cost for o in r.outputs if o.qty_good})
        return money(costs[0]) if len(costs) <= 1 else money(costs[0]) + '–' + money(costs[-1])
    items = [dict(number=r.number, kind=svc.KINDS[r.kind], product=product_label(r, names), code=r.product_code if len(r.outputs) == 1 else f'{len(r.outputs)} mã',
                  tech=people.get(r.technician_id, 'Chưa giao'), plan=r.qty_plan, good=r.qty_good, scrap=r.qty_scrap, done=r.status in ('HOAN_THANH', 'DA_NHAP', 'DA_HUY') and r.finished_at is not None,
                  no_material=svc.no_material(r), wh=r.to_warehouse_code, cost=cost_text(r), status=r.status, tone=TONES[r.status], next=_next(r, people))
             for r in shown]
    totals = dict(plan=sum(r.qty_plan for r in shown_rows), good=sum(r.qty_good for r in shown_rows), scrap=sum(r.qty_scrap for r in shown_rows),
                  value=sum(r.unit_cost * r.qty_good for r in shown_rows if r.status == 'DA_NHAP') if viewer else None)
    return render(request, 'san_xuat_ds.jinja', rows=items, page=page, tiles=tiles, tabs=tabs, chips=chips, link=link, q=q, selected=status, loai=loai, tho=tho, vattu=vattu,
                  kinds=svc.KINDS, statuses=svc.STATUSES, technicians=[] if svc._tech_only(user) else _technicians(session), totals=totals, viewer=viewer,
                  can_create=True, tech_only=svc._tech_only(user))


def _form_context(session, user, d, lines, error='', code='', status=200):
    own, held = None, {}
    if svc._tech_only(user):
        from .. import technician_stock
        own = technician_stock.own_warehouse(session, user)
        if own: held = {b.product_code: b.qty for b in session.scalars(select(StockBalance).where(StockBalance.warehouse_code == own.code, StockBalance.qty > 0))}
    return dict(d=d, lines=lines, error=error, error_code=code, status=status, kinds=svc.KINDS, products=_products(session),
                technicians=_technicians(session), warehouses=_warehouses(session), tech_only=svc._tech_only(user), own=own, held=held)


def _qty(raw):
    try:
        return Decimal(str(raw or '0').strip().replace(',', '.') or '0')
    except InvalidOperation:
        return Decimal(0)


def _material_lines(form, qty_name):
    codes, qtys = form.getlist('material_product'), form.getlist(qty_name)
    if len(codes) != len(qtys):
        raise BusinessError('BAD_FORM', 'Dòng vật tư chưa đủ dữ liệu, hãy tải lại trang.')
    return [dict(product_code=c, qty=str(_qty(q))) for c, q in zip(codes, qtys) if c]


@router.get('/san-xuat/moi')
def new(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    svc._require(user, *svc.READERS)
    ctx = _form_context(session, user, dict(kind='DO_MUC'), [])
    ctx['d']['to_warehouse_code'] = ctx['warehouses'][0].code if ctx['warehouses'] else ''
    return render(request, 'san_xuat_moi.jinja', **ctx)


@router.post('/san-xuat/moi')
def create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    svc._require(user, *svc.READERS)
    d = {k: str(form.get(k, '')).strip() for k in ('kind', 'to_warehouse_code', 'from_warehouse_code', 'technician_id', 'note')}
    codes, plans = form.getlist('output_product'), form.getlist('output_plan')
    d['outputs'] = {c: str(_qty(q)).replace('.0', '') for c, q in zip(codes, plans) if c}
    lines = []
    try:
        if len(codes) != len(plans): raise BusinessError('BAD_FORM', 'Dòng thành phẩm chưa đủ dữ liệu, hãy tải lại trang.')
        outputs = [dict(product_code=c, qty_plan=int(_qty(q))) for c, q in zip(codes, plans) if c]
        if not outputs: raise BusinessError('BAD_PRODUCT', 'Chọn ít nhất một thành phẩm.')
        lines = [dict(product_code=l['product_code'], qty_plan=l['qty']) for l in _material_lines(form, 'material_plan') if Decimal(l['qty']) > 0]
        row = svc.create(session, user, ProductionIn(kind=d['kind'], outputs=outputs, to_warehouse_code=d['to_warehouse_code'],
                                                     technician_id=d['technician_id'] or None, from_warehouse_code=d['from_warehouse_code'] or None, note=d['note'], lines=lines))
    except (BusinessError, ValidationError) as exc:
        message, code, status = _message(exc)
        return render(request, 'san_xuat_moi.jinja', **_form_context(session, user, d, [dict(code=l['product_code'], qty=l['qty_plan']) for l in lines], message, code, status))
    return RedirectResponse('/san-xuat/' + row.number + '?da_luu=1', status_code=303)


def _detail(request, session, user, number, d=None, error='', code='', status=200):
    row = svc.load(session, user, number)
    people, viewer = _people(session), _cost_viewer(user)
    names = {p.code: p.name for p in session.scalars(select(Product))}
    product = session.get(Product, row.product_code)
    outputs = [dict(line_no=o.line_no, code=o.product_code, name=names.get(o.product_code, o.product_code), plan=o.qty_plan, good=o.qty_good, scrap=o.qty_scrap,
                    cost=o.unit_cost if viewer else None, warranty=(session.get(Product, o.product_code) or product).warranty_months) for o in row.outputs]
    line_cost = {}
    if row.px_doc_id and viewer:
        for led in session.scalars(select(StockLedger).where(StockLedger.doc_id == row.px_doc_id, StockLedger.is_reversal.is_(False))):
            line_cost[led.product_code] = line_cost.get(led.product_code, 0) - led.amount
    held = {}
    if row.from_warehouse_code:
        held = {b.product_code: b.qty for b in session.scalars(select(StockBalance).where(StockBalance.warehouse_code == row.from_warehouse_code, StockBalance.qty > 0))}
    lines = [dict(line_no=l.line_no, code=l.product_code, name=names.get(l.product_code, l.product_code), plan=l.qty_plan, used=l.qty_used,
                  held=held.get(l.product_code, Decimal(0)), cost=line_cost.get(l.product_code)) for l in row.lines]
    docs = {x.id: x.number for x in session.scalars(select(StockDoc).where(StockDoc.id.in_([i for i in (row.px_doc_id, row.pn_doc_id) if i])))} if row.px_doc_id else {}
    history = [dict(at=a.at, label=ACTIONS.get(a.action, a.action), who=people.get(a.user_id, '')) for a in
               session.scalars(select(AuditLog).where(AuditLog.entity == 'production_order', AuditLog.entity_id == str(row.id)).order_by(AuditLog.id))]
    is_tech = 'technician' in user.roles and row.technician_id == user.id
    can = dict(assign=row.status == 'MOI' and has_role(user, 'dispatcher', 'warehouse'),
               materials=row.status == 'HOAN_THANH' and row.px_doc_id is None and (has_role(user, 'dispatcher', 'warehouse') or is_tech),
               start=row.status == 'MOI' and row.technician_id is not None and (has_role(user, 'dispatcher') or row.technician_id == user.id),
               complete=row.status == 'DANG_LAM' and (has_role(user, 'director') or is_tech),
               receive=row.status == 'HOAN_THANH' and has_role(user, 'warehouse') and (has_role(user, 'director') or not user.warehouse_codes or row.to_warehouse_code in user.warehouse_codes),
               void=row.status in ('MOI', 'DANG_LAM') and not row.px_doc_id and (has_role(user, 'dispatcher') or user.id in (row.created_by, row.technician_id)))
    cur = STEP_ORDER.index(row.status) if row.status in STEP_ORDER else None
    steps = [(STEP_LABELS[k], 'done' if cur is not None and (i < cur or row.status == 'DA_NHAP') else 'cur' if i == cur and row.status != 'DA_NHAP' else '') for i, k in enumerate(STEP_ORDER)]
    month_good = 0
    if is_tech:
        today = _today()
        month_good = sum(r.qty_good for r in session.scalars(select(svc.Order).where(svc.Order.technician_id == user.id, svc.Order.status == 'DA_NHAP'))
                         if _month_of(r.received_at, today))
    products = _products(session) if can['complete'] or can['materials'] else []
    # SX2: thiếu vật tư ở bước Hoàn thành → màn xác nhận, mang theo số tốt / hỏng đã gõ.
    confirm = code == 'MATERIAL_REQUIRED' and d is not None and hasattr(d, 'getlist') and d.getlist('out_line_no') and not d.get('confirm_no_material')
    confirm_rows = list(zip(d.getlist('out_line_no'), d.getlist('out_good'), d.getlist('out_scrap'))) if confirm else []
    return render(request, 'san_xuat_xem.jinja', r=row, row=svc.public(user, row), product=product, outputs=outputs, label=product_label(row, names),
                  no_material=svc.no_material(row), confirm=confirm, confirm_rows=confirm_rows, confirm_good=sum(int(_qty(g)) for _, g, _ in confirm_rows),
                  confirm_scrap=sum(int(_qty(x)) for _, _, x in confirm_rows), lines=lines, docs=docs, history=history, people=people,
                  kinds=svc.KINDS, statuses=svc.STATUSES, tone=TONES[row.status], steps=steps, next=_next(row, people), can=can, viewer=viewer,
                  technicians=_technicians(session), products=products, held=held, month_good=month_good, is_tech=is_tech,
                  d=d or {}, error=error, error_code=code, status=status, tz=svc.TZ)


@router.get('/san-xuat/{number}')
def detail(request: Request, number: str, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _detail(request, session, user, number)


@router.post('/san-xuat/{number}/{action}')
def action(request: Request, number: str, action: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    try:
        version = ProductionVersion(version=form.get('version')).version
        if action == 'giao':
            body = ProductionAssign(version=version, technician_id=form.get('technician_id'))
            svc.assign(session, user, number, body.technician_id, version)
        elif action == 'bat-dau':
            svc.start(session, user, number, version)
        elif action in ('hoan-thanh', 'bo-sung-vat-tu'):
            nos, codes, qtys = form.getlist('line_no'), form.getlist('material_product'), form.getlist('qty_used')
            if len({len(nos), len(codes), len(qtys)}) != 1:
                raise BusinessError('BAD_FORM', 'Dòng vật tư thực dùng chưa đủ dữ liệu, hãy tải lại trang.')
            lines = [dict(line_no=n or None, product_code=c or None, qty_used=str(_qty(q))) for n, c, q in zip(nos, codes, qtys) if n or c]
            if action == 'bo-sung-vat-tu':
                svc.add_materials(session, user, number, ProductionMaterials(version=version, lines=lines))
            else:
                outs, goods, scraps = form.getlist('out_line_no'), form.getlist('out_good'), form.getlist('out_scrap')
                if len({len(outs), len(goods), len(scraps)}) != 1:
                    raise BusinessError('BAD_FORM', 'Dòng thành phẩm chưa đủ dữ liệu, hãy tải lại trang.')
                outputs = [dict(line_no=n, qty_good=int(_qty(g)), qty_scrap=int(_qty(x))) for n, g, x in zip(outs, goods, scraps) if n]
                svc.complete(session, user, number, ProductionComplete(version=version, outputs=outputs, void_reason=form.get('void_reason', ''), lines=lines,
                                                                       confirm_no_material=form.get('confirm_no_material') == '1', no_material_reason=form.get('no_material_reason', '')))
        elif action == 'nhan-kho':
            outs, recs = form.getlist('out_line_no'), form.getlist('out_received')
            if len(outs) != len(recs): raise BusinessError('BAD_FORM', 'Dòng thành phẩm chưa đủ dữ liệu, hãy tải lại trang.')
            outputs = [dict(line_no=n, qty_received=int(_qty(q)), serials=sales_warranty.parse_serials(form.get('out_serials_' + n, ''))) for n, q in zip(outs, recs) if n]
            svc.receive(session, user, number, ProductionReceive(version=version, outputs=outputs, note=form.get('note', '')))
        elif action == 'huy':
            body = ProductionVoid(version=version, reason=form.get('reason', ''))
            svc.void(session, user, number, body.reason, version)
        else:
            raise BusinessError('NOT_FOUND', 'Không tìm thấy thao tác.', status=404)
    except (BusinessError, ValidationError) as exc:
        message, code, status = _message(exc)
        return _detail(request, session, user, number, form, message, code, status)
    return RedirectResponse('/san-xuat/' + number + '?da_luu=1', status_code=303)
