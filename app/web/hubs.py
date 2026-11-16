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


# Đợt 3 rà soát (TUẤN duyệt mẫu 16/11/2026): 4 chỗ báo giá gom thành mục "Báo giá", tab theo quyền.
QUOTE_TABS = (('/bao-gia', 'Hàng hóa'), ('/hoi-gia', 'Hỏi giá sửa'), ('/bao-gia-sua', 'Sửa máy'), ('/bao-gia-hop-dong', 'Hợp đồng'))
QUOTE_HOME = '/cac-bao-gia'


def _quote_count(session, href):
    """Số đang chờ khách ở mỗi tab (chỉ đọc)."""
    if href == '/bao-gia':
        from ..models import Quote
        return int(session.scalar(select(func.count()).select_from(Quote).where(Quote.status == 'DA_GUI')) or 0)
    if href == '/hoi-gia':
        from .. import quick_quotes as qq
        return int(qq.counts(session).get('cho', 0))
    if href == '/bao-gia-sua':
        from .. import repair_quotes as rqs
        from ..models import RepairQuoteVersion
        return int(session.scalar(select(func.count()).select_from(RepairQuoteVersion).where(
            RepairQuoteVersion.status == 'CHO', RepairQuoteVersion.valid_until >= rqs._today())) or 0)
    from .. import contract_quotes as cq
    return int(cq.counts(session).get('cho', 0))


def quotes(session, user, active):
    """Tab Báo giá theo quyền người xem. Báo giá gộp (/bao-gia-gop) sáng tab Sửa máy."""
    active = '/bao-gia-sua' if active == '/bao-gia-gop' else active
    return [dict(label=label, n=_quote_count(session, href), href=href, on=href == active)
            for href, label in QUOTE_TABS if nav.allowed(user, href)]


def quote_choices(user):
    """Nút "+ Tạo báo giá": khách hỏi gì → trang lập. Chỉ dòng người xem lập được."""
    from .. import quick_quotes as qq
    from ..quotes import WRITERS
    from ..security import has_role
    from ..service_tickets import DISPATCHERS
    rows = (
        ('/bao-gia/moi', 'Mua máy, mực, linh kiện', 'Báo giá hàng hóa · Bán hàng làm', has_role(user, *WRITERS)),
        ('/hoi-gia/moi', 'Hỏi giá sửa, chưa thấy máy', 'Giá theo model và gói sửa, ghi "giá dự kiến" · Bán hàng, Điều phối',
         qq.can_use(user)),
        ('/kiem-tra-may', 'Máy đã kiểm tra, cần báo giá chính thức', 'Mở Máy chờ kiểm tra, chọn phiếu · Điều phối',
         has_role(user, *DISPATCHERS)),
        ('/bao-gia-hop-dong/moi', 'Hợp đồng, nhiều model một bảng', 'Báo giá hợp đồng · Điều phối', has_role(user, *DISPATCHERS)),
    )
    return [dict(href=h, title=t, hint=s) for h, t, s, ok in rows if ok and nav.allowed(user, h)]


@router.get(QUOTE_HOME)
def quote_home(request: Request, user: User = Depends(web_user)):
    """Mục menu Báo giá: mở tab đầu tiên người xem có quyền."""
    first = next((href for href, _ in QUOTE_TABS if nav.allowed(user, href)), None)
    nav.check_access(user, first or QUOTE_HOME)
    return RedirectResponse(first, status_code=303)


@router.get(QUOTE_HOME + '/tao')
def quote_new(request: Request, user: User = Depends(web_user)):
    """Khách hỏi gì? Một lựa chọn thì mở thẳng trang lập."""
    from . import render
    nav.check_access(user, QUOTE_HOME)
    choices = quote_choices(user)
    if not choices:
        nav.check_access(user, '/bao-gia/moi')
    if len(choices) == 1:
        return RedirectResponse(choices[0]['href'], status_code=303)
    return render(request, 'bao_gia_tao.jinja', choices=choices)


@router.get('/cho-anh-duyet')
def approvals_page(request: Request, nhom: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    """Đợt 4 rà soát (TUẤN duyệt mẫu 16/11/2026): mọi việc chờ giám đốc duyệt ở một trang; bấm dòng mở phiếu cũ."""
    from datetime import datetime

    from .. import director_approvals as da
    from ..service_sla import TZ
    from . import render
    nav.check_access(user, '/cho-anh-duyet')
    items = da.rows(session)
    now = datetime.now(TZ)
    counts = {k: sum(1 for r in items if r['group'] == k) for k in da.GROUPS}
    nhom = nhom if nhom in da.GROUPS else ''
    shown = [dict(r, age=da.age(r['since'], now)) for r in items if not nhom or r['group'] == nhom]
    tiles = [dict(label='Tất cả', n=len(items), href='/cho-anh-duyet', on=not nhom)] + [
        dict(label=label, n=counts[k], href=f'/cho-anh-duyet?nhom={k}', on=nhom == k) for k, label in da.GROUPS.items()]
    return render(request, 'cho_anh_duyet.jinja', rows=shown, tiles=tiles, total=len(items),
                  cash=sum(r['amount'] or 0 for r in items if r['kind'] in da.CASH_OUT))
