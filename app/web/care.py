"""Care (care.quanlythanhduc.com, TUẤN duyệt mẫu 19/11/2026): Máy đã bán và Bảo hành sửa chữa.

Chỉ đọc, không đổi dữ liệu. Máy đã bán lấy từ serial trên phiếu giao hàng (sales_delivery_serials);
Bảo hành sửa chữa lấy phiếu sửa đã xong còn số tháng bảo hành, hạn tính như service_warranty.warranty_end.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import nav, paging, render, web_user
from .lists import _today
from .. import service_sla
from .. import search as tim
from ..config import get_settings
from ..db import get_session
from ..models import (Customer, CustomerPhone, Equipment, Product, SalesDelivery, SalesDeliverySerial, SalesOrder, ServiceStep,
                      ServiceTicket, User)
from ..service_warranty import add_months

router = APIRouter(include_in_schema=False)

SOLD_SOON = 30      # Máy bán: sắp hết bảo hành trong 30 ngày.
REPAIR_SOON = 7     # Phiếu sửa: sắp hết bảo hành trong 7 ngày.
RETURN_DAYS = 90    # Tỷ lệ quay lại: phiếu sửa xong trong 90 ngày gần nhất.
DONE = ('HOAN_THANH', 'DA_DONG')


def _sold_state(e, today):
    if e.warranty_void_on is not None:
        return 'tu-choi'
    if not e.warranty_until or e.warranty_until < today:
        return 'het'
    return 'sap' if (e.warranty_until - today).days <= SOLD_SOON else 'con'


@router.get('/may-da-ban')
def sold_machines(request: Request, q: str = '', bh: str = '', trang: str = '', so: str = '',
                  user: User = Depends(web_user), session: Session = Depends(get_session)):
    """Máy Thành Đức bán có serial, còn ở khách đã mua (phiếu giao chưa hủy, máy chưa trả lại/đổi chủ)."""
    q, today = q[:200], _today()
    bh = bh if bh in ('sap', 'het', 'tu-choi', 'tat-ca') else ''
    latest = (select(SalesDeliverySerial.serial, func.max(SalesDeliverySerial.id).label('link_id'))
              .join(SalesDelivery, SalesDelivery.id == SalesDeliverySerial.delivery_id)
              .where(SalesDelivery.status == 'DA_GIAO').group_by(SalesDeliverySerial.serial).subquery())
    found = session.execute(
        select(Equipment, SalesDelivery, SalesOrder)
        .join(latest, latest.c.serial == Equipment.serial)
        .join(SalesDeliverySerial, SalesDeliverySerial.id == latest.c.link_id)
        .join(SalesDelivery, SalesDelivery.id == SalesDeliverySerial.delivery_id)
        .join(SalesOrder, SalesOrder.id == SalesDelivery.order_id)
        .where(Equipment.active.is_(True), Equipment.customer_code == SalesOrder.customer_code)
        .order_by(SalesDelivery.delivered_at.desc(), Equipment.serial)).all()
    products = dict(session.execute(select(Product.code, Product.name).where(Product.code.in_({e.product_code for e, _, _ in found if e.product_code}))).all()) if found else {}
    customers = {c.code: c for c in session.scalars(select(Customer).where(Customer.code.in_({e.customer_code for e, _, _ in found})))} if found else {}
    phones = {}
    if customers:
        for code, phone in session.execute(select(CustomerPhone.customer_code, CustomerPhone.phone).where(CustomerPhone.customer_code.in_(customers))
                                           .order_by(CustomerPhone.is_contact, CustomerPhone.phone)):
            phones.setdefault(code, phone)

    def text(e, d, o):
        c = customers.get(e.customer_code)
        return ' '.join(str(x) for x in (e.serial, products.get(e.product_code, ''), e.model_name, c.name if c else '', c.code if c else '',
                                         phones.get(e.customer_code, ''), o.number, d.number))
    if q:
        found = [r for r in found if tim.text_matches(q, text(*r))]
    states = {e.serial: _sold_state(e, today) for e, _, _ in found}
    counts = dict(con=sum(1 for s in states.values() if s in ('con', 'sap')), sap=sum(1 for s in states.values() if s == 'sap'),
                  het=sum(1 for s in states.values() if s == 'het'), void=sum(1 for s in states.values() if s == 'tu-choi'))
    if bh == 'tat-ca':
        rows = found
    elif bh:
        rows = [r for r in found if states[r[0].serial] == bh]
    else:
        rows = [r for r in found if states[r[0].serial] in ('con', 'sap')]
    query = dict(q=q, bh=bh)
    page, shown = paging.slice_rows(rows, trang, so, query)
    link = paging.linker('/may-da-ban', query)
    serials = [e.serial for e, _, _ in shown]
    repairs = dict(session.execute(select(ServiceTicket.serial, func.count()).where(ServiceTicket.serial.in_(serials), ServiceTicket.status != 'DA_HUY')
                                   .group_by(ServiceTicket.serial)).all()) if serials else {}
    tiles = [dict(label='Còn bảo hành', n=counts['con'], href=link(bh=''), on=not bh, tone='xong'),
             dict(label='Hết hạn trong 30 ngày', n=counts['sap'], href=link(bh='sap'), on=bh == 'sap', tone='cho' if counts['sap'] else ''),
             dict(label='Đã hết bảo hành', n=counts['het'], href=link(bh='het'), on=bh == 'het'),
             dict(label='Bị từ chối bảo hành', n=counts['void'], href=link(bh='tu-choi'), on=bh == 'tu-choi', tone='huy' if counts['void'] else '')]
    tabs = [dict(label='Còn bảo hành', n=counts['con'], href=link(bh=''), on=not bh), dict(label='Sắp hết', n=counts['sap'], href=link(bh='sap'), on=bh == 'sap'),
            dict(label='Hết bảo hành', n=counts['het'], href=link(bh='het'), on=bh == 'het'), dict(label='Tất cả', n=len(found), href=link(bh='tat-ca'), on=bh == 'tat-ca')]
    return render(request, 'may_da_ban.jinja', rows=shown, page=page, tiles=tiles, tabs=tabs, q=q, bh=bh, link=link, today=today,
                  states=states, products=products, customers=customers, phones=phones, repairs=repairs,
                  can_order=nav.allowed(user, '/don-ban'))


def _completions(session, ids):
    """Lần chuyển Hoàn thành gần nhất của mỗi phiếu (giống service_warranty.completion)."""
    if not ids:
        return {}
    return dict(session.execute(select(ServiceStep.ticket_id, func.max(ServiceStep.at))
                                .where(ServiceStep.ticket_id.in_(ids), ServiceStep.to_status == 'HOAN_THANH',
                                       ServiceStep.from_status.is_distinct_from('HOAN_THANH'))
                                .group_by(ServiceStep.ticket_id)).all())


@router.get('/bao-hanh-sua-chua')
def repair_warranty(request: Request, q: str = '', tt: str = '', trang: str = '', so: str = '',
                    user: User = Depends(web_user), session: Session = Depends(get_session)):
    """Phiếu sửa đã xong có bảo hành: còn hạn, đang quay lại (có phiếu bảo hành mở), đã hết hạn (trong 1 năm gần nhất)."""
    q, today = q[:200], _today()
    tt = tt if tt in ('quay-lai', 'het') else ''
    since = today - timedelta(days=430)
    tickets = list(session.scalars(select(ServiceTicket).where(
        ServiceTicket.status.in_(DONE), ServiceTicket.kind.is_distinct_from('MUC_DINH_KY'),
        ServiceTicket.warranty_months.is_distinct_from(0), ServiceTicket.created_at >= since)))
    stamps = _completions(session, [t.id for t in tickets])
    days = get_settings().service_warranty_days
    info = {}
    for t in tickets:
        stamp = stamps.get(t.id) or t.priced_at
        if stamp is None:
            continue
        done = stamp.astimezone(service_sla.TZ).date()
        end = add_months(done, t.warranty_months) if t.warranty_months is not None else done + timedelta(days=days)
        if end < today - timedelta(days=365):
            continue
        info[t.id] = dict(done=done, end=end, left=(end - today).days)
    tickets = [t for t in tickets if t.id in info]
    children = {}
    if tickets:
        for c in session.scalars(select(ServiceTicket).where(ServiceTicket.warranty_of_id.in_([t.id for t in tickets]), ServiceTicket.status != 'DA_HUY')
                                 .order_by(ServiceTicket.id)):
            children.setdefault(c.warranty_of_id, []).append(c)
    open_child = {pid: next((c for c in cs if c.status not in DONE), None) for pid, cs in children.items()}
    from .services import _list_rows
    customers, phones, sites, machines = _list_rows(session, tickets)

    def text(t):
        return ' '.join(str(x or '') for x in (t.number, t.serial, customers.get(t.customer_code, ''), t.customer_code, phones.get(t.customer_code, ''),
                                               machines.get(t.serial, ''), t.fix_done, t.symptom))
    if q:
        tickets = [t for t in tickets if tim.text_matches(q, text(t))]
    back = [t for t in tickets if open_child.get(t.id)]
    live = [t for t in tickets if info[t.id]['left'] >= 0 and not open_child.get(t.id)]
    gone = [t for t in tickets if info[t.id]['left'] < 0 and not open_child.get(t.id)]
    rows = back if tt == 'quay-lai' else gone if tt == 'het' else live
    rows.sort(key=lambda t: (info[t.id]['left'], t.id) if tt != 'het' else (-info[t.id]['end'].toordinal(), t.id))
    query = dict(q=q, tt=tt)
    page, shown = paging.slice_rows(rows, trang, so, query)
    link = paging.linker('/bao-hanh-sua-chua', query)
    # Tỷ lệ quay lại: phiếu sửa xong trong 90 ngày có phiếu bảo hành gắn vào / số phiếu sửa xong trong 90 ngày.
    recent = [t for t in tickets if (today - info[t.id]['done']).days <= RETURN_DAYS]
    returned = sum(1 for t in recent if children.get(t.id))
    rate = f"{round(returned * 100 / len(recent))}%" if recent else '—'
    soon = sum(1 for t in live if info[t.id]['left'] <= REPAIR_SOON)
    tiles = [dict(label='Phiếu còn bảo hành', n=len(live), href=link(tt=''), on=not tt, tone='xong'),
             dict(label='Hết hạn trong 7 ngày', n=soon, href='', on=False, tone='cho' if soon else ''),
             dict(label='Máy đang quay lại', n=len(back), href=link(tt='quay-lai'), on=tt == 'quay-lai'),
             dict(label='Tỷ lệ quay lại 90 ngày', n=rate, href='', on=False, sub=f'{returned}/{len(recent)} phiếu sửa xong')]
    tabs = [dict(label='Còn bảo hành', n=len(live), href=link(tt=''), on=not tt),
            dict(label='Đang quay lại', n=len(back), href=link(tt='quay-lai'), on=tt == 'quay-lai'),
            dict(label='Đã hết hạn', n=len(gone), href=link(tt='het'), on=tt == 'het')]
    users = dict(session.execute(select(User.id, User.full_name)).all())
    return render(request, 'bao_hanh_sua_chua.jinja', rows=shown, page=page, tiles=tiles, tabs=tabs, q=q, tt=tt, link=link, info=info,
                  open_child=open_child, customers=customers, phones=phones, machines=machines, users=users, soon_days=REPAIR_SOON,
                  can_take=nav.allowed(user, '/dich-vu/moi'), ticket_url=lambda tid: nav.ticket_url(user, tid))
