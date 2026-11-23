"""Trả máy cả chuyến (TUẤN duyệt mẫu 23/11/2026): giao thợ trả nhiều máy một lần, thợ trả cả chuyến một lần vuốt.

Lõi: app/service_return_trip.py (dùng lại các bước Giao thợ trả máy, trả máy, hoàn thành, báo thu của từng phiếu).
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import equipment, service_appointments, service_return_trip as trip, service_tickets, vietqr
from ..db import get_session
from ..errors import BusinessError
from ..models import Customer, CustomerSite, Equipment, Product, ServiceTicket, User
from . import checked_form, render, web_user
from .money_input import parse_money

router = APIRouter()
METHODS = {'TIEN_MAT': 'Tiền mặt', 'CHUYEN_KHOAN': 'Chuyển khoản'}


def _names(session, rows):
    serials = [t.serial for t in rows if t.serial]
    return dict(session.execute(select(Equipment.serial, equipment.name_column()).outerjoin(Product, Product.code == Equipment.product_code)
                                .where(Equipment.serial.in_(serials))).all()) if serials else {}


def _message(exc):
    from .services import _message as msg
    return msg(exc)


# ---------- Màn 1: điều phối giao thợ trả cả chuyến ----------

def _schedule_page(request, session, user, code, form=None, error=None, error_code='', status=200):
    from .. import service_workshop
    service_tickets._require(user, service_tickets.DISPATCHERS, 'Chỉ điều phối hoặc giám đốc được giao thợ trả máy.')
    rows = trip.ready_by_customer(session, code).get(code, [])
    customer = session.get(Customer, code)
    if customer is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy khách hàng.', status=404)
    picked = {int(x) for x in (form.getlist('p') if form is not None else []) if str(x).isdigit()} or {t.id for t in rows}
    return render(request, 'tra_ca_chuyen_giao.jinja', customer=customer, rows=rows, names=_names(session, rows), picked=picked,
                  technicians=service_workshop.repairers(session), form=form or {},
                  appointment_return_choices=service_appointments.choices(service_tickets._now(), True), error=error, error_code=error_code, status=status)


@router.get('/tra-may-ca-chuyen')
def schedule_form(request: Request, khach: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _schedule_page(request, session, user, khach)


@router.post('/tra-may-ca-chuyen')
def schedule(request: Request, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    code = str(form.get('khach', ''))
    try:
        items = []
        for raw in form.getlist('p'):
            ident = str(raw)
            version = str(form.get(f'v_{ident}', ''))
            if not ident.isdigit() or not version.isdigit():
                raise BusinessError('BAD_FORM', 'Danh sách máy không hợp lệ, tải lại trang.')
            items.append((int(ident), int(version)))
        appointment, slot = service_appointments.parse(form, service_tickets._now())
        tech = str(form.get('assignee_id', ''))
        if not tech.isdigit():
            raise BusinessError('BAD_ASSIGNEE', 'Chọn thợ đi trả máy.')
        rows = trip.schedule_many(session, user, items, appointment, slot, int(tech), str(form.get('note', ''))[:2000])
    except (BusinessError, ValidationError) as exc:
        session.rollback()
        message, err, status = _message(exc)
        return _schedule_page(request, session, user, code, form=form, error=message, error_code=err, status=status)
    return RedirectResponse(f'/kiem-tra-may?view=s6&giao_chuyen={len(rows)}', status_code=303)


# ---------- Màn 2: thợ trả cả chuyến ----------

def _trip_page(request, session, user, ticket_id, data=None, error=None, error_code='', status=200):
    from .. import attachments
    t = service_tickets.load(session, user, ticket_id)
    rows = trip.trip_for(session, user, t)
    if not rows:
        return RedirectResponse(f'/tho/phieu/{ticket_id}', status_code=303)
    customer = session.get(Customer, t.customer_code)
    site = session.get(CustomerSite, t.site_id) if t.site_id else None
    photos = [a for a in attachments.list_for(session, user, 'service_ticket', str(t.id)) if a.tag == 'TRA_MAY' and a.content_type.startswith('image/')]
    missing = [x.number for x in rows if not any(a.tag == 'TRA_MAY' for a in attachments.list_for(session, user, 'service_ticket', str(x.id)))]
    dues = {x.id: trip.due(x) for x in rows}
    machines = {x.id: session.get(Equipment, x.serial) for x in rows if x.serial}
    waiting = [x.number for x in rows if machines.get(x.id) is not None and (machines[x.id].custody_location != 'THO_GIU' or machines[x.id].custody_holder_id != user.id)]
    picked = set(data['picks']) if data and data.get('picks') is not None else {x.id for x in rows}
    data = data or dict(mode='FULL', method='TIEN_MAT', amount='', note='')
    total = sum(dues.values())
    return render(request, 'tra_ca_chuyen_tho.jinja', t=t, rows=rows, names=_names(session, rows), customer=customer, site=site,
                  photos=photos, missing=missing, waiting=waiting, dues=dues, machines=machines, picked=picked, total=total, data=data, methods=METHODS,
                  qr_url=f'/qr/dich-vu/{t.id}' if total and vietqr.company_account(session) else '',
                  error=error, error_code=error_code, status=status)


@router.get('/tho/chuyen-tra/{ticket_id}')
def trip_form(request: Request, ticket_id: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _trip_page(request, session, user, ticket_id)


@router.post('/tho/chuyen-tra/{ticket_id}/anh')
async def trip_photo(request: Request, ticket_id: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    """Một ảnh trả máy gắn vào mọi phiếu của chuyến (thợ chụp chung các máy tại chỗ khách)."""
    from starlette.datastructures import UploadFile
    from .. import attachments
    from ..config import get_settings
    t = service_tickets.load(session, user, ticket_id)
    rows = trip.trip_for(session, user, t)
    upload = form.get('file')
    try:
        if not rows:
            raise BusinessError('NOT_IN_TRIP', 'Phiếu không còn thuộc chuyến trả nào.', status=409)
        if not isinstance(upload, UploadFile):
            raise BusinessError('EMPTY_FILE', 'Chọn ảnh cần tải.')
        data = await upload.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
        if data.startswith(b'%PDF-'):
            raise BusinessError('BAD_FILE_TYPE', 'Ảnh trả máy phải là ảnh.')
        for x in rows:
            attachments.add(session, user, 'service_ticket', str(x.id), 'TRA_MAY', upload.filename or 'tra-may.jpg', data)
    except BusinessError as exc:
        session.rollback()
        return _trip_page(request, session, user, ticket_id, error=exc.message, error_code=exc.code, status=exc.status)
    return RedirectResponse(f'/tho/chuyen-tra/{ticket_id}?anh=1#anh', status_code=303)


@router.post('/tho/chuyen-tra/{ticket_id}/hoan-thanh')
def trip_complete(request: Request, ticket_id: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    data = {key: str(form.get(key, '')) for key in ('mode', 'method', 'amount', 'note')}
    data['picks'] = [int(x) for x in form.getlist('p') if str(x).isdigit()]
    try:
        picks = []
        for ident in data['picks']:
            version, mv = str(form.get(f'v_{ident}', '')), str(form.get(f'mv_{ident}', '') or '1')
            if not version.isdigit() or not mv.isdigit():
                raise BusinessError('BAD_FORM', 'Danh sách máy không hợp lệ, tải lại trang.')
            picks.append((ident, int(version), int(mv)))
        if data['method'] not in METHODS:
            raise BusinessError('BAD_METHOD', 'Chọn cách thu: Tiền mặt hoặc Chuyển khoản.')
        session.expire_all()
        total = sum(trip.due(session.get(ServiceTicket, i)) for i, _, _ in picks)
        if data['mode'] == 'FULL':
            amount = total
        elif data['mode'] == 'PARTIAL':
            amount = parse_money(data['amount'], 'Số tiền khách đưa', allow_zero=False)
            if amount >= total:
                raise BusinessError('BAD_PARTIAL_AMOUNT', 'Thu một phần phải nhỏ hơn tổng phải thu; khách trả đủ thì chọn Thu đủ.')
        elif data['mode'] == 'LATER':
            amount = 0
        else:
            raise BusinessError('BAD_MODE', 'Chọn Thu đủ, Thu một phần hoặc Khách trả sau.')
        arrive = dict(note='Đến khách trả máy cả chuyến', lat=form.get('gps_lat'), lng=form.get('gps_lng'), accuracy=form.get('gps_accuracy'))
        done = trip.complete(session, user, ticket_id, picks, amount, data['method'], data['note'][:500], arrive=arrive)
    except (BusinessError, ValidationError) as exc:
        session.rollback()
        message, err, status = _message(exc)
        if err in ('FORBIDDEN', 'NOT_FOUND'):
            raise
        return _trip_page(request, session, user, ticket_id, data=data, error=message, error_code=err, status=status)
    return RedirectResponse(f'/tho?tra_chuyen={len(done)}', status_code=303)


@router.post('/tho/chuyen-tra/{ticket_id}/nhan-may')
def trip_receive(request: Request, ticket_id: int, form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    """Thợ xác nhận đã nhận từ kho mọi máy của chuyến đang chờ mình nhận (từng máy một bước nhận như hiện có)."""
    from .. import equipment_custody as custody
    t = service_tickets.load(session, user, ticket_id)
    rows = trip.trip_for(session, user, t)
    n = 0
    try:
        for x in rows:
            hand = custody.pending(session, x.serial) if x.serial else None
            if hand is not None and hand.holder_id == user.id:
                custody.confirm(session, user, hand.id, hand.version)
                n += 1
    except BusinessError as exc:
        session.rollback()
        return _trip_page(request, session, user, ticket_id, error=exc.message, error_code=exc.code, status=exc.status)
    return RedirectResponse(f'/tho/chuyen-tra/{ticket_id}?nhan={n}', status_code=303)
