from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from .. import audit
from ..customers import like_pattern
from ..db import get_session
from ..errors import BusinessError
from ..models import Product, User
from ..schemas import ProductIn, ProductOut, ProductPatch
from ..security import current_user, require
from ..text import fold

router = APIRouter(prefix="/products", tags=["Hàng hóa"])
# Danh mục hàng do Kho quản lý, giống quy tắc "Kho là nơi ghi duy nhất".
WRITERS = ("warehouse",)


def _search_text(p: Product) -> str:
    return fold(" ".join([p.code, p.name, p.group_name]))


@router.get("", response_model=list[ProductOut])
def search(
    q: str = Query(default="", max_length=200),
    active_only: bool = True,
    _: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    stmt = select(Product)
    if active_only:
        stmt = stmt.where(Product.active.is_(True))
    if fold(q):
        stmt = stmt.where(Product.search_text.like(like_pattern(fold(q))))
    return session.scalars(stmt.order_by(Product.code).limit(50)).all()


@router.get("/{code}", response_model=ProductOut)
def get(code: str, _: User = Depends(current_user), session: Session = Depends(get_session)):
    p = session.get(Product, code.upper())
    if not p:
        raise BusinessError("NOT_FOUND", "Không tìm thấy mã hàng.", status=404)
    return p


@router.post("", response_model=ProductOut, status_code=201)
def create(body: ProductIn, user: User = Depends(require(*WRITERS)), session: Session = Depends(get_session)):
    p = Product(**body.model_dump())
    p.name = p.name.strip()
    p.search_text = _search_text(p)
    session.add(p)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise BusinessError("DUPLICATE_CODE", "Mã hàng đã có.", status=409)
    audit.record(session, user, "product.create", "product", p.code, None, ProductOut.model_validate(p).model_dump())
    session.commit()
    return p


@router.patch("/{code}", response_model=ProductOut)
def update(code: str, body: ProductPatch, user: User = Depends(require(*WRITERS)), session: Session = Depends(get_session)):
    p = session.get(Product, code.upper())
    if not p:
        raise BusinessError("NOT_FOUND", "Không tìm thấy mã hàng.", status=404)
    if body.version != p.version:
        raise BusinessError("VERSION_CONFLICT", "Người khác vừa sửa mã hàng này, hãy tải lại.", {"current": p.version}, status=409)
    before = ProductOut.model_validate(p).model_dump()
    for k, v in body.model_dump(exclude_unset=True, exclude={"version"}).items():
        setattr(p, k, v.strip() if isinstance(v, str) else v)
    p.search_text = _search_text(p)
    try:
        session.flush()
    except StaleDataError:
        session.rollback()
        raise BusinessError("VERSION_CONFLICT", "Người khác vừa sửa mã hàng này, hãy tải lại.", status=409)
    audit.record(session, user, "product.update", "product", p.code, before, ProductOut.model_validate(p).model_dump())
    session.commit()
    return p
