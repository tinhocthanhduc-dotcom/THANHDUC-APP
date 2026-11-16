"""Đợt 2 rà soát (TUẤN duyệt mẫu 16/11/2026): gom trang cùng việc thành một mục menu có tab.

- Đội AI (chỉ giám đốc): Hôm nay · Chờ anh duyệt · Thành viên · Nhật ký · Zalo. Mỗi tab là trang cũ, giữ nút và cài đặt.
- Hộp Zalo (nhân viên): Cần trả lời · Đơn nháp · Phiếu nháp · Chờ gửi khách; mỗi người chỉ thấy tab mình mở được.
Chỉ là điều hướng: không đổi quyền, không đổi việc của từng trang.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import User
from . import nav, web_user

router = APIRouter()

ZALO_TABS = (('/tin-zalo', 'Cần trả lời'), ('/don-nhap-zalo', 'Đơn nháp'), ('/phieu-nhap-zalo', 'Phiếu nháp'), ('/tin-gui-khach', 'Chờ gửi khách'))
ZALO_PAGES = ('/zalo/tro-ly', '/zalo/tin-nhan', '/zalo/bao-cao-sang', '/zalo/nhac-no')


def _zalo_count(session, user, href):
    if href == '/tin-zalo':
        from ..models import ZaloTriage
        return int(session.scalar(select(func.count()).select_from(ZaloTriage).where(
            ZaloTriage.owner_id == user.id, ZaloTriage.done_at.is_(None), ZaloTriage.status.in_(('XONG', 'LOI')))) or 0)
    if href == '/don-nhap-zalo':
        from .. import zalo_agent
        return int(zalo_agent.draft_counts(session).get('cho', 0))
    if href == '/phieu-nhap-zalo':
        from .. import zalo_phieu_nhap
        return zalo_phieu_nhap.waiting(session)
    from .. import tin_gui_khach
    return int(tin_gui_khach.counts(session, user).get('cho', 0))


def zalo(session, user, active):
    """Tab Hộp Zalo theo quyền người xem; trang đang mở là tab sáng."""
    return [dict(label=label, n=_zalo_count(session, user, href), href=href, on=href == active)
            for href, label in ZALO_TABS if nav.allowed(user, href)]


def ai(session, user, path, tab=''):
    """Tab Đội AI. Chờ anh duyệt / Nhật ký cùng trang /tro-ly-ai, phân theo ?tab=."""
    from .. import ai_agent
    from ..models import AiAction
    waiting = int(session.scalar(select(func.count()).select_from(AiAction).where(AiAction.status.in_(ai_agent.TABS['cho-duyet'][1]))) or 0)
    zalo_on = path in ZALO_PAGES
    return [dict(label='Hôm nay', n='', href='/doi-ai', on=path == '/doi-ai'),
            dict(label='Chờ anh duyệt', n=waiting, href='/tro-ly-ai', on=path == '/tro-ly-ai' and tab != 'nhat-ky'),
            dict(label='Thành viên', n='', href='/nhan-vien-ao', on=path == '/nhan-vien-ao'),
            dict(label='Nhật ký', n='', href='/tro-ly-ai?tab=nhat-ky', on=path == '/tro-ly-ai' and tab == 'nhat-ky'),
            dict(label='Zalo', n='', href='/zalo/tro-ly', on=zalo_on)]


@router.get('/hop-zalo')
def zalo_home(request: Request, user: User = Depends(web_user)):
    """Mục menu Hộp Zalo: mở tab đầu tiên người xem có quyền."""
    first = next((href for href, _ in ZALO_TABS if nav.allowed(user, href)), None)
    nav.check_access(user, first or '/hop-zalo')
    return RedirectResponse(first, status_code=303)
