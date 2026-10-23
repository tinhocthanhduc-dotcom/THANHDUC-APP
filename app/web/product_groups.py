"""Danh mục nhóm hàng: Kho thêm, đổi tên, xóa nhóm chưa có mã hàng."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from . import checked_form, render, web_user
from .. import products as product_svc
from ..db import get_session
from ..errors import BusinessError
from ..models import User
from ..security import has_role

router = APIRouter(include_in_schema=False)


def _page(request, session, error=None, saved='', status=200, shown=None):
    return render(request, 'nhom_hang.jinja', rows=product_svc.group_rows(session), error=error, saved=saved, shown=shown or {},
                  can_write=has_role(request.state.user, *product_svc.WRITERS), status=status)


def _check(user):
    if not has_role(user, *product_svc.WRITERS):
        raise BusinessError('FORBIDDEN', 'Chỉ Kho được sửa danh mục nhóm hàng.', status=403)


@router.get('/nhom-hang')
def listing(request: Request, da_luu: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _page(request, session, saved=da_luu)


@router.post('/nhom-hang/moi')
def create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    _check(request.state.user)
    try:
        product_svc.create_group(session, request.state.user, str(form.get('name', '')))
    except BusinessError as exc:
        session.rollback()
        return _page(request, session, error=exc.message, status=exc.status, shown={'new': str(form.get('name', ''))})
    return RedirectResponse('/nhom-hang?da_luu=them', status_code=303)


@router.post('/nhom-hang/{group_id}')
def update(request: Request, group_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    _check(request.state.user)
    try:
        if form.get('xoa') == '1':
            product_svc.delete_group(session, request.state.user, group_id)
            return RedirectResponse('/nhom-hang?da_luu=xoa', status_code=303)
        product_svc.rename_group(session, request.state.user, group_id, str(form.get('name', '')))
    except BusinessError as exc:
        session.rollback()
        return _page(request, session, error=exc.message, status=exc.status, shown={group_id: str(form.get('name', ''))})
    return RedirectResponse('/nhom-hang?da_luu=sua', status_code=303)
