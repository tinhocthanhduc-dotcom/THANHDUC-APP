"""Khách lâu chưa gọi mực (TUẤN duyệt mẫu 06/11/2026): danh sách khuôn 3 + ghi kết quả gọi. Luật ở app/ink_rhythm.py."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import ink_rhythm
from ..db import get_session
from ..errors import BusinessError
from ..models import User
from . import checked_form, nav, paging, render, web_user
from .lists import _today

router = APIRouter()
TABS = (('goi', 'Cần gọi'), ('sap', 'Sắp tới nhịp'), ('hen', 'Đã gọi, chờ lại'), ('mat', 'Đã chuyển nơi khác'))


@router.get('/khach-hang/nhac-muc')
def listing(request: Request, tab: str = 'goi', trang: str = '', so: str = '', user: User = Depends(web_user),
            session: Session = Depends(get_session)):
    tab = tab if tab in dict(TABS) else 'goi'
    data = ink_rhythm.collect(session, _today())
    query = dict(tab=tab)
    page, shown = paging.slice_rows(data[tab], trang, so, query)
    link = paging.linker('/khach-hang/nhac-muc', query)
    tiles = [dict(label='Quá nhịp, cần gọi', n=len(data['goi']), sub='quá 1,5 lần nhịp', href=link(tab='goi'), on=tab == 'goi', tone='huy' if data['goi'] else ''),
             dict(label='Sắp tới nhịp', n=len(data['sap']), sub=f'trong {ink_rhythm.SOON_DAYS} ngày tới', href=link(tab='sap'), on=tab == 'sap'),
             dict(label='Đã gọi, chờ lại', n=len(data['hen']), sub='tạm ẩn sau khi gọi', href=link(tab='hen'), on=tab == 'hen'),
             dict(label='Gọi xong tuần này', n=data['xong'], sub='cuộc gọi đã ghi', href=None, on=False)]
    tabs = [dict(label=label, n=len(data[key]), href=link(tab=key), on=tab == key) for key, label in TABS]
    return render(request, 'khach_nhac_muc.jinja', rows=shown, page=page, tiles=tiles, tabs=tabs, tab=tab, results=ink_rhythm.RESULTS,
                  can_ticket=nav.allowed(user, '/dich-vu/moi'), can_write=ink_rhythm.has_role(user, *ink_rhythm.WRITERS),
                  threshold=ink_rhythm.THRESHOLD, saved=request.query_params.get('da_luu', ''))


@router.post('/khach-hang/nhac-muc/{code}/goi')
def record(request: Request, code: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    user = request.state.user
    result = str(form.get('result', ''))
    tab = str(form.get('tab', 'goi'))[:8]
    try:
        row = ink_rhythm.record(session, user, code, result, str(form.get('note', '')))
    except BusinessError as exc:
        return RedirectResponse(f'/khach-hang/nhac-muc?tab={quote(tab)}&loi={quote(exc.message)}', status_code=303)
    if row.result == 'DAT_MUC' and nav.allowed(user, '/dich-vu/moi'):
        return RedirectResponse(f'/dich-vu/moi?customer_code={quote(row.customer_code)}', status_code=303)
    return RedirectResponse(f'/khach-hang/nhac-muc?tab={quote(tab)}&da_luu={quote(row.customer_code)}', status_code=303)
