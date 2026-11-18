"""Đợt 2 rà soát (TUẤN duyệt mẫu 16/11/2026): gom trang cùng việc thành một mục menu có tab.

- Đội AI (chỉ giám đốc): Hôm nay · Chờ anh duyệt · Thành viên · Nhật ký · Zalo. Mỗi tab là trang cũ, giữ nút và cài đặt.
- Hộp Zalo (nhân viên): Cần trả lời · Đơn nháp · Phiếu nháp · Chờ gửi khách; mỗi người chỉ thấy tab mình mở được.
Chỉ là điều hướng: không đổi quyền, không đổi việc của từng trang.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import User
from . import checked_form, nav, web_user

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
    from ..service_tickets import can_quote
    active = '/bao-gia-sua' if active == '/bao-gia-gop' else active
    return [dict(label=label, n=_quote_count(session, href), href=href, on=href == active)
            for href, label in QUOTE_TABS if nav.allowed(user, href) and (href != '/bao-gia-sua' or can_quote(user))]


def quote_choices(user):
    """Nút "+ Tạo báo giá": khách hỏi gì → trang lập. Chỉ dòng người xem lập được."""
    from .. import quick_quotes as qq
    from ..quotes import WRITERS
    from ..security import has_role
    from ..service_tickets import DISPATCHERS, can_quote
    rows = (
        ('/bao-gia/moi', 'Mua máy, mực, linh kiện', 'Báo giá hàng hóa · Bán hàng làm', has_role(user, *WRITERS)),
        ('/hoi-gia/moi', 'Hỏi giá sửa, chưa thấy máy', 'Giá theo model và gói sửa, ghi "giá dự kiến" · Bán hàng, Điều phối',
         qq.can_use(user)),
        ('/kiem-tra-may', 'Máy đã kiểm tra, cần báo giá chính thức', 'Mở Bảng xưởng, cột Chờ duyệt giá · Kinh doanh, Giám đốc',
         can_quote(user)),
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
    """Đợt 4 rà soát (TUẤN duyệt mẫu 16/11/2026): mọi việc chờ giám đốc duyệt ở một trang.
    Đợt 2 tối ưu thao tác (TUẤN duyệt mẫu 17/11/2026): Duyệt/Từ chối ngay tại dòng; kiểm kê vẫn mở phiếu."""
    nav.check_access(user, '/cho-anh-duyet')
    return _approvals(request, session, nhom, saved=request.query_params.get('da_luu', ''))


def _approvals(request, session, nhom='', error=None, code='', status=200, saved='', failed=None):
    from datetime import datetime

    from .. import director_approvals as da
    from ..service_sla import TZ
    from . import render
    items = da.rows(session)
    now = datetime.now(TZ)
    counts = {k: sum(1 for r in items if r['group'] == k) for k in da.GROUPS}
    nhom = nhom if nhom in da.GROUPS else ''
    shown = [dict(r, age=da.age(r['since'], now)) for r in items if not nhom or r['group'] == nhom]
    tiles = [dict(label='Tất cả', n=len(items), href='/cho-anh-duyet', on=not nhom)] + [
        dict(label=label, n=counts[k], href=f'/cho-anh-duyet?nhom={k}', on=nhom == k) for k, label in da.GROUPS.items()]
    return render(request, 'cho_anh_duyet.jinja', rows=shown, tiles=tiles, total=len(items), nhom=nhom,
                  cash=sum(r['amount'] or 0 for r in items if r['kind'] in da.CASH_OUT),
                  error=error, error_code=code, status=status, saved=saved, failed=failed or {})


@router.post('/cho-anh-duyet/{kind}/{ident}/{action}')
def approvals_decide(request: Request, kind: str, ident: int, action: str, form=Depends(checked_form),
                     user: User = Depends(web_user), session: Session = Depends(get_session)):
    """Một nút tại dòng → đúng hàm nghiệp vụ trang gốc (quyền, hạn mức, phiên bản, lý do giữ nguyên)."""
    from .. import director_approvals as da
    from ..errors import BusinessError
    nav.check_access(user, '/cho-anh-duyet')
    nhom = str(form.get('nhom', ''))
    nhom = nhom if nhom in da.GROUPS else ''
    if action not in ('duyet', 'tu-choi'):
        raise BusinessError('NOT_FOUND', 'Không có thao tác này.', status=404)
    try:
        da.decide(session, user, kind, ident, action == 'duyet', form.get('version'), str(form.get('reason', '')))
    except BusinessError as exc:
        session.rollback()
        if _inline(request):
            return JSONResponse(dict(ok=False, message=exc.message, code=exc.code), status_code=exc.status)
        return _approvals(request, session, nhom, exc.message, exc.code, exc.status,
                          failed=dict(key=f'{kind}-{ident}', reason=str(form.get('reason', '')), action=action))
    if _inline(request):
        return JSONResponse(dict(ok=True))
    back = '/cho-anh-duyet?' + (f'nhom={nhom}&' if nhom else '') + 'da_luu=' + ('duyet' if action == 'duyet' else 'tu-choi')
    return RedirectResponse(back, status_code=303)


def _inline(request) -> bool:
    """duyet-tai-dong.js gửi bằng fetch kèm header này: trả JSON thay vì cả trang."""
    return request.headers.get('x-tai-dong') == '1'


@router.post('/viec-cua-toi/xu-ly/{kind}/{ident}/{action}')
def work_decide(request: Request, kind: str, ident: int, action: str, form=Depends(checked_form),
                user: User = Depends(web_user), session: Session = Depends(get_session)):
    """Đợt 2b tối ưu thao tác (TUẤN duyệt mẫu 17/11/2026): Việc của tôi xác nhận báo thu / duyệt khoản trong hạn mức tại dòng.
    Cùng hàm nghiệp vụ trang gốc; quyền, hạn mức, phiên bản, lý do kiểm ở đó."""
    from .. import director_approvals as da, my_work
    from ..errors import BusinessError
    if action not in ('duyet', 'tu-choi') or kind not in my_work.INLINE_KINDS:
        raise BusinessError('NOT_FOUND', 'Không có thao tác này.', status=404)
    try:
        da.decide(session, user, kind, ident, action == 'duyet', form.get('version'), str(form.get('reason', '')))
    except BusinessError as exc:
        # Hàm nghiệp vụ tự rollback giao dịch của nó; không rollback ở đây để trang lỗi chung còn đọc được người dùng.
        if _inline(request):
            return JSONResponse(dict(ok=False, message=exc.message, code=exc.code), status_code=exc.status)
        raise
    if _inline(request):
        return JSONResponse(dict(ok=True))
    return RedirectResponse('/viec-cua-toi?xong=tai-dong', status_code=303)


@router.post('/viec-cua-toi/kho/{kind}/{ident}')
def work_kho(request: Request, kind: str, ident: int, form=Depends(checked_form),
             user: User = Depends(web_user), session: Session = Depends(get_session)):
    """Đợt 6 tối ưu thao tác (TUẤN duyệt mẫu 18/11/2026): Giao đủ / Nhận đủ tại dòng Việc của tôi.
    Cùng hàm nghiệp vụ trang gốc; tồn kho, giữ hàng, quyền, phiên bản kiểm ở đó."""
    from .. import kho_inline
    from ..errors import BusinessError
    try:
        kho_inline.run(session, user, kind, ident, form.get('version'))
    except BusinessError as exc:
        if _inline(request):
            return JSONResponse(dict(ok=False, message=exc.message, code=exc.code), status_code=exc.status)
        raise
    if _inline(request):
        return JSONResponse(dict(ok=True))
    return RedirectResponse('/viec-cua-toi?xong=tai-dong', status_code=303)
