"""F2 (báo cáo đồng bộ 25/10/2026): bản xem chỉ đọc phiếu dịch vụ cho kế toán và kho.

Kế toán đối chiếu báo thu: tiền phiếu, đã thu, còn thiếu, các báo thu. Kho: linh kiện đã xin, thu hồi, máy đang giữ.
Không hiện giá vốn vật tư, không có nút thao tác; quyền route theo nav (finance, warehouse), vai khác vẫn 403.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import equipment, sales_receipts, service_tickets as svc
from ..db import get_session
from ..models import Customer, CustomerSite, Equipment, EquipmentCustody, PartRequest, Product, SalesReceipt, ServiceRecoveredPart, User
from ..security import has_role
from . import nav, render, web_user
from .customer_overview import _service_next
from .. import part_requests as part_svc, equipment_custody
from .receipts import METHODS, STATUSES as RECEIPT_STATUSES

router = APIRouter()


@router.get('/xem-dich-vu/{ticket_id}')
def view(request: Request, ticket_id: int, user: User = Depends(web_user), session: Session = Depends(get_session)):
    t = svc.load(session, user, ticket_id)
    users = dict(session.execute(select(User.id, User.full_name)).all())
    customer = session.get(Customer, t.customer_code)
    site = session.get(CustomerSite, t.site_id) if t.site_id else None
    machine = session.get(Equipment, t.serial) if t.serial else None
    machine_name = equipment.display_name(machine, session.get(Product, machine.product_code) if machine and machine.product_code else None) if machine else ''
    custody = session.scalar(select(EquipmentCustody).where(EquipmentCustody.ticket_id == t.id).order_by(EquipmentCustody.id.desc()).limit(1))
    finance = has_role(user, 'finance')
    money = None
    if finance and t.receivable is not None:
        balance = sales_receipts.balance(t)
        money = dict(total=t.total or 0, receivable=t.receivable or 0, paid=int(balance.get('paid', 0)), pending=int(balance.get('pending', 0)),
                     remaining=int(balance['remaining']))
    receipts = list(session.scalars(select(SalesReceipt).where(SalesReceipt.ticket_id == t.id).order_by(SalesReceipt.reported_at, SalesReceipt.id))) if finance else []
    parts = list(session.scalars(select(PartRequest).where(PartRequest.ticket_id == t.id).order_by(PartRequest.id)))
    recovered = list(session.scalars(select(ServiceRecoveredPart).where(ServiceRecoveredPart.ticket_id == t.id).order_by(ServiceRecoveredPart.id)))
    names = dict(session.execute(select(Product.code, Product.name).where(Product.code.in_({r.product_code for r in parts} | {r.product_code for r in recovered}))).all()) if parts or recovered else {}
    who, title = _service_next(t, users, session)
    return render(request, 'dich_vu_xem.jinja', t=t, customer=customer, site=site, machine=machine, machine_name=machine_name, custody=custody,
                  users=users, money=money, receipts=receipts, receipt_methods=METHODS, receipt_statuses=RECEIPT_STATUSES,
                  parts=parts, part_statuses=part_svc.STATUSES, custody_locations=equipment_custody.LOCATIONS, recovered=recovered, names=names, next_who=who, next_title=title,
                  back=('/bao-thu' if finance and not has_role(user, 'warehouse') else '/yeu-cau-linh-kien'),
                  full_url=f'/dich-vu/{t.id}' if nav.allowed(user, '/dich-vu') else None)
