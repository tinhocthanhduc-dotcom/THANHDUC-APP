"""Nghiệp vụ danh mục hàng mã TD. API và giao diện web cùng gọi vào đây."""
import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from . import audit
from .customers import like_pattern
from .errors import BusinessError
from .models import Product, ProductGroup, User
from .schemas import ProductIn, ProductOut, ProductPatch
from .text import fold
from .security import has_role

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
    return list(session.scalars(select(ProductGroup.name).order_by(ProductGroup.name)))


def group_rows(session: Session) -> list[tuple[ProductGroup, int]]:
    """Nhóm kèm số mã hàng đang thuộc nhóm (kể cả mã ngừng bán)."""
    counts = dict(session.execute(select(Product.group_name, func.count()).group_by(Product.group_name)).all())
    return [(g, counts.get(g.name, 0)) for g in session.scalars(select(ProductGroup).order_by(ProductGroup.name))]


def _group_name(name: str) -> str:
    name = " ".join(str(name or "").split())
    if not name or len(name) > 100:
        raise BusinessError("GROUP_NAME", "Tên nhóm hàng cần từ 1 đến 100 ký tự.")
    return name


def _check_group(session: Session, name: str) -> None:
    if name and not session.scalar(select(ProductGroup.id).where(ProductGroup.name == name)):
        raise BusinessError("GROUP_NOT_FOUND", f"Nhóm hàng \"{name}\" chưa có trong danh mục, hãy chọn nhóm có sẵn hoặc thêm nhóm ở trang Nhóm hàng.")


def _group_taken(session: Session, name: str, except_id: int | None = None) -> None:
    other = session.scalar(select(ProductGroup).where(func.lower(ProductGroup.name) == name.lower()))
    if other and other.id != except_id:
        raise BusinessError("DUPLICATE_GROUP", f"Đã có nhóm \"{other.name}\".", status=409)


def load_group(session: Session, group_id: int) -> ProductGroup:
    g = session.get(ProductGroup, group_id)
    if not g:
        raise BusinessError("NOT_FOUND", "Không tìm thấy nhóm hàng.", status=404)
    return g


def create_group(session: Session, user: User, name: str) -> ProductGroup:
    name = _group_name(name)
    _group_taken(session, name)
    g = ProductGroup(name=name)
    session.add(g)
    session.flush()
    audit.record(session, user, "product_group.create", "product_group", str(g.id), None, {"name": name})
    session.commit()
    return g


def rename_group(session: Session, user: User, group_id: int, name: str) -> ProductGroup:
    """Đổi tên nhóm và đổi luôn tên nhóm trên mọi mã hàng thuộc nhóm, trong cùng một lần lưu."""
    g = load_group(session, group_id)
    name = _group_name(name)
    if name == g.name:
        return g
    _group_taken(session, name, g.id)
    old = g.name
    for p in session.scalars(select(Product).where(Product.group_name == old)):
        p.group_name = name
        p.search_text = _search_text(p)
    g.name = name
    session.flush()
    audit.record(session, user, "product_group.rename", "product_group", str(g.id), {"name": old}, {"name": name})
    session.commit()
    return g


def delete_group(session: Session, user: User, group_id: int) -> None:
    g = load_group(session, group_id)
    used = session.scalar(select(func.count()).select_from(Product).where(Product.group_name == g.name))
    if used:
        raise BusinessError("GROUP_IN_USE", f"Nhóm \"{g.name}\" còn {used} mã hàng. Hãy chuyển các mã đó sang nhóm khác rồi mới xóa.", status=409)
    audit.record(session, user, "product_group.delete", "product_group", str(g.id), {"name": g.name}, None)
    session.delete(g)
    session.commit()


def suggest_code(session: Session) -> str:
    """Mã kế tiếp sau mã TD lớn nhất hiện có, chỉ để gợi ý; người nhập vẫn quyết định."""
    codes = session.scalars(select(Product.code).where(Product.code.like("TD-%")).order_by(func.length(Product.code).desc(), Product.code.desc()).limit(1)).all()
    n = int(re.sub(r"\D", "", codes[0])) + 1 if codes else 1
    return f"TD-{n:04d}"


def create(session: Session, user: User, body: ProductIn) -> Product:
    if (body.warranty_months or body.code=='MAY-CU-CARE') and not has_role(user,'director'):
        raise BusinessError('FORBIDDEN','Chỉ giám đốc được cấu hình bảo hành máy.',status=403)
    p = Product(**body.model_dump())
    p.code = p.code.upper()
    p.name = p.name.strip()
    p.group_name = p.group_name.strip()
    _check_group(session, p.group_name)
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
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    if 'warranty_months' in changes and changes['warranty_months']!=p.warranty_months and not has_role(user,'director'):
        raise BusinessError('FORBIDDEN','Chỉ giám đốc được cấu hình bảo hành máy.',status=403)
    if changes.get("group_name") is not None and changes["group_name"].strip() != before["group_name"]:
        _check_group(session, changes["group_name"].strip())
    for k, v in changes.items():
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
