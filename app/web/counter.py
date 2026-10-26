"""Máy khách tại quầy (TUẤN duyệt 26/10/2026): nhận máy khách mang tới, khách tới lấy và trả tiền tại quầy.

Điều phối, giám đốc và kinh doanh dùng; kinh doanh chỉ thấy phiếu nhận tại quầy (quyền kiểm ở lõi service_counter).
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from .. import customers as customer_svc, equipment, equipment_custody, sales_receipts, service_counter as counter, service_tickets as svc
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, Equipment, EquipmentCustody, Product, ServiceLine, User
from ..schemas import CustodyIntake, ServiceTicketIn
from ..security import has_role
from . import checked_form, paging, picked, render, web_user
from .custody import _accessory_values
from .services import _choices, _message, intake_print_page
from . import service_machines
from .. import search as tim

router = APIRouter()
TABS = (('xuong', 'Đang ở xưởng'), ('cho-lay', 'Chờ khách lấy'), ('tho-tra', 'Thợ mang trả'), ('da-tra', 'Đã trả'))


def _today():
    return svc._now().astimezone(svc.service_sla.TZ).date()


def _tab(t):
    if t.status == 'TAM_DUNG' and t.hold_reason == counter.WAITING:return 'xuong'
    if t.status == 'TAM_DUNG' and t.hold_reason == counter.READY:return 'cho-lay'
    if t.status in ('HOAN_THANH', 'DA_DONG', 'DA_HUY', 'KHONG_LAM_DUOC'):return 'da-tra'
    return 'tho-tra'


def stage(session, t):
    """Một dòng trạng thái dễ hiểu cho người ở quầy."""
    if t.status == 'TAM_DUNG' and t.hold_reason == counter.WAITING:
        if t.quote_approved_at:
            return 'Khách đã đồng ý' + (f' · hẹn trả {t.promised_return_on:%d/%m}' if t.promised_return_on else '')
        priced = session.scalar(select(ServiceLine.id).where(ServiceLine.ticket_id == t.id, ServiceLine.amount + ServiceLine.vat_amount > 0).limit(1))
        return 'Chờ khách duyệt báo giá' if priced else 'Chờ báo giá'
    if t.status == 'TAM_DUNG' and t.hold_reason == counter.READY:
        return 'Khách không sửa · chờ khách lấy' if t.declined_at else 'Sửa xong · chờ khách lấy'
    if t.status in ('HOAN_THANH', 'DA_DONG'):return 'Đã trả khách'
    if t.status == 'DA_HUY':return 'Đã hủy'
    if t.status == 'KHONG_LAM_DUOC':return 'Không làm được'
    return 'Thợ mang trả'


@router.get('/quay-sua')
def index(request: Request, tab: str = 'xuong', q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    rows = counter.rows(session, user)
    key = q.strip()[:100].lower()
    if key:
        rows = [r for r in rows if tim.text_matches(key, ' '.join((r['ticket'].number, r['customer'] or '', r['machine'] or '', r['ticket'].serial or '')))]
    counts = {k: 0 for k, _ in TABS}
    for r in rows:counts[_tab(r['ticket'])] += 1
    tab = tab if tab in counts else 'xuong'
    shown = [dict(r, stage=stage(session, r['ticket'])) for r in rows if _tab(r['ticket']) == tab]
    query = dict(tab=tab, q=q)
    page, shown = paging.slice_rows(shown, request.query_params.get('trang'), request.query_params.get('so'), query)
    link = paging.linker('/quay-sua', query)
    return render(request, 'quay_ds.jinja', rows=shown, p=page, q=q, tab=tab,
                  tabs=[dict(label=label, n=counts[k], on=k == tab, href=link(tab=k)) for k, label in TABS])


def _form(request, session, data=None, error='', error_code='', status=200):
    data = data or dict(customer_code='', serial='', symptom='', accessories=[], accessories_note='', condition_note='', expected='', expected_pick='')
    customer = session.get(Customer, data['customer_code']) if data['customer_code'] else None
    sites, machines = _choices(session, data['customer_code'])
    if not error and not data.get('serial') and len(machines) == 1:
        data['serial'] = machines[0][0].serial
    today = _today()
    days = [('Hôm nay', today), ('Ngày mai', today + timedelta(days=1)), ('2 ngày', today + timedelta(days=2)), ('3 ngày', today + timedelta(days=3))]
    return render(request, 'quay_nhan.jinja', d=data, customer_card=picked.customer_cards(session, request.state.user, [customer]).get(customer.code) if customer else None,
                  customer_name=customer.name if customer else '', sites=sites, sites_main_address=customer.address if customer else '', machines=machines,
                  can_create_machine=True, accessories=equipment_custody.ACCESSORIES, days=days,
                  error=error, error_code=error_code, status=status)


@router.get('/quay-sua/nhan')
def new(request: Request, customer_code: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    counter._require(user)
    chosen = session.get(Customer, customer_code[:50]) if customer_code else None
    data = None
    if chosen and chosen.status == 'DANG_DUNG':
        data = dict(customer_code=chosen.code, serial='', symptom='', accessories=[], accessories_note='', condition_note='', expected='', expected_pick='')
    return _form(request, session, data)


@router.get('/quay-sua/tim-khach')
def pick_customer(request: Request, q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    counter._require(user)
    rows = [c for c in customer_svc.search(session, q[:200]) if c.status == 'DANG_DUNG']
    return render(request, 'dich_vu_chon_khach.jinja', rows=rows, customer_cards=picked.customer_cards(session, user, rows))


@router.get('/quay-sua/chon-khach')
def pick_machine(request: Request, customer_code: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    counter._require(user)
    sites, machines = _choices(session, customer_code)
    return render(request, 'quay_chon_may.jinja', machines=machines, can_create_machine=True,
                  d=dict(serial=machines[0][0].serial if len(machines) == 1 else ''))


async def _photo(form, name='photo'):
    upload = form.get(name)
    if not isinstance(upload, UploadFile) or not upload.filename:
        return None
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise BusinessError('FILE_TOO_LARGE', 'Ảnh quá lớn.', {'max_mb': get_settings().max_upload_mb})
    return upload.filename, data


def _expected(form):
    raw = str(form.get('expected_pick', '') or form.get('expected', '')).strip()
    if not raw:return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise BusinessError('BAD_APPOINTMENT', 'Ngày hẹn khách quay lại chưa hợp lệ.')


@router.post('/quay-sua/nhan')
async def create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    keys = ('customer_code', 'serial', 'symptom', 'accessories_note', 'condition_note', 'expected', 'expected_pick',
            'create_machine', 'new_serial', 'new_product_code', 'new_product_q')
    data = {k: str(form.get(k, '')) for k in keys}
    data['accessories'] = [str(v) for v in form.getlist('accessories')]
    try:
        photo = await _photo(form)
        new_machine = service_machines.intake(form)
        body = ServiceTicketIn(customer_code=data['customer_code'], serial=None if new_machine else data['serial'] or None,
                               new_machine=new_machine, kind='MANG_VE', priority='THUONG', symptom=data['symptom'])
        t = counter.intake(session, user, body, _accessory_values(form), data['accessories_note'], data['condition_note'], photo, _expected(form))
    except (BusinessError, ValidationError) as exc:
        message, code, status = _message(exc)
        return _form(request, session, data, message, code, status)
    return RedirectResponse(f'/quay-sua/{t.id}?da_nhan=1', status_code=303)


def _detail(request, session, user, ticket_id, error='', error_code='', status=200, ret=None):
    t = counter.load(session, user, ticket_id)
    customer = session.get(Customer, t.customer_code)
    machine = session.get(Equipment, t.serial) if t.serial else None
    machine_name = equipment.display_name(machine, session.get(Product, machine.product_code) if machine and machine.product_code else None) if machine else ''
    first = session.scalar(select(EquipmentCustody).where(EquipmentCustody.ticket_id == t.id, EquipmentCustody.status == 'DA_NHAN',
        EquipmentCustody.location.in_(('THO_GIU', 'KHO_KY_THUAT'))).order_by(EquipmentCustody.id).limit(1))
    users = dict(session.execute(select(User.id, User.full_name)).all())
    ready = t.status == 'TAM_DUNG' and t.hold_reason == counter.READY
    at_shop = bool(machine and machine.custody_ticket_id == t.id and machine.custody_location == 'KHO_KY_THUAT')
    money = None
    if t.receivable is not None:
        b = sales_receipts.balance(t)
        money = dict(receivable=t.receivable or 0, paid=int(b.get('paid', 0)), pending=int(b.get('pending', 0)), remaining=int(b['remaining']))
    walk_in = counter.walk_in(session, t.id)
    steps = [('Nhận máy tại quầy' if walk_in else 'Nhận máy', 'done'),
             ('Báo giá', 'done' if t.quote_approved_at or t.priced_at else 'cur' if t.hold_reason == counter.WAITING else ''),
             ('Khách đồng ý', 'done' if t.quote_approved_at or t.priced_at else ''),
             ('Sửa xong', 'done' if t.priced_at else ''),
             ('Khách lấy máy', 'done' if t.status in ('HOAN_THANH', 'DA_DONG') else 'cur' if ready else '')]
    if t.hold_reason == counter.WAITING and t.quote_approved_at:
        steps[3] = ('Sửa xong', 'cur')
    return render(request, 'quay_phieu.jinja', t=t, customer=customer, machine=machine, machine_name=machine_name, first=first, users=users,
                  stage=stage(session, t), steps=steps, ready=ready, at_shop=at_shop, money=money, walk_in=walk_in,
                  accessories=equipment_custody.ACCESSORIES, custody_locations=equipment_custody.LOCATIONS, ret=ret or {}, can_dispatch=has_role(user, 'dispatcher'),
                  just_received=request.query_params.get('da_nhan') == '1', just_returned=request.query_params.get('da_tra') == '1',
                  error=error, error_code=error_code, status=status)


@router.get('/quay-sua/{ticket_id}')
def detail(request: Request, ticket_id: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _detail(request, session, user, ticket_id)


@router.post('/quay-sua/{ticket_id}/tra')
async def give_back(request: Request, ticket_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    ret = {k: str(form.get(k, '')) for k in ('mode', 'partial', 'method', 'note', 'accessories_note', 'condition_note')}
    ret['accessories'] = [str(v) for v in form.getlist('accessories')]
    try:
        photo = await _photo(form)
        version = form.get('version')
        body = CustodyIntake(version=form.get('machine_version'), accessories=_accessory_values(form),
                             accessories_note=ret['accessories_note'], condition_note=ret['condition_note'])
        counter.return_at_counter(session, user, ticket_id, int(version) if str(version).isdigit() else None, body,
                                  ret['mode'], ret['partial'], ret['method'] or 'TIEN_MAT', ret['note'].strip()[:2000], photo)
    except (BusinessError, ValidationError) as exc:
        message, code, status = _message(exc)
        return _detail(request, session, user, ticket_id, message, code, status, ret)
    return RedirectResponse(f'/quay-sua/{ticket_id}?da_tra=1', status_code=303)


@router.get('/quay-sua/{ticket_id}/phieu-nhan-may')
def intake_print(request: Request, ticket_id: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    t = counter.load(session, user, ticket_id)
    return intake_print_page(request, session, t, back=f'/quay-sua/{t.id}')
