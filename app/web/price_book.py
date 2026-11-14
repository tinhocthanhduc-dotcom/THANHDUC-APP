"""Bảng giá chung (TUẤN duyệt mẫu 14/11/2026): /bang-gia 6 tab, sửa dịch vụ/gói, model máy, xuất cho khách, lịch sử, Excel.

Xem: kinh doanh, điều phối, lễ tân, kế toán, kho, thợ. Sửa: chỉ giám đốc. Giá vốn, % lãi: giám đốc + kế toán.
"""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from .. import price_book as svc
from ..db import get_session
from ..errors import BusinessError
from ..models import RepairGroup, RepairModel, User
from . import checked_form, paging, render, web_user

router = APIRouter(include_in_schema=False)


def _tab(tab: str) -> str:
    return tab if tab in svc.TABS else 'muc'


def _group_id(session, nhom) -> int:
    gs = svc.groups(session)
    ids = [g.id for g in gs]
    try:
        gid = int(nhom)
    except (TypeError, ValueError):
        gid = 0
    return gid if gid in ids else (ids[0] if ids else 0)


def _page(request, session, user, tab='muc', q='', nhom='', loc='', trang='', so='', error=None, saved='', status=200, tra=None,
          form=None):
    svc.require_view(user)
    tab = _tab(tab)
    q = q[:200]
    ctx = dict(tab=tab, tabs=svc.TABS, q=q, nhom=nhom, loc=loc, can_edit=svc.can_edit(user), can_cost=svc.can_cost(user),
               levels=svc.LEVELS, error=error.message if error else None, error_code=error.code if error else '', saved=saved,
               status=status, form=form or {})
    counts = svc.product_rows(session, 'muc')['counts']
    counts.update({'dich-vu': len(svc.services(session, 'PHI', active_only=True)), 'goi': len(svc.services(session, 'GOI', active_only=True)),
                   'hien-tuong': len(svc.symptoms(session))})
    ctx['counts'] = counts
    query = dict(tab=tab, q=q, nhom=nhom, loc=loc)
    link = paging.linker('/bang-gia', query)
    ctx['link'] = link
    if tab in svc.PRODUCT_TABS:
        data = svc.product_rows(session, tab, q, nhom, loc, with_cost=ctx['can_cost'])
        page, shown = paging.slice_rows(data['rows'], trang, so, query)
        ctx.update(rows=shown, page=page, groups=data['groups'], chips=data['chips'])
    elif tab == 'dich-vu':
        rows = [s for s in svc.services(session, 'PHI') if all(w in s.search_text for w in svc.fold(q).split())]
        if not ctx['can_edit']:
            rows = [s for s in rows if s.active]
        ctx.update(rows=rows, with_vat=svc.with_vat)
    elif tab == 'goi':
        gid = _group_id(session, nhom)
        ctx.update(rows=svc.package_rows(session, gid, q) if gid else [], machine_groups=svc.groups(session), gid=gid,
                   with_vat=svc.with_vat, model_count=len(svc.models(session)), packages=svc.services(session, 'GOI', active_only=True),
                   model_names=[m.name for m in svc.models(session) if m.active], tra=tra)
        if not ctx['can_edit']:
            ctx['rows'] = [r for r in ctx['rows'] if r['service'].active and not r['empty']]
    else:
        names = {s.id: s.name for s in svc.services(session, 'GOI')}
        gnames = {g.id: g.name for g in svc.groups(session, active_only=False)}
        ctx.update(rows=svc.symptoms(session, q), service_names=names, group_names=gnames,
                   packages=svc.services(session, 'GOI', active_only=True), machine_groups=svc.groups(session))
    return render(request, 'bang_gia.jinja', **ctx)


