"""Tạo tài khoản đầu tiên trên máy chủ mới.

    python -m scripts.create_admin --username tuan --name "Bùi Minh Tuấn" --roles admin,director
Mật khẩu được hỏi khi chạy, không gõ trên dòng lệnh.
"""
import argparse
import getpass

from sqlalchemy import select

from app.db import get_session
from app.models import ROLES, User
from app.security import hash_password


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--roles", default="admin,director")
    args = ap.parse_args()
    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    bad = [r for r in roles if r not in ROLES]
    if bad:
        raise SystemExit(f"Vai trò không tồn tại: {bad}. Có: {ROLES}")
    password = getpass.getpass("Mật khẩu (ít nhất 10 ký tự): ")
    if password != getpass.getpass("Nhập lại: "):
        raise SystemExit("Hai lần nhập không khớp.")
    session = next(get_session())
    if session.scalars(select(User).where(User.username == args.username)).first():
        raise SystemExit("Tên đăng nhập đã có.")
    session.add(User(username=args.username.lower(), full_name=args.name, password_hash=hash_password(password), roles=roles))
    session.commit()
    print(f"Đã tạo {args.username} với vai trò {roles}")


if __name__ == "__main__":
    main()
