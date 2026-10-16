from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_session
from ..errors import BusinessError
from ..models import ROLES, User
from ..schemas import Token, UserIn, UserOut
from ..security import current_user, hash_password, issue_token, require, verify_password

router = APIRouter(tags=["Đăng nhập"])


@router.post("/auth/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    user = session.scalars(select(User).where(User.username == form.username.strip().lower())).first()
    if not user or not user.active or not verify_password(form.password, user.password_hash):
        raise BusinessError("BAD_LOGIN", "Sai tên đăng nhập hoặc mật khẩu.", status=401)
    return Token(access_token=issue_token(user))


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require("admin")), session: Session = Depends(get_session)):
    return session.scalars(select(User).order_by(User.username)).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserIn, admin: User = Depends(require("admin")), session: Session = Depends(get_session)):
    bad = [r for r in body.roles if r not in ROLES]
    if bad:
        raise BusinessError("UNKNOWN_ROLE", "Vai trò không tồn tại.", {"roles": bad})
    if session.scalars(select(User).where(User.username == body.username)).first():
        raise BusinessError("USERNAME_TAKEN", "Tên đăng nhập đã có.", status=409)
    user = User(username=body.username, full_name=body.full_name, password_hash=hash_password(body.password), roles=body.roles)
    session.add(user)
    session.flush()
    audit.record(session, admin, "user.create", "user", str(user.id), None, {"username": user.username, "roles": user.roles})
    session.commit()
    return user
