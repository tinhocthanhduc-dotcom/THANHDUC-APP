"""API read gates share the web navigation policy; write dependencies stay separate."""
from fastapi import Depends

from .models import User
from .errors import BusinessError
from .security import current_user, has_role


def check_web_read(user: User, web_path: str) -> None:
    # Resolve at request time to avoid a security/web package import cycle.
    from .web.nav import check_access
    check_access(user, web_path)


def require_web_read(web_path: str):
    def dependency(user: User = Depends(current_user)) -> User:
        check_web_read(user, web_path)
        return user
    return dependency


def require_cost_read(user: User = Depends(current_user)) -> User:
    # LG2: raw cost payloads are restricted even when the web has a price-free view.
    if not has_role(user, 'warehouse', 'finance'):
        raise BusinessError('FORBIDDEN', 'Chỉ kho, kế toán hoặc giám đốc được đọc dữ liệu giá vốn.', status=403)
    return user


def check_attachment_read(user: User, entity: str) -> None:
    # Other attachment entities already enforce role and ownership in TARGETS.
    paths = {'stock_doc': '/phieu-kho', 'service_ticket': '/dich-vu'}
    if entity in paths:
        check_web_read(user, paths[entity])
