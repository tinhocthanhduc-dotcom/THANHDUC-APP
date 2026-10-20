from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import users as user_service
from ..db import get_session
from ..models import User
from ..schemas import Token, UserIn, UserOut
from ..security import current_user, require

router = APIRouter(tags=["Đăng nhập"])


@router.post("/auth/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    user,token=user_service.authenticate(session,form.username,form.password)
    return Token(access_token=token)


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.get("/auth/users", response_model=list[UserOut])
@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require("admin")), session: Session = Depends(get_session)):
    return session.scalars(select(User).order_by(User.username)).all()


@router.post("/auth/users", response_model=UserOut, status_code=201)
@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserIn, admin: User = Depends(require("admin")), session: Session = Depends(get_session)):
    user,_=user_service.create(session,admin,body.username,body.full_name,'',body.roles,password=body.password)
    return user
