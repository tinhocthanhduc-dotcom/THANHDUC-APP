"""Nghiệp vụ danh mục hàng mã TD. API và giao diện web cùng gọi vào đây."""
import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from . import audit
from .customers import like_pattern
from .errors import BusinessError
from .models import Product, User
from .schemas import ProductIn, ProductOut, ProductPatch
from .text import fold

# Danh mục hàng do Kho quản lý, giống quy tắc "Kho là nơi ghi duy nhất".
WRITERS = ("warehouse",)


def _search_text(p: Product) -> str:
    return fold(" ".join([p.code, p.name, p.group_name]))


def snapshot(p: Product) -> dict:
    return ProductOut.model_validate(p).model_dump()


def search(session: Session, q: str, active_only: bool = True, limit: int = 50, group: str = "") -> list[Product]:
    stmt = select(Product)
    if group:
        stmt = stmt.where(Product.group_name == group)
    if active_only:
        stmt = stmt.where(Product.active.is_(True))
    if fold(q):
        stmt = stmt.where(Product.search_text.like(like_pattern(fold(q))))
    return list(session.scalars(stmt.order_by(Product.code).limit(limit)).all())


def load(session: Session, code: str) -> Product:
    p = session.get(Product, code.strip().upper())
    if not p:
        raise BusinessError("NOT_FOUND", "Không tìm thấy mã hàng.", status=404)
    return p


def groups(session: Session) -> list[str]:
    return list(session.scalars(select(Product.group_name).where(Product.group_name != "").distinct().order_by(Product.group_name)))


def suggest_code(session: Session) -> str:
    """Mã kế tiếp sau mã TD lớn nhất hiện có, chỉ để gợi ý; người nhập vẫn quyết định."""
    codes = session.scalars(select(Product.code).order_by(func.length(Product.code).desc(), Product.code.desc()).limit(1)).all()
    n = int(re.sub(r"\D", "", codes[0])) + 1 if codes else 1
    return f"TD-{n:04d}"


def create(session: Session, user: User, body: ProductIn) -> Product:
    p = Product(**body.model_dump())
    p.code = p.code.upper()
    p.name = p.name.strip()
    p.group_name = p.group_name.strip()
    p.unit = p.unit.strip()
    p.search_text = _search_text(p)
    session.add(p)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise BusinessError("DUPLICATE_CODE", "Mã hàng đã có.", status=409)
    audit.record(session, user, "product.create", "product", p.code, None, snapshot(p))
    session.commit()
    return p


def update(session: Session, user: User, code: str, body: ProductPatch) -> Product:
    p = load(session, code)
    if body.version != p.version:
        raise BusinessError("VERSION_CONFLICT", "Người khác vừa sửa mã hàng này, hãy tải lại.", {"current": p.version}, status=409)
    before = snapshot(p)
    for k, v in body.model_dump(exclude_unset=True, exclude={"version"}).items():
        setattr(p, k, v.strip() if isinstance(v, str) else v)
    p.search_text = _search_text(p)
    try:
        session.flush()
    except StaleDataError:
        session.rollback()
        raise BusinessError("VERSION_CONFLICT", "Người khác vừa sửa mã hàng này, hãy tải lại.", status=409)
    audit.record(session, user, "product.update", "product", p.code, before, snapshot(p))
    session.commit()
    return p
