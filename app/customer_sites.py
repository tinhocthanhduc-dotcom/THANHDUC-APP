"""Địa điểm của khách hàng; tạo/sửa dùng chung cho web và các nghiệp vụ dịch vụ."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from . import audit
from .errors import BusinessError
from .models import Customer, CustomerSite
from .schemas import CustomerSiteOut
from .security import has_role

WRITERS = ('dispatcher', 'technician')


def check_writer(user):
    if not has_role(user, *WRITERS):
        raise BusinessError('FORBIDDEN', 'Chỉ điều phối, kỹ thuật hoặc giám đốc được sửa địa điểm và thiết bị.', status=403)


def snapshot(row):
    return CustomerSiteOut.model_validate(row).model_dump(mode='json')


def load(session: Session, site_id: int) -> CustomerSite:
    site = session.get(CustomerSite, site_id)
    if site is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy địa điểm.', status=404)
    return site


def search(session: Session, customer_code: str, active_only=False):
    stmt = select(CustomerSite).where(CustomerSite.customer_code == customer_code)
    if active_only:
        stmt = stmt.where(CustomerSite.active.is_(True))
    return list(session.scalars(stmt.order_by(CustomerSite.id)))


def _save(session, user, site, before):
    try:
        session.flush()
        audit.record(session, user, 'customer_site.' + ('create' if before is None else 'update'),
                     'customer_site', str(site.id), before, snapshot(site))
        session.commit()
    except StaleDataError:
        session.rollback()
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa địa điểm, hãy tải lại.', status=409)
    except Exception:
        session.rollback()
        raise
    return site


def create(session: Session, user, customer_code, body):
    check_writer(user)
    if session.get(Customer, customer_code) is None:
        raise BusinessError('BAD_CUSTOMER', 'Không tìm thấy khách hàng.', status=404)
    site = CustomerSite(customer_code=customer_code, created_by=user.id, **body.model_dump())
    session.add(site)
    return _save(session, user, site, None)


def update(session: Session, user, site_id, body):
    check_writer(user)
    site = load(session, site_id)
    if site.version != body.version:
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa địa điểm, hãy tải lại.', status=409)
    before = snapshot(site)
    for field, value in body.model_dump(exclude_unset=True, exclude={'version'}).items():
        setattr(site, field, value)
    site.version += 1
    return _save(session, user, site, before)
