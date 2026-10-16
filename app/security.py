"""Đăng nhập và phân quyền theo vai trò."""
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session
from .errors import BusinessError
from .models import User

_hasher = PasswordHasher()
_oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)
# Giám đốc có mọi quyền nghiệp vụ; admin chỉ quản lý tài khoản.
_ALL_BUSINESS = "director"


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise BusinessError("WEAK_PASSWORD", "Mật khẩu phải có ít nhất 10 ký tự.")
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def issue_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {"sub": str(user.id), "iat": now, "exp": now + timedelta(minutes=s.token_minutes)}
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def current_user(token: str | None = Depends(_oauth2), session: Session = Depends(get_session)) -> User:
    if not token:
        raise BusinessError("UNAUTHENTICATED", "Cần đăng nhập.", status=401)
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise BusinessError("UNAUTHENTICATED", "Phiên đăng nhập không hợp lệ hoặc đã hết hạn.", status=401)
    user = session.get(User, user_id)
    if not user or not user.active:
        raise BusinessError("UNAUTHENTICATED", "Tài khoản đã bị khóa.", status=401)
    return user


def has_role(user: User, *roles: str) -> bool:
    if "admin" in roles and "admin" in user.roles:
        return True
    return any(r in user.roles for r in roles) or (_ALL_BUSINESS in user.roles and "admin" not in roles)


def require(*roles: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if not has_role(user, *roles):
            raise BusinessError("FORBIDDEN", "Tài khoản không có quyền thao tác này.", status=403)
        return user

    return dependency