@router.get('/bang-gia')
def listing(request: Request, tab: str = 'muc', q: str = '', nhom: str = '', loc: str = '', trang: str = '', so: str = '',
            da_luu: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _page(request, session, user, tab, q, nhom, loc, trang, so, saved=da_luu)


@router.get('/bang-gia/tra-gia')
def lookup(request: Request, dv: str = '', model: str = '', cap: str = '2', ch: str = '', nhom: str = '',
           user: User = Depends(web_user), session: Session = Depends(get_session)):
    svc.require_view(user)
    tra = dict(dv=dv, model=model[:160], cap=cap, ch=ch == '1')
    if dv:
        try:
            tra['result'] = svc.lookup(session, dv, model, cap, genuine=ch == '1')
        except BusinessError as exc:
            tra['result'] = dict(ok=False, reason=exc.message)
    m = svc.find_model(session, model)
    return _page(request, session, user, 'goi', nhom=str(m.group_id) if m else nhom, tra=tra)


# ---------- dịch vụ ----------

def _service_page(request, session, user, service=None, kind='GOI', form=None, error=None, status=200, saved=''):
    svc.require_view(user)
    s = service
    kind = s.kind if s else kind
    table = svc.price_table(session, s.id) if s else {'group': {}, 'model': {}}
    models = {m.id: m for m in svc.models(session)}
    overrides = sorted(((models[mid], lv) for mid, lv in table['model'].items() if mid in models), key=lambda x: x[0].name.lower())
    opts = svc.options(session, s.id) if s else []
    return render(request, 'bang_gia_dich_vu.jinja', s=s, kind=kind, form=form, machine_groups=svc.groups(session, active_only=False),
                  table=table, overrides=overrides, options=opts, levels=svc.LEVELS, can_edit=svc.can_edit(user),
                  model_names=[m.name for m in models.values() if m.active], with_vat=svc.with_vat,
                  sample_name=svc.customer_name(s, 'Canon LBP 2900') if s else '', saved=saved,
                  error=error.message if error else None, error_code=error.code if error else '', status=status)


@router.get('/bang-gia/dich-vu/moi')
def new_service(request: Request, kind: str = 'GOI', user: User = Depends(web_user), session: Session = Depends(get_session)):
    svc.require_edit(user)
    return _service_page(request, session, user, kind='PHI' if kind.upper() == 'PHI' else 'GOI')


@router.get('/bang-gia/dich-vu/{service_id}')
def show_service(request: Request, service_id: int, da_luu: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _service_page(request, session, user, svc.load_service(session, service_id), saved=da_luu)


@router.post('/bang-gia/dich-vu/luu')
def save_service(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    svc.require_edit(user)
    data = {k: form.get(k) for k in form.keys()}
    try:
        s = svc.save_service(session, user, data)
    except BusinessError as exc:
        session.rollback()
        if exc.status == 403:
            raise
        sid = str(data.get('id') or '')
        service = svc.load_service(session, sid) if sid.isdigit() else None
        return _service_page(request, session, user, service, kind=str(data.get('kind', 'GOI')).upper(), form=data, error=exc, status=exc.status)
    return RedirectResponse(f'/bang-gia/dich-vu/{s.id}?da_luu=1', status_code=303)


@router.post('/bang-gia/dich-vu/{service_id}/gia-model')
def save_model_price(request: Request, service_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    svc.require_edit(user)
    data = {k: form.get(k) for k in form.keys()}
    try:
        if data.get('xoa_model'):
            svc.delete_model_price(session, user, service_id, data['xoa_model'])
        else:
            svc.save_model_price(session, user, service_id, str(data.get('model', '')), data)
    except BusinessError as exc:
        session.rollback()
        if exc.status == 403:
            raise
        return _service_page(request, session, user, svc.load_service(session, service_id), form=data, error=exc, status=exc.status)
    return RedirectResponse(f'/bang-gia/dich-vu/{service_id}?da_luu=model#gia-model', status_code=303)


# ---------- model máy ----------

def _models_page(request, session, user, q='', nhom='', form=None, error=None, status=200, saved=''):
    svc.require_view(user)
    gid = int(nhom) if str(nhom).isdigit() else None
    rows = svc.models(session, q[:200], gid)
    gnames = {g.id: g.name for g in svc.groups(session, active_only=False)}
    return render(request, 'bang_gia_model.jinja', rows=rows, q=q, nhom=nhom, machine_groups=svc.groups(session), group_names=gnames,
                  suggestions=svc.known_model_names(session) if svc.can_edit(user) else [], can_edit=svc.can_edit(user), form=form or {},
                  saved=saved, error=error.message if error else None, error_code=error.code if error else '', status=status)


@router.get('/bang-gia/model-may')
def models_page(request: Request, q: str = '', nhom: str = '', da_luu: str = '', user: User = Depends(web_user),
                session: Session = Depends(get_session)):
    return _models_page(request, session, user, q, nhom, saved=da_luu)


@router.post('/bang-gia/model-may/luu')
def save_model(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    svc.require_edit(user)
    data = {k: form.get(k) for k in form.keys()}
    mid = str(data.get('id', ''))
    try:
        svc.save_model(session, user, str(data.get('name', '')), data.get('group_id'), str(data.get('note', '')),
                       int(mid) if mid.isdigit() else None, active=str(data.get('active', '1')) == '1')
    except BusinessError as exc:
        session.rollback()
        if exc.status == 403:
            raise
        return _models_page(request, session, user, form=data, error=exc, status=exc.status)
    return RedirectResponse('/bang-gia/model-may?da_luu=1', status_code=303)


# ---------- hiện tượng ----------

@router.post('/bang-gia/hien-tuong/luu')
def save_symptom(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    svc.require_edit(user)
    data = {k: form.get(k) for k in form.keys()}
    try:
        svc.save_symptom(session, user, data)
    except BusinessError as exc:
        session.rollback()
        if exc.status == 403:
            raise
        return _page(request, session, user, 'hien-tuong', error=exc, status=exc.status, form=data)
    return RedirectResponse('/bang-gia?tab=hien-tuong&da_luu=1', status_code=303)


# ---------- xuất cho khách, lịch sử, Excel ----------

@router.get('/bang-gia/in')
def print_page(request: Request, tab: str = 'muc', nhom: str = '', q: str = '', vat: str = '1', user: User = Depends(web_user),
               session: Session = Depends(get_session)):
    """Bảng giá gửi khách: chỉ tên, đơn vị, giá, bảo hành. Không có mã kho, tồn, giá vốn (máy chủ không gửi)."""
    svc.require_view(user)
    tab = _tab(tab)
    show_vat = vat != '0'
    title, rows, cols = svc.TABS[tab], [], 3
    if tab in svc.PRODUCT_TABS:
        for r in svc.product_rows(session, tab, q, nhom)['rows']:
            rows.append(dict(name=r['name'], unit=r['unit'], warranty=r['warranty'], prices=[r['vat'] if show_vat else r['price']]))
    elif tab == 'dich-vu':
        for s in svc.services(session, 'PHI', active_only=True):
            rows.append(dict(name=svc.customer_name(s), unit=s.unit, warranty=s.warranty_months, note=s.description,
                             prices=[svc.with_vat(s.price) if show_vat else s.price]))
    elif tab == 'goi':
        gid = _group_id(session, nhom)
        g = session.get(RepairGroup, gid) if gid else None
        title = f'Bảng giá sửa chữa máy {g.name[0].lower() + g.name[1:]}' if g else title
        for r in (svc.package_rows(session, gid) if gid else []):
            if r['empty'] or not r['service'].active:
                continue
            prices = [(svc.with_vat(lv.price) if show_vat else lv.price) if lv else None for lv in (r['levels'].get(i) for i in (1, 2, 3))]
            months = [str(r['levels'][i].warranty_months) if i in r['levels'] else '—' for i in (1, 2, 3)]
            rows.append(dict(name=svc.customer_name(r['service']), note=r['service'].description, prices=prices,
                             warranty_text=' · '.join(months)))
        cols = 5
    else:
        raise BusinessError('BAD_TAB', 'Tab Hiện tượng không xuất cho khách.', status=404)
    return render(request, 'bang_gia_in.jinja', title=title, rows=rows, tab=tab, show_vat=show_vat, cols=cols, today=_today(), nhom=nhom)


def _today():
    from .lists import _today as today
    return today()


@router.get('/bang-gia/lich-su')
def history(request: Request, trang: str = '', so: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    svc.require_view(user)
    page = paging.build(trang, so, 0, {})
    rows, total = svc.history(session, page['offset'], page['size'])
    page = paging.build(trang, so, total, {})
    rows, _ = svc.history(session, page['offset'], page['size'])
    return render(request, 'bang_gia_lich_su.jinja', rows=rows, page=page)


@router.get('/bang-gia/excel')
def export(request: Request, tab: str = 'muc', user: User = Depends(web_user), session: Session = Depends(get_session)):
    svc.require_edit(user)
    tab = _tab(tab)
    body = svc.export_csv(session, tab)
    return Response(body.encode('utf-8'), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="bang-gia-{tab}.csv"', 'Cache-Control': 'private, no-store'})


@router.post('/bang-gia/nhap-excel')
async def import_preview(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    svc.require_edit(user)
    tab = _tab(str(form.get('tab', '')))
    upload = form.get('file')
    content = form.get('content')
    try:
        if isinstance(upload, UploadFile) and upload.filename:
            raw = await upload.read(2_000_001)
        elif content:
            raw = str(content).encode('utf-8')
        else:
            raise BusinessError('FILE_REQUIRED', 'Chọn file CSV đã sửa.')
        if form.get('ap_dung') == '1':
            n = svc.apply_import(session, user, tab, raw)
            return RedirectResponse('/bang-gia?' + urlencode(dict(tab=tab, da_luu=f'excel-{n}')), status_code=303)
        plan = svc.plan_import(session, tab, raw)
    except BusinessError as exc:
        session.rollback()
        if exc.status == 403:
            raise
        return _page(request, session, user, tab, error=exc, status=exc.status)
    return render(request, 'bang_gia_nhap.jinja', tab=tab, tabs=svc.TABS, plan=plan, content=raw.decode('utf-8-sig', 'replace'))
