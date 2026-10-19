"""Tạo tóm tắt ngày bằng tài khoản giám đốc được chỉ định; không gửi ra ngoài."""
import argparse
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import daily_digest, dashboard
from app.db import engine
from app.errors import BusinessError
from app.models import User
from app.security import has_role


def main(argv=None):
    parser = argparse.ArgumentParser(description='Lưu và in tóm tắt ngày cho giám đốc.')
    parser.add_argument('--user', required=True, help='Tên đăng nhập của giám đốc đang hoạt động')
    parser.add_argument('--day', type=date.fromisoformat, default=None, help='YYYY-MM-DD; mặc định hôm nay giờ Việt Nam')
    args = parser.parse_args(argv)
    with Session(engine(), expire_on_commit=False) as session:
        user = session.scalar(select(User).where(User.username == args.user.strip().lower()))
        if user is None or not user.active or not has_role(user, 'director'):
            parser.exit(1, 'Lỗi: tài khoản phải là giám đốc đang hoạt động. Không lưu bản tóm tắt.\n')
        try:
            row = daily_digest.save(session, user, args.day or dashboard._now().date(), scheduled=True)
        except BusinessError as exc:
            parser.exit(1, f'Lỗi {exc.code}: {exc.message}\n')
        print(row.text)


if __name__ == '__main__':
    main()
