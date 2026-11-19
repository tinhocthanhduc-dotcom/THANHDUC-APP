"""Trả lời tự động Zalo (TUẤN duyệt mẫu 19/11/2026): giám đốc bật/tắt và chọn mức cho từng loại tin khách."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import zalo_tu_tra_loi as svc
from ..db import get_session
from ..models import User
from . import checked_form, hubs, nav, render, web_user

router = APIRouter()
PATH = '/zalo/tu-tra-loi'
EXAMPLES = {
    'auto_ngoai_gio': svc.OFF_HOURS_TEXT.format(back='07:30 sáng mai'),
    'auto_tinh_trang': '\n'.join([svc.STATUS_HEAD, '- DV-26-01187 (Mang về xưởng): Hoàn thành.', svc.STATUS_TAIL]),
    'auto_bao_hong': svc.BROKEN_TEXT.format(what='sửa máy'),
    'auto_nguoi_la': svc.STRANGER_TEXT,
}


@router.get(PATH)
def page(request: Request, luu: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    nav.check_access(user, PATH)
    from .. import zalo_agent
    cfg = svc.config(session)
    return render(request, 'zalo_tu_tra_loi.jinja', hub=hubs.zalo(session, user, PATH), cfg=cfg, rules=svc.RULES, modes=svc.MODES,
                  examples=EXAMPLES, luu=luu, z2=zalo_agent.z2_alive(session) and cfg.staff_engine in ('codex', 'claude'))


@router.post(PATH)
def save(form=Depends(checked_form), user: User = Depends(web_user), session: Session = Depends(get_session)):
    nav.check_access(user, PATH)
    svc.save(session, user, form)
    return RedirectResponse(PATH + '?luu=1', status_code=303)
