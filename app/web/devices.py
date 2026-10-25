"""Địa điểm trong trang khách và danh mục thiết bị D1."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import _customer_dict, _customer_form, checked_form, render, web_user
from .. import customer_sites, customers, equipment, products
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, CustomerSite, Equipment, Product, User
from ..schemas import CustomerSiteIn, CustomerSitePatch, EquipmentIn, EquipmentPatch
from ..security import has_role

router = APIRouter(include_in_schema=False)


def _error(exc):
    if isinstance(exc, BusinessError):
        return exc.message
    fields = {'name': 'Tên địa điểm', 'product_code': 'Mã hàng', 'model_name': 'Tên máy', 'customer_code': 'Mã khách', 'site_id': 'Địa điểm',
              'serial': 'Số sê-ri', 'warranty_until': 'Ngày hết bảo hành', 'version': 'Phiên bản', 'condition': 'Tình trạng'}
    where = exc.errors()[0]['loc']
    if not where:
        return 'Cần chọn hàng trong danh mục hoặc nhập tên máy.'
    return fields.get(str(where[-1]), 'Dữ liệu') + ' chưa hợp lệ, hãy kiểm tra lại.'


def _site_data(form):
    return {**{key: str(form.get(key, '')) for key in ('name', 'address', 'contact_name', 'contact_phone', 'note')},
            'active': form.get('active') == '1'}


def _save_site(request, session, form, customer_code, site_id=None):
    customer_sites.check_writer(request.state.user)
    customer = customers.load(session, customer_code, resolve_merged=False)
    shown = {**_site_data(form), 'version': form.get('version', '')}
    if site_id is not None and customer_sites.load(session, site_id).customer_code != customer.code:
        raise BusinessError('BAD_SITE', 'Địa điểm không thuộc khách hàng này.', status=404)
    try:
        if site_id is None:
            customer_sites.create(session, request.state.user, customer.code, CustomerSiteIn(**_site_data(form)))
        else:
            customer_sites.update(session, request.state.user, site_id, CustomerSitePatch(**shown))
    except (BusinessError, ValidationError) as exc:
        return _customer_form(request, request.state.user, _customer_dict(customer), session=session,
                              error=None, ask_confirm=None, site_error=_error(exc), site_shown=shown,
                              site_target=site_id, status=getattr(exc, 'status', 400))
    return RedirectResponse(f'/khach-hang/{quote(customer.code, safe="")}/sua?da_luu=1#dia-diem', status_code=303)


@router.post('/khach-hang/{customer_code}/dia-diem/moi')
def site_create(request: Request, customer_code: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    return _save_site(request, session, form, customer_code)


@router.post('/khach-hang/{customer_code}/dia-diem/{site_id}')
def site_update(request: Request, customer_code: str, site_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    return _save_site(request, session, form, customer_code, site_id)


@router.get('/thiet-bi')
def listing(request: Request, q: str = '', phan: int = 0, tt: str = '', bh: str = '', o: str = '', trang: str = '', so: str = '',
            user: User = Depends(web_user), session: Session = Depends(get_session)):
    if not phan:
        from .lists import devices_page
        return devices_page(request, session, user, q, tt, bh, o, trang, so, can_write=has_role(user, *customer_sites.WRITERS))
    rows = equipment.search(session, q[:200])
    return render(request, 'thiet_bi_dong.jinja' if phan else 'thiet_bi_ds.jinja', rows=rows, q=q,
                  product_names={code: name for code, name in session.execute(select(Product.code, Product.name).where(Product.code.in_([r.product_code for r in rows if r.product_code])))},
                  customer_names={code: name for code, name in session.execute(select(Customer.code, Customer.name).where(Customer.code.in_([r.customer_code for r in rows if r.customer_code])))},
                  can_device_write=has_role(user, *customer_sites.WRITERS))


@router.get('/thiet-bi/tim-hang')
def pick_product(request: Request, q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    return render(request, 'thiet_bi_chon.jinja', rows=products.search(session, q[:200], active_only=False, limit=20), field='product_code', q=q[:160].strip())


@router.get('/thiet-bi/tim-khach')
def pick_customer(request: Request, q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    return render(request, 'thiet_bi_chon.jinja', rows=customers.search(session, q[:200]), field='customer_code', q='')


@router.get('/thiet-bi/dia-diem')
def pick_site(request: Request, customer_code: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    return render(request, 'thiet_bi_dia_diem.jinja', sites=customer_sites.search(session, customer_code, active_only=True), selected_site=None)


def _form(request, session, data, is_new=False, error=None, status=200, saved=False):
    sites = []
    selected = int(str(data.get('site_id'))) if str(data.get('site_id', '')).isdigit() else None
    if data.get('customer_code'):
        sites = list(session.scalars(select(CustomerSite).where(CustomerSite.customer_code == data['customer_code'],
                                  or_(CustomerSite.active.is_(True), CustomerSite.id == selected)).order_by(CustomerSite.id)))
    product = session.get(Product, data.get('product_code')) if data.get('product_code') else None
    customer = session.get(Customer, data.get('customer_code')) if data.get('customer_code') else None
    from ..equipment_custody import history, LOCATIONS, STATUSES, ACCESSORIES
    return render(request, 'thiet_bi_form.jinja',custody_history=history(session,data['serial']) if not is_new else [],
                  custody_locations=LOCATIONS,custody_statuses=STATUSES,custody_accessories=ACCESSORIES,custody_users=dict(session.execute(select(User.id,User.full_name)).all()), d=data, sites=sites, selected_site=data.get('site_id'),
                  product_name=product.name if product else '', customer_name=customer.name if customer else '',
                  is_new=is_new, error=error, saved=saved, status=status,
                  can_device_write=has_role(request.state.user, *customer_sites.WRITERS))


@router.get('/thiet-bi/moi')
def new(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _form(request, session, dict(serial='', product_code='', model_name='', customer_code=None, site_id=None, warranty_until=None,
                                      condition='MOI', note='', active=True, version=0), is_new=True)


def _picked(session, model, code, text):
    """Mã đã chọn trong danh sách; nếu chưa bấm chọn thì chỉ nhận khi chữ gõ khớp đúng một mã hoặc một tên."""
    code, text = str(code or '').strip(), str(text or '').strip()
    if code or not text:
        return code
    key = text.split(' · ')[0].strip()
    rows = session.scalars(select(model.code).where(or_(func.upper(model.code) == key.upper(),
                                                        func.lower(model.name) == text.lower())).limit(2)).all()
    return rows[0] if len(rows) == 1 else None


def _equipment_data(form, session):
    # Một ô duy nhất: bấm chọn hàng trong danh mục thì có mã hàng; không chọn thì tên đã gõ là tên máy ngoài danh mục.
    text = str(form.get('product_q', '')).strip()
    product = _picked(session, Product, form.get('product_code'), text)
    model_name = '' if product else text[:160]
    customer = _picked(session, Customer, form.get('customer_code'), form.get('customer_q'))
    if not product and not model_name:
        raise BusinessError('PRODUCT_NOT_PICKED', 'Chưa có máy: gõ tên hoặc mã máy, có trong Hàng hóa thì bấm chọn một dòng.')
    if customer is None:
        raise BusinessError('CUSTOMER_NOT_PICKED', 'Chưa chọn khách: gõ tên hoặc mã rồi bấm chọn một dòng trong danh sách hiện ra.')
    return dict(product_code=product or None, model_name=model_name, customer_code=customer or None,
                site_id=form.get('site_id') or None, warranty_until=form.get('warranty_until') or None, support_until=form.get('support_until') or None,
                condition=str(form.get('condition', 'MOI')), note=str(form.get('note', '')), active=form.get('active') == '1')


@router.post('/thiet-bi/moi')
def create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    customer_sites.check_writer(request.state.user)
    shown = dict(serial=str(form.get('serial', '')), product_code=str(form.get('product_code', '')),
                 model_name='', customer_code=form.get('customer_code') or None, site_id=form.get('site_id') or None,
                 warranty_until=form.get('warranty_until') or None, support_until=form.get('support_until') or None, condition=str(form.get('condition', 'MOI')),
                 note=str(form.get('note', '')), active=form.get('active') == '1', version=0,
                 product_q=str(form.get('product_q', '')), customer_q=str(form.get('customer_q', '')))
    try:
        data = dict(serial=shown['serial'], **_equipment_data(form, session))
        row = equipment.create(session, request.state.user, EquipmentIn(**data))
    except (BusinessError, ValidationError) as exc:
        return _form(request, session, shown, is_new=True, error=_error(exc), status=getattr(exc, 'status', 400))
    return RedirectResponse('/thiet-bi/' + quote(row.serial, safe='') + '?da_luu=1', status_code=303)


@router.get('/thiet-bi/{serial:path}')
def view(request: Request, serial: str, da_luu: int = 0, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _form(request, session, equipment.snapshot(equipment.load(session, serial)), saved=bool(da_luu))


@router.post('/thiet-bi/{serial:path}')
def update(request: Request, serial: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    customer_sites.check_writer(request.state.user)
    shown = {**equipment.snapshot(equipment.load(session, serial)), 'version': form.get('version', ''),
             'product_code': str(form.get('product_code', '')), 'customer_code': form.get('customer_code') or None,
             'product_q': str(form.get('product_q', '')), 'customer_q': str(form.get('customer_q', ''))}
    try:
        data = _equipment_data(form, session)
        shown.update(data)
        row = equipment.update(session, request.state.user, serial, EquipmentPatch(**data, version=form.get('version')))
    except (BusinessError, ValidationError) as exc:
        return _form(request, session, shown, error=_error(exc), status=getattr(exc, 'status', 400))
    return RedirectResponse('/thiet-bi/' + quote(row.serial, safe='') + '?da_luu=1', status_code=303)
